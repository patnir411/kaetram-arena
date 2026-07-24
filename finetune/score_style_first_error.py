#!/usr/bin/env python3
"""Prepare and validate a fail-closed SCoRe-style first-error objective.

This is deliberately not a full reproduction of SCoRe.  It materializes the
correction-SFT inputs available in a matched-training backend bundle and
defines deterministic loss adapters for both correction SFT and subsequent
short-horizon target-reward optimization.  It never trains a model, and it
does not declare Stage 2 runnable without separately registered post-Stage-1
rollouts, reward evidence, and per-stage budgets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


BACKEND_PLAN_SCHEMA = "kaetram.matched-training-backend-plan.v1"
BACKEND_RESULT_SCHEMA = "kaetram.matched-training-result.v2"
NORMALIZED_SCHEMA = "kaetram.normalized-training-record.v1"
STAGE1_RECORD_SCHEMA = "kaetram.score-style-first-error-stage1-record.v1"
OBJECTIVE_PLAN_SCHEMA = "kaetram.score-style-first-error-objective-plan.v1"
OBJECTIVE_RESULT_SCHEMA = "kaetram.score-style-first-error-objective-result.v1"
OBJECTIVE_ID = "score_style_verified_first_error_two_stage"
ADAPTER_PATH = "finetune/score_style_first_error.py"
ROUTE_COMPATIBILITY = "score_style_two_stage_adapter_stage2_inputs_required_not_executed"
BLOCKERS = (
    "missing_hash_registered_stage1_checkpoint",
    "missing_post_stage1_short_horizon_rollouts_and_target_reward_evidence",
    "missing_reviewed_per_stage_budget_allocation",
)


class ObjectiveContractError(ValueError):
    """The prepared objective does not satisfy its immutable contract."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObjectiveContractError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ObjectiveContractError(f"{label} fields must be exactly {sorted(expected)}")


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(char not in "0123456789abcdef" for char in value) \
            or value == "0" * 64:
        raise ObjectiveContractError(f"{label} must be a resolved lowercase SHA-256")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ObjectiveContractError(f"{label} must be a nonnegative integer")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ObjectiveContractError(f"{label} must be a positive integer")
    return value


def _positive_finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise ObjectiveContractError(f"{label} must be positive and finite")
    return float(value)


