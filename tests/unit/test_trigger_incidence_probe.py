import copy
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from scripts.opd.trigger_incidence_probe import (
    ANALYSIS_SCHEMA,
    DESIGN_SCHEMA,
    REGISTRATION_SCHEMA,
    RUN_SCHEMA,
    ProbeError,
    _request_one,
    analyze,
    condition_messages,
    decode_endpoint_health,
    load_design,
    sha256_file,
    sha256_json,
    surface_families,
)


def test_surface_families_and_repaired_documentation() -> None:
    assert surface_families('<function=gather("Oak")>') == ["python_call"]
    assert surface_families("<parameter=accept=True>") == ["kwarg_in_key"]
    assert surface_families("ordinary prose") == []

    original = [
        {
            "role": "system",
            "content": "Use interact_npc(npc_name, accept_quest_offer=False).",
        },
        {"role": "user", "content": "continue"},
    ]
    frozen = copy.deepcopy(original)
    repaired = condition_messages(original, "canonical_docs")
    assert original == frozen
    assert "interact_npc [params:" in repaired[0]["content"]
    assert condition_messages(original, "python_docs") == original


def test_request_records_recovery_opportunity_and_native_tools() -> None:
    seen_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '<function=gather("Oak")></function>',
                        }
                    }
                ]
            },
        )

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            records = []
            for index, tool_state in enumerate(("absent", "present")):
                records.append(
                    await _request_one(
                        client,
                        asyncio.Semaphore(1),
                        endpoint="http://127.0.0.1:9999/v1",
                        api_model="test-model",
                        messages=[{"role": "system", "content": "tools"}],
                        condition={
                            "condition_id": f"condition-{tool_state}",
                            "documentation": "python_docs",
                            "native_tool_schema": tool_state,
                        },
                        sampling={
                            "base_seed": 10,
                            "max_tokens": 20,
                            "temperature": 1.0,
                            "top_p": 0.95,
                            "top_k": 20,
                            "presence_penalty": 1.5,
                            "attempts": 1,
                            "request_timeout_seconds": 5,
                        },
                        state_id="state-01",
                        state_index=0,
                        sample_index=0,
                        schedule_index=index,
                    )
                )
            return records

    records = asyncio.run(exercise())
    assert all(record["recovery_opportunity"] for record in records)
    assert "tools" not in seen_payloads[0]
    assert len(seen_payloads[1]["tools"]) > 1


def test_malformed_health_json_is_a_retained_probe_error() -> None:
    response = httpx.Response(
        200,
        text="{not-json",
        request=httpx.Request("GET", "http://127.0.0.1:9999/health"),
    )
    with pytest.raises(ProbeError, match="not valid JSON"):
        decode_endpoint_health(response)


def _registration(tmp_path: Path) -> Path:
    conditions = [
        {
            "condition_id": "python-docs_no-tools",
            "documentation": "python_docs",
            "native_tool_schema": "absent",
        },
        {
            "condition_id": "python-docs_native-tools",
            "documentation": "python_docs",
            "native_tool_schema": "present",
        },
        {
            "condition_id": "canonical-docs_no-tools",
            "documentation": "canonical_docs",
            "native_tool_schema": "absent",
        },
        {
            "condition_id": "canonical-docs_native-tools",
            "documentation": "canonical_docs",
            "native_tool_schema": "present",
        },
    ]
    registration = {
        "schema_version": REGISTRATION_SCHEMA,
        "study_id": "test-study",
        "state_pool": {"state_count": 2, "personality": "completionist"},
        "snapshots": {
            name: {"api_model": name, "checkpoint_sha256": name * 8}
            for name in ("base", "r2", "r3")
        },
        "endpoint_contract": {"tokenizer_sha256": "f" * 64},
        "conditions": conditions,
        "sampling": {"samples_per_state_condition": 1, "base_seed": 100},
        "claim_boundary": {"confirmatory": False},
    }
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(registration))
    return path


