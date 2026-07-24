#!/usr/bin/env python3
"""Paired copy-prior diagnostic for malformed tool-call syntax.

For malformed emissions in a recorded student run, build contexts that vary
only the prior syntax treatment (real history, repaired history, doc-literal
repair, or both) and score both the original malformed completion and its
canonical semantic equivalent. Results are JSONL so every pair remains
inspectable; a sibling summary JSON records aggregate deltas.

No training or gameplay occurs. Use --dry-run to materialize the paired design
without contacting model endpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "finetune"))

from canonicalize import (  # noqa: E402
    canonicalize_text,
    docify_system_prompt,
    is_malformed,
    recover_tool_calls,
)
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from render import patch_qwen_chat_template  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402

NO_ARG_OK = {
    "observe", "loot", "respawn", "stuck_reset", "cancel_nav",
    "set_attack_style", "warp", "interact_npc",
}
CANON_CALL_RE = re.compile(
    r"<function=([A-Za-z_]\w*)>\n((?:<parameter=\w+>\n.*?\n</parameter>\n)*)</function>",
    re.DOTALL,
)
CANON_PARAM_RE = re.compile(r"<parameter=(\w+)>\n(.*?)\n</parameter>", re.DOTALL)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_endpoint(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("endpoint must be NAME=URL")
    name, url = value.split("=", 1)
    name, url = name.strip(), url.strip().rstrip("/")
    if not name or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("endpoint must be NAME=http(s)://...")
    return name, url


def restore_args(turn) -> dict | None:
    """Rebuild empty structured arguments from canonicalized raw text."""
    message = copy.deepcopy(turn_to_chat(turn))
    bare = [
        call for call in (message.get("tool_calls") or [])
        if not (call.get("function", {}).get("arguments") or {})
        and call.get("function", {}).get("name") not in NO_ARG_OK
    ]
    if not bare:
        return None
    canonical = canonicalize_text(turn.text or "")
    if canonical is None or canonical[1] == 0:
        return None
    by_name: dict[str, list[dict]] = {}
    for match in CANON_CALL_RE.finditer(canonical[0]):
        args = {
            param.group(1): param.group(2)
            for param in CANON_PARAM_RE.finditer(match.group(2))
        }
        if args:
            by_name.setdefault(match.group(1), []).append(args)
    restored = 0
    for call in bare:
        name = call["function"]["name"]
        if by_name.get(name):
            call["function"]["arguments"] = by_name[name].pop(0)
            restored += 1
    return message if restored else None


def collect_states(repo: Path, run_id: str, limit: int, max_hist_messages: int) -> list[dict]:
    logs = sorted(repo.glob(f"dataset/raw/agent_*/runs/{run_id}/session_*.log"))
    parsed_logs = []
    errors = []
    for log_path in logs:
        try:
            parsed_logs.append((log_path, *reconstruct_session(log_path)))
        except Exception as exc:
            errors.append(f"{log_path.relative_to(repo)}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(
            "refusing a selection-biased diagnostic because session logs could not be "
            "reconstructed:\n" + "\n".join(errors)
        )

    states = []
    for log_path, base_messages, turns in parsed_logs:
        real_history, repaired_history = list(base_messages), list(base_messages)
        for turn, results in turns:
            emission = (turn.text or "").strip()
            if turn.tool_calls and not turn.thinking and emission and is_malformed(emission):
                canonical = canonicalize_text(emission)
                if canonical and canonical[1] > 0:
                    def tail(history: list[dict]) -> list[dict]:
                        body = history[2:]
                        return history[:2] + body[-max_hist_messages:]

                    malformed_calls = recover_tool_calls(emission)
                    canonical_calls = recover_tool_calls(canonical[0])
                    if malformed_calls and malformed_calls == canonical_calls:
                        real = tail(real_history)
                        repaired = tail(repaired_history)
                        identity = json.dumps({
                            "log": str(log_path),
                            "line": turn.line_no,
                            "emission": emission,
                        }, sort_keys=True)
                        states.append({
                            "state_id": sha256_text(identity)[:20],
                            "log": str(log_path.relative_to(repo)),
                            "line": turn.line_no,
                            "messages_real": real,
                            "messages_repaired": repaired,
                            "history_changed": real != repaired,
                            "malformed_completion": emission + "<|im_end|>\n",
                            "canonical_completion": canonical[0] + "<|im_end|>\n",
                            "semantic_calls": malformed_calls,
                        })
            real_message = turn_to_chat(turn)
            repaired_message = restore_args(turn) or real_message
            real_history.append(real_message)
            repaired_history.append(repaired_message)
            for result in results:
                tool_message = {
                    "role": "tool",
                    "content": result.result_str,
                    "name": result.name,
                }
                real_history.append(tool_message)
                repaired_history.append(tool_message)
        if len(states) >= limit:
            break
    return states[:limit]


def context_conditions(state: dict) -> dict[str, list[dict]]:
    def docs(messages: list[dict]) -> list[dict]:
        copied = [dict(message) for message in messages]
        copied[0]["content"] = docify_system_prompt(copied[0]["content"])
        return copied

    return {
        "real": state["messages_real"],
        "history_repaired": state["messages_repaired"],
        "docs_repaired": docs(state["messages_real"]),
        "history_and_docs_repaired": docs(state["messages_repaired"]),
    }


def target_stats(response: dict) -> tuple[float | None, float | None, int]:
    values = [
        value for value in response.get("target_logprobs", [])
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )
    ]
    if not values:
        return None, None, 0
    return sum(values), statistics.fmean(values), len(values)


def render_context(tokenizer, messages: list[dict], tool_schema_source: str) -> str:
    """Render exactly one declared model-visible schema condition."""
    kwargs = {"tokenize": False, "add_generation_prompt": True}
    if tool_schema_source == "canonical":
        kwargs["tools"] = MODEL_VISIBLE_TOOL_DEFINITIONS
    elif tool_schema_source != "none":
        raise ValueError(f"unsupported tool schema source: {tool_schema_source!r}")
    return tokenizer.apply_chat_template(messages, **kwargs)


async def score(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, endpoint: str,
                context_text: str, full_text: str, retries: int) -> dict | None:
    async with semaphore:
        for attempt in range(retries):
            try:
                response = await client.post(
                    f"{endpoint}/score",
                    json={"context_text": context_text, "full_text": full_text},
                    timeout=300,
                )
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 400:
                    return None
            except (httpx.HTTPError, httpx.TimeoutException, ValueError):
                pass
            await asyncio.sleep(2 * (attempt + 1))
    return None


def _stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def summarize(rows: list[dict]) -> dict:
    groups: dict[tuple[str, str, str], list[float]] = {}
    paired: dict[tuple[str, str, str], float] = {}
    for row in rows:
        mean = row.get("mean_target_logprob")
        if isinstance(mean, (int, float)):
            key = (row["endpoint"], row["context_condition"], row["candidate"])
            groups.setdefault(key, []).append(mean)
            paired[(row["state_id"], *key)] = mean
    group_summary = {}
    for (endpoint, condition, candidate), values in sorted(groups.items()):
        key = f"{endpoint}/{condition}/{candidate}"
        group_summary[key] = {
            "n": len(values),
            "mean_target_logprob": statistics.fmean(values),
            "median_target_logprob": statistics.median(values),
        }

    effects: dict[str, list[float]] = {}
    state_endpoints = sorted({(key[0], key[1]) for key in paired})
    conditions = ("real", "history_repaired", "docs_repaired", "history_and_docs_repaired")
    for state_id, endpoint in state_endpoints:
        for condition in conditions:
            malformed = paired.get((state_id, endpoint, condition, "malformed"))
            canonical = paired.get((state_id, endpoint, condition, "canonical"))
            if malformed is not None and canonical is not None:
                effects.setdefault(
                    f"{endpoint}/{condition}/canonical_minus_malformed", []
                ).append(canonical - malformed)
        for condition in conditions[1:]:
            for candidate in ("malformed", "canonical"):
                real = paired.get((state_id, endpoint, "real", candidate))
                repaired = paired.get((state_id, endpoint, condition, candidate))
                if real is not None and repaired is not None:
                    effects.setdefault(
                        f"{endpoint}/{condition}/{candidate}_minus_real", []
                    ).append(repaired - real)

    return {
        "groups": group_summary,
        "paired_effects": {
            key: _stats(values) for key, values in sorted(effects.items())
        },
        "interpretation": {
            "canonical_minus_malformed": (
                "positive means the endpoint assigns higher mean token log-probability "
                "to the canonical rendering in the same context"
            ),
            "candidate_minus_real": (
                "negative means repairing the indicated prior reduced that candidate's "
                "mean token log-probability relative to the untouched context"
            ),
        },
    }


async def run_scoring(args, tokenizer, states: list[dict], endpoints: list[tuple[str, str]]) -> list[dict]:
    semaphore = asyncio.Semaphore(args.concurrency)
    specs = []
    for state in states:
        for condition, messages in context_conditions(state).items():
            context_text = render_context(tokenizer, messages, args.tool_schema_source)
            for candidate in ("malformed", "canonical"):
                completion = state[f"{candidate}_completion"]
                for endpoint_name, endpoint_url in endpoints:
                    specs.append((state, condition, messages, context_text, candidate,
                                  completion, endpoint_name, endpoint_url))

    async with httpx.AsyncClient() as client:
        responses = await asyncio.gather(*(
            score(client, semaphore, endpoint_url, context_text,
                  context_text + completion, args.retries)
            for (_, _, _, context_text, _, completion, _, endpoint_url) in specs
        ))

    rows = []
    for spec, response in zip(specs, responses, strict=True):
        state, condition, messages, context_text, candidate, completion, endpoint_name, _ = spec
        total, mean, count = target_stats(response or {})
        row = {
            "schema_version": "kaetram-copy-prior-result-v1",
            "state_id": state["state_id"],
            "log": state["log"],
            "line": state["line"],
            "history_changed": state["history_changed"],
            "endpoint": endpoint_name,
            "tool_schema_source": args.tool_schema_source,
            "context_condition": condition,
            "candidate": candidate,
            "semantic_calls": state["semantic_calls"],
            "context_sha256": sha256_text(context_text),
            "completion_sha256": sha256_text(completion),
            "target_token_count": count,
            "total_target_logprob": total,
            "mean_target_logprob": mean,
            "score_ok": response is not None,
        }
        if args.include_text:
            row["messages"] = messages
            row["completion"] = completion
        rows.append(row)
    return rows


def publish_new_texts(artifacts: dict[Path, str]) -> None:
    """Atomically create a set of outputs, rolling back this publication on failure."""
    if len(artifacts) != len(set(artifacts)):
        raise ValueError("output paths must be unique")
    staged: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for path, content in artifacts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                staged[path] = Path(handle.name)
        for path, temporary in staged.items():
            os.link(temporary, path)
            published.append(path)
        for parent in {path.parent for path in artifacts}:
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def jsonl_text(rows: list[dict]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    publish_new_texts({path: jsonl_text(rows)})


async def main_async() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("-n", "--limit", type=int, default=60)
    parser.add_argument("--max-history-messages", type=int, default=28)
    parser.add_argument("--endpoint", action="append", type=parse_endpoint, default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-text", action="store_true",
        help="Include prompts/completions in output; hashes are always included",
    )
    parser.add_argument("--tokenizer", default="Qwen/Qwen3.5-2B")
    parser.add_argument(
        "--tool-schema-source", choices=("none", "canonical"), default="none",
        help=(
            "Model-visible tool schema: none reproduces historical OPD grading; "
            "canonical tests the frozen native schema used by new contracts"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args()

    if args.limit < 1 or args.max_history_messages < 1:
        parser.error("limits must be positive")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    if args.retries < 1:
        parser.error("--retries must be positive")
    endpoints = list(args.endpoint)
    if not endpoints and os.environ.get("FOURB_EP"):
        endpoints.append(("teacher-4b", os.environ["FOURB_EP"].rstrip("/")))
    if not args.dry_run and not endpoints:
        parser.error("provide --endpoint NAME=URL or FOURB_EP, or use --dry-run")

    states = collect_states(REPO, args.run_id, args.limit, args.max_history_messages)
    if not states:
        parser.error("no recoverable malformed states found for the run")

    design_rows = []
    for state in states:
        row = {
            "schema_version": "kaetram-copy-prior-design-v1",
            "state_id": state["state_id"],
            "log": state["log"],
            "line": state["line"],
            "history_changed": state["history_changed"],
            "semantic_calls": state["semantic_calls"],
            "context_conditions": list(context_conditions(state)),
            "tool_schema_source": args.tool_schema_source,
            "tool_schema_sha256": (
                sha256_text(json.dumps(
                    MODEL_VISIBLE_TOOL_DEFINITIONS,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )) if args.tool_schema_source == "canonical" else None
            ),
            "malformed_completion_sha256": sha256_text(state["malformed_completion"]),
            "canonical_completion_sha256": sha256_text(state["canonical_completion"]),
        }
        if args.include_text:
            row["messages_by_condition"] = context_conditions(state)
            row["malformed_completion"] = state["malformed_completion"]
            row["canonical_completion"] = state["canonical_completion"]
        design_rows.append(row)

    if args.dry_run:
        write_jsonl(args.out, design_rows)
        print(f"wrote {len(design_rows)} paired designs to {args.out}")
        return 0

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer, trust_remote_code=True
    )
    patch_qwen_chat_template(tokenizer)
    rows = await run_scoring(args, tokenizer, states, endpoints)
    summary_path = args.out.with_suffix(args.out.suffix + ".summary.json")
    summary_text = json.dumps({
        "schema_version": "kaetram-copy-prior-summary-v1",
        "run_id": args.run_id,
        "states": len(states),
        "endpoints": [name for name, _ in endpoints],
        "tool_schema_source": args.tool_schema_source,
        "results": summarize(rows),
    }, indent=2, sort_keys=True) + "\n"
    publish_new_texts({args.out: jsonl_text(rows), summary_path: summary_text})
    print(f"wrote {len(rows)} scores to {args.out}")
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
