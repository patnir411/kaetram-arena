from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finetune import score_style_first_error as score


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_json(value) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _fixture(tmp_path: Path, *, mutate_record=None, budgets=None) -> Path:
    output = tmp_path / "cell"
    output.mkdir()
    prefix = [10, 11]
    correction = [21, 22]
    identities = {
        "base_checkpoint_artifact_id": "base",
        "teacher_artifact_id": "teacher",
        "render_contract_sha256": "a" * 64,
        "held_out_registration_artifact_id": "heldout",
    }
    record = {
        "schema_version": score.NORMALIZED_SCHEMA,
        "record_id": "score-0001",
        "cell_id": "score-seed-7",
        "arm_id": "score_first_error_prefixes",
        "role": "mechanism_or_baseline",
        "objective": "score",
        "training_seed": 7,
        "recovery": "on",
        "identities": identities,
        "state": {"kind": "state"},
        "history": {"kind": "history"},
        "semantics": {
            "mode": "verified_first_model_visible_error_prefix",
            "student_trajectory_id": "student-1",
            "first_error_index": 3,
            "verified_prefix_token_count": len(prefix),
            "verified_prefix_sha256": _sha_json(prefix),
            "correction_target_sha256": _sha_json(correction),
            "first_error_evidence_sha256": "b" * 64,
            "prefix_verifier_sha256": "c" * 64,
        },
        "input_ids": prefix + correction,
        "labels": [-100] * len(prefix) + correction,
        "advantages": None,
        "behavior_logprobs": None,
        "step_weight": 1.5,
        "budget_usage": {
            "action_tokens": len(correction),
            "teacher_scoring_tokens": 3,
            "environment_interactions": 1,
        },
        "source": {
            "artifact_id": "score-source",
            "payload_sha256": "d" * 64,
            "source_record_sha256": "e" * 64,
        },
        "curriculum": {},
    }
    if mutate_record is not None:
        mutate_record(record)
    records_path = output / "normalized-records.jsonl"
    records_bytes = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    records_path.write_bytes(records_bytes)
    registered_budgets = budgets or {
        "action_tokens": 2,
        "teacher_scoring_tokens": 3,
        "environment_interactions": 1,
    }
    plan = {
        "schema_version": score.BACKEND_PLAN_SCHEMA,
        "experiment_id": "score-test",
        "cell_id": "score-seed-7",
        "arm_id": "score_first_error_prefixes",
        "role": "mechanism_or_baseline",
        "objective": "score",
        "training_seed": 7,
        "source_git_commit": "f" * 40,
        "experiment_manifest_sha256": "1" * 64,
        "cell_config": {"path": "/immutable/cell.json", "sha256": "2" * 64},
        "artifact_registry": {"path": "/immutable/registry.json", "sha256": "3" * 64},
        "artifact_root": "/immutable/artifacts",
        "source_artifact": {
            "artifact_id": "score-source",
            "material_path": "/immutable/score.jsonl",
            "sha256": "d" * 64,
            "records": 1,
        },
        "identities": identities,
        "optimizer": {"name": "adamw_8bit"},
        "budgets": registered_budgets,
        "trainer_route": {
            "entrypoint": score.ADAPTER_PATH,
            "compatibility": score.ROUTE_COMPATIBILITY,
            "reason": "test",
        },
        "execution_status": "not_run",
        "normalized_records": {
            "path": str(records_path),
            "sha256": _sha_bytes(records_bytes),
            "schema_version": score.NORMALIZED_SCHEMA,
            "records": 1,
        },
    }
    plan_path = output / "backend-plan.json"
    plan_bytes = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    plan_path.write_bytes(plan_bytes)
    result = {
        "schema_version": score.BACKEND_RESULT_SCHEMA,
        "experiment_id": "score-test",
        "cell_id": "score-seed-7",
        "status": "prepared_not_trained",
        "source_git_commit": "f" * 40,
        "experiment_manifest_sha256": "1" * 64,
        "base_checkpoint_artifact_id": "base",
        "teacher_artifact_id": "teacher",
        "training_seed": 7,
        "allocated_budgets": registered_budgets,
        "backend_plan": {"path": str(plan_path), "sha256": _sha_bytes(plan_bytes)},
        "output_artifact": {
            "kind": "normalized_training_records",
            "uri": f"file:{records_path}",
            "sha256": _sha_bytes(records_bytes),
        },
        "trainer_execution_status": "not_run",
        "trainer_compatibility": score.ROUTE_COMPATIBILITY,
    }
    result_path = output / "result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result_path


