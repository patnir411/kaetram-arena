import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from scripts.opd import trigger_incidence_probe as v1
from scripts.opd import trigger_incidence_probe_v2 as v2


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _registration(tmp_path: Path, monkeypatch) -> tuple[Path, dict]:
    monkeypatch.setattr(v2, "REPO", tmp_path)
    excluded_path = Path("research/artifacts/v1/design.json")
    excluded_logs = [
        "dataset/raw/agent_1/runs/run/session-old-1.log",
        "dataset/raw/agent_1/runs/run/session-old-2.log",
    ]
    _write_json(
        tmp_path / excluded_path,
        {"states": [{"source_log": item} for item in excluded_logs]},
    )
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
        "schema_version": v1.REGISTRATION_SCHEMA,
        "study_id": "seeded-test",
        "state_pool": {
            "state_count": 2,
            "personality": "completionist",
            "excluded_design": excluded_path.as_posix(),
            "excluded_design_sha256": v1.sha256_file(tmp_path / excluded_path),
            "excluded_source_logs": excluded_logs,
        },
        "snapshots": {
            name: {"api_model": name, "checkpoint_sha256": name * 8}
            for name in ("base", "r2", "r3")
        },
        "endpoint_contract": {"tokenizer_sha256": "f" * 64},
        "conditions": conditions,
        "sampling": {"samples_per_state_condition": 2, "base_seed": 500},
        "seed_gate": {
            "required": True,
            "messages": [{"role": "user", "content": "vary"}],
            "base_seed": 100,
            "distinct_seed_count": 3,
            "repeat_seed_index": 1,
            "minimum_unique_semantic_responses": 2,
            "sampling": {
                "max_tokens": 20,
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "presence_penalty": 1.5,
                "attempts": 1,
                "request_timeout_seconds": 5,
            },
        },
        "analysis": {
            "directional_replication_criterion": "all native effects positive"
        },
        "claim_boundary": {"confirmatory": False},
    }
    path = tmp_path / "registration.json"
    _write_json(path, registration)
    return path, registration


def _health(registration: dict, snapshot: str) -> dict:
    return {
        "status": "ok",
        "attestation": {
            "api_model": registration["snapshots"][snapshot]["api_model"],
            "checkpoint_sha256": registration["snapshots"][snapshot][
                "checkpoint_sha256"
            ],
            **registration["endpoint_contract"],
        },
    }


def _seal_gate(
    root: Path,
    registration_path: Path,
    registration: dict,
    snapshot: str,
) -> dict:
    health = _health(registration, snapshot)
    gate = registration["seed_gate"]
    _write_json(
        root / "preflight.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.preflight",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "registration_sha256": v1.sha256_file(registration_path),
            "endpoint_health": health,
            "seed_gate": gate,
            "source_git_commit": "a" * 40,
            "dirty_paths": [],
        },
    )
    messages = ("alpha", "beta", "gamma", "beta")
    seeds = (100, 101, 102, 101)
    request_ids = ("seed-0", "seed-1", "seed-2", "repeat-1")
    rows = []
    for request_id, seed, content in zip(request_ids, seeds, messages, strict=True):
        message = {"role": "assistant", "content": content}
        rows.append(
            {
                "schema_version": v2.SEED_GATE_SCHEMA,
                "request_id": request_id,
                "seed": seed,
                "status": "ok",
                "latency_seconds": 0.1,
                "attempt_errors": [],
                "response_message": message,
                "semantic_response_sha256": v2.semantic_response_sha256(message),
            }
        )
    (root / "results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    _write_json(
        root / "postflight.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.postflight",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "endpoint_identity_stable": True,
            "endpoint_health": health,
            "error": None,
        },
    )
    _write_json(
        root / "completed.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.completed",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "scheduled_requests": 4,
            "successful_requests": 4,
            "unique_semantic_responses": 3,
            "minimum_unique_semantic_responses": 2,
            "repeated_seed_reproducible": True,
            "endpoint_identity_stable": True,
            "passed": True,
        },
    )
    records = []
    for name in (
        "preflight.json",
        "results.jsonl",
        "postflight.json",
        "completed.json",
    ):
        item = root / name
        records.append(
            {
                "path": name,
                "size_bytes": item.stat().st_size,
                "sha256": v1.sha256_file(item),
            }
        )
    _write_json(
        root / "artifact-index.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.artifacts",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "files": records,
            "tree_sha256": v1.sha256_json(records),
        },
    )
    return health


def test_registration_binds_exact_excluded_design(tmp_path: Path, monkeypatch) -> None:
    registration_path, registration = _registration(tmp_path, monkeypatch)
    loaded, _digest = v2.load_registration(registration_path)
    assert loaded == registration
    registration["state_pool"]["excluded_source_logs"][0] = "different.log"
    _write_json(registration_path, registration)
    with pytest.raises(v2.ProbeError, match="excluded source logs"):
        v2.load_registration(registration_path)


