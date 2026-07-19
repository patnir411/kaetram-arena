"""Fail-closed Guided-OPD rollout scheduling and prepared-bundle validation.

Guided-OPD samples the actor independently for each *complete turn*.  The
teacher probability is derived from training progress and held fixed within a
trajectory.  Student turns require reverse KL; teacher turns require forward
KL.  This module validates collection records but does not claim that the
legacy offline OPD trainer implements that online asymmetric objective.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


GUIDANCE_SCHEMA = "kaetram.guided-opd-role-decision.v1"
GUIDANCE_ALGORITHM = "sha256-turn-role-53bit-v1"
COMPLETE_TURN_BOUNDARY = "complete_actor_response_before_environment_observation"
BACKEND_PLAN_SCHEMA = "kaetram.matched-training-backend-plan.v1"
NORMALIZED_SCHEMA = "kaetram.normalized-training-record.v1"
MAX_SEED = 2**31 - 1


class GuidedContractError(ValueError):
    pass


def _sha256_json(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()


def _seed(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SEED:
        raise GuidedContractError(f"training seed must be an integer between 0 and {MAX_SEED}")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GuidedContractError(f"{label} must be a nonnegative integer")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    result = _nonnegative_int(value, label=label)
    if result < 1:
        raise GuidedContractError(f"{label} must be a positive integer")
    return result


def _schedule(config: Any) -> dict[str, Any]:
    expected = {
        "schedule", "schedule_basis", "start_teacher_turn_probability",
        "end_teacher_turn_probability", "curriculum_ratio",
        "trajectory_probability", "total_training_steps", "student_turn_loss",
        "teacher_turn_loss",
    }
    if not isinstance(config, dict) or set(config) != expected:
        raise GuidedContractError("guided curriculum contract has unexpected fields")
    if config["schedule"] != "cosine" or config["schedule_basis"] != "training_progress":
        raise GuidedContractError("published Guided-OPD requires the cosine training-progress schedule")
    if config["start_teacher_turn_probability"] != 1.0 \
            or config["end_teacher_turn_probability"] != 0.0:
        raise GuidedContractError("teacher-turn probability must decay from 1 to 0")
    if config["curriculum_ratio"] != 0.8:
        raise GuidedContractError("published Guided-OPD curriculum_ratio must be 0.8")
    if config["trajectory_probability"] != "held_fixed_within_trajectory":
        raise GuidedContractError("teacher-turn probability must be held fixed within a trajectory")
    if config["total_training_steps"] != 250:
        raise GuidedContractError("published Guided-OPD total_training_steps must be 250")
    if config["student_turn_loss"] != "reverse_kl" \
            or config["teacher_turn_loss"] != "forward_kl":
        raise GuidedContractError("Guided-OPD requires student rKL and teacher fKL")
    return config


def teacher_turn_probability(
    config: dict[str, Any], *, training_step: int,
) -> float:
    cfg = _schedule(config)
    step = _nonnegative_int(training_step, label="training_step")
    total = cfg["total_training_steps"]
    if step > total:
        raise GuidedContractError("training_step must not exceed total_training_steps")
    decay_progress = min(step / (cfg["curriculum_ratio"] * total), 1.0)
    return 0.5 * (1.0 + math.cos(math.pi * decay_progress))


def _draw_hex(seed: int, trajectory_id: str, turn_index: int) -> str:
    if not isinstance(trajectory_id, str) or not trajectory_id:
        raise GuidedContractError("trajectory_id must be non-empty")
    material = (
        f"{GUIDANCE_ALGORITHM}:{_seed(seed)}:{trajectory_id}:"
        f"{_nonnegative_int(turn_index, label='turn_index')}"
    ).encode()
    return hashlib.sha256(material).digest()[:8].hex()


def make_role_decision(
    *, seed: int, decision_id: str, trajectory_id: str, turn_index: int,
    training_step: int, config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(decision_id, str) or not decision_id:
        raise GuidedContractError("decision_id must be non-empty")
    probability = teacher_turn_probability(
        config, training_step=training_step
    )
    draw_hex = _draw_hex(seed, trajectory_id, turn_index)
    draw = (int(draw_hex, 16) >> 11) / (1 << 53)
    return {
        "schema_version": GUIDANCE_SCHEMA,
        "algorithm": GUIDANCE_ALGORITHM,
        "training_seed": _seed(seed),
        "decision_id": decision_id,
        "trajectory_id": trajectory_id,
        "turn_index": _nonnegative_int(turn_index, label="turn_index"),
        "training_step": _nonnegative_int(training_step, label="training_step"),
        "total_training_steps": config["total_training_steps"],
        "teacher_turn_probability": probability,
        "draw_u64_hex": draw_hex,
        "actor_role": "teacher" if draw < probability else "student",
    }


def validate_role_decision(
    value: Any, *, seed: int, decision_id: str, trajectory_id: str,
    turn_index: int, training_step: int, config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GuidedContractError("Guided role decision must be an object")
    expected = make_role_decision(
        seed=seed,
        decision_id=decision_id,
        trajectory_id=trajectory_id,
        turn_index=turn_index,
        training_step=training_step,
        config=config,
    )
    if value != expected:
        mismatches = {
            key: {"expected": expected_value, "actual": value.get(key)}
            for key, expected_value in expected.items()
            if value.get(key) != expected_value
        }
        raise GuidedContractError(
            f"Guided role-decision mismatch: fields={mismatches}, "
            f"extra={sorted(set(value) - set(expected))}"
        )
    return expected


def _validate_turn_history(record: dict[str, Any]) -> list[dict[str, Any]]:
    record_id = record.get("record_id")
    semantics = record.get("semantics")
    history = record.get("history")
    if not isinstance(semantics, dict) or semantics.get("actor_role") not in {"teacher", "student"}:
        raise GuidedContractError(f"record {record_id} actor_role must be teacher or student")
    expected_loss = {"student": "reverse_kl", "teacher": "forward_kl"}[
        semantics["actor_role"]
    ]
    if semantics.get("turn_loss") != expected_loss:
        raise GuidedContractError(
            f"record {record_id} has the wrong actor-conditional KL objective"
        )
    if not isinstance(history, dict) or history.get("kind") != "guided_mixed_history" \
            or history.get("source") != "same_live_mixed_rollout":
        raise GuidedContractError(f"record {record_id} lacks canonical Guided mixed history")
    content = history.get("content")
    if not isinstance(content, dict) or set(content) != {"trajectory_id", "turns"} \
            or content.get("trajectory_id") != semantics.get("trajectory_id"):
        raise GuidedContractError(f"record {record_id} mixed-history trajectory mismatch")
    turns = content.get("turns")
    index = semantics.get("turn_index")
    if not isinstance(turns, list) or isinstance(index, bool) or not isinstance(index, int) \
            or index != len(turns) - 1:
        raise GuidedContractError(f"record {record_id} must end at its complete actor turn")
    for expected_index, turn in enumerate(turns):
        if not isinstance(turn, dict) or set(turn) != {
            "turn_id", "turn_index", "actor_role", "content", "content_sha256",
            "actor_token_ids", "boundary", "role_decision_id",
        } or turn.get("turn_index") != expected_index \
                or turn.get("actor_role") not in {"teacher", "student"} \
                or not isinstance(turn.get("turn_id"), str) or not turn["turn_id"]:
            raise GuidedContractError(f"record {record_id} has malformed mixed-history turns")
        if not isinstance(turn["content"], str) or not turn["content"] \
                or turn["content_sha256"] != _sha256_json(turn["content"]):
            raise GuidedContractError(f"record {record_id} has unbound actor-turn content")
        if turn["boundary"] != COMPLETE_TURN_BOUNDARY:
            raise GuidedContractError(
                f"record {record_id} does not end before the environment observation"
            )
        if not isinstance(turn["actor_token_ids"], list) or not turn["actor_token_ids"] \
                or not all(
                    isinstance(token, int) and not isinstance(token, bool) and token >= 0
                    for token in turn["actor_token_ids"]
                ):
            raise GuidedContractError(f"record {record_id} has invalid complete-turn token IDs")
    current = turns[index]
    if current["actor_role"] != semantics["actor_role"] \
            or current["role_decision_id"] != record_id:
        raise GuidedContractError(f"record {record_id} role decision contradicts its complete turn")
    return turns


def validate_guided_records(
    records: list[dict[str, Any]], *, seed: int, config: dict[str, Any],
    action_token_budget: int,
) -> None:
    """Verify complete-turn role decisions and trajectory-level schedule invariants."""
    _seed(seed)
    _schedule(config)
    budget = _positive_int(action_token_budget, label="action-token budget")
    if not records:
        raise GuidedContractError("Guided record bundle is empty")
    seen_ids: set[str] = set()
    trajectories: dict[str, list[dict[str, Any]]] = {}
    observed_action_tokens = 0
    for record in records:
        record_id = record.get("record_id")
        semantics = record.get("semantics")
        if not isinstance(record_id, str) or not record_id or record_id in seen_ids:
            raise GuidedContractError("Guided record IDs must be non-empty and unique")
        seen_ids.add(record_id)
        if not isinstance(semantics, dict) or semantics.get("mode") != "guided_opd_actor_turn":
            raise GuidedContractError(f"record {record_id} is not a Guided-OPD actor turn")
        turns = _validate_turn_history(record)
        trajectory_id = semantics.get("trajectory_id")
        decision = semantics.get("role_decision")
        if not isinstance(decision, dict):
            raise GuidedContractError(f"record {record_id} role decision is missing")
        validate_role_decision(
            decision,
            seed=seed,
            decision_id=record_id,
            trajectory_id=trajectory_id,
            turn_index=semantics.get("turn_index"),
            training_step=decision.get("training_step"),
            config=config,
        )
        if semantics.get("actor_role") != decision["actor_role"]:
            raise GuidedContractError(f"record {record_id} actor role differs from its decision")
        usage = record.get("budget_usage")
        action_tokens = usage.get("action_tokens") if isinstance(usage, dict) else None
        action_tokens = _positive_int(action_tokens, label=f"record {record_id} action-token usage")
        labels = record.get("labels")
        input_ids = record.get("input_ids")
        if not isinstance(input_ids, list) or not isinstance(labels, list) \
                or len(input_ids) != len(labels) \
                or sum(label != -100 for label in labels) != action_tokens:
            raise GuidedContractError(f"record {record_id} labels do not match actor-turn tokens")
        supervised_tokens = [label for label in labels if label != -100]
        actor_input_tokens = [
            token for token, label in zip(input_ids, labels, strict=True) if label != -100
        ]
        if turns[-1]["actor_token_ids"] != supervised_tokens \
                or actor_input_tokens != supervised_tokens:
            raise GuidedContractError(
                f"record {record_id} complete actor turn is not bound to supervised tokens"
            )
        behavior = record.get("behavior_logprobs")
        advantages = record.get("advantages")
        if not isinstance(behavior, list) or len(behavior) != len(input_ids):
            raise GuidedContractError(f"record {record_id} actor logprobs are missing")
        if semantics["actor_role"] == "teacher":
            if advantages is not None:
                raise GuidedContractError(
                    f"record {record_id} teacher turn must not carry reverse-KL advantages"
                )
        elif not isinstance(advantages, list) or len(advantages) != len(input_ids):
            raise GuidedContractError(
                f"record {record_id} student turn lacks reverse-KL advantages"
            )
        observed_action_tokens += action_tokens
        trajectories.setdefault(trajectory_id, []).append({"record": record, "turns": turns})
    if observed_action_tokens != budget:
        raise GuidedContractError(
            f"Guided records do not fill action-token budget: expected {budget}, "
            f"got {observed_action_tokens}"
        )
    for trajectory_id, items in trajectories.items():
        items.sort(key=lambda item: item["record"]["semantics"]["turn_index"])
        decisions = [item["record"]["semantics"]["role_decision"] for item in items]
        indices = [item["record"]["semantics"]["turn_index"] for item in items]
        if indices != list(range(len(items))):
            raise GuidedContractError(f"trajectory {trajectory_id} turn records are incomplete")
        if len({(d["training_step"], d["total_training_steps"],
                 d["teacher_turn_probability"]) for d in decisions}) != 1:
            raise GuidedContractError(
                f"trajectory {trajectory_id} does not hold teacher probability fixed"
            )
        for index, item in enumerate(items):
            expected_prefix = item["turns"]
            if index and expected_prefix[:-1] != items[index - 1]["turns"]:
                raise GuidedContractError(
                    f"trajectory {trajectory_id} records do not share one append-only history"
                )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_guided_training_bundle(
    records_path: str | Path, backend_plan_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate a prepared Guided bundle; execution remains objective-blocked."""
    records_file = Path(records_path).resolve()
    plan_file = Path(backend_plan_path).resolve()
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuidedContractError(f"cannot load backend plan: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != BACKEND_PLAN_SCHEMA:
        raise GuidedContractError("backend plan schema mismatch")
    if plan.get("arm_id") != "guided_opd" or plan.get("objective") != "opd":
        raise GuidedContractError("backend plan is not a Guided-OPD cell")
    if plan.get("execution_status") != "not_run":
        raise GuidedContractError("prepared backend plan must retain execution_status=not_run")
    route = plan.get("trainer_route")
    if not isinstance(route, dict) or route.get("entrypoint") != "finetune/train_opd_2b.py" \
            or route.get("compatibility") != "guided_collection_supported_objective_blocked":
        raise GuidedContractError("backend plan must retain the blocked asymmetric-objective route")
    scheduler = plan.get("intervention_scheduler")
    if not isinstance(scheduler, dict) or set(scheduler) != {
        "entrypoint", "entrypoint_sha256", "contract_module", "contract_sha256",
        "schema_version", "algorithm",
    }:
        raise GuidedContractError("backend plan role scheduler provenance is incomplete")
    if scheduler["entrypoint"] != "scripts/opd/guided_opd_schedule.py" \
            or scheduler["contract_module"] != "scripts/opd/guided_opd_contract.py" \
            or scheduler["schema_version"] != GUIDANCE_SCHEMA \
            or scheduler["algorithm"] != GUIDANCE_ALGORITHM:
        raise GuidedContractError("backend plan role scheduler contract mismatch")
    contract_file = Path(__file__).resolve()
    scheduler_file = contract_file.with_name("guided_opd_schedule.py")
    if scheduler["contract_sha256"] != _sha256(contract_file) \
            or not scheduler_file.is_file() \
            or scheduler["entrypoint_sha256"] != _sha256(scheduler_file):
        raise GuidedContractError("loaded Guided scheduler differs from backend plan")
    normalized = plan.get("normalized_records")
    if not isinstance(normalized, dict) or set(normalized) != {
        "path", "sha256", "schema_version", "records",
    }:
        raise GuidedContractError("backend plan normalized-record provenance is incomplete")
    if Path(normalized["path"]).name != records_file.name \
            or normalized["schema_version"] != NORMALIZED_SCHEMA \
            or _sha256(records_file) != normalized["sha256"]:
        raise GuidedContractError("normalized records filename, schema, or SHA-256 mismatch")
    records: list[dict[str, Any]] = []
    try:
        with records_file.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise GuidedContractError(
                            f"normalized record {line_number} is not an object"
                        )
                    records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise GuidedContractError(f"cannot load normalized records: {exc}") from exc
    if len(records) != normalized["records"]:
        raise GuidedContractError("normalized record count differs from backend plan")
    seed = _seed(plan.get("training_seed"))
    identities = plan.get("identities")
    for record in records:
        if record.get("schema_version") != NORMALIZED_SCHEMA \
                or record.get("cell_id") != plan.get("cell_id") \
                or record.get("arm_id") != "guided_opd" \
                or record.get("objective") != "opd" \
                or record.get("training_seed") != seed \
                or record.get("identities") != identities:
            raise GuidedContractError("normalized Guided record provenance mismatch")
        semantics = record.get("semantics")
        decision = semantics.get("role_decision") if isinstance(semantics, dict) else None
        expected_curriculum = {
            "kind": "guided_opd",
            "teacher_turn_probability": decision.get("teacher_turn_probability")
            if isinstance(decision, dict) else None,
            "actor_role": decision.get("actor_role") if isinstance(decision, dict) else None,
            "role_decision": decision,
        }
        if record.get("curriculum") != expected_curriculum:
            raise GuidedContractError("normalized Guided curriculum differs from role decision")
    budgets = plan.get("budgets")
    if not isinstance(budgets, dict):
        raise GuidedContractError("backend plan budgets are missing")
    validate_guided_records(
        records,
        seed=seed,
        config=plan.get("curriculum_contract"),
        action_token_budget=budgets.get("action_tokens"),
    )
    return records, plan