def _verified_child(path_value: Any, *, root: Path, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ObjectiveContractError(f"{label} must be a non-empty path")
    raw = Path(path_value)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ObjectiveContractError(f"{label} must remain inside the backend output directory") from exc
    if ".." in relative.parts:
        raise ObjectiveContractError(f"{label} must not contain parent traversal")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ObjectiveContractError(f"{label} must not traverse a symlink")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ObjectiveContractError(f"{label} must remain inside the backend output directory") from exc
    if not resolved.is_file():
        raise ObjectiveContractError(f"{label} does not exist: {resolved}")
    return resolved


def correction_sft_nll(
    token_log_probabilities: list[list[float]],
    labels: list[list[int]],
    step_weights: list[float],
) -> float:
    """Return weighted mean NLL over correction tokens only.

    ``token_log_probabilities`` contains the log probability assigned to each
    already-selected label, not a vocabulary-sized logits tensor. Prefix
    positions must use label ``-100`` and are excluded from both numerator and
    denominator.
    """
    if not token_log_probabilities or len(token_log_probabilities) != len(labels) \
            or len(labels) != len(step_weights):
        raise ObjectiveContractError("correction-SFT loss inputs must be non-empty aligned batches")
    numerator = 0.0
    denominator = 0.0
    for row_index, (logps, row_labels, raw_weight) in enumerate(
        zip(token_log_probabilities, labels, step_weights, strict=True)
    ):
        weight = _positive_finite(raw_weight, label=f"step_weights[{row_index}]")
        if not isinstance(logps, list) or not isinstance(row_labels, list) \
                or len(logps) != len(row_labels) or not logps:
            raise ObjectiveContractError(f"loss row {row_index} must contain aligned token arrays")
        supervised = 0
        for token_index, (logp, label) in enumerate(zip(logps, row_labels, strict=True)):
            if isinstance(label, bool) or not isinstance(label, int) or label < -100:
                raise ObjectiveContractError(f"labels[{row_index}][{token_index}] is invalid")
            if label == -100:
                continue
            if isinstance(logp, bool) or not isinstance(logp, (int, float)) \
                    or not math.isfinite(logp) or logp > 0:
                raise ObjectiveContractError(
                    f"token_log_probabilities[{row_index}][{token_index}] must be finite and <= 0"
                )
            numerator += -float(logp) * weight
            denominator += weight
            supervised += 1
        if supervised == 0:
            raise ObjectiveContractError(f"loss row {row_index} has no correction tokens")
    return numerator / denominator


def short_horizon_target_reward_loss(
    sampled_action_log_probabilities: list[list[float]],
    action_masks: list[list[int]],
    target_rewards: list[float],
    baselines: list[float],
    step_weights: list[float],
) -> float:
    """Return a deterministic REINFORCE-style short-horizon policy loss.

    The caller must supply post-Stage-1 samples and independently verified
    target rewards. This function deliberately accepts no normalized Stage-1
    record as a substitute for those missing runtime materials.
    """
    size = len(sampled_action_log_probabilities)
    if size < 1 or any(len(values) != size for values in (
        action_masks, target_rewards, baselines, step_weights,
    )):
        raise ObjectiveContractError("short-horizon loss inputs must be non-empty aligned batches")
    numerator = 0.0
    denominator = 0.0
    for row_index in range(size):
        logps = sampled_action_log_probabilities[row_index]
        mask = action_masks[row_index]
        if not isinstance(logps, list) or not isinstance(mask, list) \
                or not logps or len(logps) != len(mask):
            raise ObjectiveContractError(f"short-horizon row {row_index} must be aligned")
        reward = target_rewards[row_index]
        baseline = baselines[row_index]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in (reward, baseline)):
            raise ObjectiveContractError(f"short-horizon row {row_index} rewards must be finite")
        advantage = float(reward) - float(baseline)
        weight = _positive_finite(step_weights[row_index], label=f"step_weights[{row_index}]")
        selected = 0
        for token_index, (logp, include) in enumerate(zip(logps, mask, strict=True)):
            if include not in (0, 1) or isinstance(include, bool):
                raise ObjectiveContractError(f"action_masks[{row_index}][{token_index}] must be 0 or 1")
            if include == 0:
                continue
            if isinstance(logp, bool) or not isinstance(logp, (int, float)) \
                    or not math.isfinite(logp) or logp > 0:
                raise ObjectiveContractError(f"sample log probability at row {row_index} is invalid")
            numerator += -float(logp) * advantage * weight
            denominator += weight
            selected += 1
        if selected == 0:
            raise ObjectiveContractError(f"short-horizon row {row_index} has no sampled action tokens")
    return numerator / denominator