def _design(tmp_path: Path, registration_path: Path) -> Path:
    states = []
    for index in range(2):
        messages = [{"role": "system", "content": f"state {index}"}]
        states.append(
            {
                "state_id": f"state-{index + 1:02d}",
                "personality": "completionist",
                "source_log": f"dataset/raw/source-{index}.log",
                "source_log_sha256": str(index) * 64,
                "messages": messages,
                "messages_sha256": sha256_json(messages),
            }
        )
    design = {
        "schema_version": DESIGN_SCHEMA,
        "study_id": "test-study",
        "registration_sha256": sha256_file(registration_path),
        "source_log_count": 10,
        "eligible_source_log_count": 4,
        "personality": "completionist",
        "selection_stride": 1,
        "states": states,
        "source_git_commit": "a" * 40,
        "dirty_paths": [],
    }
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design))
    receipt = {
        "schema_version": f"{DESIGN_SCHEMA}.receipt",
        "study_id": "test-study",
        "registration_sha256": sha256_file(registration_path),
        "design_sha256": sha256_file(path),
        "state_count": 2,
        "selected_source_tree_sha256": sha256_json(
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
        ),
        "source_git_commit": "a" * 40,
        "dirty_paths": [],
    }
    path.with_suffix(".receipt.json").write_text(json.dumps(receipt))
    return path


