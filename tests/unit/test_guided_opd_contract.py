from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.guided_opd_contract import (
    BACKEND_PLAN_SCHEMA,
    COMPLETE_TURN_BOUNDARY,
    GUIDANCE_ALGORITHM,
    GUIDANCE_SCHEMA,
    NORMALIZED_SCHEMA,
    GuidedContractError,
    load_guided_training_bundle,
    make_role_decision,
    teacher_turn_probability,
    validate_guided_records,
)
from scripts.opd.guided_opd_schedule import decision_from_cell


SCHEDULE = {
    "schedule": "cosine",
    "schedule_basis": "training_progress",
    "start_teacher_turn_probability": 1.0,
    "end_teacher_turn_probability": 0.0,
    "curriculum_ratio": 0.8,
    "trajectory_probability": "held_fixed_within_trajectory",
    "total_training_steps": 250,
    "student_turn_loss": "reverse_kl",
    "teacher_turn_loss": "forward_kl",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_json(value) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()


def _records(*, training_step: int = 100) -> list[dict]:
    records = []
    turns = []
    for turn_index in range(2):
        record_id = f"trajectory-1-turn-{turn_index}"
        decision = make_role_decision(
            seed=7,
            decision_id=record_id,
            trajectory_id="trajectory-0",
            turn_index=turn_index,
            training_step=training_step,
            config=SCHEDULE,
        )
        actor = decision["actor_role"]
        content = f"complete {actor} turn"
        actor_token_ids = [10 + turn_index] * 5
        turns.append({
            "turn_id": record_id,
            "turn_index": turn_index,
            "actor_role": actor,
            "content": content,
            "content_sha256": _sha_json(content),
            "actor_token_ids": list(actor_token_ids),
            "boundary": COMPLETE_TURN_BOUNDARY,
            "role_decision_id": record_id,
        })
        records.append({
            "schema_version": NORMALIZED_SCHEMA,
            "record_id": record_id,
            "cell_id": "guided-seed-7",
            "arm_id": "guided_opd",
            "objective": "opd",
            "training_seed": 7,
            "identities": {"base_checkpoint_artifact_id": "base", "teacher_artifact_id": "teacher"},
            "history": {
                "kind": "guided_mixed_history",
                "source": "same_live_mixed_rollout",
                "content": {
                    "trajectory_id": "trajectory-0",
                    "turns": [dict(turn) for turn in turns],
                },
            },
            "semantics": {
                "mode": "guided_opd_actor_turn",
                "trajectory_id": "trajectory-0",
                "turn_index": turn_index,
                "actor_role": actor,
                "turn_loss": "forward_kl" if actor == "teacher" else "reverse_kl",
                "role_decision": decision,
            },
            "input_ids": list(actor_token_ids),
            "labels": list(actor_token_ids),
            "advantages": None if actor == "teacher" else [0.2] * 5,
            "behavior_logprobs": [-0.2] * 5,
            "budget_usage": {
                "action_tokens": 5,
                "teacher_scoring_tokens": 1,
                "environment_interactions": 1,
            },
            "curriculum": {
                "kind": "guided_opd",
                "teacher_turn_probability": decision["teacher_turn_probability"],
                "actor_role": actor,
                "role_decision": decision,
            },
        })
    return records


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    records_path = tmp_path / "normalized-records.jsonl"
    records_path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in _records()))
    root = Path(__file__).parents[2]
    contract_path = root / "scripts/opd/guided_opd_contract.py"
    scheduler_path = root / "scripts/opd/guided_opd_schedule.py"
    plan = {
        "schema_version": BACKEND_PLAN_SCHEMA,
        "cell_id": "guided-seed-7",
        "arm_id": "guided_opd",
        "objective": "opd",
        "training_seed": 7,
        "identities": {"base_checkpoint_artifact_id": "base", "teacher_artifact_id": "teacher"},
        "budgets": {"action_tokens": 10, "teacher_scoring_tokens": 2,
                    "environment_interactions": 2},
        "curriculum_contract": SCHEDULE,
        "trainer_route": {
            "entrypoint": "finetune/train_opd_2b.py",
            "compatibility": "guided_collection_supported_objective_blocked",
        },
        "intervention_scheduler": {
            "entrypoint": "scripts/opd/guided_opd_schedule.py",
            "entrypoint_sha256": _sha(scheduler_path),
            "contract_module": "scripts/opd/guided_opd_contract.py",
            "contract_sha256": _sha(contract_path),
            "schema_version": GUIDANCE_SCHEMA,
            "algorithm": GUIDANCE_ALGORITHM,
        },
        "normalized_records": {
            "path": str(records_path.resolve()),
            "sha256": _sha(records_path),
            "schema_version": NORMALIZED_SCHEMA,
            "records": 2,
        },
        "execution_status": "not_run",
    }
    plan_path = tmp_path / "backend-plan.json"
    plan_path.write_text(json.dumps(plan))
    return records_path, plan_path


