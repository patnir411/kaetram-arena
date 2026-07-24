"""OPD data build: current-student rollouts scored against the 4B teacher.

Round-agnostic: the student endpoint (TWOB_EP) is whatever policy GENERATED the
source rollouts — base-2B for round 1, the 2b-opd-r1 endpoint for round 2 — so
behavior_logprobs always come from the rollout policy and init==generator holds
per round. For every action turn it renders the (context, action) pair ONCE with
the student's own tokenizer (Qwen/Qwen3.5-2B + patch — identical to what the
serve files render) and POSTs the pre-rendered text to BOTH /v1/score
endpoints via the raw-text path. The endpoints tokenize the same string with a
shared vocab, so student and teacher logprobs land on identical token ids — the
Qwen3.5 repos ship different chat-template revisions, which is why the messages
path can't be used across sizes.

Load discipline (the first run wedged both endpoints): the serve classes take
@modal.concurrent(max_inputs=16); this client holds at most PER_EP_CONCURRENCY
in flight per endpoint, renders in a worker thread so the event loop keeps
servicing responses, and submits in chunks. Provenance-sealed builds are
create-only: a crash leaves an explicitly partial artifact that must be moved
aside before a fresh build. The builder never attaches a new receipt to
pre-existing records.

Emits pre-tokenized training records for finetune/train_opd_2b.py:
    input_ids          = ctx_ids + target_ids
    labels             = -100 over context, target ids over the action
    advantages         = 0 over context, -KL_COEF*(logp_student - logp_teacher) over action
    behavior_logprobs  = 0 over context, student logprobs over the action
    step_weight        = 1.5 for turns in the first third of their session, else 1.0
                         (TCOD-style insurance: early-turn states are the best-supported
                         supervision for a small multi-turn student)

Every 10th session is held out (never trained) into heldout.jsonl, carrying the
rendered texts plus both endpoints' build-time logprobs — the post-train KL gate
(scripts/opd/opd_gate.py) re-scores only the new student on these.

Usage (round 2 — student EP is the r1 endpoint):
  TWOB_EP=https://patnir411--kaetram-qwen-2b-opd-inference-serve.modal.run/v1 \
  FOURB_EP=https://.../v1 \
    python3 scripts/opd/opd_2b_data.py --run-ids run_20260610_140358 <seeded_run> \
      --out-dir dataset/opd_2b/round2 \
      --student-artifact-id qwen-2b-r1 --student-artifact-sha256 <64-hex> \
      --teacher-artifact-id qwen-4b --teacher-artifact-sha256 <64-hex> \
      --tokenizer-path /immutable/qwen-tokenizer
  # Keep the emitted records and records.manifest.json together. The trainer
  # requires both --records-path and --records-manifest-path.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

FROZEN_MARKER_NAME = ".kaetram-opd-frozen-build.json"
FROZEN_MARKER_SCHEMA = "kaetram-opd-frozen-entrypoint-v1"
EXECUTION_ROOT = Path(__file__).resolve().parents[2]
FROZEN_MARKER_PATH = EXECUTION_ROOT / FROZEN_MARKER_NAME
if FROZEN_MARKER_PATH.is_file():
    try:
        _FROZEN_MARKER = json.loads(FROZEN_MARKER_PATH.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("frozen OPD entrypoint marker is invalid") from exc
    if (
        not isinstance(_FROZEN_MARKER, dict)
        or set(_FROZEN_MARKER)
        != {"schema_version", "source_repo", "build_sources_sha256"}
        or _FROZEN_MARKER.get("schema_version") != FROZEN_MARKER_SCHEMA
        or not isinstance(_FROZEN_MARKER.get("source_repo"), str)
        or not _FROZEN_MARKER["source_repo"]
    ):
        raise RuntimeError("frozen OPD entrypoint marker fields are invalid")
    REPO = Path(_FROZEN_MARKER["source_repo"]).resolve()
else:
    _FROZEN_MARKER = None
    REPO = EXECUTION_ROOT
# This list is deliberately available without importing any local module. Main
# snapshots it first, then imports every local dependency from that immutable
# copy. receipt_chain.py carries the same closed inventory and checks equality
# when the frozen modules are loaded.
BUILD_SOURCE_PATHS = (
    "bootstrap.py",
    "canonical_start.py",
    "eval_harness.py",
    "inference_seed.py",
    "finetune/render.py",
    "scripts/opd/canonicalize.py",
    "heldout_guard.py",
    "port_probe.py",
    "run_manifest.py",
    "scripts/isolated_python_entry.py",
    "tool_surface.py",
    "scripts/opd/opd_2b_data.py",
    "scripts/opd/opd_data_manifest.py",
    "scripts/opd/opd_probe.py",
    "scripts/opd/opd_round1.py",
    "scripts/opd/opd_wall_probe.py",
    "scripts/opd/record_schema.py",
    "scripts/opd/receipt_chain.py",
    "scripts/log_analysis/parse.py",
    "prompts/game_knowledge.md",
    "prompts/personalities/completionist.md",
    "prompts/personalities/explorer_tinkerer.md",
    "prompts/personalities/grinder.md",
    "prompts/system.md",
    "research/experiments/heldout-quest-v2.json",
    "research/experiments/heldout-quest.json",
)

STUDENT_EP = os.environ["TWOB_EP"].rstrip("/")
TEACHER_EP = os.environ["FOURB_EP"].rstrip("/")

MAX_HIST_MSGS = 28
MAX_SEQ = 16384
KL_COEF = 1.0
HOLDOUT_EVERY = 10        # session-level holdout for the post-train KL gate
EARLY_WEIGHT = 1.5        # step_weight for the first third of each session
# Malformed tool-call parameter key (kwarg written into the key, e.g.
# <parameter=accept_quest_offer=True>) — advantages on these spans are masked.
MALFORMED_PARAM_RE = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PER_EP_CONCURRENCY = 6    # per-endpoint in-flight cap (server max_running_requests=8)
CHUNK = int(os.environ.get("OPD_BUILD_CHUNK", "200"))  # states per submission wave; records flush per wave, so smaller = more frequent progress/failure visibility on slow (16K-context) tails
SCORE_TIMEOUT = 240.0
SCORE_RETRIES = 3


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _training_handoff_text(records_path: Path, manifest_path: Path) -> str:
    def display(path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    records = display(records_path)
    manifest = display(manifest_path)
    return "\n".join([
        "",
        "Next: stage this inseparable training-record bundle:",
        f"  records:  {records}",
        f"  manifest: {manifest}",
        "Trainer arguments after staging (replace <staged-bundle>; both mandatory):",
        "  --records-path <staged-bundle>/records.jsonl",
        "  --records-manifest-path <staged-bundle>/records.manifest.json",
    ])


def is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _load_frozen_local_dependencies(snapshot_root: Path) -> None:
    """Import all local execution code from the pre-import immutable snapshot."""
    global BUILDER_RELATIVE_PATH, MANIFEST_SCHEMA_VERSION
    global OPD_TRAIN_RECORD_SCHEMA_SHA256, OPD_TRAIN_RECORD_SCHEMA_VERSION
    global OPD_TRAIN_RECORD_VALIDATOR_SHA256
    global _finished_from_payload, _frontier, assert_text_not_reserved
    global docify_system_prompt, is_malformed, patch_qwen_chat_template
    global reconstruct_session, turn_to_chat

    local_module_names = {
        "bootstrap",
        "canonical_start",
        "canonicalize",
        "eval_harness",
        "heldout_guard",
        "inference_seed",
        "opd_data_manifest",
        "opd_probe",
        "opd_round1",
        "opd_wall_probe",
        "parse",
        "port_probe",
        "receipt_chain",
        "record_schema",
        "render",
        "run_manifest",
        "scripts.isolated_python_entry",
        "tool_surface",
    }
    preloaded = sorted(local_module_names & set(sys.modules))
    if preloaded:
        raise RuntimeError(
            "local builder dependencies were cached before the frozen import: "
            + ", ".join(preloaded)
        )

    for path in (
        snapshot_root,
        snapshot_root / "scripts" / "opd",
        snapshot_root / "scripts" / "log_analysis",
        snapshot_root / "finetune",
    ):
        sys.path.insert(0, str(path))

    from canonicalize import docify_system_prompt as frozen_docify_system_prompt
    from canonicalize import is_malformed as frozen_is_malformed
    from heldout_guard import assert_text_not_reserved as frozen_assert_text_not_reserved
    from opd_data_manifest import BUILDER_RELATIVE_PATH as frozen_builder_path
    from opd_data_manifest import MANIFEST_SCHEMA_VERSION as frozen_manifest_schema
    from opd_probe import reconstruct_session as frozen_reconstruct_session
    from opd_round1 import turn_to_chat as frozen_turn_to_chat
    from opd_wall_probe import _finished_from_payload as frozen_finished_from_payload
    from opd_wall_probe import _frontier as frozen_frontier
    from receipt_chain import BUILD_SOURCE_PATHS as receipt_build_source_paths
    from record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_SHA256 as frozen_record_schema_sha256,
    )
    from record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_VERSION as frozen_record_schema_version,
    )
    from record_schema import (
        OPD_TRAIN_RECORD_VALIDATOR_SHA256 as frozen_validator_sha256,
    )
    from render import patch_qwen_chat_template as frozen_patch_qwen_chat_template

    if tuple(receipt_build_source_paths) != tuple(BUILD_SOURCE_PATHS):
        raise RuntimeError("builder and receipt source inventories disagree")
    BUILDER_RELATIVE_PATH = frozen_builder_path
    MANIFEST_SCHEMA_VERSION = frozen_manifest_schema
    OPD_TRAIN_RECORD_SCHEMA_SHA256 = frozen_record_schema_sha256
    OPD_TRAIN_RECORD_SCHEMA_VERSION = frozen_record_schema_version
    OPD_TRAIN_RECORD_VALIDATOR_SHA256 = frozen_validator_sha256
    _finished_from_payload = frozen_finished_from_payload
    _frontier = frozen_frontier
    assert_text_not_reserved = frozen_assert_text_not_reserved
    docify_system_prompt = frozen_docify_system_prompt
    is_malformed = frozen_is_malformed
    patch_qwen_chat_template = frozen_patch_qwen_chat_template
    reconstruct_session = frozen_reconstruct_session
    turn_to_chat = frozen_turn_to_chat


def load_student_tokenizer(tokenizer_path: Path):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    patch_qwen_chat_template(tok)
    return tok


def _emission_text(turn):
    """The model's raw continuation after the generation prompt: the logged
    text block VERBATIM (it already carries the inline <tool_call> XML —
    verified 100% of assistant text blocks in the qwen runs) plus the
    end-of-turn token. No re-synthesis from the parsed tool_calls: that
    doubles every call, and the parsed copy has malformed parameter keys
    already stripped — the verbatim bytes are what the policy emitted and
    what the teacher must grade.

    The emission is appended to ctx_text directly instead of re-rendering the
    completed turn through the template: the template extracts reasoning from
    any '</think>' inside content and re-renders it as a real think block,
    which diverges from the closed-empty-think generation prompt the model was
    actually served (round-1: 4% of base-2B turns; round-2: ~100% of r1 turns,
    whose dialect ends content with a dangling '</think>').

    Returns None for turns whose reasoning was logged as a separate thinking
    block (Claude-shaped logs) — the raw interleaving is lossy to reconstruct.
    """
    if turn.thinking:
        return None
    content = (turn.text or "").strip()
    if not content:
        return None
    return content + "<|im_end|>\n"


def _copy_and_hash(source: Path, destination: Path | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    output = None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            output = destination.open("xb")
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                if output is not None:
                    output.write(chunk)
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
    finally:
        if output is not None:
            output.close()
    return digest.hexdigest(), size


def _snapshot_source_logs(
    run_ids: list[str],
    *,
    snapshot_root: Path | None = None,
) -> list[dict]:
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise RuntimeError("--run-ids must contain unique run identifiers")
    inventory = []
    for run_id in run_ids:
        logs = sorted((REPO / "dataset" / "raw").glob(
            f"agent_*/runs/{run_id}/session_*.log"
        ))
        if not logs:
            raise RuntimeError(f"declared run has no source logs: {run_id}")
        for path in logs:
            resolved = path.resolve()
            meta = resolved.with_suffix(".meta.json")
            if not meta.is_file():
                raise RuntimeError(
                    f"source log has no adjacent session metadata: {meta}"
                )
            relative = resolved.relative_to(REPO).as_posix()
            meta_relative = meta.relative_to(REPO).as_posix()
            log_sha, log_size = _copy_and_hash(
                resolved,
                snapshot_root / relative if snapshot_root is not None else None,
            )
            meta_sha, meta_size = _copy_and_hash(
                meta,
                snapshot_root / meta_relative if snapshot_root is not None else None,
            )
            try:
                metadata = json.loads(
                    (snapshot_root / meta_relative if snapshot_root is not None else meta)
                    .read_text(encoding="utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"source session metadata is invalid: {meta}") from exc
            if not isinstance(metadata, dict):
                raise RuntimeError(f"source session metadata is not an object: {meta}")
            personality = metadata.get("personality") or "completionist"
            if not isinstance(personality, str) or not personality:
                raise RuntimeError(f"source session personality is invalid: {meta}")
            personality_prompt = f"prompts/personalities/{personality}.md"
            if personality_prompt not in BUILD_SOURCE_PATHS:
                raise RuntimeError(
                    f"source session selects an unbound personality prompt: {personality}"
                )
            inventory.append({
                "run_id": run_id,
                "path": relative,
                "sha256": log_sha,
                "size_bytes": log_size,
                "meta_path": meta_relative,
                "meta_sha256": meta_sha,
                "meta_size_bytes": meta_size,
                "personality_prompt_path": personality_prompt,
            })
    inventory.sort(key=lambda item: item["path"])
    if len({item["path"] for item in inventory}) != len(inventory):
        raise RuntimeError("source-log inventory contains duplicate paths")
    return inventory


def _verify_source_snapshot(inventory: list[dict]) -> None:
    for item in inventory:
        path = REPO / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item["size_bytes"]
            or sha256_path(path) != item["sha256"]
        ):
            raise RuntimeError(f"source log changed during the build: {item['path']}")
        meta = REPO / item["meta_path"]
        if (
            not meta.is_file()
            or meta.stat().st_size != item["meta_size_bytes"]
            or sha256_path(meta) != item["meta_sha256"]
        ):
            raise RuntimeError(
                f"source metadata changed during the build: {item['meta_path']}"
            )


def _snapshot_build_sources() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for relative in BUILD_SOURCE_PATHS:
        path = REPO / relative
        if not path.is_file():
            raise RuntimeError(f"material build input is missing: {relative}")
        snapshot[relative] = sha256_path(path)
    return snapshot


def _materialize_build_inputs(
    snapshot: dict[str, str],
    snapshot_root: Path,
) -> None:
    if set(snapshot) != set(BUILD_SOURCE_PATHS):
        raise RuntimeError("material build-input snapshot is incomplete")
    for relative, expected in snapshot.items():
        actual, _ = _copy_and_hash(REPO / relative, snapshot_root / relative)
        if actual != expected:
            raise RuntimeError(
                f"material build input changed while snapshotting: {relative}"
            )


def _snapshot_hashes(snapshot_root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for relative in BUILD_SOURCE_PATHS:
        path = snapshot_root / relative
        if not path.is_file():
            raise RuntimeError(f"frozen material build input is missing: {relative}")
        snapshot[relative] = sha256_path(path)
    return snapshot


def _verify_build_source_snapshot(snapshot: dict[str, str]) -> None:
    if set(snapshot) != set(BUILD_SOURCE_PATHS):
        raise RuntimeError("material build-input snapshot is incomplete")
    for relative, expected in snapshot.items():
        path = REPO / relative
        if not path.is_file() or sha256_path(path) != expected:
            raise RuntimeError(
                f"material build input changed during the build: {relative}"
            )


def _directory_digest(root: Path) -> str:
    inventory = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"tokenizer snapshot contains a symlink: {path}")
        if path.is_file():
            inventory.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_path(path),
                "size_bytes": path.stat().st_size,
            })
    if not inventory:
        raise RuntimeError("tokenizer snapshot contains no regular files")
    return canonical_sha256(inventory)


def _materialize_directory_snapshot(
    source_root: Path,
    destination_root: Path,
    *,
    expected_sha256: str,
) -> Path:
    inventory = []
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise RuntimeError(f"snapshot source contains a symlink: {source}")
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        digest, size = _copy_and_hash(source, destination_root / relative)
        inventory.append({
            "path": relative.as_posix(),
            "sha256": digest,
            "size_bytes": size,
        })
    if canonical_sha256(inventory) != expected_sha256:
        raise RuntimeError("directory changed while materializing its snapshot")
    return destination_root


def collect_action_states(
    inventory: list[dict],
    *,
    source_root: Path | None = None,
):
    """Per-turn (messages, emission, verb, frontier, session, turn_idx, n_turns,
    holdout) over the rollout logs. messages = [system, bootstrap, ...tail...]
    (context only); emission = the raw action continuation."""
    states = []
    n_no_emission = 0
    states_by_run = defaultdict(int)
    reconstruction_root = source_root.resolve() if source_root is not None else REPO
    for log_i, source in enumerate(inventory):
        lp = reconstruction_root / source["path"]
        try:
            base_messages, turns = reconstruct_session(
                lp,
                source_repo=reconstruction_root,
                render_project_dir=REPO,
            )
        except Exception as exc:
            raise RuntimeError(f"failed to parse declared source log: {lp}") from exc
        # Exclude the always-on system prompt from this scan: it mentions the
        # quest name as a warp prerequisite but contains no walkthrough.  Any
        # model action/reasoning or tool result touching the reserved quest is
        # nevertheless a hard stop before either endpoint grades the session.
        activity_text = json.dumps([
            {
                "text": turn.text,
                "tool_calls": turn.tool_calls,
                "results": [result.result_str for result in results],
            }
            for turn, results in turns
        ])
        assert_text_not_reserved(
            activity_text,
            use="teacher_grading",
            source=str(lp),
            path=(
                reconstruction_root
                / "research"
                / "experiments"
                / "heldout-quest-v2.json"
            ),
        )
        if not turns:
            raise RuntimeError(f"declared source log contains no turns: {lp}")
        holdout = (log_i % HOLDOUT_EVERY) == 0
        rolling = list(base_messages)
        finished: set[str] = set()
        session_states = []
        for turn_idx, (turn, results) in enumerate(turns):
            if turn.tool_calls:
                emission = _emission_text(turn)
                if emission is None:
                    n_no_emission += 1
                if emission is not None:
                    hist = rolling[2:]
                    tail = hist[-MAX_HIST_MSGS:] if len(hist) > MAX_HIST_MSGS else hist
                    session_states.append({
                        "messages": rolling[:2] + list(tail),
                        "emission": emission,
                        "verb": turn.short_tool_names[0],
                        "frontier": _frontier(finished),
                        "session": lp.name,
                        "turn_idx": turn_idx,
                        "holdout": holdout,
                        "source_run": source["run_id"],
                        "source_log": source["path"],
                    })
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
                fin = _finished_from_payload(tr.payload)
                if fin is not None:
                    finished = fin
        n_turns = len(turns)
        for st in session_states:
            st["n_turns"] = n_turns
            states_by_run[st["source_run"]] += 1
        states.extend(session_states)
    missing = sorted({item["run_id"] for item in inventory} - set(states_by_run))
    if missing:
        raise RuntimeError(
            "declared run produced no usable action state(s): " + ", ".join(missing)
        )
    if n_no_emission:
        raise RuntimeError(
            f"{n_no_emission} tool-call turn(s) have no reconstructible emission; "
            "refusing an incompletely accounted corpus"
        )
    return states


_BUILD_TOOLS = None
if os.environ.get("OPD_BUILD_TOOLS_JSON"):
    # Serving-context parity (Seam-1 repair): rollouts are generated and evals
    # served WITH the native tools= block (play_qwen sends it; the chat template
    # renders the canonical-format reminder). The historical builds rendered
    # build/score contexts WITHOUT it, so every gradient was computed on a
    # context missing that reminder — the environment where even base leaks
    # Python-call forms at 7–9% (defect-origin probe, 2026-07-16). Passing the
    # snapshot restores byte-parity between the gradient context and serving.
    with open(os.environ["OPD_BUILD_TOOLS_JSON"]) as _f:
        _d = json.load(_f)
        _BUILD_TOOLS = _d if isinstance(_d, list) else _d.get("tools")
    print(f"serving-context parity ON: rendering with {len(_BUILD_TOOLS)} tool specs")


def _render(tok, msgs, emission):
    """Synchronous template render + encode — runs in a worker thread.
    ctx = the exact serving prompt; full = ctx + the raw emission, so the
    prefix property holds by construction (see _emission_text)."""
    ctx_text = tok.apply_chat_template(
        msgs, tools=_BUILD_TOOLS, tokenize=False, add_generation_prompt=True)
    full_text = ctx_text + emission
    ctx_ids = tok.encode(ctx_text, add_special_tokens=False)
    return ctx_text, full_text, ctx_ids


async def _score_raw(client, endpoint, ctx_text, full_text):
    body = {"context_text": ctx_text, "full_text": full_text}
    for attempt in range(SCORE_RETRIES):
        try:
            r = await client.post(f"{endpoint}/score", json=body, timeout=SCORE_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                return None  # malformed turn (e.g. target empty) — skip
        except (httpx.TimeoutException, httpx.HTTPError):
            pass
        await asyncio.sleep(5.0 * (attempt + 1))
    return None


async def build_record(client, tok, st, sem_s, sem_t):
    ctx_text, full_text, ctx_ids = await asyncio.to_thread(
        _render, tok, st["messages"], st["emission"])
    if len(ctx_ids) >= MAX_SEQ:
        return None, "overlong"
    if not full_text.startswith(ctx_text):
        return None, "prefix_mismatch"  # structurally impossible now; kept as a tripwire

    # Counterfactual-canonicalized grading (flip-probe-verified, June 12): for
    # records whose EMISSION carries malformed tool syntax, the TEACHER grades
    # under a context whose system-prompt doc literals are reshaped to non-call
    # prose — its clean-convention preference then yields a corrective negative
    # advantage on the malformed tokens (median -1.21 nats, 86% of states)
    # instead of the +0.09 copy-prior endorsement. Student/behavior scoring
    # always uses the real context.
    # OPD_BUILD_NO_CF=1 forces the round-2 recipe (abstention masking, no
    # counterfactual grading) — required for arm parity in ablation builds
    # whose comparison arm was built pre-round-3 (e.g. the ±seeding ablation
    # against the round-2 corpus).
    counterfactual = (not os.environ.get("OPD_BUILD_NO_CF")) and is_malformed(st["emission"])
    if counterfactual:
        cf_msgs = [{**st["messages"][0],
                    "content": docify_system_prompt(st["messages"][0]["content"])}] \
                   + st["messages"][1:]
        cf_ctx, cf_full, _ = await asyncio.to_thread(_render, tok, cf_msgs, st["emission"])

    async def scored(ep, sem, c, f):
        async with sem:
            return await _score_raw(client, ep, c, f)

    if counterfactual:
        s_resp, t_resp, t_plain = await asyncio.gather(
            scored(STUDENT_EP, sem_s, ctx_text, full_text),
            scored(TEACHER_EP, sem_t, cf_ctx, cf_full),
            scored(TEACHER_EP, sem_t, ctx_text, full_text))  # kept for the ablation table
    else:
        s_resp, t_resp = await asyncio.gather(
            scored(STUDENT_EP, sem_s, ctx_text, full_text),
            scored(TEACHER_EP, sem_t, ctx_text, full_text))
        t_plain = None
    if not s_resp or not t_resp:
        return None, "score_fail"
    target = s_resp["target_token_ids"]
    if counterfactual and target != t_resp["target_token_ids"]:
        # Token-boundary guard: the emission must tokenize identically after
        # both prefixes. On mismatch fall back to the plain-ctx teacher score
        # and round-2 masking for this record.
        if t_plain and t_plain["target_token_ids"] == target:
            t_resp, t_plain, counterfactual = t_plain, None, False
        else:
            return None, "cf_boundary_mismatch"
    if target != t_resp["target_token_ids"]:
        return None, "target_mismatch"
    if len(ctx_ids) != s_resp["n_context_tokens"]:
        return None, "ctx_len_mismatch"
    if len(ctx_ids) + len(target) > MAX_SEQ:
        return None, "overlong"
    s_lp = s_resp["target_logprobs"]
    t_lp = t_resp["target_logprobs"]

    if st["holdout"]:
        rec = {
            "context_text": ctx_text, "full_text": full_text,
            "teacher_logprobs": t_lp, "student_base_logprobs": s_lp,
            "verb": st["verb"], "frontier": st["frontier"], "session": st["session"],
            "turn_idx": st["turn_idx"],
        }
        return rec, "holdout"

    # Raw advantages; the trainer's ADV_CLAMP=3 handles tails (round-1 recipe —
    # with byte-faithful emissions, large disagreements are signal, not seams).
    adv_t, beh_t, rkls = [], [], []
    for si, ti in zip(s_lp, t_lp):
        if si is None or ti is None:
            adv_t.append(0.0); beh_t.append(0.0)
        else:
            rkl = si - ti
            adv_t.append(-KL_COEF * rkl); beh_t.append(si); rkls.append(rkl)

    # Round-2 abstention masking — now the FALLBACK path only: applied when a
    # flagged record could not be counterfactually graded (boundary mismatch).
    # Counterfactually-graded records keep their flagged spans LIVE: the
    # clean-doc teacher grade there is the corrective signal.
    n_masked = 0
    spans = [(m.start(), m.end()) for m in MALFORMED_PARAM_RE.finditer(full_text)
             if m.start() >= len(ctx_text)]
    if spans and not counterfactual:
        enc = await asyncio.to_thread(
            tok, full_text, add_special_tokens=False, return_offsets_mapping=True)
        if enc["input_ids"][len(ctx_ids):] != target:
            return None, "mask_align_fail"
        offs = enc["offset_mapping"][len(ctx_ids):]
        for i, (a, b) in enumerate(offs):
            if any(a < e and b > s for s, e in spans) and adv_t[i] != 0.0:
                adv_t[i] = 0.0
                n_masked += 1

    rec = {
        "input_ids": ctx_ids + target,
        "labels": [-100] * len(ctx_ids) + list(target),
        "advantages": [0.0] * len(ctx_ids) + adv_t,
        "behavior_logprobs": [0.0] * len(ctx_ids) + beh_t,
        "step_weight": EARLY_WEIGHT if st["turn_idx"] < st["n_turns"] / 3 else 1.0,
        "verb": st["verb"], "frontier": st["frontier"], "session": st["session"],
        "turn_idx": st["turn_idx"],
        "n_action": len(target), "mean_rkl": (sum(rkls) / len(rkls)) if rkls else 0.0,
        "n_masked": n_masked, "n_masked_spans": len(spans),
        "counterfactual": counterfactual,
    }
    if counterfactual and t_plain:
        # Ablation bookkeeping: the plain-ctx teacher logprobs alongside the
        # clean-doc grades actually used for the advantages.
        rec["teacher_logprobs_plain"] = t_plain["target_logprobs"]
    return rec, "ok_cf" if counterfactual else "ok"


def _print_diagnostic(diag):
    print("\n## Pre-train reverse-KL diagnostic  (mean logp_2B - logp_4B, per action token)")
    print("   positive => the 2B is over-confident where the 4B disagrees (OPD suppresses);")
    print("   expect the 2B's weak verbs (eat_food, observe-loops) among the largest.\n")
    by_verb = defaultdict(lambda: [0.0, 0])
    by_front = defaultdict(lambda: [0.0, 0])
    for (verb, front), (s, n) in diag.items():
        by_verb[verb][0] += s; by_verb[verb][1] += n
        by_front[front][0] += s; by_front[front][1] += n
    print("  by tool verb:")
    for verb in sorted(by_verb, key=lambda v: -(by_verb[v][0] / max(by_verb[v][1], 1))):
        s, n = by_verb[verb]
        if n:
            print(f"    {verb:<14} mean_rkl {s/n:+.4f}   ({n} action tokens)")
    print("\n  by Core-3 frontier:")
    for front in ["Foresting", "Herbalist", "Ricks", "post-core"]:
        s, n = by_front.get(front, [0.0, 0])
        if n:
            print(f"    {front:<12} mean_rkl {s/n:+.4f}   ({n} action tokens)")


def _health_url(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise RuntimeError("scoring endpoint must be an HTTP(S) URL")
    base_path = parts.path.rstrip("/")
    if base_path.endswith("/v1"):
        base_path = base_path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, base_path + "/health", "", ""))


async def _verified_endpoint_attestation(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    expected_deployment_id: str,
    expected_checkpoint_sha256: str,
) -> dict:
    try:
        response = await client.get(_health_url(endpoint), timeout=60)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError("scoring endpoint did not return valid /health JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError("scoring endpoint is not healthy")
    if "score" not in (payload.get("capabilities") or []):
        raise RuntimeError("scoring endpoint does not attest the score capability")
    attestation = payload.get("attestation")
    fields = {
        "deployment_id",
        "api_model",
        "checkpoint_sha256",
        "tokenizer_sha256",
        "render_contract_sha256",
    }
    if not isinstance(attestation, dict) or set(attestation) != fields:
        raise RuntimeError("scoring endpoint has no complete identity attestation")
    if (
        attestation.get("deployment_id") != expected_deployment_id
        or attestation.get("checkpoint_sha256") != expected_checkpoint_sha256
        or not isinstance(attestation.get("api_model"), str)
        or not attestation["api_model"]
        or any(
            not is_digest(attestation.get(field))
            for field in (
                "checkpoint_sha256",
                "tokenizer_sha256",
                "render_contract_sha256",
            )
        )
    ):
        raise RuntimeError("scoring endpoint identity does not match the requested artifact")
    return attestation


def _state_identity(state: dict) -> dict:
    return {
        "source_run": state["source_run"],
        "source_log": state["source_log"],
        "session": state["session"],
        "turn_idx": state["turn_idx"],
        "verb": state["verb"],
        "frontier": state["frontier"],
        "holdout": state["holdout"],
    }


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise RuntimeError(
            f"refusing to replace a concurrently created artifact: {destination}"
        ) from exc
    temporary.unlink()


async def main():
    if _FROZEN_MARKER is None:
        raise RuntimeError(
            "OPD builder must run through its frozen entrypoint; execute this "
            "file as a script instead of importing main()"
        )
    snapshot_root = EXECUTION_ROOT
    system_temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        snapshot_root == REPO
        or snapshot_root.is_relative_to(REPO)
        or not snapshot_root.is_relative_to(system_temp_root)
    ):
        raise RuntimeError(
            "OPD frozen build root must be isolated from the mutable source repository"
        )

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", default=["run_20260610_140358"])
    ap.add_argument("--out-dir", default="dataset/opd_2b/round2")
    ap.add_argument("--limit", type=int, default=0, help="cap states (0 = all; for smoke tests)")
    ap.add_argument("--student-artifact-id", required=True)
    ap.add_argument("--student-artifact-sha256", required=True)
    ap.add_argument("--teacher-artifact-id", required=True)
    ap.add_argument("--teacher-artifact-sha256", required=True)
    ap.add_argument(
        "--tokenizer-path",
        required=True,
        help="immutable local tokenizer snapshot containing tokenizer.json",
    )
    args = ap.parse_args()
    # The launcher copied every local input before this interpreter loaded the
    # builder. This process executes that copied builder and imports all other
    # local code only from the same tree.
    build_sources = _snapshot_hashes(snapshot_root)
    if (
        _FROZEN_MARKER.get("build_sources_sha256")
        != canonical_sha256(build_sources)
    ):
        raise RuntimeError("frozen OPD source inventory does not match its launcher")
    _verify_build_source_snapshot(build_sources)
    _load_frozen_local_dependencies(snapshot_root)
    for name in ("student_artifact_sha256", "teacher_artifact_sha256"):
        value = getattr(args, name)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            ap.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256")
    for name in ("student_artifact_id", "teacher_artifact_id"):
        value = getattr(args, name)
        if not value.strip() or "://" in value or any(char.isspace() for char in value):
            ap.error(
                f"--{name.replace('_', '-')} must be a non-URL artifact identifier"
            )

    source_inventory = _snapshot_source_logs(
        args.run_ids,
        snapshot_root=snapshot_root,
    )
    _verify_source_snapshot(source_inventory)
    async with httpx.AsyncClient() as identity_client:
        student_attestation, teacher_attestation = await asyncio.gather(
            _verified_endpoint_attestation(
                identity_client,
                STUDENT_EP,
                expected_deployment_id=args.student_artifact_id,
                expected_checkpoint_sha256=args.student_artifact_sha256,
            ),
            _verified_endpoint_attestation(
                identity_client,
                TEACHER_EP,
                expected_deployment_id=args.teacher_artifact_id,
                expected_checkpoint_sha256=args.teacher_artifact_sha256,
            ),
        )
    tokenizer_path = Path(args.tokenizer_path).resolve()
    tokenizer_file = tokenizer_path / "tokenizer.json"
    if not tokenizer_file.is_file():
        ap.error("--tokenizer-path must contain tokenizer.json")
    tokenizer_sha256 = sha256_path(tokenizer_file)
    tokenizer_snapshot_sha256 = _directory_digest(tokenizer_path)
    frozen_tokenizer_path = _materialize_directory_snapshot(
        tokenizer_path,
        snapshot_root / "tokenizer",
        expected_sha256=tokenizer_snapshot_sha256,
    )
    if (
        sha256_path(frozen_tokenizer_path / "tokenizer.json")
        != tokenizer_sha256
    ):
        raise RuntimeError("tokenizer.json changed while materializing its snapshot")
    if (
        student_attestation["tokenizer_sha256"] != tokenizer_sha256
        or teacher_attestation["tokenizer_sha256"] != tokenizer_sha256
    ):
        ap.error(
            "local tokenizer.json does not match both endpoint identity attestations"
        )

    tok = load_student_tokenizer(frozen_tokenizer_path)
    states = collect_action_states(source_inventory, source_root=snapshot_root)
    if args.limit:
        states = states[: args.limit]
    candidate_state_identities = [_state_identity(state) for state in states]

    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rec_path = out_dir / "records.jsonl"
    hold_path = out_dir / "heldout.jsonl"
    manifest_path = out_dir / "records.manifest.json"

    existing = [path for path in (rec_path, hold_path, manifest_path) if path.exists()]
    if existing:
        rendered = ", ".join(str(path) for path in existing)
        sys.exit(
            "FATAL: provenance-sealed builds are create-only; move the partial or "
            f"completed artifacts before retrying: {rendered}"
        )

    n_hold = sum(1 for s in states if s["holdout"])
    print(f"action states to score: {len(states)} ({n_hold} held out at session level) "
          f"from {args.run_ids}", flush=True)

    sem_s = asyncio.Semaphore(PER_EP_CONCURRENCY)
    sem_t = asyncio.Semaphore(PER_EP_CONCURRENCY)
    counts = defaultdict(int)
    excluded_states: list[dict] = []
    diag = defaultdict(lambda: [0.0, 0])
    n_ok = n_hold_done = 0
    n_masked_tokens = n_masked_spans = n_action_tokens = 0

    limits = httpx.Limits(max_connections=PER_EP_CONCURRENCY * 2 + 2)
    async with httpx.AsyncClient(limits=limits) as client:
        if states:  # warm both endpoints with one state before the waves
            first = states[0]
            ctx, full, _ = await asyncio.to_thread(
                _render, tok, first["messages"], first["emission"])
            warm_s, warm_t = await asyncio.gather(
                _score_raw(client, STUDENT_EP, ctx, full),
                _score_raw(client, TEACHER_EP, ctx, full))
            # A dead/undeployed endpoint fails every state in minutes while
            # looking like transient score_fail churn — die loudly instead.
            if not warm_s or not warm_t:
                sys.exit(f"FATAL: warm-up scoring failed "
                         f"(student={'ok' if warm_s else 'FAIL'} @ {STUDENT_EP}, "
                         f"teacher={'ok' if warm_t else 'FAIL'} @ {TEACHER_EP}) — "
                         f"endpoint down or URL wrong; not starting the build.")
        with open(rec_path, "x") as rf, open(hold_path, "x") as hf:
            for i in range(0, len(states), CHUNK):
                chunk = states[i:i + CHUNK]
                results = await asyncio.gather(
                    *(build_record(client, tok, st, sem_s, sem_t) for st in chunk))
                unexpected = [
                    {**_state_identity(state), "status": status}
                    for state, (_, status) in zip(chunk, results)
                    if status not in {"ok", "ok_cf", "holdout", "overlong"}
                ]
                if unexpected:
                    first = unexpected[0]
                    raise RuntimeError(
                        "record construction failed closed: "
                        f"{first['status']} at {first['source_log']} "
                        f"turn {first['turn_idx']}"
                    )
                for st, (rec, status) in zip(chunk, results):
                    counts[status] += 1
                    if status in ("ok", "ok_cf"):
                        rf.write(json.dumps(rec) + "\n")
                        n_ok += 1
                        n_act = rec["n_action"]
                        n_masked_tokens += rec["n_masked"]
                        n_masked_spans += rec["n_masked_spans"]
                        n_action_tokens += n_act
                        diag[(rec["verb"], rec["frontier"])][0] += rec["mean_rkl"] * n_act
                        diag[(rec["verb"], rec["frontier"])][1] += n_act
                    elif status == "holdout":
                        hf.write(json.dumps(rec) + "\n")
                        n_hold_done += 1
                    else:
                        excluded_states.append({
                            **_state_identity(st),
                            "status": status,
                        })
                rf.flush(); hf.flush()
                print(f"  {min(i + CHUNK, len(states))}/{len(states)}  {dict(counts)}", flush=True)

    print(f"\n=== build done: {dict(counts)} ===")
    print(f"train records appended: {n_ok} -> {rec_path}")
    print(f"heldout appended:       {n_hold_done} -> {hold_path}")
    if n_ok == 0:
        raise RuntimeError("record build produced no training records")
    if n_action_tokens:
        print(f"malformed-param spans masked: {n_masked_spans} spans / "
              f"{n_masked_tokens} tokens ({n_masked_tokens/n_action_tokens*100:.2f}% of action tokens)")
    _print_diagnostic(diag)
    _verify_source_snapshot(source_inventory)
    async with httpx.AsyncClient() as identity_client:
        ending_student, ending_teacher = await asyncio.gather(
            _verified_endpoint_attestation(
                identity_client,
                STUDENT_EP,
                expected_deployment_id=args.student_artifact_id,
                expected_checkpoint_sha256=args.student_artifact_sha256,
            ),
            _verified_endpoint_attestation(
                identity_client,
                TEACHER_EP,
                expected_deployment_id=args.teacher_artifact_id,
                expected_checkpoint_sha256=args.teacher_artifact_sha256,
            ),
        )
    if ending_student != student_attestation or ending_teacher != teacher_attestation:
        raise RuntimeError("scoring endpoint identity changed during the build")
    _verify_build_source_snapshot(build_sources)
    if (
        sha256_path(tokenizer_file) != tokenizer_sha256
        or _directory_digest(tokenizer_path) != tokenizer_snapshot_sha256
    ):
        raise RuntimeError("tokenizer snapshot changed during the build")
    # Root receipts are emitted only here, from the two files opened with
    # exclusive mode by this invocation. There is intentionally no reusable
    # post-hoc attestor that could relabel arbitrary existing records.
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "builder": BUILDER_RELATIVE_PATH,
        "script_sha256": build_sources[BUILDER_RELATIVE_PATH],
        "source_runs": list(args.run_ids),
        "source_logs": source_inventory,
        "source_sha256": canonical_sha256(source_inventory),
        "output": str(rec_path.resolve()),
        "output_sha256": sha256_path(rec_path),
        "heldout": str(hold_path.resolve()),
        "heldout_sha256": sha256_path(hold_path),
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "n_records": n_ok,
        "n_heldout": n_hold_done,
        "candidate_states": len(states),
        "candidate_states_sha256": canonical_sha256(candidate_state_identities),
        "status_counts": dict(sorted(counts.items())),
        "excluded_states": excluded_states,
        "excluded_states_sha256": canonical_sha256(excluded_states),
        "build_sources": build_sources,
        "parameters": {
            "student_endpoint_attestation": student_attestation,
            "teacher_endpoint_attestation": teacher_attestation,
            "tokenizer_sha256": tokenizer_sha256,
            "tokenizer_snapshot_sha256": tokenizer_snapshot_sha256,
            "runtime_versions": {
                "python": platform.python_version(),
                "httpx": importlib.metadata.version("httpx"),
                "transformers": importlib.metadata.version("transformers"),
                "tokenizers": importlib.metadata.version("tokenizers"),
            },
            "max_history_messages": MAX_HIST_MSGS,
            "max_sequence_tokens": MAX_SEQ,
            "kl_coefficient": KL_COEF,
            "holdout_every": HOLDOUT_EVERY,
            "early_weight": EARLY_WEIGHT,
            "malformed_parameter_pattern": MALFORMED_PARAM_RE.pattern,
            "counterfactual_grading": True,
            "limit": args.limit,
        },
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _publish_create_only(temporary, manifest_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"sealed build receipt: {manifest_path} ({manifest['output_sha256']})")
    print(_training_handoff_text(rec_path, manifest_path))


def _launch_frozen_entrypoint() -> int:
    """Run the actual builder in a clean interpreter from a frozen source tree."""
    if _FROZEN_MARKER is not None:
        asyncio.run(main())
        return 0
    with tempfile.TemporaryDirectory(
        prefix="kaetram-opd-build-inputs-"
    ) as directory:
        snapshot_root = Path(directory).resolve()
        build_sources = _snapshot_build_sources()
        _materialize_build_inputs(build_sources, snapshot_root)
        marker = {
            "schema_version": FROZEN_MARKER_SCHEMA,
            "source_repo": str(REPO),
            "build_sources_sha256": canonical_sha256(build_sources),
        }
        marker_path = snapshot_root / FROZEN_MARKER_NAME
        with marker_path.open("x", encoding="utf-8") as handle:
            json.dump(marker, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed = subprocess.run(
            [
                sys.executable,
                str(snapshot_root / "scripts/opd/opd_2b_data.py"),
                *sys.argv[1:],
            ],
            env=os.environ.copy(),
            check=False,
        )
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(_launch_frozen_entrypoint())
