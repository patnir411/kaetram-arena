#!/usr/bin/env python3
"""Prepare, run, and analyze the registered local trigger-incidence probe.

The runner accepts loopback endpoints only. It never launches an endpoint and
never permits the metered-endpoint override used by some development probes.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
for import_root in (
    REPO,
    REPO / "scripts" / "opd",
    REPO / "scripts" / "log_analysis",
):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from canonicalize import (  # noqa: E402
    docify_system_prompt,
    is_malformed,
    recover_tool_calls,
)
from endpoint_policy import require_zero_spend_endpoints  # noqa: E402
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from parse import session_meta  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-registration.v1"
DESIGN_SCHEMA = "kaetram.local-trigger-incidence-design.v1"
RUN_SCHEMA = "kaetram.local-trigger-incidence-run.v1"
ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
KWARG_IN_KEY = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PYTHON_CALL = re.compile(r"<function=\w+\s*\(")
CORRUPT_CLOSE = re.compile(
    r"</(?!parameter>|function>|tool_call>|think>)[A-Za-z_]{0,12}>"
)


class ProbeError(RuntimeError):
    """Raised when the registered probe contract cannot be satisfied."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return sha256_bytes(payload)


def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def load_registration(path: Path) -> tuple[dict, str]:
    try:
        registration = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load registration: {exc}") from exc
    if (
        not isinstance(registration, dict)
        or registration.get("schema_version") != REGISTRATION_SCHEMA
    ):
        raise ProbeError("unexpected registration schema")
    conditions = registration.get("conditions")
    snapshots = registration.get("snapshots")
    if not isinstance(conditions, list) or len(conditions) != 4:
        raise ProbeError("registration must contain four conditions")
    if not isinstance(snapshots, dict) or not snapshots:
        raise ProbeError("registration has no snapshots")
    condition_ids = [condition.get("condition_id") for condition in conditions]
    if len(set(condition_ids)) != len(condition_ids) or not all(condition_ids):
        raise ProbeError("registration condition IDs must be unique strings")
    return registration, sha256_file(path)


def _git_identity() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ProbeError("experiment artifacts require a clean Arena checkout")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"source_git_commit": commit, "dirty_paths": []}


def _render_decision_state(
    log_path: Path,
    *,
    decision_turn: int,
    max_history_messages: int,
) -> list[dict] | None:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            base_messages, turns = reconstruct_session(log_path)
    except Exception:  # noqa: BLE001 - parseability is the registered gate
        return None
    rolling = list(base_messages)
    for turn_index, (turn, results) in enumerate(turns, start=1):
        if turn_index == decision_turn:
            head, history = rolling[:2], rolling[2:]
            messages = head + history[-max_history_messages:]
            return messages if messages else None
        rolling.append(turn_to_chat(turn))
        for result in results:
            rolling.append(
                {
                    "role": "tool",
                    "content": result.result_str,
                    "name": result.name,
                }
            )
    return None


