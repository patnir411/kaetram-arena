#!/usr/bin/env python3
"""Preflight and launch the matched OPD training-arm protocol.

Dry-run is the default.  Compute requires a reviewed manifest switch,
``--execute``, and an exact experiment-ID confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
PROTOCOL_ID = "kaetram.opd-matched-training.v1"
REGISTRY_SCHEMA = "kaetram.matched-training-artifact-registry.v1"
INTERFACE_CONTRACT = "kaetram-tool-render-contract-v1"
PARAMETERIZATION_CONTRACT = "kaetram-matched-lora-v1"
LORA_TARGET_MODULES = (
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
)
UNRESOLVED = "UNRESOLVED"
PRIMARY_ARMS = (
    "natural_opd",
    "targeted_persistent_state",
    "random_valid_state",
    "progress_matched_state",
    "tcod_b2f_prefixes",
    "guided_opd",
)
MECHANISM_ARMS = (
    "visitation_only",
    "teacher_advantage_only",
    "corrected_interface_sft",
    "score_first_error_prefixes",
)
ARM_IDS = PRIMARY_ARMS + MECHANISM_ARMS
HISTORY_ABLATION_IDS = (
    "snapshot_minimal_history",
    "teacher_replay_authentic_prefix",
    "snapshot_matched_reconstructed_history",
    "backplay_witness_annealing",
)
PREFIX_ARMS = {"tcod_b2f_prefixes"}
FIRST_ERROR_ARMS = {"score_first_error_prefixes"}
REACHABILITY_ARMS = {
    "targeted_persistent_state",
    "random_valid_state",
    "progress_matched_state",
    "visitation_only",
    "teacher_advantage_only",
}
EXPECTED_ARTIFACT_KINDS = {
    "natural_opd": "on_policy_rollouts",
    "targeted_persistent_state": "persistent_player_snapshots",
    "random_valid_state": "persistent_player_snapshots",
    "progress_matched_state": "persistent_player_snapshots",
    "tcod_b2f_prefixes": "teacher_success_prefixes",
    "guided_opd": "guided_live_rollouts",
    "visitation_only": "persistent_player_snapshots",
    "teacher_advantage_only": "persistent_player_snapshots",
    "corrected_interface_sft": "corrected_interface_teacher_trajectories",
    "score_first_error_prefixes": "verified_first_error_prefixes",
}
EXPECTED_CONSTRUCTORS = {
    "natural_opd": (
        "canonical_natural_rollout", "fresh_canonical_world_online",
        "authentic_online_history", "same_rollout",
    ),
    "targeted_persistent_state": (
        "targeted_persistent_player_state", "hash_verified_database_snapshot_restore",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "random_valid_state": (
        "random_valid_persistent_player_state", "uniform_over_registered_valid_snapshot_pool",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "progress_matched_state": (
        "progress_matched_persistent_player_state", "stratified_match_on_registered_progress_vector",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "tcod_b2f_prefixes": (
        "teacher_success_prefix_state", "restore_state_at_registered_prefix_boundary",
        "authentic_teacher_success_prefix", "same_evidence_backed_teacher_trajectory",
    ),
    "guided_opd": (
        "canonical_guided_rollout", "fresh_canonical_world_online",
        "guided_mixed_history", "same_live_mixed_rollout",
    ),
    "visitation_only": (
        "visitation_only_persistent_player_state", "hash_verified_database_snapshot_restore",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "teacher_advantage_only": (
        "teacher_advantage_only_persistent_player_state", "hash_verified_database_snapshot_restore",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "corrected_interface_sft": (
        "corrected_interface_teacher_trajectory_state", "corrected_interface_teacher_trajectory_replay",
        "corrected_interface_teacher_history", "same_corrected_teacher_trajectory",
    ),
    "score_first_error_prefixes": (
        "verified_first_error_prefix_state", "restore_verified_pre_error_state",
        "verified_pre_error_prefix", "same_verified_student_trajectory",
    ),
}
HISTORY_ABLATION_CONSTRUCTORS = {
    "snapshot_minimal_history": (
        "targeted_persistent_player_state", "hash_verified_database_snapshot_restore",
        "minimal_model_visible_history", "single_post_restore_observation",
    ),
    "teacher_replay_authentic_prefix": (
        "teacher_replay_state", "replay_witness_trajectory_to_identical_state",
        "authentic_teacher_history", "same_witness_trajectory_prefix",
    ),
    "snapshot_matched_reconstructed_history": (
        "targeted_persistent_player_state", "hash_verified_database_snapshot_restore",
        "matched_reconstructed_history", "snapshot_visible_fields_only",
    ),
    "backplay_witness_annealing": (
        "backplay_witness_state", "restore_along_registered_witness_trajectory",
        "authentic_witness_history", "matching_witness_trajectory_prefix",
    ),
}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingCell:
    cell_id: str
    arm_id: str
    role: str
    seed: int
    output_dir: str
    config: dict[str, Any]


@dataclass(frozen=True)
class TrainingPlan:
    experiment_id: str
    manifest: str
    manifest_sha256: str
    source_git_commit: str
    registry_path: str
    registry_sha256: str
    base_checkpoint_artifact_id: str
    teacher_artifact_id: str
    teacher_endpoint_env: str
    held_out_registration_artifact_id: str
    frozen_interfaces: tuple[dict[str, str], ...]
    parameterization: dict[str, Any]
    parameterization_sha256: str
    optimizer: dict[str, Any]
    budgets: dict[str, int]
    training_seed_schedule: tuple[int, ...]
    arms: tuple[dict[str, Any], ...]
    history_ablations: tuple[dict[str, Any], ...]
    allow_launch: bool
    max_parallel: int
    output_root: str
    artifact_root: str
    backend_adapter_path: str
    backend_adapter_sha256: str
    launch_blockers: tuple[str, ...]
    cells: tuple[TrainingCell, ...]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _repo_file(raw_path: str, *, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ProtocolError(f"{label} must be a non-empty path string")
    path = (REPO / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
    try:
        path.relative_to(REPO)
    except ValueError as exc:
        raise ProtocolError(f"{label} must resolve inside the repository") from exc
    if not path.is_file():
        raise ProtocolError(f"{label} does not exist: {path}")
    return path


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ProtocolError(
            f"{label} fields must be exactly {sorted(expected)}; got {sorted(value)}"
        )


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProtocolError(f"{label} must be a positive integer")
    return value


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"{label} must be finite numeric")
    return float(value)


def _digest(value: Any, *, label: str, nonzero: bool = False) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256")
    if nonzero and value == "0" * 64:
        raise ProtocolError(f"{label} must not be an unresolved zero digest")
    return value


def _artifact(
    artifacts: dict[str, Any], artifact_id: str, *, kind: str, blockers: list[str]
) -> dict[str, Any]:
    record = _mapping(artifacts.get(artifact_id), label=f"artifact {artifact_id}")
    if record.get("kind") != kind:
        raise ProtocolError(f"artifact {artifact_id} kind must be {kind!r}")
    status = record.get("status")
    if status not in {"verified", "unresolved_example"}:
        raise ProtocolError(f"artifact {artifact_id} status must be verified or unresolved_example")
    payload = _mapping(record.get("payload"), label=f"artifact {artifact_id}.payload")
    _exact_keys(payload, {"uri", "sha256"}, label=f"artifact {artifact_id}.payload")
    uri = payload.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ProtocolError(f"artifact {artifact_id}.payload.uri must be non-empty")
    digest = _digest(payload.get("sha256"), label=f"artifact {artifact_id}.payload.sha256")
    if status != "verified" or uri.startswith(UNRESOLVED) or digest == "0" * 64:
        blockers.append(f"artifact {artifact_id} is not a verified immutable payload")
    return record


def _validate_optimizer(raw: Any) -> dict[str, Any]:
    optimizer = _mapping(raw, label="shared_inputs.optimizer")
    _exact_keys(
        optimizer,
        {
            "name", "learning_rate", "betas", "weight_decay", "scheduler",
            "warmup_ratio", "gradient_clip_norm", "effective_batch_size", "epochs",
        },
        label="shared_inputs.optimizer",
    )
    if optimizer["name"] != "adamw_8bit" or optimizer["scheduler"] != "cosine":
        raise ProtocolError("optimizer must freeze adamw_8bit with cosine scheduling")
    if not isinstance(optimizer["betas"], list) or len(optimizer["betas"]) != 2:
        raise ProtocolError("optimizer.betas must contain exactly two values")
    for index, beta in enumerate(optimizer["betas"]):
        if not 0 <= _finite(beta, label=f"optimizer.betas[{index}]") < 1:
            raise ProtocolError("optimizer betas must be in [0, 1)")
    for key in ("learning_rate", "weight_decay", "warmup_ratio", "gradient_clip_norm"):
        if _finite(optimizer[key], label=f"optimizer.{key}") < 0:
            raise ProtocolError(f"optimizer.{key} must be nonnegative")
    _positive_int(optimizer["effective_batch_size"], label="optimizer.effective_batch_size")
    _positive_int(optimizer["epochs"], label="optimizer.epochs")
    return optimizer


def _validate_parameterization(raw: Any) -> tuple[dict[str, Any], str]:
    value = _mapping(raw, label="shared_inputs.parameterization")
    _exact_keys(
        value,
        {
            "contract_id", "method", "fresh_adapter_per_cell", "precision",
            "rank", "alpha", "dropout", "bias", "target_modules",
            "task_type", "base_model_trainable", "init_lora_weights",
        },
        label="shared_inputs.parameterization",
    )
    expected_scalars = {
        "contract_id": PARAMETERIZATION_CONTRACT,
        "method": "lora",
        "fresh_adapter_per_cell": True,
        "precision": "bf16",
        "rank": 64,
        "alpha": 64,
        "dropout": 0.0,
        "bias": "none",
        "task_type": "CAUSAL_LM",
        "base_model_trainable": False,
        "init_lora_weights": True,
    }
    for key, expected in expected_scalars.items():
        if value.get(key) != expected:
            raise ProtocolError(f"parameterization.{key} must be frozen to {expected!r}")
    if value.get("target_modules") != list(LORA_TARGET_MODULES):
        raise ProtocolError(
            f"parameterization.target_modules must be exactly {list(LORA_TARGET_MODULES)}"
        )
    normalized = {**value, "target_modules": list(LORA_TARGET_MODULES)}
    return normalized, _sha256_json(normalized)


def _validate_arm(
    raw: Any,
    *,
    index: int,
    artifacts: dict[str, Any],
    heldout_id: str,
    action_budget: int,
    blockers: list[str],
) -> dict[str, Any]:
    arm = _mapping(raw, label=f"arms[{index}]")
    required = {
        "arm_id", "role", "objective", "training_artifact_id", "recovery",
        "state_source", "history_constructor",
    }
    optional = {"tcod_b2f", "guided_annealing"}
    if not required <= set(arm) or (set(arm) - required) - optional:
        raise ProtocolError(
            f"arms[{index}] must use shared model/teacher/optimizer/budgets and only arm-specific fields"
        )
    arm_id = arm.get("arm_id")
    if arm_id not in ARM_IDS:
        raise ProtocolError(f"arms[{index}].arm_id is not registered: {arm_id!r}")
    expected_role = "primary" if arm_id in PRIMARY_ARMS else "mechanism_or_baseline"
    if arm.get("role") != expected_role:
        raise ProtocolError(f"arm {arm_id} role must be {expected_role}")
    if arm_id == "corrected_interface_sft":
        expected_objective = "sft"
    elif arm_id == "score_first_error_prefixes":
        expected_objective = "score"
    else:
        expected_objective = "opd"
    if arm.get("objective") != expected_objective:
        raise ProtocolError(f"arm {arm_id} objective must be {expected_objective}")
    expected_recovery = "on"
    if arm.get("recovery") != expected_recovery:
        raise ProtocolError(f"arm {arm_id} recovery must be {expected_recovery}")
    artifact_id = arm.get("training_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ProtocolError(f"arm {arm_id} training_artifact_id must be non-empty")
    record = _artifact(
        artifacts, artifact_id, kind=EXPECTED_ARTIFACT_KINDS[arm_id], blockers=blockers
    )
    exclusion = _mapping(
        record.get("held_out_exclusion"), label=f"artifact {artifact_id}.held_out_exclusion"
    )
    if exclusion.get("registration_artifact_id") != heldout_id:
        raise ProtocolError(f"artifact {artifact_id} is not bound to held-out registration {heldout_id}")
    if exclusion.get("status") != "pass" or not isinstance(exclusion.get("scanned_records"), int) \
            or exclusion.get("scanned_records", 0) < 1:
        blockers.append(f"artifact {artifact_id} has no completed held-out exclusion scan")

    state = _mapping(arm.get("state_source"), label=f"arm {arm_id}.state_source")
    history = _mapping(arm.get("history_constructor"), label=f"arm {arm_id}.history_constructor")
    _exact_keys(state, {"kind", "constructor"}, label=f"arm {arm_id}.state_source")
    _exact_keys(history, {"kind", "source"}, label=f"arm {arm_id}.history_constructor")
    if not all(isinstance(value, str) and value for value in (*state.values(), *history.values())):
        raise ProtocolError(f"arm {arm_id} state/history constructors must be explicit strings")
    actual_constructors = (
        state["kind"], state["constructor"], history["kind"], history["source"]
    )
    if actual_constructors != EXPECTED_CONSTRUCTORS[arm_id]:
        raise ProtocolError(
            f"arm {arm_id} state-source/history constructor differs from the frozen protocol"
        )

    if arm_id in REACHABILITY_ARMS:
        evidence = _mapping(
            record.get("reachability_evidence"),
            label=f"artifact {artifact_id}.reachability_evidence",
        )
        if evidence.get("method") != "witness_trajectory_or_invariant_certificate":
            raise ProtocolError(f"artifact {artifact_id} must state its legal-reachability method")
        if evidence.get("status") != "pass":
            blockers.append(f"artifact {artifact_id} lacks passed legal-reachability evidence")

    if arm_id in PREFIX_ARMS:
        evidence = _mapping(
            record.get("teacher_success_evidence"),
            label=f"artifact {artifact_id}.teacher_success_evidence",
        )
        if evidence.get("metric") != "db_authoritative_quest_completion":
            raise ProtocolError(f"artifact {artifact_id} must use DB-authoritative teacher success")
        evidence_sha = _digest(
            evidence.get("evidence_sha256"),
            label=f"artifact {artifact_id}.teacher_success_evidence.evidence_sha256",
        )
        if evidence.get("status") != "pass" or evidence_sha == "0" * 64:
            blockers.append(f"artifact {artifact_id} lacks hash-backed teacher-success evidence")

    if arm_id in FIRST_ERROR_ARMS:
        evidence = _mapping(
            record.get("first_error_evidence"),
            label=f"artifact {artifact_id}.first_error_evidence",
        )
        if evidence.get("metric") != "first_model_visible_student_error":
            raise ProtocolError(
                f"artifact {artifact_id} must identify the first model-visible student error"
            )
        if evidence.get("status") != "pass":
            blockers.append(f"artifact {artifact_id} lacks verified SCoRe first-error evidence")
        for field in ("evidence_sha256", "prefix_verifier_sha256"):
            value = evidence.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) \
                    or value == "0" * 64:
                blockers.append(f"artifact {artifact_id} has unresolved {field}")

    if arm_id == "tcod_b2f_prefixes":
        cfg = _mapping(arm.get("tcod_b2f"), label="arm tcod_b2f_prefixes.tcod_b2f")
        _exact_keys(
            cfg,
            {"schedule", "initial_success_fraction", "final_success_fraction", "schedule_basis"},
            label="arm tcod_b2f_prefixes.tcod_b2f",
        )
        if cfg["schedule"] != "backward_from_success" or cfg["schedule_basis"] != "action_tokens":
            raise ProtocolError("TCOD-B2F must schedule backward from success over action tokens")
        initial = _finite(cfg["initial_success_fraction"], label="tcod initial_success_fraction")
        final = _finite(cfg["final_success_fraction"], label="tcod final_success_fraction")
        if not 0 <= final < initial <= 1:
            raise ProtocolError("TCOD-B2F success fractions must satisfy 0 <= final < initial <= 1")
    elif "tcod_b2f" in arm:
        raise ProtocolError(f"arm {arm_id} must not define tcod_b2f")

    if arm_id == "guided_opd":
        cfg = _mapping(arm.get("guided_annealing"), label="arm guided_opd.guided_annealing")
        _exact_keys(
            cfg,
            {
                "schedule", "schedule_basis", "start_teacher_turn_probability",
                "end_teacher_turn_probability", "curriculum_ratio",
                "trajectory_probability", "total_training_steps", "student_turn_loss",
                "teacher_turn_loss",
            },
            label="arm guided_opd.guided_annealing",
        )
        if cfg["schedule"] != "cosine" or cfg["schedule_basis"] != "training_progress":
            raise ProtocolError("published Guided-OPD must use cosine training-progress decay")
        if cfg["start_teacher_turn_probability"] != 1.0 \
                or cfg["end_teacher_turn_probability"] != 0.0:
            raise ProtocolError("Guided-OPD teacher-turn probability must decay from 1 to 0")
        if cfg["curriculum_ratio"] != 0.8:
            raise ProtocolError("published Guided-OPD curriculum ratio must be 0.8")
        if cfg["trajectory_probability"] != "held_fixed_within_trajectory":
            raise ProtocolError("Guided probability must be held fixed within each trajectory")
        if cfg["total_training_steps"] != 250:
            raise ProtocolError("published Guided-OPD uses 250 total training steps")
        if cfg["student_turn_loss"] != "reverse_kl" \
                or cfg["teacher_turn_loss"] != "forward_kl":
            raise ProtocolError("Guided-OPD requires student rKL and teacher fKL")
    elif "guided_annealing" in arm:
        raise ProtocolError(f"arm {arm_id} must not define guided_annealing")
    return arm


def _validate_history_ablation(
    raw: Any,
    *,
    index: int,
    artifacts: dict[str, Any],
    heldout_id: str,
    action_budget: int,
    blockers: list[str],
) -> dict[str, Any]:
    ablation = _mapping(raw, label=f"history_ablations[{index}]")
    required = {
        "ablation_id", "role", "objective", "training_artifact_id", "recovery",
        "state_source", "history_constructor",
    }
    optional = {"backplay_annealing"}
    if not required <= set(ablation) or (set(ablation) - required) - optional:
        raise ProtocolError(f"history_ablations[{index}] has unexpected or missing fields")
    ablation_id = ablation.get("ablation_id")
    if ablation_id not in HISTORY_ABLATION_IDS:
        raise ProtocolError(f"unregistered history ablation: {ablation_id!r}")
    if ablation.get("role") != "history_ablation" or ablation.get("objective") != "opd" \
            or ablation.get("recovery") != "on":
        raise ProtocolError(f"history ablation {ablation_id} must be OPD/recovery-on")
    artifact_id = ablation.get("training_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ProtocolError(f"history ablation {ablation_id} artifact ID is missing")
    record = _artifact(artifacts, artifact_id, kind="state_history_pairs", blockers=blockers)
    exclusion = _mapping(
        record.get("held_out_exclusion"), label=f"artifact {artifact_id}.held_out_exclusion"
    )
    if exclusion.get("registration_artifact_id") != heldout_id:
        raise ProtocolError(f"artifact {artifact_id} is not bound to held-out registration {heldout_id}")
    if exclusion.get("status") != "pass" or not isinstance(exclusion.get("scanned_records"), int) \
            or exclusion.get("scanned_records", 0) < 1:
        blockers.append(f"artifact {artifact_id} has no completed held-out exclusion scan")
    evidence = _mapping(
        record.get("reachability_evidence"), label=f"artifact {artifact_id}.reachability_evidence"
    )
    if evidence.get("method") != "witness_trajectory_or_invariant_certificate":
        raise ProtocolError(f"artifact {artifact_id} must state its legal-reachability method")
    if evidence.get("status") != "pass":
        blockers.append(f"artifact {artifact_id} lacks passed legal-reachability evidence")
    state = _mapping(ablation.get("state_source"), label=f"history ablation {ablation_id}.state_source")
    history = _mapping(
        ablation.get("history_constructor"), label=f"history ablation {ablation_id}.history_constructor"
    )
    _exact_keys(state, {"kind", "constructor"}, label=f"history ablation {ablation_id}.state_source")
    _exact_keys(history, {"kind", "source"}, label=f"history ablation {ablation_id}.history_constructor")
    actual = (state["kind"], state["constructor"], history["kind"], history["source"])
    if actual != HISTORY_ABLATION_CONSTRUCTORS[ablation_id]:
        raise ProtocolError(f"history ablation {ablation_id} constructors differ from frozen protocol")
    if ablation_id == "backplay_witness_annealing":
        cfg = _mapping(
            ablation.get("backplay_annealing"),
            label="history ablation backplay_witness_annealing.backplay_annealing",
        )
        _exact_keys(
            cfg,
            {
                "schedule", "schedule_basis", "start_distance_fraction",
                "end_distance_fraction", "anneal_action_tokens",
            },
            label="backplay_annealing",
        )
        if cfg["schedule"] != "backward_along_witness" or cfg["schedule_basis"] != "action_tokens" \
                or cfg["start_distance_fraction"] != 0.0 or cfg["end_distance_fraction"] != 1.0 \
                or cfg["anneal_action_tokens"] != action_budget:
            raise ProtocolError("Backplay must anneal from success to canonical start over full action budget")
    elif "backplay_annealing" in ablation:
        raise ProtocolError(f"history ablation {ablation_id} must not define Backplay annealing")
    return ablation


def build_plan(path: str | Path) -> TrainingPlan:
    manifest_path = Path(path).resolve()
    try:
        manifest_path.relative_to(REPO)
    except ValueError as exc:
        raise ProtocolError("experiment manifest must resolve inside the repository") from exc
    try:
        raw = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ProtocolError("manifest schema_version must be 1")
    _exact_keys(
        raw,
        {
            "schema_version", "experiment_id", "protocol", "shared_inputs", "arms",
            "history_ablations", "execution",
        },
        label="manifest",
    )
    experiment_id = raw.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", experiment_id):
        raise ProtocolError("experiment_id must be a lowercase filesystem-safe identifier")

    protocol = _mapping(raw.get("protocol"), label="protocol")
    _exact_keys(protocol, {"protocol_id", "source_git_commit", "artifact_registry"}, label="protocol")
    if protocol.get("protocol_id") != PROTOCOL_ID:
        raise ProtocolError(f"protocol.protocol_id must be {PROTOCOL_ID}")
    source_git = protocol.get("source_git_commit")
    if not isinstance(source_git, str) or not (
        source_git.startswith(UNRESOLVED) or re.fullmatch(r"[0-9a-f]{40}", source_git)
    ):
        raise ProtocolError("protocol.source_git_commit must be a full Git SHA or unresolved marker")
    blockers: list[str] = []
    if source_git.startswith(UNRESOLVED):
        blockers.append("protocol.source_git_commit is unresolved")

    registry_ref = _mapping(protocol.get("artifact_registry"), label="protocol.artifact_registry")
    _exact_keys(registry_ref, {"path", "sha256"}, label="protocol.artifact_registry")
    registry_path = _repo_file(registry_ref.get("path"), label="protocol.artifact_registry.path")
    registry_sha = _digest(registry_ref.get("sha256"), label="protocol.artifact_registry.sha256")
    if _sha256(registry_path) != registry_sha:
        raise ProtocolError("artifact registry SHA-256 mismatch")
    registry = json.loads(registry_path.read_text())
    if not isinstance(registry, dict) or registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ProtocolError(f"artifact registry schema_version must be {REGISTRY_SCHEMA}")
    artifacts = _mapping(registry.get("artifacts"), label="artifact registry.artifacts")

    shared = _mapping(raw.get("shared_inputs"), label="shared_inputs")
    _exact_keys(
        shared,
        {
            "base_checkpoint_artifact_id", "teacher", "held_out_registration_artifact_id",
            "frozen_interfaces", "parameterization", "optimizer", "budgets",
            "training_seed_schedule",
        },
        label="shared_inputs",
    )
    base_id = shared.get("base_checkpoint_artifact_id")
    teacher = _mapping(shared.get("teacher"), label="shared_inputs.teacher")
    _exact_keys(teacher, {"artifact_id", "endpoint_env"}, label="shared_inputs.teacher")
    teacher_id = teacher.get("artifact_id")
    teacher_env = teacher.get("endpoint_env")
    if not isinstance(teacher_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", teacher_env):
        raise ProtocolError("teacher.endpoint_env must be an environment-variable name")
    heldout_id = shared.get("held_out_registration_artifact_id")
    if not all(isinstance(value, str) and value for value in (base_id, teacher_id, heldout_id)):
        raise ProtocolError("shared artifact IDs must be non-empty strings")
    _artifact(artifacts, base_id, kind="checkpoint", blockers=blockers)
    _artifact(artifacts, teacher_id, kind="teacher_attestation", blockers=blockers)
    heldout = _artifact(artifacts, heldout_id, kind="heldout_registration", blockers=blockers)
    if not isinstance(heldout.get("quest"), str) or not heldout["quest"]:
        raise ProtocolError("held-out registration must name a quest")
    aliases = heldout.get("aliases")
    if not isinstance(aliases, list) or not aliases or not all(
        isinstance(alias, str) and alias for alias in aliases
    ):
        raise ProtocolError("held-out registration aliases must be non-empty strings")
    heldout_payload = _mapping(heldout.get("payload"), label="held-out registration payload")
    expected_inline_digest = _sha256_json({"quest": heldout["quest"], "aliases": aliases})
    if heldout_payload.get("uri") == "inline://heldout-registration-v1" \
            and heldout_payload.get("sha256") != expected_inline_digest:
        raise ProtocolError("held-out inline registration digest mismatch")

    interface = _mapping(shared.get("frozen_interfaces"), label="shared_inputs.frozen_interfaces")
    _exact_keys(interface, {"contract_id", "files"}, label="shared_inputs.frozen_interfaces")
    if interface.get("contract_id") != INTERFACE_CONTRACT:
        raise ProtocolError(f"frozen interface contract_id must be {INTERFACE_CONTRACT}")
    interface_files = interface.get("files")
    if not isinstance(interface_files, list) or len(interface_files) < 3:
        raise ProtocolError("frozen interface contract must hash at least three files")
    frozen: list[dict[str, str]] = []
    for index, item_raw in enumerate(interface_files):
        item = _mapping(item_raw, label=f"frozen_interfaces.files[{index}]")
        _exact_keys(item, {"path", "sha256"}, label=f"frozen_interfaces.files[{index}]")
        item_path = _repo_file(item.get("path"), label=f"frozen_interfaces.files[{index}].path")
        expected = _digest(item.get("sha256"), label=f"frozen_interfaces.files[{index}].sha256")
        actual = _sha256(item_path)
        if actual != expected:
            raise ProtocolError(f"frozen interface drift: {item_path}")
        frozen.append({"path": item_path.relative_to(REPO).as_posix(), "sha256": actual})
    if len({item["path"] for item in frozen}) != len(frozen):
        raise ProtocolError("frozen interface files must be unique")

    parameterization, parameterization_sha = _validate_parameterization(
        shared.get("parameterization")
    )
    optimizer = _validate_optimizer(shared.get("optimizer"))
    budgets_raw = _mapping(shared.get("budgets"), label="shared_inputs.budgets")
    _exact_keys(
        budgets_raw,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label="shared_inputs.budgets",
    )
    budgets = {key: _positive_int(value, label=f"budgets.{key}") for key, value in budgets_raw.items()}
    seeds_raw = shared.get("training_seed_schedule")
    if not isinstance(seeds_raw, list) or len(seeds_raw) < 3:
        raise ProtocolError("training_seed_schedule must contain at least three seeds")
    seeds = tuple(_positive_int(seed, label=f"training_seed_schedule[{i}]") for i, seed in enumerate(seeds_raw))
    if len(set(seeds)) != len(seeds):
        raise ProtocolError("training_seed_schedule seeds must be unique")

    arms_raw = raw.get("arms")
    if not isinstance(arms_raw, list) or len(arms_raw) != len(ARM_IDS):
        raise ProtocolError("arms must contain exactly six primary and four mechanism/baseline arms")
    arms = tuple(
        _validate_arm(
            item,
            index=index,
            artifacts=artifacts,
            heldout_id=heldout_id,
            action_budget=budgets["action_tokens"],
            blockers=blockers,
        )
        for index, item in enumerate(arms_raw)
    )
    if tuple(arm["arm_id"] for arm in arms) != ARM_IDS:
        raise ProtocolError(f"arms must appear exactly in registered order: {list(ARM_IDS)}")

    history_raw = raw.get("history_ablations")
    if not isinstance(history_raw, list) or len(history_raw) != len(HISTORY_ABLATION_IDS):
        raise ProtocolError("history_ablations must contain exactly four registered conditions")
    history_ablations = tuple(
        _validate_history_ablation(
            item,
            index=index,
            artifacts=artifacts,
            heldout_id=heldout_id,
            action_budget=budgets["action_tokens"],
            blockers=blockers,
        )
        for index, item in enumerate(history_raw)
    )
    if tuple(item["ablation_id"] for item in history_ablations) != HISTORY_ABLATION_IDS:
        raise ProtocolError(
            f"history_ablations must appear exactly in registered order: {list(HISTORY_ABLATION_IDS)}"
        )

    execution = _mapping(raw.get("execution"), label="execution")
    _exact_keys(
        execution,
        {"allow_launch", "max_parallel", "output_root", "artifact_root", "backend_adapter"},
        label="execution",
    )
    allow_launch = execution.get("allow_launch")
    if not isinstance(allow_launch, bool):
        raise ProtocolError("execution.allow_launch must be boolean")
    max_parallel = _positive_int(execution.get("max_parallel"), label="execution.max_parallel")
    output_raw = execution.get("output_root")
    if not isinstance(output_raw, str) or not output_raw:
        raise ProtocolError("execution.output_root must be a non-empty path")
    output_path = Path(output_raw)
    output_root = output_path.resolve() if output_path.is_absolute() else (REPO / output_path).resolve()
    if output_root == REPO or REPO not in output_root.parents:
        raise ProtocolError("execution.output_root must be a specific path inside the repository")
    artifact_root_raw = execution.get("artifact_root")
    if not isinstance(artifact_root_raw, str) or not artifact_root_raw:
        raise ProtocolError("execution.artifact_root must be a non-empty absolute path")
    if artifact_root_raw.startswith(UNRESOLVED):
        blockers.append("execution artifact root is unresolved")
        artifact_root = artifact_root_raw
    else:
        artifact_path = Path(artifact_root_raw)
        if not artifact_path.is_absolute():
            raise ProtocolError("execution.artifact_root must be absolute")
        artifact_path = artifact_path.resolve()
        if artifact_path == Path(artifact_path.anchor) or len(artifact_path.parts) < 3:
            raise ProtocolError("execution.artifact_root must be a specific non-root directory")
        artifact_root = str(artifact_path)
    backend = _mapping(execution.get("backend_adapter"), label="execution.backend_adapter")
    _exact_keys(backend, {"path", "sha256"}, label="execution.backend_adapter")
    backend_path = backend.get("path")
    backend_sha = _digest(backend.get("sha256"), label="execution.backend_adapter.sha256")
    if not isinstance(backend_path, str) or backend_path.startswith(UNRESOLVED) or backend_sha == "0" * 64:
        blockers.append("execution backend adapter is unresolved")
    else:
        resolved_backend = _repo_file(backend_path, label="execution.backend_adapter.path")
        if _sha256(resolved_backend) != backend_sha:
            raise ProtocolError("backend adapter SHA-256 mismatch")
        backend_path = str(resolved_backend)

    shared_contract = {
        "source_git_commit": source_git,
        "experiment_manifest_sha256": _sha256(manifest_path),
        "base_checkpoint_artifact_id": base_id,
        "teacher_artifact_id": teacher_id,
        "teacher_endpoint_env": teacher_env,
        "held_out_registration_artifact_id": heldout_id,
        "interface_contract_id": INTERFACE_CONTRACT,
        "frozen_interfaces": frozen,
        "parameterization": parameterization,
        "parameterization_sha256": parameterization_sha,
        "optimizer": optimizer,
        "budgets": budgets,
        "artifact_registry": {
            "path": registry_path.relative_to(REPO).as_posix(),
            "sha256": registry_sha,
        },
        "artifact_root": artifact_root,
    }
    cells: list[TrainingCell] = []
    for arm in arms:
        for seed in seeds:
            cell_id = f"{arm['arm_id']}-seed-{seed}"
            cell_output = output_root / experiment_id / cell_id
            cells.append(TrainingCell(
                cell_id=cell_id,
                arm_id=arm["arm_id"],
                role=arm["role"],
                seed=seed,
                output_dir=str(cell_output),
                config={
                    "schema_version": "kaetram.matched-training-cell.v1",
                    "experiment_id": experiment_id,
                    "cell_id": cell_id,
                    "arm": arm,
                    "training_seed": seed,
                    "shared_contract": shared_contract,
                },
            ))
    for ablation in history_ablations:
        normalized = {"arm_id": ablation["ablation_id"], **{
            key: value for key, value in ablation.items() if key != "ablation_id"
        }}
        for seed in seeds:
            cell_id = f"{normalized['arm_id']}-seed-{seed}"
            cell_output = output_root / experiment_id / cell_id
            cells.append(TrainingCell(
                cell_id=cell_id,
                arm_id=normalized["arm_id"],
                role=normalized["role"],
                seed=seed,
                output_dir=str(cell_output),
                config={
                    "schema_version": "kaetram.matched-training-cell.v1",
                    "experiment_id": experiment_id,
                    "cell_id": cell_id,
                    "arm": normalized,
                    "training_seed": seed,
                    "shared_contract": shared_contract,
                },
            ))
    if max_parallel > len(cells):
        raise ProtocolError("execution.max_parallel cannot exceed generated cell count")
    return TrainingPlan(
        experiment_id=experiment_id,
        manifest=str(manifest_path),
        manifest_sha256=_sha256(manifest_path),
        source_git_commit=source_git,
        registry_path=str(registry_path),
        registry_sha256=registry_sha,
        base_checkpoint_artifact_id=base_id,
        teacher_artifact_id=teacher_id,
        teacher_endpoint_env=teacher_env,
        held_out_registration_artifact_id=heldout_id,
        frozen_interfaces=tuple(frozen),
        parameterization=parameterization,
        parameterization_sha256=parameterization_sha,
        optimizer=optimizer,
        budgets=budgets,
        training_seed_schedule=seeds,
        arms=arms,
        history_ablations=history_ablations,
        allow_launch=allow_launch,
        max_parallel=max_parallel,
        output_root=str(output_root),
        artifact_root=artifact_root,
        backend_adapter_path=backend_path,
        backend_adapter_sha256=backend_sha,
        launch_blockers=tuple(sorted(set(blockers))),
        cells=tuple(cells),
    )


def plan_dict(plan: TrainingPlan) -> dict[str, Any]:
    payload = asdict(plan)
    payload["mode"] = "preflight_only"
    payload["counts"] = {
        "primary_arms": len(PRIMARY_ARMS),
        "mechanism_or_baseline_arms": len(MECHANISM_ARMS),
        "history_ablation_conditions": len(HISTORY_ABLATION_IDS),
        "seeds_per_arm": len(plan.training_seed_schedule),
        "core_training_cells": len(ARM_IDS) * len(plan.training_seed_schedule),
        "history_ablation_cells": len(HISTORY_ABLATION_IDS) * len(plan.training_seed_schedule),
        "training_cells_total": len(plan.cells),
    }
    return payload


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _git_state() -> tuple[str, list[str]]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return commit, dirty


def launch(plan: TrainingPlan, *, confirmation: str, environ: dict[str, str] | None = None) -> int:
    if not plan.allow_launch:
        raise ProtocolError("launch blocked: set execution.allow_launch=true in the reviewed manifest")
    if confirmation != plan.experiment_id:
        raise ProtocolError("launch blocked: --confirm-launch must exactly match experiment_id")
    if plan.launch_blockers:
        raise ProtocolError("launch blocked: " + "; ".join(plan.launch_blockers))
    env = dict(os.environ if environ is None else environ)
    if not env.get(plan.teacher_endpoint_env):
        raise ProtocolError(f"launch blocked: missing {plan.teacher_endpoint_env}")
    commit, dirty = _git_state()
    if commit != plan.source_git_commit:
        raise ProtocolError("launch blocked: current Git commit differs from protocol.source_git_commit")
    if dirty:
        raise ProtocolError("launch blocked: immutable protocol requires a clean worktree")
    root = Path(plan.output_root) / plan.experiment_id
    if root.exists():
        raise ProtocolError(f"launch blocked: refusing to reuse output root {root}")
    root.mkdir(parents=True, exist_ok=False)
    prelaunch = {
        "schema_version": "kaetram.matched-training-prelaunch.v1",
        "experiment_id": plan.experiment_id,
        "source_git_commit": commit,
        "manifest": {"path": plan.manifest, "sha256": plan.manifest_sha256},
        "artifact_registry": {"path": plan.registry_path, "sha256": plan.registry_sha256},
        "backend_adapter": {
            "path": plan.backend_adapter_path,
            "sha256": plan.backend_adapter_sha256,
        },
        "cells": [
            {"cell_id": cell.cell_id, "cell_contract_sha256": _sha256_json(cell.config)}
            for cell in plan.cells
        ],
    }
    _write_new_json(root / "prelaunch.json", prelaunch)
    return_code = 0
    for start in range(0, len(plan.cells), plan.max_parallel):
        processes: list[subprocess.Popen] = []
        try:
            for cell in plan.cells[start:start + plan.max_parallel]:
                cell_dir = Path(cell.output_dir)
                cell_dir.mkdir(parents=True, exist_ok=False)
                config_path = cell_dir / "cell-config.json"
                _write_new_json(config_path, cell.config)
                processes.append(subprocess.Popen(
                    [sys.executable, plan.backend_adapter_path, "--cell-config", str(config_path)],
                    cwd=REPO,
                    env=env,
                ))
        except Exception:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            raise
        active_processes = list(processes)
        while active_processes and return_code == 0:
            for process in list(active_processes):
                rc = process.poll()
                if rc is None:
                    continue
                active_processes.remove(process)
                if rc != 0:
                    return_code = rc
                    for active in active_processes:
                        if active.poll() is None:
                            active.terminate()
                    for active in active_processes:
                        active.wait()
                    active_processes.clear()
                    break
            if active_processes and return_code == 0:
                time.sleep(1)
        if return_code == 0:
            for cell in plan.cells[start:start + plan.max_parallel]:
                validate_cell_result(plan, cell)
        if return_code:
            break
    return return_code


def validate_cell_result(plan: TrainingPlan, cell: TrainingCell) -> None:
    """Require an attributed result without conflating preparation and training."""
    config_path = Path(cell.output_dir) / "cell-config.json"
    try:
        recorded_config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cell {cell.cell_id} config artifact is unreadable: {exc}") from exc
    if recorded_config != cell.config:
        raise ProtocolError(f"cell {cell.cell_id} immutable config changed after launch")
    path = Path(cell.output_dir) / "result.json"
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cell {cell.cell_id} has no valid result.json: {exc}") from exc
    if not isinstance(result, dict):
        raise ProtocolError(f"cell {cell.cell_id} result root must be an object")
    if result.get("schema_version") == "kaetram.matched-training-result.v2":
        _validate_prepared_cell_result(plan, cell, result)
        return
    expected = {
        "schema_version": "kaetram.matched-training-result.v1",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "status": "completed",
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
        "teacher_artifact_id": plan.teacher_artifact_id,
        "training_seed": cell.seed,
        "consumed_budgets": plan.budgets,
    }
    mismatches = {
        key: {"expected": value, "actual": result.get(key)}
        for key, value in expected.items()
        if result.get(key) != value
    }
    if mismatches:
        raise ProtocolError(f"cell {cell.cell_id} result contract mismatch: {mismatches}")
    output = _mapping(result.get("output_artifact"), label=f"cell {cell.cell_id}.output_artifact")
    _exact_keys(output, {"uri", "sha256"}, label=f"cell {cell.cell_id}.output_artifact")
    if not isinstance(output["uri"], str) or not output["uri"] or output["uri"].startswith(UNRESOLVED):
        raise ProtocolError(f"cell {cell.cell_id} output artifact URI is unresolved")
    _digest(output["sha256"], label=f"cell {cell.cell_id}.output_artifact.sha256", nonzero=True)


def _verified_result_file(value: Any, expected_sha: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{label} path is missing")
    raw = value[len("file:"):] if value.startswith("file:") else value
    path = _repo_file(raw, label=label)
    expected = _digest(expected_sha, label=f"{label}.sha256", nonzero=True)
    if _sha256(path) != expected:
        raise ProtocolError(f"{label} material SHA-256 mismatch")
    return path


def _validate_prepared_cell_result(
    plan: TrainingPlan, cell: TrainingCell, result: dict[str, Any]
) -> None:
    """Validate a materialized bundle while preserving its not-trained status."""
    _exact_keys(
        result,
        {
            "schema_version", "experiment_id", "cell_id", "status", "source_git_commit",
            "experiment_manifest_sha256", "base_checkpoint_artifact_id",
            "teacher_artifact_id", "training_seed", "allocated_budgets", "backend_plan",
            "output_artifact", "trainer_execution_status", "trainer_compatibility",
        },
        label=f"cell {cell.cell_id} prepared result",
    )
    expected = {
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "status": "prepared_not_trained",
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
        "teacher_artifact_id": plan.teacher_artifact_id,
        "training_seed": cell.seed,
        "allocated_budgets": plan.budgets,
        "trainer_execution_status": "not_run",
    }
    mismatches = {
        key: {"expected": value, "actual": result.get(key)}
        for key, value in expected.items()
        if result.get(key) != value
    }
    if mismatches:
        raise ProtocolError(f"cell {cell.cell_id} prepared result contract mismatch: {mismatches}")
    if not isinstance(result.get("trainer_compatibility"), str) \
            or not result["trainer_compatibility"]:
        raise ProtocolError(f"cell {cell.cell_id} trainer compatibility is missing")
    backend_plan = _mapping(
        result.get("backend_plan"), label=f"cell {cell.cell_id}.backend_plan"
    )
    _exact_keys(backend_plan, {"path", "sha256"}, label=f"cell {cell.cell_id}.backend_plan")
    backend_path = _verified_result_file(
        backend_plan["path"], backend_plan["sha256"], label=f"cell {cell.cell_id}.backend_plan"
    )
    output = _mapping(result.get("output_artifact"), label=f"cell {cell.cell_id}.output_artifact")
    _exact_keys(output, {"kind", "uri", "sha256"}, label=f"cell {cell.cell_id}.output_artifact")
    if output.get("kind") != "normalized_training_records":
        raise ProtocolError(f"cell {cell.cell_id} output is not normalized training records")
    output_path = _verified_result_file(
        output["uri"], output["sha256"], label=f"cell {cell.cell_id}.output_artifact"
    )
    try:
        backend_payload = json.loads(backend_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cell {cell.cell_id} backend plan is unreadable: {exc}") from exc
    if not isinstance(backend_payload, dict):
        raise ProtocolError(f"cell {cell.cell_id} backend plan root must be an object")
    backend_expected = {
        "schema_version": "kaetram.matched-training-backend-plan.v1",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "arm_id": cell.arm_id,
        "training_seed": cell.seed,
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "budgets": plan.budgets,
        "execution_status": "not_run",
    }
    backend_mismatches = {
        key: {"expected": value, "actual": backend_payload.get(key)}
        for key, value in backend_expected.items()
        if backend_payload.get(key) != value
    }
    if backend_mismatches:
        raise ProtocolError(
            f"cell {cell.cell_id} backend plan contract mismatch: {backend_mismatches}"
        )
    normalized = _mapping(
        backend_payload.get("normalized_records"),
        label=f"cell {cell.cell_id}.backend_plan.normalized_records",
    )
    if normalized.get("path") != str(output_path) or normalized.get("sha256") != output["sha256"]:
        raise ProtocolError(f"cell {cell.cell_id} backend plan/output material mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-launch", default="")
    args = parser.parse_args()
    try:
        plan = build_plan(args.manifest)
        if not args.execute:
            print(json.dumps(plan_dict(plan), indent=2, sort_keys=True))
            print("Nothing was launched. Resolve every blocker and use the triple launch interlock.")
            return 0
        return launch(plan, confirmation=args.confirm_launch)
    except (ProtocolError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