def _run_dir(
    tmp_path: Path,
    snapshot: str,
    registration_path: Path,
    design_path: Path,
    *,
    fail_first: bool = False,
) -> Path:
    root = tmp_path / snapshot
    root.mkdir()
    registration = json.loads(registration_path.read_text())
    health = {
        "status": "ok",
        "attestation": {
            "api_model": snapshot,
            "checkpoint_sha256": registration["snapshots"][snapshot][
                "checkpoint_sha256"
            ],
            "tokenizer_sha256": "f" * 64,
        },
    }
    prelaunch = {
        "schema_version": f"{RUN_SCHEMA}.prelaunch",
        "study_id": "test-study",
        "snapshot": snapshot,
        "registration_sha256": sha256_file(registration_path),
        "design_sha256": sha256_file(design_path),
        "endpoint_health": health,
        "sampling": registration["sampling"],
        "source_git_commit": "a" * 40,
        "dirty_paths": [],
    }
    (root / "prelaunch.json").write_text(json.dumps(prelaunch))
    rows = []
    schedule = 0
    for state_index in range(2):
        offset = state_index % len(registration["conditions"])
        conditions = (
            registration["conditions"][offset:] + registration["conditions"][:offset]
        )
        for condition in conditions:
            failed = fail_first and schedule == 0
            row = {
                "schema_version": RUN_SCHEMA,
                "snapshot": snapshot,
                "schedule_index": schedule,
                "condition_id": condition["condition_id"],
                "documentation": condition["documentation"],
                "native_tool_schema": condition["native_tool_schema"],
                "state_id": f"state-{state_index + 1:02d}",
                "state_index": state_index,
                "sample_index": 0,
                "seed": 100 + 100 * state_index,
                "status": "failed" if failed else "ok",
                "latency_seconds": 0.1,
                "attempt_errors": [],
            }
            if not failed:
                row.update(
                    {
                        "response_message": {
                            "role": "assistant",
                            "content": "ordinary prose",
                        },
                        "structured_tool_call_count": 0,
                        "has_structured_tool_call": False,
                        "no_structured_tool_call": True,
                        "has_content": True,
                        "recovery_opportunity": False,
                        "recoverable_calls": [],
                        "malformed_emission": False,
                        "malformed_families": [],
                    }
                )
            rows.append(row)
            schedule += 1
    (root / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    postflight = {
        "schema_version": f"{RUN_SCHEMA}.postflight",
        "study_id": "test-study",
        "snapshot": snapshot,
        "endpoint_identity_stable": True,
        "endpoint_health": health,
        "error": None,
    }
    (root / "postflight.json").write_text(json.dumps(postflight))
    completed = {
        "schema_version": f"{RUN_SCHEMA}.completed",
        "study_id": "test-study",
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": sum(row["status"] == "ok" for row in rows),
        "failed_requests": sum(row["status"] != "ok" for row in rows),
        "recovery_opportunities": 0,
        "malformed_emissions": 0,
        "structured_tool_responses": 0,
        "no_structured_tool_call_responses": sum(
            row.get("no_structured_tool_call", False) for row in rows
        ),
        "endpoint_identity_stable": True,
    }
    (root / "completed.json").write_text(json.dumps(completed))
    records = []
    for name in (
        "prelaunch.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    ):
        path = root / name
        records.append(
            {
                "path": name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    (root / "artifact-index.json").write_text(
        json.dumps(
            {
                "schema_version": f"{RUN_SCHEMA}.artifacts",
                "study_id": "test-study",
                "snapshot": snapshot,
                "files": records,
                "tree_sha256": sha256_json(records),
            }
        )
    )
    return root


def _reseal_run(root: Path) -> None:
    records = []
    for name in (
        "prelaunch.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    ):
        path = root / name
        records.append(
            {
                "path": name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    index = json.loads((root / "artifact-index.json").read_text())
    index["files"] = records
    index["tree_sha256"] = sha256_json(records)
    (root / "artifact-index.json").write_text(json.dumps(index))


def test_complete_analysis_reports_finite_grid_and_nine_contrasts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.trigger_incidence_probe._git_identity",
        lambda: {"source_git_commit": "b" * 40, "dirty_paths": []},
    )
    registration = _registration(tmp_path)
    design = _design(tmp_path, registration)
    runs = [
        _run_dir(tmp_path, snapshot, registration, design)
        for snapshot in ("base", "r2", "r3")
    ]
    summary = analyze(registration, design, runs, tmp_path / "analysis")
    assert summary["schema_version"] == ANALYSIS_SCHEMA
    assert summary["analysis_status"] == "complete"
    assert summary["scheduled_requests"] == 24
    assert summary["recovery_opportunities"] == 0
    assert len(summary["cells"]) == 12
    assert len(summary["registered_contrasts"]) == 9
    assert all(row["effect_rate_difference"] == 0 for row in summary["registered_contrasts"])
    assert all(row["states_zero"] == 2 for row in summary["registered_contrasts"])
    assert len(summary["input_runs"]) == 3


def test_incomplete_analysis_retains_failures_and_suppresses_contrasts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.trigger_incidence_probe._git_identity",
        lambda: {"source_git_commit": "b" * 40, "dirty_paths": []},
    )
    registration = _registration(tmp_path)
    design = _design(tmp_path, registration)
    runs = [
        _run_dir(
            tmp_path,
            snapshot,
            registration,
            design,
            fail_first=snapshot == "base",
        )
        for snapshot in ("base", "r2", "r3")
    ]
    summary = analyze(registration, design, runs, tmp_path / "analysis")
    assert summary["analysis_status"] == "incomplete"
    assert summary["failed_requests"] == 1
    assert summary["registered_contrasts"] == []


def test_design_rejects_self_hashed_wrong_personality(tmp_path: Path) -> None:
    registration_path = _registration(tmp_path)
    design_path = _design(tmp_path, registration_path)
    registration = json.loads(registration_path.read_text())
    design = json.loads(design_path.read_text())
    design["states"][0]["personality"] = "grinder"
    design_path.write_text(json.dumps(design))
    receipt = json.loads(design_path.with_suffix(".receipt.json").read_text())
    receipt["design_sha256"] = sha256_file(design_path)
    design_path.with_suffix(".receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ProbeError, match="message hash mismatch"):
        load_design(design_path, registration, sha256_file(registration_path))


def test_analysis_rejects_empty_artifact_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.trigger_incidence_probe._git_identity",
        lambda: {"source_git_commit": "b" * 40, "dirty_paths": []},
    )
    registration = _registration(tmp_path)
    design = _design(tmp_path, registration)
    runs = [
        _run_dir(tmp_path, snapshot, registration, design)
        for snapshot in ("base", "r2", "r3")
    ]
    index_path = runs[0] / "artifact-index.json"
    index = json.loads(index_path.read_text())
    index["files"] = []
    index_path.write_text(json.dumps(index))
    with pytest.raises(ProbeError, match="artifact index contract mismatch"):
        analyze(registration, design, runs, tmp_path / "analysis")


def test_analysis_recomputes_outcome_from_raw_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.trigger_incidence_probe._git_identity",
        lambda: {"source_git_commit": "b" * 40, "dirty_paths": []},
    )
    registration = _registration(tmp_path)
    design = _design(tmp_path, registration)
    runs = [
        _run_dir(tmp_path, snapshot, registration, design)
        for snapshot in ("base", "r2", "r3")
    ]
    rows = [
        json.loads(line)
        for line in (runs[0] / "results.jsonl").read_text().splitlines()
    ]
    rows[0]["recovery_opportunity"] = True
    (runs[0] / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    completed = json.loads((runs[0] / "completed.json").read_text())
    completed["recovery_opportunities"] = 1
    (runs[0] / "completed.json").write_text(json.dumps(completed))
    _reseal_run(runs[0])
    with pytest.raises(ProbeError, match="stored outcome differs"):
        analyze(registration, design, runs, tmp_path / "analysis")