def _derive_design(
    registration: dict,
    registration_sha256: str,
    historical_root: Path,
    git_identity: dict,
) -> dict:
    state_contract = registration["state_pool"]
    source_glob = state_contract["source_glob"]
    logs = sorted(
        historical_root.glob(source_glob),
        key=lambda item: item.relative_to(historical_root).as_posix(),
    )
    if not logs:
        raise ProbeError(f"no source logs match {source_glob!r}")
    personality = state_contract["personality"]
    eligible_logs = [
        log_path
        for log_path in logs
        if (session_meta(log_path) or {}).get("personality") == personality
    ]
    target = int(state_contract["state_count"])
    stride = max(1, len(eligible_logs) // (2 * target))
    states = []
    for log_path in eligible_logs[::stride]:
        messages = _render_decision_state(
            log_path,
            decision_turn=int(state_contract["decision_turn"]),
            max_history_messages=int(state_contract["max_history_messages"]),
        )
        if messages is None:
            continue
        relative = log_path.relative_to(historical_root).as_posix()
        states.append(
            {
                "state_id": f"state-{len(states) + 1:02d}",
                "personality": personality,
                "source_log": relative,
                "source_log_sha256": sha256_file(log_path),
                "messages_sha256": sha256_json(messages),
                "messages": messages,
            }
        )
        if len(states) == target:
            break
    if len(states) != target:
        raise ProbeError(f"prepared {len(states)} states; registration requires {target}")
    return {
        "schema_version": DESIGN_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "source_log_count": len(logs),
        "eligible_source_log_count": len(eligible_logs),
        "personality": personality,
        "selection_stride": stride,
        "states": states,
        **git_identity,
    }


def _source_tree_sha256(states: list[dict]) -> str:
    return sha256_json(
        [
            {
                "state_id": state["state_id"],
                "personality": state["personality"],
                "source_log": state["source_log"],
                "source_log_sha256": state["source_log_sha256"],
                "messages_sha256": state["messages_sha256"],
            }
            for state in states
        ]
    )


def prepare_design(
    registration_path: Path,
    historical_root: Path,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    if output_dir.exists():
        raise ProbeError("refusing to overwrite design directory")
    git_identity = _git_identity()
    design = _derive_design(
        registration,
        registration_sha256,
        historical_root,
        git_identity,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)
    output_path = output_dir / "design.json"
    receipt_path = output_dir / "design.receipt.json"
    write_json(output_path, design, exclusive=True)
    receipt = {
        "schema_version": f"{DESIGN_SCHEMA}.receipt",
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": sha256_file(output_path),
        "state_count": len(design["states"]),
        "selected_source_tree_sha256": _source_tree_sha256(design["states"]),
        **git_identity,
    }
    write_json(receipt_path, receipt, exclusive=True)
    return receipt


def load_design(
    path: Path,
    registration: dict,
    registration_sha256: str,
    *,
    historical_root: Path | None = None,
) -> dict:
    try:
        design = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load design: {exc}") from exc
    if design.get("schema_version") != DESIGN_SCHEMA:
        raise ProbeError("unexpected design schema")
    if design.get("study_id") != registration["study_id"]:
        raise ProbeError("design study ID mismatch")
    if design.get("registration_sha256") != registration_sha256:
        raise ProbeError("design registration hash mismatch")
    states = design.get("states")
    if not isinstance(states, list) or len(states) != registration["state_pool"]["state_count"]:
        raise ProbeError("design state count mismatch")
    personality = registration["state_pool"]["personality"]
    for state_index, state in enumerate(states):
        messages = state.get("messages")
        source_path = Path(str(state.get("source_log", "")))
        if (
            state.get("state_id") != f"state-{state_index + 1:02d}"
            or state.get("personality") != personality
            or source_path.is_absolute()
            or ".." in source_path.parts
            or not isinstance(state.get("source_log_sha256"), str)
            or not isinstance(messages, list)
            or sha256_json(messages) != state.get("messages_sha256")
        ):
            raise ProbeError(f"{state.get('state_id')}: message hash mismatch")
    if (
        design.get("personality") != personality
        or design.get("dirty_paths") != []
        or re.fullmatch(r"[0-9a-f]{40}", str(design.get("source_git_commit", "")))
        is None
    ):
        raise ProbeError("design identity does not match the registration")
    receipt_path = path.with_suffix(".receipt.json")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load design receipt: {exc}") from exc
    expected_receipt = {
        "schema_version": f"{DESIGN_SCHEMA}.receipt",
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": sha256_file(path),
        "state_count": registration["state_pool"]["state_count"],
        "selected_source_tree_sha256": _source_tree_sha256(states),
        "source_git_commit": design.get("source_git_commit"),
        "dirty_paths": [],
    }
    if receipt != expected_receipt:
        raise ProbeError("design receipt does not match the exact design")
    if historical_root is not None:
        expected = _derive_design(
            registration,
            registration_sha256,
            historical_root,
            _git_identity(),
        )
        if design != expected:
            raise ProbeError("design does not rederive from the registered source pool")
    return design


def surface_families(content: str) -> list[str]:
    families = []
    if KWARG_IN_KEY.search(content):
        families.append("kwarg_in_key")
    if PYTHON_CALL.search(content):
        families.append("python_call")
    if "<tool_call>" in content and CORRUPT_CLOSE.search(content):
        families.append("corrupt_close")
    if not families and is_malformed(content):
        families.append("other_malformed")
    return families


def classify_response_message(message: dict) -> dict:
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = ""
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    recoverable = recover_tool_calls(content) if not tool_calls else []
    return {
        "structured_tool_call_count": len(tool_calls),
        "has_structured_tool_call": bool(tool_calls),
        "no_structured_tool_call": not tool_calls,
        "has_content": bool(content),
        "malformed_emission": is_malformed(content),
        "malformed_families": surface_families(content),
        "recovery_opportunity": bool(recoverable),
        "recoverable_calls": recoverable,
    }


def condition_messages(messages: list[dict], documentation: str) -> list[dict]:
    copied = copy.deepcopy(messages)
    if documentation == "python_docs":
        return copied
    if documentation != "canonical_docs":
        raise ProbeError(f"unknown documentation condition: {documentation}")
    for message in copied:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] = docify_system_prompt(message["content"])
    return copied


def _health_url(endpoint: str) -> str:
    return endpoint[:-3] + "/health" if endpoint.endswith("/v1") else endpoint + "/health"


def decode_endpoint_health(response: httpx.Response) -> dict:
    response.raise_for_status()
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProbeError("endpoint health is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ProbeError("endpoint health is not ok")
    return payload


async def endpoint_health(endpoint: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(_health_url(endpoint), timeout=10)
    return decode_endpoint_health(response)


def validate_endpoint_health(health: dict, registration: dict, snapshot: str) -> None:
    expected = registration["snapshots"][snapshot]
    attestation = health.get("attestation")
    if not isinstance(attestation, dict):
        raise ProbeError("endpoint health lacks attestation")
    for key in ("api_model", "checkpoint_sha256"):
        if attestation.get(key) != expected[key]:
            raise ProbeError(f"endpoint {key} does not match registration")
    for key, expected_value in registration["endpoint_contract"].items():
        if attestation.get(key) != expected_value:
            raise ProbeError(f"endpoint {key} does not match registration")


async def _request_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    endpoint: str,
    api_model: str,
    messages: list[dict],
    condition: dict,
    sampling: dict,
    state_id: str,
    state_index: int,
    sample_index: int,
    schedule_index: int,
) -> dict:
    seed = int(sampling["base_seed"]) + 100 * state_index + sample_index
    payload: dict[str, Any] = {
        "model": api_model,
        "messages": condition_messages(messages, condition["documentation"]),
        "max_tokens": sampling["max_tokens"],
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "top_k": sampling["top_k"],
        "presence_penalty": sampling["presence_penalty"],
        "seed": seed,
    }
    if condition["native_tool_schema"] == "present":
        payload["tools"] = copy.deepcopy(MODEL_VISIBLE_TOOL_DEFINITIONS)
    elif condition["native_tool_schema"] != "absent":
        raise ProbeError("unknown native-tool-schema condition")
    attempts = int(sampling["attempts"])
    started = time.monotonic()
    errors = []
    message: dict[str, Any] | None = None
    async with semaphore:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    f"{endpoint}/chat/completions",
                    json=payload,
                    timeout=float(sampling["request_timeout_seconds"]),
                )
                if response.status_code == 200:
                    body = response.json()
                    candidate = body["choices"][0]["message"]
                    if not isinstance(candidate, dict):
                        raise ValueError("response message is not an object")
                    message = candidate
                    break
                errors.append(f"attempt {attempt}: HTTP {response.status_code}")
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(f"attempt {attempt}: {type(exc).__name__}")
            if attempt < attempts:
                await asyncio.sleep(float(attempt))
    common = {
        "schema_version": RUN_SCHEMA,
        "schedule_index": schedule_index,
        "state_id": state_id,
        "state_index": state_index,
        "sample_index": sample_index,
        "seed": seed,
        "condition_id": condition["condition_id"],
        "documentation": condition["documentation"],
        "native_tool_schema": condition["native_tool_schema"],
        "latency_seconds": round(time.monotonic() - started, 6),
        "attempt_errors": errors,
    }
    if message is None:
        return {**common, "status": "failed"}
    return {
        **common,
        "status": "ok",
        "response_message": message,
        **classify_response_message(message),
    }


async def run_checkpoint(
    registration_path: Path,
    design_path: Path,
    historical_root: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    design = load_design(
        design_path,
        registration,
        registration_sha256,
        historical_root=historical_root,
    )
    endpoint = require_zero_spend_endpoints([endpoint])[0]
    if snapshot not in registration["snapshots"]:
        raise ProbeError(f"snapshot is not registered: {snapshot}")
    if output_dir.exists():
        raise ProbeError(f"refusing to overwrite outcome directory: {output_dir}")
    health = await endpoint_health(endpoint)
    expected = registration["snapshots"][snapshot]
    validate_endpoint_health(health, registration, snapshot)
    git_identity = _git_identity()
    output_dir.mkdir(parents=True, exist_ok=False)
    design_sha256 = sha256_file(design_path)
    prelaunch = {
        "schema_version": f"{RUN_SCHEMA}.prelaunch",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "registration_sha256": registration_sha256,
        "design_sha256": design_sha256,
        "endpoint_health": health,
        "sampling": registration["sampling"],
        **git_identity,
    }
    write_json(output_dir / "prelaunch.json", prelaunch)

    conditions = registration["conditions"]
    sampling = registration["sampling"]
    tasks = []
    schedule_index = 0
    samples_per_state = int(sampling["samples_per_state_condition"])
    semaphore = asyncio.Semaphore(int(sampling["concurrency"]))
    async with httpx.AsyncClient() as client:
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(samples_per_state):
                block_index = state_index * samples_per_state + sample_index
                offset = block_index % len(conditions)
                ordered = conditions[offset:] + conditions[:offset]
                for condition in ordered:
                    tasks.append(
                        _request_one(
                            client,
                            semaphore,
                            endpoint=endpoint,
                            api_model=expected["api_model"],
                            messages=state["messages"],
                            condition=condition,
                            sampling=sampling,
                            state_id=state["state_id"],
                            state_index=state_index,
                            sample_index=sample_index,
                            schedule_index=schedule_index,
                        )
                    )
                    schedule_index += 1
        results = await asyncio.gather(*tasks)
    try:
        postflight_health = await endpoint_health(endpoint)
        validate_endpoint_health(postflight_health, registration, snapshot)
        endpoint_identity_stable = postflight_health == health
        postflight_error = None if endpoint_identity_stable else "health payload drift"
    except (ProbeError, httpx.HTTPError) as exc:
        postflight_health = None
        endpoint_identity_stable = False
        postflight_error = type(exc).__name__
    postflight = {
        "schema_version": f"{RUN_SCHEMA}.postflight",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "endpoint_identity_stable": endpoint_identity_stable,
        "endpoint_health": postflight_health,
        "error": postflight_error,
    }
    write_json(output_dir / "postflight.json", postflight)
    results.sort(key=lambda row: row["schedule_index"])
    result_path = output_dir / "results.jsonl"
    with result_path.open("x") as handle:
        for row in results:
            row["snapshot"] = snapshot
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    completed = {
        "schema_version": f"{RUN_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(results),
        "successful_requests": sum(row["status"] == "ok" for row in results),
        "failed_requests": sum(row["status"] != "ok" for row in results),
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity")) for row in results
        ),
        "malformed_emissions": sum(bool(row.get("malformed_emission")) for row in results),
        "structured_tool_responses": sum(
            bool(row.get("has_structured_tool_call")) for row in results
        ),
        "no_structured_tool_call_responses": sum(
            bool(row.get("no_structured_tool_call")) for row in results
        ),
        "endpoint_identity_stable": endpoint_identity_stable,
    }
    write_json(output_dir / "completed.json", completed)
    artifact_records = []
    for name in (
        "prelaunch.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    ):
        path = output_dir / name
        artifact_records.append(
            {"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    index = {
        "schema_version": f"{RUN_SCHEMA}.artifacts",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "files": artifact_records,
        "tree_sha256": sha256_json(artifact_records),
    }
    write_json(output_dir / "artifact-index.json", index)
    if not endpoint_identity_stable:
        raise ProbeError(
            "endpoint identity changed or became unavailable; invalid run retained"
        )
    return completed


def _verify_run_directory(
    path: Path,
    registration: dict,
) -> tuple[dict, dict, dict, list[dict], dict]:
    try:
        index = json.loads((path / "artifact-index.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{path.name}: invalid artifact index") from exc
    snapshot = index.get("snapshot")
    expected_names = (
        "prelaunch.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    )
    directory_items = list(path.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in directory_items):
        raise ProbeError(f"{path.name}: non-regular run artifact")
    actual_names = {item.name for item in directory_items}
    if actual_names != {*expected_names, "artifact-index.json"}:
        raise ProbeError(f"{path.name}: unexpected or missing run artifacts")
    records = index.get("files")
    if (
        index.get("schema_version") != f"{RUN_SCHEMA}.artifacts"
        or index.get("study_id") != registration["study_id"]
        or snapshot not in registration["snapshots"]
        or not isinstance(records, list)
        or tuple(record.get("path") for record in records) != expected_names
        or index.get("tree_sha256") != sha256_json(records)
    ):
        raise ProbeError(f"{path.name}: artifact index contract mismatch")
    for record in records:
        if set(record) != {"path", "size_bytes", "sha256"}:
            raise ProbeError(f"{path.name}: malformed artifact descriptor")
        artifact = path / record["path"]
        if (
            not artifact.is_file()
            or artifact.is_symlink()
            or artifact.stat().st_size != record["size_bytes"]
            or sha256_file(artifact) != record["sha256"]
        ):
            raise ProbeError(f"{path.name}: artifact mismatch for {record['path']}")
    try:
        prelaunch = json.loads((path / "prelaunch.json").read_text())
        postflight = json.loads((path / "postflight.json").read_text())
        completed = json.loads((path / "completed.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{path.name}: malformed run envelope") from exc
    for envelope, suffix in (
        (prelaunch, "prelaunch"),
        (postflight, "postflight"),
        (completed, "completed"),
    ):
        if (
            envelope.get("schema_version") != f"{RUN_SCHEMA}.{suffix}"
            or envelope.get("study_id") != registration["study_id"]
            or envelope.get("snapshot") != snapshot
        ):
            raise ProbeError(f"{path.name}: {suffix} identity mismatch")
    validate_endpoint_health(prelaunch.get("endpoint_health", {}), registration, snapshot)
    validate_endpoint_health(postflight.get("endpoint_health", {}), registration, snapshot)
    if (
        not postflight.get("endpoint_identity_stable")
        or not completed.get("endpoint_identity_stable")
        or postflight.get("endpoint_health") != prelaunch.get("endpoint_health")
        or postflight.get("error") is not None
    ):
        raise ProbeError(f"{path.name}: endpoint identity was not stable")
    rows = []
    for line_number, line in enumerate(
        (path / "results.jsonl").read_text().splitlines(), start=1
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{path.name}: malformed result line {line_number}") from exc
        rows.append(row)
    recomputed = {
        "scheduled_requests": len(rows),
        "successful_requests": sum(row.get("status") == "ok" for row in rows),
        "failed_requests": sum(row.get("status") != "ok" for row in rows),
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity")) for row in rows
        ),
        "malformed_emissions": sum(bool(row.get("malformed_emission")) for row in rows),
        "structured_tool_responses": sum(
            bool(row.get("has_structured_tool_call")) for row in rows
        ),
        "no_structured_tool_call_responses": sum(
            bool(row.get("no_structured_tool_call")) for row in rows
        ),
        "endpoint_identity_stable": True,
    }
    expected_completed_keys = {
        "schema_version",
        "study_id",
        "snapshot",
        *recomputed.keys(),
    }
    if set(completed) != expected_completed_keys or any(
        completed.get(key) != value for key, value in recomputed.items()
    ):
        raise ProbeError(f"{path.name}: completed totals do not match results")
    input_identity = {
        "snapshot": snapshot,
        "artifact_index_sha256": sha256_file(path / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
    }
    return prelaunch, postflight, completed, rows, input_identity


def analyze(
    registration_path: Path,
    design_path: Path,
    run_dirs: list[Path],
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    design = load_design(design_path, registration, registration_sha256)
    if output_dir.exists():
        raise ProbeError(f"refusing to overwrite analysis directory: {output_dir}")
    analysis_code_provenance = {
        **_git_identity(),
        "analysis_script_sha256": sha256_file(Path(__file__).resolve()),
        "python_version": sys.version.split()[0],
    }
    rows = []
    seen_snapshots = set()
    input_runs = []
    for run_dir in run_dirs:
        prelaunch, _postflight, _completed, run_rows, input_identity = (
            _verify_run_directory(run_dir, registration)
        )
        snapshot = prelaunch.get("snapshot")
        if snapshot in seen_snapshots or snapshot not in registration["snapshots"]:
            raise ProbeError("run directories must contain each registered snapshot once")
        if (
            prelaunch.get("registration_sha256") != registration_sha256
            or prelaunch.get("design_sha256") != sha256_file(design_path)
            or prelaunch.get("sampling") != registration["sampling"]
            or prelaunch.get("source_git_commit") != design.get("source_git_commit")
            or prelaunch.get("dirty_paths") != []
        ):
            raise ProbeError(f"{snapshot}: prelaunch contract mismatch")
        seen_snapshots.add(snapshot)
        input_runs.append(input_identity)
        rows.extend(run_rows)
    expected_snapshots = set(registration["snapshots"])
    if seen_snapshots != expected_snapshots:
        raise ProbeError("analysis requires all registered snapshots")

    conditions = registration["conditions"]
    condition_by_id = {item["condition_id"]: item for item in conditions}
    state_ids = [item["state_id"] for item in design["states"]]
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    base_seed = int(registration["sampling"]["base_seed"])
    expected_metadata = {}
    for snapshot in registration["snapshots"]:
        schedule_index = 0
        for state_index, state_id in enumerate(state_ids):
            for sample_index in range(sample_count):
                block_index = state_index * sample_count + sample_index
                offset = block_index % len(conditions)
                ordered = conditions[offset:] + conditions[:offset]
                for condition in ordered:
                    key = (
                        snapshot,
                        condition["condition_id"],
                        state_id,
                        sample_index,
                    )
                    expected_metadata[key] = {
                        "schema_version": RUN_SCHEMA,
                        "snapshot": snapshot,
                        "schedule_index": schedule_index,
                        "state_id": state_id,
                        "state_index": state_index,
                        "sample_index": sample_index,
                        "seed": base_seed + 100 * state_index + sample_index,
                        "condition_id": condition["condition_id"],
                        "documentation": condition["documentation"],
                        "native_tool_schema": condition["native_tool_schema"],
                    }
                    schedule_index += 1
    by_key = {}
    for row in rows:
        key = (
            row.get("snapshot"),
            row.get("condition_id"),
            row.get("state_id"),
            row.get("sample_index"),
        )
        if key in by_key:
            raise ProbeError(f"duplicate scheduled result: {key}")
        expected = expected_metadata.get(key)
        if expected is None or any(row.get(name) != value for name, value in expected.items()):
            raise ProbeError(f"scheduled row invariant mismatch: {key}")
        if row.get("status") not in {"ok", "failed"}:
            raise ProbeError(f"unknown row status: {key}")
        condition = condition_by_id[row["condition_id"]]
        if (
            row.get("documentation") != condition["documentation"]
            or row.get("native_tool_schema") != condition["native_tool_schema"]
        ):
            raise ProbeError(f"factor label mismatch: {key}")
        if row["status"] == "ok":
            message = row.get("response_message")
            if not isinstance(message, dict):
                raise ProbeError(f"successful row lacks raw response: {key}")
            recomputed = classify_response_message(message)
            if any(row.get(name) != value for name, value in recomputed.items()):
                raise ProbeError(f"stored outcome differs from raw response: {key}")
        else:
            outcome_fields = {
                "response_message",
                "structured_tool_call_count",
                "has_structured_tool_call",
                "no_structured_tool_call",
                "has_content",
                "malformed_emission",
                "malformed_families",
                "recovery_opportunity",
                "recoverable_calls",
            }
            if outcome_fields.intersection(row):
                raise ProbeError(f"failed row unexpectedly contains outcomes: {key}")
        by_key[key] = row
    if set(by_key) != set(expected_metadata):
        raise ProbeError("result schedule does not match the registration")

    complete = all(row.get("status") == "ok" for row in rows)
    cell_rows = []
    for snapshot in registration["snapshots"]:
        for condition in registration["conditions"]:
            subset = [
                by_key[(snapshot, condition["condition_id"], state_id, sample_index)]
                for state_id in state_ids
                for sample_index in range(sample_count)
            ]
            successful = [row for row in subset if row["status"] == "ok"]
            opportunities = sum(
                bool(row.get("recovery_opportunity")) for row in successful
            )
            cell_rows.append(
                {
                    "snapshot": snapshot,
                    "condition_id": condition["condition_id"],
                    "documentation": condition["documentation"],
                    "native_tool_schema": condition["native_tool_schema"],
                    "requests": len(subset),
                    "successful_requests": len(successful),
                    "failures": len(subset) - len(successful),
                    "recovery_opportunities": opportunities,
                    "opportunity_rate": (
                        opportunities / len(successful) if successful else None
                    ),
                    "malformed_emissions": sum(
                        bool(row.get("malformed_emission")) for row in successful
                    ),
                    "structured_tool_responses": sum(
                        bool(row.get("has_structured_tool_call")) for row in successful
                    ),
                    "no_structured_tool_call_responses": sum(
                        bool(row.get("no_structured_tool_call")) for row in successful
                    ),
                }
            )

    contrasts = []
    if complete:
        condition_lookup = {
            (item["documentation"], item["native_tool_schema"]): item["condition_id"]
            for item in registration["conditions"]
        }
        contrast_specs = (
            ("native_tools_main", 2 * sample_count),
            ("canonical_docs_main", 2 * sample_count),
            ("interaction", sample_count),
        )
        for snapshot in registration["snapshots"]:
            numerators_by_name = {name: [] for name, _ in contrast_specs}
            for state_id in state_ids:
                counts = {}
                for docs in ("python_docs", "canonical_docs"):
                    for tools in ("absent", "present"):
                        condition_id = condition_lookup[(docs, tools)]
                        counts[(docs, tools)] = sum(
                            bool(
                                by_key[
                                    (snapshot, condition_id, state_id, sample_index)
                                ].get("recovery_opportunity")
                            )
                            for sample_index in range(sample_count)
                        )
                numerators_by_name["native_tools_main"].append(
                    counts[("python_docs", "present")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("canonical_docs", "absent")]
                )
                numerators_by_name["canonical_docs_main"].append(
                    counts[("canonical_docs", "absent")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("python_docs", "present")]
                )
                numerators_by_name["interaction"].append(
                    counts[("canonical_docs", "present")]
                    - counts[("canonical_docs", "absent")]
                    - counts[("python_docs", "present")]
                    + counts[("python_docs", "absent")]
                )
            for name, denominator in contrast_specs:
                numerators = numerators_by_name[name]
                state_effects = [value / denominator for value in numerators]
                contrasts.append(
                    {
                        "snapshot": snapshot,
                        "contrast": name,
                        "finite_grid_states": len(state_ids),
                        "effect_rate_difference": (
                            sum(numerators) / (denominator * len(state_ids))
                        ),
                        "states_positive": sum(value > 0 for value in state_effects),
                        "states_negative": sum(value < 0 for value in state_effects),
                        "states_zero": sum(value == 0 for value in state_effects),
                        "state_effect_min": min(state_effects),
                        "state_effect_max": max(state_effects),
                    }
                )

    total_successes = sum(row.get("status") == "ok" for row in rows)
    total_opportunities = sum(
        bool(row.get("recovery_opportunity")) for row in rows if row.get("status") == "ok"
    )
    summary = {
        "schema_version": ANALYSIS_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": sha256_file(design_path),
        "analysis_code_provenance": analysis_code_provenance,
        "input_runs": sorted(input_runs, key=lambda item: item["snapshot"]),
        "analysis_status": "complete" if complete else "incomplete",
        "scheduled_requests": len(expected_metadata),
        "successful_requests": total_successes,
        "failed_requests": len(rows) - total_successes,
        "recovery_opportunities": total_opportunities,
        "claim_boundary": registration["claim_boundary"],
        "cells": cell_rows,
        "registered_contrasts": contrasts,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "analysis-summary.json", summary)
    for filename, records in (("cells.csv", cell_rows), ("contrasts.csv", contrasts)):
        path = output_dir / filename
        if not records:
            path.write_text("")
            continue
        with path.open("x", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    artifacts = []
    for name in ("analysis-summary.json", "cells.csv", "contrasts.csv"):
        path = output_dir / name
        artifacts.append(
            {"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    write_json(
        output_dir / "artifact-index.json",
        {
            "schema_version": f"{ANALYSIS_SCHEMA}.artifacts",
            "files": artifacts,
            "tree_sha256": sha256_json(artifacts),
        },
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--registration", type=Path, required=True)
    prepare.add_argument("--historical-root", type=Path, required=True)
    prepare.add_argument("--out-dir", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--design", type=Path, required=True)
    run.add_argument("--historical-root", type=Path, required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--snapshot", required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--registration", type=Path, required=True)
    analyze_parser.add_argument("--design", type=Path, required=True)
    analyze_parser.add_argument("--run-dir", type=Path, action="append", required=True)
    analyze_parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        receipt = prepare_design(
            args.registration,
            args.historical_root,
            args.out_dir,
        )
        print(json.dumps(receipt, indent=2, sort_keys=True))
    elif args.command == "run":
        completed = asyncio.run(
            run_checkpoint(
                args.registration,
                args.design,
                args.historical_root,
                args.endpoint,
                args.snapshot,
                args.out_dir,
            )
        )
        print(json.dumps(completed, indent=2, sort_keys=True))
    else:
        summary = analyze(
            args.registration,
            args.design,
            args.run_dir,
            args.out_dir,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
