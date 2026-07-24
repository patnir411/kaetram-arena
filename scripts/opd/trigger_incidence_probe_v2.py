#!/usr/bin/env python3
"""Run the seeded, outcome-unseen trigger-incidence replication.

Version 1 remains byte-for-byte frozen because its public artifact records the
exact analyzer source hash. This module extends that protocol prospectively
with explicit source exclusions, a mandatory model-level seed gate, and
registered seed-heterogeneity and directional-replication outputs.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import httpx

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import trigger_incidence_probe as v1  # noqa: E402
from scripts.opd.endpoint_policy import require_zero_spend_endpoints  # noqa: E402


SEED_GATE_SCHEMA = "kaetram.local-trigger-incidence-seed-gate.v1"
ProbeError = v1.ProbeError
_V1_LOAD_REGISTRATION = v1.load_registration
_V1_LOAD_DESIGN = v1.load_design
_V1_WRITE_JSON = v1.write_json


def load_registration(path: Path) -> tuple[dict, str]:
    registration, registration_sha256 = _V1_LOAD_REGISTRATION(path)
    seed_gate = registration.get("seed_gate")
    if (
        not isinstance(seed_gate, dict)
        or seed_gate.get("required") is not True
        or not isinstance(seed_gate.get("messages"), list)
        or not seed_gate["messages"]
        or int(seed_gate.get("distinct_seed_count", 0)) < 2
        or int(seed_gate.get("minimum_unique_semantic_responses", 0)) < 2
        or int(seed_gate.get("repeat_seed_index", -1))
        not in range(int(seed_gate.get("distinct_seed_count", 0)))
        or not isinstance(seed_gate.get("sampling"), dict)
    ):
        raise ProbeError("invalid registered seed-gate contract")

    state_pool = registration.get("state_pool", {})
    excluded_design = Path(str(state_pool.get("excluded_design", "")))
    if (
        not str(excluded_design)
        or excluded_design.is_absolute()
        or ".." in excluded_design.parts
    ):
        raise ProbeError("excluded design must be a repository-relative path")
    resolved_excluded_path = REPO / excluded_design
    if (
        not resolved_excluded_path.is_file()
        or v1.sha256_file(resolved_excluded_path)
        != state_pool.get("excluded_design_sha256")
    ):
        raise ProbeError("excluded design identity does not match registration")
    try:
        excluded_payload = json.loads(resolved_excluded_path.read_text())
    except json.JSONDecodeError as exc:
        raise ProbeError("excluded design is not valid JSON") from exc
    registered_paths = state_pool.get("excluded_source_logs")
    design_paths = [
        item.get("source_log")
        for item in excluded_payload.get("states", [])
        if isinstance(item, dict)
    ]
    if registered_paths != design_paths or not registered_paths:
        raise ProbeError("excluded source logs do not match the registered design")
    return registration, registration_sha256


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
    excluded_source_logs = state_contract["excluded_source_logs"]
    if (
        not isinstance(excluded_source_logs, list)
        or len(set(excluded_source_logs)) != len(excluded_source_logs)
        or any(
            not isinstance(item, str)
            or Path(item).is_absolute()
            or ".." in Path(item).parts
            for item in excluded_source_logs
        )
    ):
        raise ProbeError("invalid excluded source-log registration")
    excluded = set(excluded_source_logs)
    eligible_logs = [
        log_path
        for log_path in logs
        if (v1.session_meta(log_path) or {}).get("personality") == personality
        and log_path.relative_to(historical_root).as_posix() not in excluded
    ]
    target = int(state_contract["state_count"])
    stride = max(1, len(eligible_logs) // (2 * target))
    states = []
    for log_path in eligible_logs[::stride]:
        messages = v1._render_decision_state(
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
                "source_log_sha256": v1.sha256_file(log_path),
                "messages_sha256": v1.sha256_json(messages),
                "messages": messages,
            }
        )
        if len(states) == target:
            break
    if len(states) != target:
        raise ProbeError(f"prepared {len(states)} states; registration requires {target}")
    return {
        "schema_version": v1.DESIGN_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "source_log_count": len(logs),
        "eligible_source_log_count": len(eligible_logs),
        "personality": personality,
        "selection_stride": stride,
        "excluded_source_log_count": len(excluded_source_logs),
        "excluded_source_logs_sha256": v1.sha256_json(sorted(excluded_source_logs)),
        "states": states,
        **git_identity,
    }


def prepare_design(
    registration_path: Path,
    historical_root: Path,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    if output_dir.exists():
        raise ProbeError("refusing to overwrite design directory")
    git_identity = v1._git_identity()
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
    v1.write_json(output_path, design, exclusive=True)
    receipt = {
        "schema_version": f"{v1.DESIGN_SCHEMA}.receipt",
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": v1.sha256_file(output_path),
        "state_count": len(design["states"]),
        "selected_source_tree_sha256": v1._source_tree_sha256(design["states"]),
        **git_identity,
    }
    v1.write_json(receipt_path, receipt, exclusive=True)
    return receipt


def load_design(
    path: Path,
    registration: dict,
    registration_sha256: str,
    *,
    historical_root: Path | None = None,
) -> dict:
    design = _V1_LOAD_DESIGN(path, registration, registration_sha256)
    excluded_paths = set(registration["state_pool"]["excluded_source_logs"])
    selected_paths = {state["source_log"] for state in design["states"]}
    if selected_paths.intersection(excluded_paths):
        raise ProbeError("v2 design overlaps the excluded v1 source panel")
    if (
        design.get("excluded_source_log_count") != len(excluded_paths)
        or design.get("excluded_source_logs_sha256")
        != v1.sha256_json(sorted(excluded_paths))
    ):
        raise ProbeError("v2 design does not bind the excluded source panel")
    if historical_root is not None:
        expected = _derive_design(
            registration,
            registration_sha256,
            historical_root,
            v1._git_identity(),
        )
        if design != expected:
            raise ProbeError("v2 design does not rederive from the registered pool")
    return design


def semantic_response_sha256(message: Any) -> str:
    if not isinstance(message, dict):
        raise ProbeError("successful seed-gate request lacks a response object")
    normalized = copy.deepcopy(message)
    tool_calls = normalized.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                call.pop("id", None)
    return v1.sha256_json(normalized)


async def _seed_gate_request(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    api_model: str,
    messages: list[dict],
    sampling: dict,
    seed: int,
    request_id: str,
) -> dict:
    payload = {
        "model": api_model,
        "messages": copy.deepcopy(messages),
        "max_tokens": sampling["max_tokens"],
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "top_k": sampling["top_k"],
        "presence_penalty": sampling["presence_penalty"],
        "seed": seed,
    }
    errors = []
    started = time.monotonic()
    message = None
    for attempt in range(1, int(sampling["attempts"]) + 1):
        try:
            response = await client.post(
                f"{endpoint}/chat/completions",
                json=payload,
                timeout=float(sampling["request_timeout_seconds"]),
            )
            if response.status_code == 200:
                candidate = response.json()["choices"][0]["message"]
                if not isinstance(candidate, dict):
                    raise ValueError("response message is not an object")
                message = candidate
                break
            errors.append(f"attempt {attempt}: HTTP {response.status_code}")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}")
        if attempt < int(sampling["attempts"]):
            await asyncio.sleep(float(attempt))
    row = {
        "schema_version": SEED_GATE_SCHEMA,
        "request_id": request_id,
        "seed": seed,
        "latency_seconds": round(time.monotonic() - started, 6),
        "attempt_errors": errors,
    }
    if message is None:
        return {**row, "status": "failed"}
    return {
        **row,
        "status": "ok",
        "response_message": message,
        "semantic_response_sha256": semantic_response_sha256(message),
    }


async def run_seed_gate(
    registration_path: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    gate = registration["seed_gate"]
    endpoint = require_zero_spend_endpoints([endpoint])[0]
    if snapshot not in registration["snapshots"]:
        raise ProbeError(f"snapshot is not registered: {snapshot}")
    if output_dir.exists():
        raise ProbeError(f"refusing to overwrite seed-gate directory: {output_dir}")
    health = await v1.endpoint_health(endpoint)
    v1.validate_endpoint_health(health, registration, snapshot)
    git_identity = v1._git_identity()
    output_dir.mkdir(parents=True, exist_ok=False)
    preflight = {
        "schema_version": f"{SEED_GATE_SCHEMA}.preflight",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "registration_sha256": registration_sha256,
        "endpoint_health": health,
        "seed_gate": gate,
        **git_identity,
    }
    v1.write_json(output_dir / "preflight.json", preflight)

    sampling = gate["sampling"]
    base_seed = int(gate["base_seed"])
    distinct_count = int(gate["distinct_seed_count"])
    repeat_index = int(gate["repeat_seed_index"])
    requests = [
        (f"seed-{index}", base_seed + index)
        for index in range(distinct_count)
    ]
    requests.append((f"repeat-{repeat_index}", base_seed + repeat_index))
    rows = []
    async with httpx.AsyncClient() as client:
        for request_id, seed in requests:
            rows.append(
                await _seed_gate_request(
                    client,
                    endpoint=endpoint,
                    api_model=registration["snapshots"][snapshot]["api_model"],
                    messages=gate["messages"],
                    sampling=sampling,
                    seed=seed,
                    request_id=request_id,
                )
            )

    try:
        postflight_health = await v1.endpoint_health(endpoint)
        v1.validate_endpoint_health(postflight_health, registration, snapshot)
        endpoint_identity_stable = postflight_health == health
        postflight_error = None if endpoint_identity_stable else "health payload drift"
    except (ProbeError, httpx.HTTPError) as exc:
        postflight_health = None
        endpoint_identity_stable = False
        postflight_error = type(exc).__name__
    v1.write_json(
        output_dir / "postflight.json",
        {
            "schema_version": f"{SEED_GATE_SCHEMA}.postflight",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "endpoint_identity_stable": endpoint_identity_stable,
            "endpoint_health": postflight_health,
            "error": postflight_error,
        },
    )
    with (output_dir / "results.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")

    distinct_rows = rows[:distinct_count]
    all_successful = all(row["status"] == "ok" for row in rows)
    unique_semantic_responses = (
        len({row["semantic_response_sha256"] for row in distinct_rows})
        if all_successful
        else 0
    )
    repeated_seed_reproducible = (
        all_successful
        and distinct_rows[repeat_index]["semantic_response_sha256"]
        == rows[-1]["semantic_response_sha256"]
    )
    passed = (
        all_successful
        and endpoint_identity_stable
        and repeated_seed_reproducible
        and unique_semantic_responses
        >= int(gate["minimum_unique_semantic_responses"])
    )
    completed = {
        "schema_version": f"{SEED_GATE_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": sum(row["status"] == "ok" for row in rows),
        "unique_semantic_responses": unique_semantic_responses,
        "minimum_unique_semantic_responses": int(
            gate["minimum_unique_semantic_responses"]
        ),
        "repeated_seed_reproducible": repeated_seed_reproducible,
        "endpoint_identity_stable": endpoint_identity_stable,
        "passed": passed,
    }
    v1.write_json(output_dir / "completed.json", completed)
    artifact_records = []
    for name in (
        "preflight.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    ):
        artifact = output_dir / name
        artifact_records.append(
            {
                "path": name,
                "size_bytes": artifact.stat().st_size,
                "sha256": v1.sha256_file(artifact),
            }
        )
    index = {
        "schema_version": f"{SEED_GATE_SCHEMA}.artifacts",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "files": artifact_records,
        "tree_sha256": v1.sha256_json(artifact_records),
    }
    v1.write_json(output_dir / "artifact-index.json", index)
    if not passed:
        raise ProbeError("seed gate failed; retained gate artifacts prohibit outcome launch")
    return completed


def verify_seed_gate(
    path: Path,
    registration: dict,
    registration_sha256: str,
    snapshot: str,
    endpoint_health_payload: dict,
) -> dict:
    expected_names = {
        "preflight.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
        "artifact-index.json",
    }
    if (
        not path.is_dir()
        or any(item.is_symlink() or not item.is_file() for item in path.iterdir())
        or {item.name for item in path.iterdir()} != expected_names
    ):
        raise ProbeError("seed-gate artifact set is incomplete")
    try:
        index = json.loads((path / "artifact-index.json").read_text())
        preflight = json.loads((path / "preflight.json").read_text())
        postflight = json.loads((path / "postflight.json").read_text())
        completed = json.loads((path / "completed.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("seed-gate envelope is invalid") from exc
    records = index.get("files")
    if not isinstance(records, list):
        raise ProbeError("seed-gate artifact index is invalid")
    expected_records = []
    for name in ("preflight.json", "results.jsonl", "postflight.json", "completed.json"):
        item = path / name
        expected_records.append(
            {
                "path": name,
                "size_bytes": item.stat().st_size,
                "sha256": v1.sha256_file(item),
            }
        )
    rows = []
    for line_number, line in enumerate(
        (path / "results.jsonl").read_text().splitlines(),
        start=1,
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError(
                f"seed-gate result line {line_number} is invalid"
            ) from exc
        rows.append(row)
    gate = registration["seed_gate"]
    distinct_count = int(gate["distinct_seed_count"])
    repeat_index = int(gate["repeat_seed_index"])
    base_seed = int(gate["base_seed"])
    expected_requests = [
        (f"seed-{index}", base_seed + index)
        for index in range(distinct_count)
    ]
    expected_requests.append((f"repeat-{repeat_index}", base_seed + repeat_index))
    if len(rows) != len(expected_requests):
        raise ProbeError("seed-gate result count does not match registration")
    for row, (request_id, seed) in zip(rows, expected_requests, strict=True):
        message = row.get("response_message")
        if (
            row.get("schema_version") != SEED_GATE_SCHEMA
            or row.get("request_id") != request_id
            or row.get("seed") != seed
            or row.get("status") != "ok"
            or not isinstance(row.get("attempt_errors"), list)
            or not isinstance(message, dict)
            or row.get("semantic_response_sha256")
            != semantic_response_sha256(message)
        ):
            raise ProbeError("seed-gate result does not match registered request")
    distinct_rows = rows[:distinct_count]
    unique_semantic_responses = len(
        {row["semantic_response_sha256"] for row in distinct_rows}
    )
    repeated_seed_reproducible = (
        distinct_rows[repeat_index]["semantic_response_sha256"]
        == rows[-1]["semantic_response_sha256"]
    )
    expected_completed = {
        "schema_version": f"{SEED_GATE_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": len(rows),
        "unique_semantic_responses": unique_semantic_responses,
        "minimum_unique_semantic_responses": int(
            gate["minimum_unique_semantic_responses"]
        ),
        "repeated_seed_reproducible": repeated_seed_reproducible,
        "endpoint_identity_stable": True,
        "passed": (
            repeated_seed_reproducible
            and unique_semantic_responses
            >= int(gate["minimum_unique_semantic_responses"])
        ),
    }
    if (
        records != expected_records
        or index.get("tree_sha256") != v1.sha256_json(expected_records)
        or index.get("study_id") != registration["study_id"]
        or index.get("snapshot") != snapshot
        or preflight.get("schema_version") != f"{SEED_GATE_SCHEMA}.preflight"
        or preflight.get("study_id") != registration["study_id"]
        or preflight.get("snapshot") != snapshot
        or preflight.get("registration_sha256") != registration_sha256
        or preflight.get("seed_gate") != gate
        or preflight.get("endpoint_health") != endpoint_health_payload
        or preflight.get("dirty_paths") != []
        or re.fullmatch(
            r"[0-9a-f]{40}",
            str(preflight.get("source_git_commit", "")),
        )
        is None
        or postflight.get("schema_version") != f"{SEED_GATE_SCHEMA}.postflight"
        or postflight.get("study_id") != registration["study_id"]
        or postflight.get("snapshot") != snapshot
        or postflight.get("endpoint_health") != endpoint_health_payload
        or postflight.get("endpoint_identity_stable") is not True
        or postflight.get("error") is not None
        or completed != expected_completed
        or expected_completed["passed"] is not True
    ):
        raise ProbeError("seed gate is not passed for the current endpoint")
    return {
        "artifact_index_sha256": v1.sha256_file(path / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
        "completed": completed,
        "source_git_commit": preflight["source_git_commit"],
    }


@contextlib.contextmanager
def _v1_protocol_extensions(
    *,
    gate_receipt: dict | None = None,
) -> Iterator[None]:
    original_load_registration = v1.load_registration
    original_load_design = v1.load_design
    original_write_json = v1.write_json

    def bound_write_json(
        path: Path,
        value: Any,
        *,
        exclusive: bool = False,
    ) -> None:
        if (
            gate_receipt is not None
            and path.name == "prelaunch.json"
            and isinstance(value, dict)
            and value.get("schema_version") == f"{v1.RUN_SCHEMA}.prelaunch"
        ):
            value = {
                **value,
                "seed_gate_artifact_index_sha256": gate_receipt[
                    "artifact_index_sha256"
                ],
                "seed_gate_tree_sha256": gate_receipt["tree_sha256"],
            }
        _V1_WRITE_JSON(path, value, exclusive=exclusive)

    v1.load_registration = load_registration
    v1.load_design = load_design
    v1.write_json = bound_write_json
    try:
        yield
    finally:
        v1.load_registration = original_load_registration
        v1.load_design = original_load_design
        v1.write_json = original_write_json


async def run_checkpoint(
    registration_path: Path,
    design_path: Path,
    historical_root: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
    seed_gate_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    if snapshot not in registration["snapshots"]:
        raise ProbeError(f"snapshot is not registered: {snapshot}")
    design = load_design(
        design_path,
        registration,
        registration_sha256,
        historical_root=historical_root,
    )
    endpoint = require_zero_spend_endpoints([endpoint])[0]
    health = await v1.endpoint_health(endpoint)
    v1.validate_endpoint_health(health, registration, snapshot)
    gate_receipt = verify_seed_gate(
        seed_gate_dir,
        registration,
        registration_sha256,
        snapshot,
        health,
    )
    if gate_receipt["source_git_commit"] != design["source_git_commit"]:
        raise ProbeError("seed gate and outcome design use different source commits")
    with _v1_protocol_extensions(gate_receipt=gate_receipt):
        return await v1.run_checkpoint(
            registration_path,
            design_path,
            historical_root,
            endpoint,
            snapshot,
            output_dir,
        )


def _seed_heterogeneity(
    registration: dict,
    design: dict,
    run_dirs: list[Path],
) -> dict:
    rows = []
    for run_dir in run_dirs:
        _preflight, _postflight, _completed, run_rows, _identity = (
            v1._verify_run_directory(run_dir, registration)
        )
        rows.extend(run_rows)
    complete = all(row.get("status") == "ok" for row in rows)
    groups = {}
    for row in rows:
        key = (row["snapshot"], row["condition_id"], row["state_id"])
        groups.setdefault(key, []).append(row)
    expected_group_count = (
        len(registration["snapshots"])
        * len(registration["conditions"])
        * len(design["states"])
    )
    if len(groups) != expected_group_count:
        raise ProbeError("seed-heterogeneity groups do not match the fixed grid")
    records = []
    if complete:
        sample_count = int(registration["sampling"]["samples_per_state_condition"])
        for key, members in sorted(groups.items()):
            if len(members) != sample_count:
                raise ProbeError(f"{key}: incomplete seed-heterogeneity group")
            records.append(
                {
                    "unique_semantic_responses": len(
                        {
                            semantic_response_sha256(row["response_message"])
                            for row in members
                        }
                    ),
                    "primary_outcome_values": len(
                        {bool(row["recovery_opportunity"]) for row in members}
                    ),
                }
            )
    return {
        "status": "complete" if complete else "not_evaluated_incomplete_grid",
        "state_condition_groups": len(records),
        "groups_with_multiple_semantic_responses": sum(
            record["unique_semantic_responses"] > 1 for record in records
        ),
        "groups_with_primary_outcome_heterogeneity": sum(
            record["primary_outcome_values"] > 1 for record in records
        ),
        "minimum_unique_semantic_responses_per_group": (
            min(record["unique_semantic_responses"] for record in records)
            if records
            else None
        ),
        "maximum_unique_semantic_responses_per_group": (
            max(record["unique_semantic_responses"] for record in records)
            if records
            else None
        ),
    }


def analyze(
    registration_path: Path,
    design_path: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    design = load_design(design_path, registration, registration_sha256)
    gates_by_snapshot = {}
    for gate_dir in seed_gate_dirs:
        preflight = json.loads((gate_dir / "preflight.json").read_text())
        snapshot = preflight.get("snapshot")
        if snapshot in gates_by_snapshot:
            raise ProbeError("duplicate seed-gate snapshot")
        gates_by_snapshot[snapshot] = gate_dir
    if set(gates_by_snapshot) != set(registration["snapshots"]):
        raise ProbeError("analysis requires one seed gate per checkpoint")

    for run_dir in run_dirs:
        prelaunch, _postflight, _completed, _rows, _identity = (
            v1._verify_run_directory(run_dir, registration)
        )
        snapshot = prelaunch["snapshot"]
        gate = verify_seed_gate(
            gates_by_snapshot[snapshot],
            registration,
            registration_sha256,
            snapshot,
            prelaunch["endpoint_health"],
        )
        if (
            gate["source_git_commit"] != design["source_git_commit"]
            or prelaunch.get("seed_gate_artifact_index_sha256")
            != gate["artifact_index_sha256"]
            or prelaunch.get("seed_gate_tree_sha256") != gate["tree_sha256"]
        ):
            raise ProbeError(f"{snapshot}: run is not bound to the passed seed gate")

    with _v1_protocol_extensions():
        summary = v1.analyze(
            registration_path,
            design_path,
            run_dirs,
            output_dir,
        )
    heterogeneity = _seed_heterogeneity(registration, design, run_dirs)
    native_effects = {
        row["snapshot"]: row["effect_rate_difference"]
        for row in summary["registered_contrasts"]
        if row["contrast"] == "native_tools_main"
    }
    complete = summary["analysis_status"] == "complete"
    summary["analysis_code_provenance"]["analysis_script_sha256"] = v1.sha256_file(
        Path(__file__).resolve()
    )
    summary["registered_seed_heterogeneity"] = heterogeneity
    summary["directional_replication"] = {
        "criterion": registration["analysis"]["directional_replication_criterion"],
        "status": "evaluated" if complete else "not_evaluated_incomplete_grid",
        "native_tools_effects": native_effects,
        "passed": (
            all(
                snapshot in native_effects and native_effects[snapshot] > 0
                for snapshot in registration["snapshots"]
            )
            if complete
            else None
        ),
    }
    v1.write_json(output_dir / "analysis-summary.json", summary)
    artifacts = []
    for name in ("analysis-summary.json", "cells.csv", "contrasts.csv"):
        artifact = output_dir / name
        artifacts.append(
            {
                "path": name,
                "size_bytes": artifact.stat().st_size,
                "sha256": v1.sha256_file(artifact),
            }
        )
    v1.write_json(
        output_dir / "artifact-index.json",
        {
            "schema_version": f"{v1.ANALYSIS_SCHEMA}.artifacts",
            "files": artifacts,
            "tree_sha256": v1.sha256_json(artifacts),
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
    seed_gate = subparsers.add_parser("seed-gate")
    seed_gate.add_argument("--registration", type=Path, required=True)
    seed_gate.add_argument("--endpoint", required=True)
    seed_gate.add_argument("--snapshot", required=True)
    seed_gate.add_argument("--out-dir", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--design", type=Path, required=True)
    run.add_argument("--historical-root", type=Path, required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--snapshot", required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--seed-gate-dir", type=Path, required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--registration", type=Path, required=True)
    analyze_parser.add_argument("--design", type=Path, required=True)
    analyze_parser.add_argument("--run-dir", type=Path, action="append", required=True)
    analyze_parser.add_argument(
        "--seed-gate-dir",
        type=Path,
        action="append",
        required=True,
    )
    analyze_parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_design(
            args.registration,
            args.historical_root,
            args.out_dir,
        )
    elif args.command == "seed-gate":
        result = asyncio.run(
            run_seed_gate(
                args.registration,
                args.endpoint,
                args.snapshot,
                args.out_dir,
            )
        )
    elif args.command == "run":
        result = asyncio.run(
            run_checkpoint(
                args.registration,
                args.design,
                args.historical_root,
                args.endpoint,
                args.snapshot,
                args.out_dir,
                args.seed_gate_dir,
            )
        )
    else:
        result = analyze(
            args.registration,
            args.design,
            args.run_dir,
            args.seed_gate_dir,
            args.out_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