def test_materializes_stage1_and_fails_closed_before_stage2(tmp_path: Path) -> None:
    result_path = _fixture(tmp_path)
    result = score.materialize(result_path)
    assert result["status"] == "prepared_stage1_stage2_blocked_not_trained"
    assert result["launch_allowed"] is False
    assert result["trainer_execution_status"] == "not_run"
    assert result["checkpoint_artifact"] is None
    plan = json.loads(Path(result["objective_plan"]["path"]).read_text())
    assert plan["scientific_scope"]["full_published_score_reproduction"] is False
    assert plan["stage1"]["status"] == "prepared_not_trained"
    assert plan["stage2"]["status"] == "blocked_missing_registered_runtime_materials"
    assert plan["stage2"]["normalized_stage1_records_are_sufficient"] is False
    assert plan["launch_allowed"] is False
    with pytest.raises(FileExistsError):
        score.materialize(result_path)


def test_stage1_adapter_is_deterministic_and_preserves_evidence(tmp_path: Path) -> None:
    result_path = _fixture(tmp_path)
    first_plan, first_records = score.build_objective_plan(result_path)
    second_plan, second_records = score.build_objective_plan(result_path)
    assert first_plan == second_plan
    assert first_records == second_records
    prepared = first_records[0]
    assert prepared["labels"] == [-100, -100, 21, 22]
    assert prepared["provenance"]["first_error_evidence_sha256"] == "b" * 64
    assert prepared["provenance"]["prefix_verifier_sha256"] == "c" * 64


def test_correction_sft_loss_masks_prefix_and_weights_tokens() -> None:
    loss = score.correction_sft_nll(
        [[-9.0, -9.0, -0.2, -0.4], [-0.1]],
        [[-100, -100, 21, 22], [7]],
        [1.0, 2.0],
    )
    assert loss == pytest.approx(0.2)
    with pytest.raises(score.ObjectiveContractError, match="no correction tokens"):
        score.correction_sft_nll([[-0.1]], [[-100]], [1.0])


def test_short_horizon_loss_requires_explicit_runtime_inputs() -> None:
    loss = score.short_horizon_target_reward_loss(
        [[-0.2, -0.4], [-0.3]],
        [[1, 1], [1]],
        [1.0, 0.0],
        [0.0, 0.5],
        [1.0, 2.0],
    )
    assert loss == pytest.approx(0.075)
    with pytest.raises(score.ObjectiveContractError, match="aligned batches"):
        score.short_horizon_target_reward_loss([[-0.2]], [[1]], [], [], [])


def test_rejects_normalized_material_hash_drift(tmp_path: Path) -> None:
    result_path = _fixture(tmp_path)
    records_path = result_path.parent / "normalized-records.jsonl"
    records_path.write_text(records_path.read_text() + "\n")
    with pytest.raises(score.ObjectiveContractError, match="normalized records SHA-256 mismatch"):
        score.build_objective_plan(result_path)


def test_rejects_unbound_correction_target(tmp_path: Path) -> None:
    def mutate(record):
        record["labels"][-1] = 23

    result_path = _fixture(tmp_path, mutate_record=mutate)
    with pytest.raises(score.ObjectiveContractError, match="correction target is not contiguous"):
        score.build_objective_plan(result_path)


def test_rejects_empty_verified_token_prefix(tmp_path: Path) -> None:
    def mutate(record):
        record["semantics"]["verified_prefix_token_count"] = 0
        record["semantics"]["verified_prefix_sha256"] = _sha_json([])

    result_path = _fixture(tmp_path, mutate_record=mutate)
    with pytest.raises(score.ObjectiveContractError, match="positive integer"):
        score.build_objective_plan(result_path)


def test_rejects_budget_mismatch(tmp_path: Path) -> None:
    result_path = _fixture(
        tmp_path,
        budgets={
            "action_tokens": 3,
            "teacher_scoring_tokens": 3,
            "environment_interactions": 1,
        },
    )
    with pytest.raises(score.ObjectiveContractError, match="exactly fill the registered budget"):
        score.build_objective_plan(result_path)