def stage1_correction_collator(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Pad prepared Stage-1 records for a PyTorch/Transformers trainer.

    PyTorch is imported lazily so provenance validation and dry-run remain
    usable in environments without accelerator dependencies.
    """
    if not features:
        raise ObjectiveContractError("Stage-1 collator requires at least one record")
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - accelerator image boundary
        raise ObjectiveContractError("PyTorch is required only for live trainer integration") from exc
    maximum = max(len(feature["input_ids"]) for feature in features)

    def pad(key: str, fill: int):
        rows = []
        for index, feature in enumerate(features):
            values = feature.get(key)
            if not isinstance(values, list) or not values:
                raise ObjectiveContractError(f"Stage-1 feature {index}.{key} must be non-empty")
            rows.append(values + [fill] * (maximum - len(values)))
        return torch.tensor(rows, dtype=torch.long)

    return {
        "input_ids": pad("input_ids", 0),
        "labels": pad("labels", -100),
        "attention_mask": torch.tensor(
            [
                [1] * len(feature["input_ids"])
                + [0] * (maximum - len(feature["input_ids"]))
                for feature in features
            ],
            dtype=torch.long,
        ),
        "step_weight": torch.tensor(
            [
                _positive_finite(
                    feature.get("step_weight"), label=f"Stage-1 feature {index}.step_weight"
                )
                for index, feature in enumerate(features)
            ],
            dtype=torch.float,
        ),
    }


def torch_correction_sft_loss(model: Any, batch: dict[str, Any]) -> Any:
    """Compute weighted correction CE with causal next-token alignment.

    The implementation projects only supervised action positions through the
    language-model head, matching the memory-bounded path used by the OPD
    trainer while keeping the verified prefix context-only.
    """
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - accelerator image boundary
        raise ObjectiveContractError("PyTorch is required only for live trainer integration") from exc
    required = {"input_ids", "labels", "attention_mask", "step_weight"}
    if set(batch) != required:
        raise ObjectiveContractError(f"Stage-1 tensor batch fields must be exactly {sorted(required)}")
    labels = batch["labels"]
    unwrapped = model
    while hasattr(unwrapped, "module"):
        unwrapped = unwrapped.module
    base = unwrapped.get_base_model() if hasattr(unwrapped, "get_base_model") else unwrapped
    body = base.model
    lm_head = base.lm_head
    hidden = body(
        input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
    ).last_hidden_state[:, :-1, :]
    shifted_labels = labels[:, 1:]
    selected = (shifted_labels != -100).nonzero(as_tuple=False)
    if selected.numel() == 0:
        raise ObjectiveContractError("Stage-1 tensor batch has no correction tokens")
    rows, positions = selected[:, 0], selected[:, 1]
    logits = lm_head(hidden[rows, positions]).float()
    token_loss = torch.nn.functional.cross_entropy(
        logits, shifted_labels[rows, positions], reduction="none"
    )
    weights = batch["step_weight"].to(token_loss.device)[rows].float()
    if not torch.isfinite(weights).all() or (weights <= 0).any():
        raise ObjectiveContractError("Stage-1 tensor step weights must be positive and finite")
    return (token_loss * weights).sum() / weights.sum()


def torch_short_horizon_target_reward_loss(
    sampled_action_log_probabilities: Any,
    action_masks: Any,
    target_rewards: Any,
    baselines: Any,
    step_weights: Any,
) -> Any:
    """Tensor form of the Stage-2 loss; it does not create runtime evidence."""
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - accelerator image boundary
        raise ObjectiveContractError("PyTorch is required only for live trainer integration") from exc
    device = sampled_action_log_probabilities.device
    action_masks = action_masks.to(device)
    target_rewards = target_rewards.to(device)
    baselines = baselines.to(device)
    step_weights = step_weights.to(device)
    if sampled_action_log_probabilities.shape != action_masks.shape:
        raise ObjectiveContractError("Stage-2 tensor log probabilities and masks must align")
    if sampled_action_log_probabilities.ndim != 2:
        raise ObjectiveContractError("Stage-2 tensor inputs must be rank two")
    batch_size = sampled_action_log_probabilities.shape[0]
    if any(value.shape != (batch_size,) for value in (target_rewards, baselines, step_weights)):
        raise ObjectiveContractError("Stage-2 tensor rewards, baselines, and weights must align")
    if not torch.isfinite(sampled_action_log_probabilities).all() \
            or (sampled_action_log_probabilities > 0).any():
        raise ObjectiveContractError("Stage-2 tensor log probabilities must be finite and <= 0")
    if not torch.isfinite(target_rewards).all() or not torch.isfinite(baselines).all():
        raise ObjectiveContractError("Stage-2 tensor rewards and baselines must be finite")
    if not torch.isfinite(step_weights).all() or (step_weights <= 0).any():
        raise ObjectiveContractError("Stage-2 tensor weights must be positive and finite")
    if not torch.all((action_masks == 0) | (action_masks == 1)):
        raise ObjectiveContractError("Stage-2 tensor action mask must contain only zero and one")
    token_weights = action_masks * step_weights[:, None]
    if (token_weights.sum(dim=1) <= 0).any():
        raise ObjectiveContractError("every Stage-2 tensor row must select an action token")
    advantages = target_rewards - baselines
    return (
        -sampled_action_log_probabilities * advantages[:, None] * token_weights
    ).sum() / token_weights.sum()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_text()), label=label)
    except (OSError, json.JSONDecodeError) as exc:
        raise ObjectiveContractError(f"cannot load {label} {path}: {exc}") from exc


def _load_jsonl_bytes(content: bytes, *, label: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        text = content.decode()
    except UnicodeDecodeError as exc:
        raise ObjectiveContractError(f"{label} must be UTF-8 JSONL") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(_mapping(json.loads(line), label=f"{label}:{line_number}"))
        except json.JSONDecodeError as exc:
            raise ObjectiveContractError(f"invalid JSONL at {label}:{line_number}: {exc}") from exc
    if not records:
        raise ObjectiveContractError(f"{label} is empty")
    return records


def _validate_record(record: dict[str, Any], *, plan: dict[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "schema_version", "record_id", "cell_id", "arm_id", "role", "objective",
        "training_seed", "recovery", "identities", "state", "history", "semantics",
        "input_ids", "labels", "advantages", "behavior_logprobs", "step_weight",
        "budget_usage", "source", "curriculum",
    }
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ObjectiveContractError("normalized record_id must be non-empty")
    _exact_keys(record, expected_fields, label=f"record {record_id}")
    if record["schema_version"] != NORMALIZED_SCHEMA:
        raise ObjectiveContractError(f"record {record_id} uses the wrong normalized schema")
    expected_header = {
        "cell_id": plan["cell_id"],
        "arm_id": "score_first_error_prefixes",
        "role": plan["role"],
        "objective": "score",
        "training_seed": plan["training_seed"],
        "recovery": "on",
    }
    if any(record[field] != value for field, value in expected_header.items()):
        raise ObjectiveContractError(f"record {record_id} header does not match the backend plan")
    if record["identities"] != plan["identities"]:
        raise ObjectiveContractError(f"record {record_id} identity mismatch")
    source = _mapping(record["source"], label=f"record {record_id}.source")
    _exact_keys(source, {"artifact_id", "payload_sha256", "source_record_sha256"}, label=f"record {record_id}.source")
    if source["artifact_id"] != plan["source_artifact"]["artifact_id"] \
            or source["payload_sha256"] != plan["source_artifact"]["sha256"]:
        raise ObjectiveContractError(f"record {record_id} source provenance mismatch")
    _digest(source["source_record_sha256"], label=f"record {record_id}.source_record_sha256")

    semantics = _mapping(record["semantics"], label=f"record {record_id}.semantics")
    _exact_keys(
        semantics,
        {
            "mode", "student_trajectory_id", "first_error_index",
            "verified_prefix_token_count", "verified_prefix_sha256",
            "correction_target_sha256", "first_error_evidence_sha256",
            "prefix_verifier_sha256",
        },
        label=f"record {record_id}.semantics",
    )
    if semantics["mode"] != "verified_first_model_visible_error_prefix" \
            or not isinstance(semantics["student_trajectory_id"], str) \
            or not semantics["student_trajectory_id"]:
        raise ObjectiveContractError(f"record {record_id} is not a verified first-error prefix")
    first_error_index = _nonnegative_int(
        semantics["first_error_index"], label=f"record {record_id}.first_error_index"
    )
    prefix_count = _positive_int(
        semantics["verified_prefix_token_count"],
        label=f"record {record_id}.verified_prefix_token_count",
    )
    for field in (
        "verified_prefix_sha256", "correction_target_sha256",
        "first_error_evidence_sha256", "prefix_verifier_sha256",
    ):
        _digest(semantics[field], label=f"record {record_id}.{field}")

    input_ids = record["input_ids"]
    labels = record["labels"]
    if not isinstance(input_ids, list) or not input_ids \
            or not all(isinstance(token, int) and not isinstance(token, bool) and token >= 0 for token in input_ids):
        raise ObjectiveContractError(f"record {record_id}.input_ids must be non-empty token IDs")
    if not isinstance(labels, list) or len(labels) != len(input_ids) \
            or not all(isinstance(token, int) and not isinstance(token, bool) for token in labels):
        raise ObjectiveContractError(f"record {record_id}.labels must align with input_ids")
    if prefix_count >= len(input_ids) or any(label != -100 for label in labels[:prefix_count]):
        raise ObjectiveContractError(f"record {record_id} does not mask its verified prefix")
    correction = labels[prefix_count:]
    if not correction or any(label < 0 for label in correction) or input_ids[prefix_count:] != correction:
        raise ObjectiveContractError(f"record {record_id} correction target is not contiguous")
    if semantics["verified_prefix_sha256"] != _sha256_json(input_ids[:prefix_count]):
        raise ObjectiveContractError(f"record {record_id} verified-prefix digest mismatch")
    if semantics["correction_target_sha256"] != _sha256_json(correction):
        raise ObjectiveContractError(f"record {record_id} correction-target digest mismatch")
    if record["advantages"] is not None or record["behavior_logprobs"] is not None:
        raise ObjectiveContractError(f"record {record_id} must not smuggle OPD arrays into Stage 1")
    step_weight = _positive_finite(record["step_weight"], label=f"record {record_id}.step_weight")
    usage = _mapping(record["budget_usage"], label=f"record {record_id}.budget_usage")
    _exact_keys(
        usage,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label=f"record {record_id}.budget_usage",
    )
    normalized_usage = {
        key: _nonnegative_int(value, label=f"record {record_id}.{key}")
        for key, value in usage.items()
    }
    if normalized_usage["action_tokens"] != len(correction):
        raise ObjectiveContractError(f"record {record_id} action-token budget mismatch")
    return {
        "schema_version": STAGE1_RECORD_SCHEMA,
        "record_id": record_id,
        "input_ids": input_ids,
        "labels": labels,
        "step_weight": step_weight,
        "provenance": {
            "normalized_record_sha256": _sha256_json(record),
            "source_record_sha256": source["source_record_sha256"],
            "student_trajectory_id": semantics["student_trajectory_id"],
            "first_error_index": first_error_index,
            "verified_prefix_token_count": prefix_count,
            "verified_prefix_sha256": semantics["verified_prefix_sha256"],
            "correction_target_sha256": semantics["correction_target_sha256"],
            "first_error_evidence_sha256": semantics["first_error_evidence_sha256"],
            "prefix_verifier_sha256": semantics["prefix_verifier_sha256"],
        },
        "budget_usage": normalized_usage,
    }


def build_objective_plan(backend_result_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result_path = Path(backend_result_path)
    if result_path.is_symlink() or not result_path.is_file():
        raise ObjectiveContractError("backend result must be a regular non-symlink file")
    result_path = result_path.resolve()
    root = result_path.parent
    result = _load_json(result_path, label="backend result")
    _exact_keys(
        result,
        {
            "schema_version", "experiment_id", "cell_id", "status", "source_git_commit",
            "experiment_manifest_sha256", "base_checkpoint_artifact_id", "teacher_artifact_id",
            "training_seed", "allocated_budgets", "backend_plan", "output_artifact",
            "trainer_execution_status", "trainer_compatibility",
        },
        label="backend result",
    )
    if result["schema_version"] != BACKEND_RESULT_SCHEMA \
            or result["status"] != "prepared_not_trained" \
            or result["trainer_execution_status"] != "not_run":
        raise ObjectiveContractError("backend result must be a prepared, never-executed bundle")
    if result["trainer_compatibility"] != ROUTE_COMPATIBILITY:
        raise ObjectiveContractError("backend result was not routed through this objective adapter")
    plan_ref = _mapping(result["backend_plan"], label="backend result.backend_plan")
    _exact_keys(plan_ref, {"path", "sha256"}, label="backend result.backend_plan")
    expected_plan_sha = _digest(plan_ref["sha256"], label="backend plan SHA-256")
    plan_path = _verified_child(plan_ref["path"], root=root, label="backend plan path")
    plan_bytes = plan_path.read_bytes()
    if _sha256_bytes(plan_bytes) != expected_plan_sha:
        raise ObjectiveContractError("backend plan SHA-256 mismatch")
    plan = _mapping(json.loads(plan_bytes), label="backend plan")
    _exact_keys(
        plan,
        {
            "schema_version", "experiment_id", "cell_id", "arm_id", "role", "objective",
            "training_seed", "source_git_commit", "experiment_manifest_sha256", "cell_config",
            "artifact_registry", "artifact_root", "source_artifact", "identities", "optimizer",
            "budgets", "trainer_route", "execution_status", "normalized_records",
        },
        label="backend plan",
    )
    if plan["schema_version"] != BACKEND_PLAN_SCHEMA \
            or plan["arm_id"] != "score_first_error_prefixes" \
            or plan["objective"] != "score" or plan["execution_status"] != "not_run":
        raise ObjectiveContractError("backend plan is not an unexecuted first-error objective cell")
    source_git = plan["source_git_commit"]
    if not isinstance(source_git, str) or len(source_git) != 40 \
            or any(char not in "0123456789abcdef" for char in source_git):
        raise ObjectiveContractError("backend plan source Git commit must be resolved")
    _digest(
        plan["experiment_manifest_sha256"],
        label="backend plan experiment manifest SHA-256",
    )
    for field in ("cell_config", "artifact_registry"):
        reference = _mapping(plan[field], label=f"backend plan.{field}")
        _exact_keys(reference, {"path", "sha256"}, label=f"backend plan.{field}")
        _digest(reference["sha256"], label=f"backend plan.{field}.sha256")
    source_artifact = _mapping(plan["source_artifact"], label="backend plan.source_artifact")
    _exact_keys(
        source_artifact,
        {"artifact_id", "material_path", "sha256", "records"},
        label="backend plan.source_artifact",
    )
    if not isinstance(source_artifact["artifact_id"], str) or not source_artifact["artifact_id"]:
        raise ObjectiveContractError("backend plan source artifact ID must be non-empty")
    _digest(source_artifact["sha256"], label="backend plan source artifact SHA-256")
    identities = _mapping(plan["identities"], label="backend plan.identities")
    _exact_keys(
        identities,
        {
            "base_checkpoint_artifact_id", "teacher_artifact_id",
            "render_contract_sha256", "held_out_registration_artifact_id",
        },
        label="backend plan.identities",
    )
    if not all(
        isinstance(identities[field], str) and identities[field]
        for field in (
            "base_checkpoint_artifact_id", "teacher_artifact_id",
            "held_out_registration_artifact_id",
        )
    ):
        raise ObjectiveContractError("backend plan artifact identities must be non-empty")
    _digest(identities["render_contract_sha256"], label="backend plan render contract SHA-256")
    route = _mapping(plan["trainer_route"], label="backend plan.trainer_route")
    if route.get("entrypoint") != ADAPTER_PATH or route.get("compatibility") != ROUTE_COMPATIBILITY:
        raise ObjectiveContractError("backend plan trainer route does not bind this adapter")
    for field in (
        "experiment_id", "cell_id", "training_seed", "source_git_commit",
        "experiment_manifest_sha256",
    ):
        if result[field] != plan[field]:
            raise ObjectiveContractError(f"backend result/plan mismatch for {field}")
    if result["allocated_budgets"] != plan["budgets"] \
            or result["base_checkpoint_artifact_id"] != plan["identities"]["base_checkpoint_artifact_id"] \
            or result["teacher_artifact_id"] != plan["identities"]["teacher_artifact_id"]:
        raise ObjectiveContractError("backend result/plan identity or budget mismatch")

    normalized_ref = _mapping(plan["normalized_records"], label="backend plan.normalized_records")
    _exact_keys(
        normalized_ref, {"path", "sha256", "schema_version", "records"},
        label="backend plan.normalized_records",
    )
    if normalized_ref["schema_version"] != NORMALIZED_SCHEMA:
        raise ObjectiveContractError("backend plan normalized-record schema mismatch")
    normalized_sha = _digest(normalized_ref["sha256"], label="normalized records SHA-256")
    records_path = _verified_child(
        normalized_ref["path"], root=root, label="normalized records path"
    )
    records_bytes = records_path.read_bytes()
    if _sha256_bytes(records_bytes) != normalized_sha:
        raise ObjectiveContractError("normalized records SHA-256 mismatch")
    output = _mapping(result["output_artifact"], label="backend result.output_artifact")
    _exact_keys(output, {"kind", "uri", "sha256"}, label="backend result.output_artifact")
    if output != {
        "kind": "normalized_training_records",
        "uri": f"file:{records_path}",
        "sha256": normalized_sha,
    }:
        raise ObjectiveContractError("backend output artifact does not match normalized records")
    raw_records = _load_jsonl_bytes(records_bytes, label="normalized records")
    if normalized_ref["records"] != len(raw_records) \
            or plan["source_artifact"].get("records") != len(raw_records):
        raise ObjectiveContractError("normalized record count does not match the backend plan")
    stage1_records = [_validate_record(record, plan=plan) for record in raw_records]
    if len({record["record_id"] for record in stage1_records}) != len(stage1_records):
        raise ObjectiveContractError("normalized records contain duplicate record_id values")
    observed_budgets = {
        key: sum(record["budget_usage"][key] for record in stage1_records)
        for key in ("action_tokens", "teacher_scoring_tokens", "environment_interactions")
    }
    registered_budgets = _mapping(plan["budgets"], label="backend plan.budgets")
    _exact_keys(
        registered_budgets,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label="backend plan.budgets",
    )
    registered_budgets = {
        key: _nonnegative_int(value, label=f"backend plan.budgets.{key}")
        for key, value in registered_budgets.items()
    }
    if observed_budgets != registered_budgets:
        raise ObjectiveContractError(
            f"normalized records do not exactly fill the registered budget: "
            f"registered={registered_budgets}, observed={observed_budgets}"
        )
    plan_out = {
        "schema_version": OBJECTIVE_PLAN_SCHEMA,
        "objective_id": OBJECTIVE_ID,
        "scientific_scope": {
            "label": "SCoRe-style verified first-model-visible-error two-stage objective",
            "full_published_score_reproduction": False,
            "deviation": (
                "This adapter borrows the verified pre-error correction boundary and two-stage "
                "shape; it is not a faithful reproduction or outcome claim for published SCoRe."
            ),
        },
        "experiment_id": plan["experiment_id"],
        "cell_id": plan["cell_id"],
        "training_seed": plan["training_seed"],
        "identities": plan["identities"],
        "optimizer": plan["optimizer"],
        "registered_budgets": registered_budgets,
        "observed_stage1_source_budgets": observed_budgets,
        "backend": {
            "result_path": str(result_path),
            "result_sha256": _sha256_bytes(result_path.read_bytes()),
            "plan_path": str(plan_path),
            "plan_sha256": expected_plan_sha,
            "normalized_records_path": str(records_path),
            "normalized_records_sha256": normalized_sha,
        },
        "stage1": {
            "name": "correction_sft_at_verified_first_error",
            "status": "prepared_not_trained",
            "loss": "weighted_masked_correction_cross_entropy",
            "records": len(stage1_records),
            "supervised_action_tokens": observed_budgets["action_tokens"],
        },
        "stage2": {
            "name": "post_stage1_short_horizon_target_reward_optimization",
            "status": "blocked_missing_registered_runtime_materials",
            "loss": "short_horizon_target_reward_policy_gradient",
            "required_inputs": [
                "hash_registered_stage1_checkpoint",
                "post_stage1_short_horizon_samples_from_each_verified_prefix",
                "hash_backed_target_reward_at_the_registered_error_step",
                "reviewed_disjoint_stage1_and_stage2_budget_allocation",
            ],
            "normalized_stage1_records_are_sufficient": False,
        },
        "blockers": list(BLOCKERS),
        "launch_allowed": False,
        "execution_status": "not_run",
    }
    return plan_out, stage1_records


def _write_new(path: Path, content: str) -> None:
    with path.open("x") as handle:
        handle.write(content)


def materialize(backend_result_path: str | Path) -> dict[str, Any]:
    objective_plan, stage1_records = build_objective_plan(backend_result_path)
    output_dir = Path(backend_result_path).resolve().parent
    records_path = output_dir / "score-style-stage1-records.jsonl"
    plan_path = output_dir / "score-style-objective-plan.json"
    result_path = output_dir / "score-style-result.json"
    records_content = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in stage1_records
    )
    records_sha = _sha256_bytes(records_content.encode())
    final_plan = {
        **objective_plan,
        "stage1_artifact": {
            "path": str(records_path),
            "sha256": records_sha,
            "schema_version": STAGE1_RECORD_SCHEMA,
        },
    }
    plan_content = json.dumps(final_plan, indent=2, sort_keys=True) + "\n"
    plan_sha = _sha256_bytes(plan_content.encode())
    result = {
        "schema_version": OBJECTIVE_RESULT_SCHEMA,
        "objective_id": OBJECTIVE_ID,
        "experiment_id": final_plan["experiment_id"],
        "cell_id": final_plan["cell_id"],
        "status": "prepared_stage1_stage2_blocked_not_trained",
        "launch_allowed": False,
        "blockers": list(BLOCKERS),
        "objective_plan": {"path": str(plan_path), "sha256": plan_sha},
        "stage1_artifact": {"path": str(records_path), "sha256": records_sha},
        "trainer_execution_status": "not_run",
        "checkpoint_artifact": None,
    }
    for path in (records_path, plan_path, result_path):
        if path.exists():
            raise FileExistsError(f"target file already exists: {path}")
    _write_new(records_path, records_content)
    _write_new(plan_path, plan_content)
    _write_new(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-result", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.dry_run:
            plan, records = build_objective_plan(args.backend_result)
            print(json.dumps({**plan, "stage1_record_count": len(records)}, indent=2, sort_keys=True))
            print("No files were written, no trainer was run, and Stage 2 remains blocked.")
        else:
            print(json.dumps(materialize(args.backend_result), indent=2, sort_keys=True))
    except (OSError, ObjectiveContractError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