def test_seed_gate_request_passes_seed_and_ignores_generated_call_id() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"seed {payload['seed']}",
                            "tool_calls": [
                                {
                                    "id": f"generated-{payload['seed']}",
                                    "type": "function",
                                    "function": {
                                        "name": "observe",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    async def exercise() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows = []
            for index, seed in enumerate((101, 102, 101)):
                rows.append(
                    await v2._seed_gate_request(
                        client,
                        endpoint="http://127.0.0.1:9999/v1",
                        api_model="test",
                        messages=[{"role": "user", "content": "vary"}],
                        sampling={
                            "max_tokens": 20,
                            "temperature": 1.0,
                            "top_p": 0.95,
                            "top_k": 20,
                            "presence_penalty": 1.5,
                            "attempts": 1,
                            "request_timeout_seconds": 5,
                        },
                        seed=seed,
                        request_id=f"request-{index}",
                    )
                )
            return rows

    rows = asyncio.run(exercise())
    assert [payload["seed"] for payload in seen] == [101, 102, 101]
    assert rows[0]["semantic_response_sha256"] != rows[1]["semantic_response_sha256"]
    assert rows[0]["semantic_response_sha256"] == rows[2]["semantic_response_sha256"]
    changed_id = copy.deepcopy(rows[0]["response_message"])
    changed_id["tool_calls"][0]["id"] = "different-generated-id"
    assert v2.semantic_response_sha256(changed_id) == rows[0][
        "semantic_response_sha256"
    ]


def test_gate_verifier_recomputes_seed_diversity_and_repeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registration_path, registration = _registration(tmp_path, monkeypatch)
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    health = _seal_gate(gate_dir, registration_path, registration, "base")
    receipt = v2.verify_seed_gate(
        gate_dir,
        registration,
        v1.sha256_file(registration_path),
        "base",
        health,
    )
    assert receipt["completed"]["unique_semantic_responses"] == 3

    rows = [
        json.loads(line)
        for line in (gate_dir / "results.jsonl").read_text().splitlines()
    ]
    rows[-1]["response_message"]["content"] = "not beta"
    (gate_dir / "results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    with pytest.raises(v2.ProbeError):
        v2.verify_seed_gate(
            gate_dir,
            registration,
            v1.sha256_file(registration_path),
            "base",
            health,
        )


def test_run_prelaunch_is_bound_to_gate_before_requests(tmp_path: Path) -> None:
    receipt = {
        "artifact_index_sha256": "d" * 64,
        "tree_sha256": "e" * 64,
    }
    path = tmp_path / "prelaunch.json"
    with v2._v1_protocol_extensions(gate_receipt=receipt):
        v1.write_json(
            path,
            {
                "schema_version": f"{v1.RUN_SCHEMA}.prelaunch",
                "study_id": "seeded-test",
            },
        )
    written = json.loads(path.read_text())
    assert written["seed_gate_artifact_index_sha256"] == "d" * 64
    assert written["seed_gate_tree_sha256"] == "e" * 64


def test_seed_heterogeneity_recomputes_every_registered_group(monkeypatch) -> None:
    conditions = [
        {"condition_id": condition}
        for condition in ("a", "b", "c", "d")
    ]
    registration = {
        "snapshots": {"base": {}, "r2": {}, "r3": {}},
        "conditions": conditions,
        "sampling": {"samples_per_state_condition": 2},
    }
    design = {"states": [{"state_id": "state-01"}, {"state_id": "state-02"}]}

    def verified_run(run_dir: Path, _registration: dict):
        rows = []
        for condition in conditions:
            for state in design["states"]:
                for sample_index in range(2):
                    rows.append(
                        {
                            "snapshot": run_dir.name,
                            "condition_id": condition["condition_id"],
                            "state_id": state["state_id"],
                            "sample_index": sample_index,
                            "status": "ok",
                            "response_message": {
                                "role": "assistant",
                                "content": f"sample {sample_index}",
                            },
                            "recovery_opportunity": False,
                        }
                    )
        return {}, {}, {}, rows, {}

    monkeypatch.setattr(v1, "_verify_run_directory", verified_run)
    summary = v2._seed_heterogeneity(
        registration,
        design,
        [Path("base"), Path("r2"), Path("r3")],
    )
    assert summary == {
        "status": "complete",
        "state_condition_groups": 24,
        "groups_with_multiple_semantic_responses": 24,
        "groups_with_primary_outcome_heterogeneity": 0,
        "minimum_unique_semantic_responses_per_group": 2,
        "maximum_unique_semantic_responses_per_group": 2,
    }