def test_published_cosine_schedule_uses_frozen_training_progress() -> None:
    assert teacher_turn_probability(SCHEDULE, training_step=0) == 1.0
    assert teacher_turn_probability(SCHEDULE, training_step=200) == 0.0
    assert teacher_turn_probability(SCHEDULE, training_step=250) == 0.0
    with pytest.raises(GuidedContractError, match="250"):
        teacher_turn_probability({**SCHEDULE, "total_training_steps": 251}, training_step=0)


def test_turn_role_draw_is_deterministic_and_independent_by_turn() -> None:
    args = dict(seed=7, trajectory_id="trajectory-1", training_step=100, config=SCHEDULE)
    first = make_role_decision(decision_id="turn-0", turn_index=0, **args)
    assert first == make_role_decision(decision_id="turn-0", turn_index=0, **args)
    second = make_role_decision(decision_id="turn-1", turn_index=1, **args)
    assert first["draw_u64_hex"] != second["draw_u64_hex"]
    assert first["teacher_turn_probability"] == second["teacher_turn_probability"]


def test_bundle_validates_both_actor_roles_and_append_only_history() -> None:
    records = _records()
    validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)
    records[1]["history"]["content"]["turns"][0]["content"] = "rewritten history"
    records[1]["history"]["content"]["turns"][0]["content_sha256"] = _sha_json(
        "rewritten history"
    )
    with pytest.raises(GuidedContractError, match="append-only history"):
        validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)


def test_complete_turn_content_and_tokens_are_bound_before_observation() -> None:
    records = _records()
    records[0]["history"]["content"]["turns"][0]["actor_token_ids"][-1] = 99
    with pytest.raises(GuidedContractError, match="bound to supervised tokens"):
        validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)

    records = _records()
    records[0]["input_ids"][-1] = 99
    with pytest.raises(GuidedContractError, match="bound to supervised tokens"):
        validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)

    records = _records()
    records[0]["history"]["content"]["turns"][0]["boundary"] = "includes_observation"
    with pytest.raises(GuidedContractError, match="before the environment observation"):
        validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)


def test_actor_role_must_match_actor_conditional_kl() -> None:
    records = _records()
    records[0]["semantics"]["turn_loss"] = (
        "reverse_kl" if records[0]["semantics"]["actor_role"] == "teacher" else "forward_kl"
    )
    with pytest.raises(GuidedContractError, match="actor-conditional KL"):
        validate_guided_records(records, seed=7, config=SCHEDULE, action_token_budget=10)


def test_bundle_loader_fails_closed_on_hash_drift(tmp_path: Path) -> None:
    records_path, plan_path = _bundle(tmp_path)
    records, plan = load_guided_training_bundle(records_path, plan_path)
    assert len(records) == 2
    assert plan["trainer_route"]["compatibility"].endswith("objective_blocked")
    records_path.write_text(records_path.read_text() + "\n")
    with pytest.raises(GuidedContractError, match="SHA-256 mismatch"):
        load_guided_training_bundle(records_path, plan_path)


def test_scheduler_derives_total_steps_from_registered_cell(tmp_path: Path) -> None:
    cell_path = tmp_path / "cell.json"
    cell_path.write_text(json.dumps({
        "schema_version": "kaetram.matched-training-cell.v1",
        "training_seed": 7,
        "arm": {"arm_id": "guided_opd", "objective": "opd", "guided_annealing": SCHEDULE},
    }))
    actual = decision_from_cell(
        cell_path,
        decision_id="turn-1",
        trajectory_id="trajectory-1",
        turn_index=1,
        training_step=100,
    )
    assert actual["total_training_steps"] == 250


def test_legacy_trainer_compiles_and_explicitly_blocks_guided_objective(tmp_path: Path) -> None:
    trainer_path = Path(__file__).parents[2] / "finetune/train_opd_2b.py"
    source = trainer_path.read_text()
    tree = ast.parse(source, filename=str(trainer_path))
    retries_keywords = [
        keyword for node in ast.walk(tree) if isinstance(node, ast.Call)
        for keyword in node.keywords if keyword.arg == "retries"
    ]
    assert len(retries_keywords) == 1
    assert "Guided-OPD bundle validated, but execution is blocked" in source
    assert "forward KL on teacher turns" in source
    assert 'isinstance(semantics, dict)' in source
    assert 'semantics.get("mode") == "guided_opd_actor_turn"' in source
    assert 'isinstance(history, dict)' in source
    assert 'history.get("kind") == "guided_mixed_history"' in source

    load_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_load_records"
    )
    namespace = {}
    exec(compile(ast.Module(body=[load_node], type_ignores=[]), str(trainer_path), "exec"), namespace)
    records_path = tmp_path / "guided-without-plan.jsonl"
    records_path.write_text(json.dumps({
        "semantics": {"mode": "guided_opd_actor_turn"},
        "input_ids": [1],
        "labels": [1],
        "advantages": [0.0],
        "behavior_logprobs": [-0.1],
    }) + "\n")
    with pytest.raises(ValueError, match="records-manifest-path is required"):
        namespace["_load_records"](records_path)
