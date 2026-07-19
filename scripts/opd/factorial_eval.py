#!/usr/bin/env python3
"""Manifest-driven, fail-closed launcher for the 2B weights x recovery eval.

Default behavior is preflight only. Launching requires all three of:
  1. execution.allow_launch=true in the manifest,
  2. --execute, and
  3. --confirm-launch matching experiment_id exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, validate_eval_selection  # noqa: E402
from inference_seed import validate_inference_seed  # noqa: E402
from run_manifest import (  # noqa: E402
    ManifestError,
    atomic_write_json,
    capture_git_state,
    hash_path,
    load_json,
    sha256_json,
    tool_schema_record,
    utc_now,
    validate_input_provenance,
    verify_descriptor,
)


REQUIRED_WEIGHTS = ("base", "r2", "r3")
REQUIRED_RECOVERY = (False, True)
REQUIRED_PERSONALITIES = ("grinder", "completionist", "explorer_tinkerer")
PERSONALITY_CODES = {"grinder": "g", "completionist": "c", "explorer_tinkerer": "e"}
SCHEDULE_ALGORITHM = "sha256-rank-v1"
ENVIRONMENT_RNG_MECHANISM = "kaetram-environment-rng-attestation/v2"
ENVIRONMENT_RNG_ALGORITHM = "mulberry32-sha256-v1"
SERVER_BUILD_ATTESTATION_SCHEMA = "kaetram-server-build-attestation/v1"
CLUSTER_SIZE = len(REQUIRED_PERSONALITIES) * len(REQUIRED_RECOVERY)
CORE3_PROTOCOL_ID = "core3_canonical_unseeded_6h_v1"
CORE3_DURATION_SECONDS = 6 * 60 * 60
PRIMARY_METRIC = "core3_stages_advanced"
PRIMARY_ESTIMANDS = (
    "r2_minus_base_recovery_off",
    "r3_minus_base_recovery_off",
    "recovery_on_minus_off_base",
    "recovery_on_minus_off_r2",
    "recovery_on_minus_off_r3",
    "r2_minus_base_recovery_interaction",
    "r3_minus_base_recovery_interaction",
)
ENDPOINT_ATTESTATION_SCHEMA = "kaetram.endpoint-attestation.v1"
POWER_ANALYSIS_SCHEMA = "kaetram-opd-power-analysis-v1"
UNRESOLVED_MARKER = "UNRESOLVED"
CELL_BUNDLE_SCHEMA = "kaetram.factorial-cell-bundle.v1"
COMPLETED_INVENTORY_SCHEMA = "kaetram.factorial-completed-inventory.v1"


@dataclass(frozen=True)
class Cell:
    cell_id: str
    pair_id: str
    cluster_id: str
    replicate: int
    weight: str
    recovery: bool
    personality: str
    endpoint_env: str
    api_model: str
    endpoint_attestation_sha256: str
    checkpoint_sha256: str
    tokenizer_sha256: str
    render_contract_sha256: str
    username: str
    server_port: int
    sandbox: str
    run_dir: str
    schedule_index: int
    batch_index: int
    inference_seed: int
    environment_seed: int


@dataclass(frozen=True)
class ExperimentPlan:
    experiment_id: str
    manifest: str
    manifest_sha256: str
    project_dir: str
    protocol_id: str
    duration_seconds: int
    world_initialization: str
    source_git_commit: str
    episodes: int
    scenario: str
    personalities: tuple[str, ...]
    tool_schema_source: str
    omit_game_knowledge: bool
    held_out_quest: str
    held_out_registration: str
    held_out_registration_sha256: str
    primary_metric: str
    primary_estimands: tuple[str, ...]
    familywise_alpha: float
    sampling_phase: str
    planned_replicates: int
    target_power: float
    confirmatory_replicates: int
    power_analysis_artifact: str
    power_analysis_sha256: str
    prompt_inputs: tuple[dict[str, Any], ...]
    model_provenance: tuple[dict[str, Any], ...]
    allow_launch: bool
    max_parallel: int
    schedule_algorithm: str
    schedule_seed: int
    inference_seeds: tuple[int, ...]
    environment_seed_mechanism: str
    environment_rng_algorithm: str
    environment_game_revision: str
    environment_game_bundle_sha256: str
    environment_seeds: tuple[int, ...]
    environment_seed_reason: str
    cells: tuple[Cell, ...]


def _require(mapping: dict[str, Any], key: str, kind: type, context: str) -> Any:
    value = mapping.get(key)
    if not isinstance(value, kind):
        raise ManifestError(f"{context}.{key} must be {kind.__name__}")
    return value


def load_manifest(path: str | Path) -> tuple[dict[str, Any], Path]:
    manifest_path = Path(path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ManifestError("manifest schema_version must be 2")
    return raw, manifest_path


def _repo_file(raw_path: str, context: str) -> Path:
    path = (REPO / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
    try:
        path.relative_to(REPO)
    except ValueError as exc:
        raise ManifestError(f"{context} must resolve inside the repository") from exc
    if not path.is_file():
        raise ManifestError(f"{context} does not exist: {path}")
    return path


def _sha256(value: object, context: str, *, allow_unresolved: bool = False) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{context} must be a lowercase SHA-256")
    if allow_unresolved and value.startswith(UNRESOLVED_MARKER):
        return value
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ManifestError(f"{context} must be a lowercase SHA-256")
    return value


def _load_hashed_json(raw_path: str, expected_sha256: str | None, context: str) -> tuple[dict, Path, str]:
    path = _repo_file(raw_path, context)
    descriptor = hash_path(path, root=REPO)
    digest = descriptor["sha256"]
    if expected_sha256 is not None and digest != _sha256(expected_sha256, f"{context}_sha256"):
        raise ManifestError(
            f"{context} digest mismatch: recorded={expected_sha256}, actual={digest}"
        )
    value = load_json(path)
    if not isinstance(value, dict):
        raise ManifestError(f"{context} must contain a JSON object")
    return value, path, digest


def build_plan(path: str | Path, *, environ: dict[str, str] | None = None) -> ExperimentPlan:
    raw, manifest_path = load_manifest(path)
    experiment_id = _require(raw, "experiment_id", str, "manifest").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", experiment_id):
        raise ManifestError("experiment_id must be 3-64 lowercase letters, digits, '_' or '-'")

    protocol = _require(raw, "protocol", dict, "manifest")
    protocol_id = _require(protocol, "protocol_id", str, "protocol")
    if protocol_id != CORE3_PROTOCOL_ID:
        raise ManifestError(f"protocol.protocol_id must be {CORE3_PROTOCOL_ID!r}")
    world_initialization = _require(protocol, "world_initialization", str, "protocol")
    if world_initialization != "canonical_unseeded":
        raise ManifestError("Core-3 confirmatory runs require a canonical_unseeded fresh world")
    duration_seconds = protocol.get("duration_seconds")
    if duration_seconds != CORE3_DURATION_SECONDS:
        raise ManifestError("Core-3 confirmatory runs require protocol.duration_seconds=21600")
    source_git_commit = _require(protocol, "source_git_commit", str, "protocol")
    if not (
        source_git_commit.startswith(UNRESOLVED_MARKER)
        or re.fullmatch(r"[0-9a-f]{40}", source_git_commit)
    ):
        raise ManifestError("protocol.source_git_commit must be a full git SHA or unresolved example marker")
    prompt_inputs_raw = _require(protocol, "prompt_inputs", list, "protocol")
    if not prompt_inputs_raw:
        raise ManifestError("protocol.prompt_inputs must contain frozen prompt files")
    prompt_inputs: list[dict[str, Any]] = []
    for index, item in enumerate(prompt_inputs_raw):
        if not isinstance(item, dict):
            raise ManifestError(f"protocol.prompt_inputs[{index}] must be an object")
        raw_prompt_path = _require(item, "path", str, f"protocol.prompt_inputs[{index}]")
        expected = _sha256(item.get("sha256"), f"protocol.prompt_inputs[{index}].sha256")
        prompt_path = _repo_file(raw_prompt_path, f"protocol.prompt_inputs[{index}].path")
        actual = hash_path(prompt_path, root=REPO)["sha256"]
        if actual != expected:
            raise ManifestError(
                f"protocol.prompt_inputs[{index}] digest mismatch: recorded={expected}, actual={actual}"
            )
        prompt_inputs.append({"path": str(prompt_path), "sha256": actual})

    design = _require(raw, "design", dict, "manifest")
    weights = _require(design, "weights", list, "design")
    recovery = _require(design, "recovery", list, "design")
    replicates = design.get("replicates")
    if tuple(weights) != REQUIRED_WEIGHTS:
        raise ManifestError(f"design.weights must be exactly {list(REQUIRED_WEIGHTS)}")
    if len(recovery) != 2 or set(recovery) != set(REQUIRED_RECOVERY):
        raise ManifestError("design.recovery must contain exactly false and true")
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise ManifestError("design.replicates must be a positive integer")

    randomization = _require(raw, "randomization", dict, "manifest")
    schedule_algorithm = _require(
        randomization, "schedule_algorithm", str, "randomization"
    )
    if schedule_algorithm != SCHEDULE_ALGORITHM:
        raise ManifestError(
            f"randomization.schedule_algorithm must be '{SCHEDULE_ALGORITHM}'"
        )
    try:
        schedule_seed = validate_inference_seed(
            randomization.get("schedule_seed"), label="randomization.schedule_seed"
        )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    inference_seeds_raw = _require(
        randomization, "inference_seeds", list, "randomization"
    )
    if len(inference_seeds_raw) != replicates:
        raise ManifestError(
            "randomization.inference_seeds must contain exactly one seed per replicate"
        )
    try:
        inference_seeds = tuple(
            validate_inference_seed(seed, label=f"randomization.inference_seeds[{index}]")
            for index, seed in enumerate(inference_seeds_raw)
        )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if len(set(inference_seeds)) != len(inference_seeds):
        raise ManifestError("randomization.inference_seeds must be unique")
    environment_seed_cfg = _require(
        randomization, "environment_seed", dict, "randomization"
    )
    environment_seed_mechanism = _require(
        environment_seed_cfg, "mechanism", str, "randomization.environment_seed"
    )
    environment_rng_algorithm = _require(
        environment_seed_cfg, "algorithm", str, "randomization.environment_seed"
    )
    environment_game_revision = _require(
        environment_seed_cfg, "game_revision", str, "randomization.environment_seed"
    )
    environment_seeds_raw = _require(
        environment_seed_cfg, "seeds", list, "randomization.environment_seed"
    )
    environment_seed_reason = _require(
        environment_seed_cfg, "reason", str, "randomization.environment_seed"
    ).strip()
    if environment_seed_mechanism != ENVIRONMENT_RNG_MECHANISM:
        raise ManifestError(
            "randomization.environment_seed.mechanism must be "
            f"'{ENVIRONMENT_RNG_MECHANISM}'"
        )
    if environment_rng_algorithm != ENVIRONMENT_RNG_ALGORITHM:
        raise ManifestError(
            "randomization.environment_seed.algorithm must be "
            f"'{ENVIRONMENT_RNG_ALGORITHM}'"
        )
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", environment_game_revision):
        raise ManifestError(
            "randomization.environment_seed.game_revision must be an exact lowercase commit hash"
        )
    if len(environment_seeds_raw) != replicates:
        raise ManifestError(
            "randomization.environment_seed.seeds must contain exactly one seed per replicate"
        )
    try:
        environment_seeds = tuple(
            validate_inference_seed(
                seed, label=f"randomization.environment_seed.seeds[{index}]"
            )
            for index, seed in enumerate(environment_seeds_raw)
        )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if len(set(environment_seeds)) != len(environment_seeds):
        raise ManifestError("randomization.environment_seed.seeds must be unique")
    if not environment_seed_reason:
        raise ManifestError(
            "randomization.environment_seed.reason must describe residual nondeterminism"
        )

    models = _require(raw, "models", dict, "manifest")
    if set(models) != set(REQUIRED_WEIGHTS):
        raise ManifestError(f"models must define exactly {list(REQUIRED_WEIGHTS)}")
    endpoint_envs: set[str] = set()
    model_provenance: list[dict[str, Any]] = []
    for weight in REQUIRED_WEIGHTS:
        cfg = _require(models, weight, dict, "models")
        endpoint_env = _require(cfg, "endpoint_env", str, f"models.{weight}")
        api_model = _require(cfg, "api_model", str, f"models.{weight}")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", endpoint_env):
            raise ManifestError(f"models.{weight}.endpoint_env is not a valid environment variable")
        if endpoint_env in endpoint_envs:
            raise ManifestError("each weight must use a distinct endpoint_env")
        endpoint_envs.add(endpoint_env)

        checkpoint_raw = _require(
            cfg, "checkpoint_provenance", str, f"models.{weight}"
        )
        checkpoint_record, checkpoint_path, checkpoint_file_sha = _load_hashed_json(
            checkpoint_raw, None, f"models.{weight}.checkpoint_provenance"
        )
        provenance_errors = validate_input_provenance(checkpoint_record)
        if provenance_errors:
            raise ManifestError(
                f"models.{weight}.checkpoint_provenance invalid: {'; '.join(provenance_errors)}"
            )
        if checkpoint_record.get("kind") != "checkpoint":
            raise ManifestError(f"models.{weight}.checkpoint_provenance must describe a checkpoint")
        checkpoint_sha = _sha256(
            (checkpoint_record.get("content") or {}).get("sha256"),
            f"models.{weight}.checkpoint_provenance.content.sha256",
        )

        attestation_raw = _require(
            cfg, "endpoint_attestation", str, f"models.{weight}"
        )
        attestation, attestation_path, attestation_file_sha = _load_hashed_json(
            attestation_raw, None, f"models.{weight}.endpoint_attestation"
        )
        if attestation.get("schema_version") != ENDPOINT_ATTESTATION_SCHEMA:
            raise ManifestError(
                f"models.{weight}.endpoint_attestation schema_version must be "
                f"{ENDPOINT_ATTESTATION_SCHEMA}"
            )
        status = _require(attestation, "status", str, f"models.{weight}.endpoint_attestation")
        health_path = _require(
            attestation, "health_path", str, f"models.{weight}.endpoint_attestation"
        )
        if health_path != "/health":
            raise ManifestError(f"models.{weight}.endpoint_attestation.health_path must be /health")
        expected_health = _require(
            attestation, "expected_health", dict, f"models.{weight}.endpoint_attestation"
        )
        expected_api_model = _require(
            expected_health, "api_model", str,
            f"models.{weight}.endpoint_attestation.expected_health",
        )
        if expected_api_model != api_model:
            raise ManifestError(f"models.{weight} api_model differs from endpoint attestation")
        deployment_id = _require(
            expected_health, "deployment_id", str,
            f"models.{weight}.endpoint_attestation.expected_health",
        )
        tokenizer_sha = _sha256(
            expected_health.get("tokenizer_sha256"),
            f"models.{weight}.endpoint_attestation.expected_health.tokenizer_sha256",
        )
        render_contract_sha = _sha256(
            expected_health.get("render_contract_sha256"),
            f"models.{weight}.endpoint_attestation.expected_health.render_contract_sha256",
        )
        attested_checkpoint_sha = _sha256(
            expected_health.get("checkpoint_sha256"),
            f"models.{weight}.endpoint_attestation.expected_health.checkpoint_sha256",
        )
        if attested_checkpoint_sha != checkpoint_sha:
            raise ManifestError(
                f"models.{weight} endpoint and checkpoint provenance digests disagree"
            )
        model_provenance.append({
            "weight": weight,
            "api_model": api_model,
            "endpoint_env": endpoint_env,
            "checkpoint_provenance": str(checkpoint_path),
            "checkpoint_provenance_sha256": checkpoint_file_sha,
            "checkpoint_sha256": checkpoint_sha,
            "endpoint_attestation": str(attestation_path),
            "endpoint_attestation_sha256": attestation_file_sha,
            "attestation_status": status,
            "health_path": health_path,
            "expected_health": {
                "deployment_id": deployment_id,
                "api_model": api_model,
                "checkpoint_sha256": checkpoint_sha,
                "tokenizer_sha256": tokenizer_sha,
                "render_contract_sha256": render_contract_sha,
            },
        })

    evaluation = _require(raw, "evaluation", dict, "manifest")
    episodes = evaluation.get("episodes")
    if episodes != 1:
        raise ManifestError(
            "confirmatory factorial requires evaluation.episodes=1; use design.replicates "
            "for independent DB-reset repetitions"
        )
    scenario = _require(evaluation, "scenario", str, "evaluation")
    if scenario not in {"A", "B", "C", "D"}:
        raise ManifestError("evaluation.scenario must be A, B, C, or D")
    personalities = evaluation.get("personalities")
    if not isinstance(personalities, list) or tuple(personalities) != REQUIRED_PERSONALITIES:
        raise ManifestError(
            f"evaluation.personalities must be exactly {list(REQUIRED_PERSONALITIES)}"
        )
    omit_game_knowledge = evaluation.get("omit_game_knowledge")
    if not isinstance(omit_game_knowledge, bool):
        raise ManifestError("evaluation.omit_game_knowledge must be boolean")
    tool_schema_source = _require(evaluation, "tool_schema_source", str, "evaluation")
    if tool_schema_source != "canonical":
        raise ManifestError(
            "confirmatory factorial requires evaluation.tool_schema_source='canonical'"
        )
    held_out_value = evaluation.get("held_out_quest", "")
    if not isinstance(held_out_value, str):
        raise ManifestError("evaluation.held_out_quest must be a string when provided")
    held_out_quest = held_out_value
    registration_raw = str(evaluation.get("held_out_registration") or "")
    registration_sha_raw = str(evaluation.get("held_out_registration_sha256") or "")
    if held_out_quest and not omit_game_knowledge:
        raise ManifestError("a held-out quest requires evaluation.omit_game_knowledge=true")
    registration = None
    if held_out_quest:
        if not registration_raw:
            raise ManifestError("a held-out quest requires evaluation.held_out_registration")
        registration_path = (
            (REPO / registration_raw).resolve()
            if not Path(registration_raw).is_absolute()
            else Path(registration_raw).resolve()
        )
        try:
            registration = validate_eval_selection(held_out_quest, registration_path)
        except HeldOutGuardError as exc:
            raise ManifestError(str(exc)) from exc
        expected_registration_sha = _sha256(
            registration_sha_raw, "evaluation.held_out_registration_sha256"
        )
        actual_registration_sha = hash_path(registration_path, root=REPO)["sha256"]
        if actual_registration_sha != expected_registration_sha:
            raise ManifestError(
                "evaluation held-out registration digest mismatch: "
                f"recorded={expected_registration_sha}, actual={actual_registration_sha}"
            )
    elif registration_raw:
        raise ManifestError("held_out_registration must be empty when held_out_quest is empty")
    elif registration_sha_raw:
        raise ManifestError("held_out_registration_sha256 must be empty without a held-out quest")

    if protocol_id == CORE3_PROTOCOL_ID:
        if scenario != "D" or omit_game_knowledge or held_out_quest:
            raise ManifestError(
                "core3_canonical_unseeded_6h_v1 requires scenario D, game knowledge, "
                "and no held-out quest"
            )

    analysis = _require(raw, "analysis", dict, "manifest")
    primary_metric = _require(analysis, "primary_metric", str, "analysis")
    if primary_metric != PRIMARY_METRIC:
        raise ManifestError(f"analysis.primary_metric must be {PRIMARY_METRIC!r}")
    estimands = _require(analysis, "primary_estimands", list, "analysis")
    if tuple(estimands) != PRIMARY_ESTIMANDS:
        raise ManifestError(f"analysis.primary_estimands must be exactly {list(PRIMARY_ESTIMANDS)}")
    if analysis.get("independent_unit") != "fresh_world_replicate_cluster":
        raise ManifestError("analysis.independent_unit must be fresh_world_replicate_cluster")
    familywise_alpha = analysis.get("familywise_alpha")
    if familywise_alpha != 0.05:
        raise ManifestError("analysis.familywise_alpha must be 0.05")
    sample_size = _require(analysis, "sample_size", dict, "analysis")
    if sample_size.get("phase") != "confirmatory":
        raise ManifestError("analysis.sample_size.phase must be confirmatory")
    planned_replicates = sample_size.get("planned_replicates")
    if planned_replicates != replicates or sample_size.get("confirmatory_replicates") != replicates:
        raise ManifestError(
            "planned_replicates, confirmatory_replicates, and design.replicates must match"
        )
    target_power = sample_size.get("target_power")
    if isinstance(target_power, bool) or not isinstance(target_power, (int, float)) or not 0.8 <= target_power < 1:
        raise ManifestError("analysis.sample_size.target_power must be >=0.8 and <1")
    power_raw = _require(
        sample_size, "power_analysis_artifact", str, "analysis.sample_size"
    )
    power_expected_sha = _sha256(
        sample_size.get("power_analysis_sha256"),
        "analysis.sample_size.power_analysis_sha256",
    )
    power, power_path, power_actual_sha = _load_hashed_json(
        power_raw, power_expected_sha, "analysis.sample_size.power_analysis_artifact"
    )
    expected_power = {
        "schema_version": POWER_ANALYSIS_SCHEMA,
        "experiment_id": experiment_id,
        "primary_metric": primary_metric,
        "primary_estimands": list(PRIMARY_ESTIMANDS),
        "familywise_alpha": familywise_alpha,
        "target_power": target_power,
        "planned_replicates": replicates,
    }
    power_mismatches = {
        key: {"expected": expected, "actual": power.get(key)}
        for key, expected in expected_power.items()
        if power.get(key) != expected
    }
    if power_mismatches:
        raise ManifestError(f"power-analysis contract mismatch: {power_mismatches}")
    if not isinstance(power.get("method"), str) or not power["method"].strip():
        raise ManifestError("power-analysis method must be non-empty")
    if not isinstance(power.get("assumptions"), list) or not power["assumptions"]:
        raise ManifestError("power-analysis assumptions must be non-empty")

    isolation = _require(raw, "isolation", dict, "manifest")
    username_prefix = _require(isolation, "username_prefix", str, "isolation")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", username_prefix):
        raise ManifestError("isolation.username_prefix must be alphanumeric and start with a letter")
    port_start = isolation.get("server_port_start")
    if isinstance(port_start, bool) or not isinstance(port_start, int) or not 1024 <= port_start <= 65529:
        raise ManifestError("isolation.server_port_start must be between 1024 and 65529")
    sandbox_root = Path(_require(isolation, "sandbox_root", str, "isolation"))
    if not sandbox_root.is_absolute() or sandbox_root == Path("/"):
        raise ManifestError("isolation.sandbox_root must be a specific absolute path")
    output_raw = Path(_require(isolation, "output_root", str, "isolation"))
    output_root = output_raw.resolve() if output_raw.is_absolute() else (REPO / output_raw).resolve()

    execution = _require(raw, "execution", dict, "manifest")
    allow_launch = execution.get("allow_launch")
    if not isinstance(allow_launch, bool):
        raise ManifestError("execution.allow_launch must be boolean")
    max_parallel = execution.get("max_parallel")
    if max_parallel != CLUSTER_SIZE:
        raise ManifestError(
            f"execution.max_parallel must be {CLUSTER_SIZE} so each batch is one analysis cluster"
        )

    cells: list[Cell] = []
    for replicate in range(1, replicates + 1):
        for weight in REQUIRED_WEIGHTS:
            cfg = models[weight]
            cluster_id = f"rep{replicate:02d}-{weight}"
            for personality in REQUIRED_PERSONALITIES:
                pair_id = f"{cluster_id}-{personality}"
                for recovery_enabled in REQUIRED_RECOVERY:
                    rec_label = "on" if recovery_enabled else "off"
                    cell_id = f"{pair_id}-recovery-{rec_label}"
                    username = (
                        f"{username_prefix}r{replicate:02d}{weight}"
                        f"{PERSONALITY_CODES[personality]}{int(recovery_enabled)}"
                    )
                    if len(username) > 24:
                        raise ManifestError(f"generated username exceeds 24 characters: {username}")
                    cell_index = len(cells)
                    cells.append(Cell(
                        cell_id=cell_id,
                        pair_id=pair_id,
                        cluster_id=cluster_id,
                        replicate=replicate,
                        weight=weight,
                        recovery=recovery_enabled,
                        personality=personality,
                        endpoint_env=cfg["endpoint_env"],
                        api_model=cfg["api_model"],
                        endpoint_attestation_sha256=next(
                            item["endpoint_attestation_sha256"]
                            for item in model_provenance if item["weight"] == weight
                        ),
                        checkpoint_sha256=next(
                            item["checkpoint_sha256"]
                            for item in model_provenance if item["weight"] == weight
                        ),
                        tokenizer_sha256=next(
                            item["expected_health"]["tokenizer_sha256"]
                            for item in model_provenance if item["weight"] == weight
                        ),
                        render_contract_sha256=next(
                            item["expected_health"]["render_contract_sha256"]
                            for item in model_provenance if item["weight"] == weight
                        ),
                        username=username,
                        server_port=port_start + cell_index,
                        sandbox=str((sandbox_root / experiment_id / cell_id).resolve()),
                        run_dir=str((output_root / experiment_id / cell_id).resolve()),
                        schedule_index=-1,
                        batch_index=-1,
                        inference_seed=inference_seeds[replicate - 1],
                        environment_seed=environment_seeds[replicate - 1],
                    ))

    if cells[-1].server_port > 65535:
        raise ManifestError("generated server ports exceed 65535; lower server_port_start")
    if max_parallel > len(cells):
        raise ManifestError("execution.max_parallel cannot exceed the generated cell count")

    def rank(label: str) -> bytes:
        return hashlib.sha256(f"{schedule_seed}:{label}".encode()).digest()

    scheduled_cells: list[Cell] = []
    cluster_ids = sorted(
        {cell.cluster_id for cell in cells},
        key=lambda value: (rank(f"cluster:{value}"), value),
    )
    for batch_index, cluster_id in enumerate(cluster_ids):
        cluster = [cell for cell in cells if cell.cluster_id == cluster_id]
        pair_ids = sorted(
            {cell.pair_id for cell in cluster},
            key=lambda value: (rank(f"pair:{value}"), value),
        )
        for pair_id in pair_ids:
            pair = sorted(
                (cell for cell in cluster if cell.pair_id == pair_id),
                key=lambda cell: (rank(f"cell:{cell.cell_id}"), cell.cell_id),
            )
            for cell in pair:
                scheduled_cells.append(replace(
                    cell,
                    schedule_index=len(scheduled_cells),
                    batch_index=batch_index,
                ))

    plan = ExperimentPlan(
        experiment_id=experiment_id,
        manifest=str(manifest_path),
        manifest_sha256=hash_path(manifest_path, root=REPO)["sha256"],
        project_dir=str(REPO),
        protocol_id=protocol_id,
        duration_seconds=duration_seconds,
        world_initialization=world_initialization,
        source_git_commit=source_git_commit,
        episodes=episodes,
        scenario=scenario,
        personalities=REQUIRED_PERSONALITIES,
        tool_schema_source=tool_schema_source,
        omit_game_knowledge=omit_game_knowledge,
        held_out_quest=registration.quest_name if registration else "",
        held_out_registration=str(registration.path) if registration else "",
        held_out_registration_sha256=(
            hash_path(registration.path, root=REPO)["sha256"] if registration else ""
        ),
        primary_metric=primary_metric,
        primary_estimands=PRIMARY_ESTIMANDS,
        familywise_alpha=familywise_alpha,
        sampling_phase="confirmatory",
        planned_replicates=planned_replicates,
        target_power=float(target_power),
        confirmatory_replicates=replicates,
        power_analysis_artifact=str(power_path),
        power_analysis_sha256=power_actual_sha,
        prompt_inputs=tuple(prompt_inputs),
        model_provenance=tuple(model_provenance),
        allow_launch=allow_launch,
        max_parallel=max_parallel,
        schedule_algorithm=schedule_algorithm,
        schedule_seed=schedule_seed,
        inference_seeds=inference_seeds,
        environment_seed_mechanism=environment_seed_mechanism,
        environment_rng_algorithm=environment_rng_algorithm,
        environment_game_revision=environment_game_revision,
        environment_game_bundle_sha256="0" * 64,
        environment_seeds=environment_seeds,
        environment_seed_reason=environment_seed_reason,
        cells=tuple(scheduled_cells),
    )
    validate_factorial_plan(plan)
    return plan


def validate_factorial_plan(plan: ExperimentPlan) -> None:
    expected = {
        (replicate, weight, recovery, personality)
        for replicate in range(1, max(cell.replicate for cell in plan.cells) + 1)
        for weight in REQUIRED_WEIGHTS
        for recovery in REQUIRED_RECOVERY
        for personality in REQUIRED_PERSONALITIES
    }
    actual = {(c.replicate, c.weight, c.recovery, c.personality) for c in plan.cells}
    if actual != expected or len(plan.cells) != len(expected):
        raise ManifestError("plan is not a complete, duplicate-free weights x recovery factorial")

    for pair_id in {c.pair_id for c in plan.cells}:
        pair = [c for c in plan.cells if c.pair_id == pair_id]
        if {c.recovery for c in pair} != set(REQUIRED_RECOVERY) or len(pair) != 2:
            raise ManifestError(f"pair {pair_id} must contain recovery off and on exactly once")

    for cluster_id in {c.cluster_id for c in plan.cells}:
        cluster = [c for c in plan.cells if c.cluster_id == cluster_id]
        cluster_cells = {(c.personality, c.recovery) for c in cluster}
        expected_cluster = {
            (personality, recovery)
            for personality in REQUIRED_PERSONALITIES
            for recovery in REQUIRED_RECOVERY
        }
        if cluster_cells != expected_cluster or len(cluster) != len(expected_cluster):
            raise ManifestError(
                f"cluster {cluster_id} must contain all three personality lanes x recovery off/on"
            )
        indices = sorted(c.schedule_index for c in cluster)
        if indices != list(range(indices[0], indices[0] + CLUSTER_SIZE)):
            raise ManifestError(f"cluster {cluster_id} must be one contiguous launch batch")
        if len({c.batch_index for c in cluster}) != 1:
            raise ManifestError(f"cluster {cluster_id} must use one batch_index")

    for pair_id in {c.pair_id for c in plan.cells}:
        pair = sorted(c.schedule_index for c in plan.cells if c.pair_id == pair_id)
        if pair[1] != pair[0] + 1:
            raise ManifestError(f"pair {pair_id} must remain adjacent in the randomized schedule")

    if [c.schedule_index for c in plan.cells] != list(range(len(plan.cells))):
        raise ManifestError("schedule_index must be complete, ordered, and duplicate-free")
    if any(c.inference_seed != plan.inference_seeds[c.replicate - 1] for c in plan.cells):
        raise ManifestError("all cells in a replicate must share its registered inference seed")
    if any(c.environment_seed != plan.environment_seeds[c.replicate - 1] for c in plan.cells):
        raise ManifestError("all cells in a replicate must share its registered environment seed")

    for attr in ("username", "server_port", "sandbox", "run_dir", "cell_id"):
        values = [getattr(c, attr) for c in plan.cells]
        if len(values) != len(set(values)):
            raise ManifestError(f"all cells must have isolated, unique {attr} values")


def cell_command(plan: ExperimentPlan, cell: Cell) -> list[str]:
    cmd = [
        sys.executable,
        str(REPO / "eval_harness.py"),
        "--models-env", f"{cell.cell_id}={cell.endpoint_env}",
        "--model-api-name", cell.api_model,
        "--episodes", str(plan.episodes),
        "--scenario", plan.scenario,
        "--duration-seconds", str(plan.duration_seconds),
        "--protocol-id", plan.protocol_id,
        "--experiment-manifest-sha256", plan.manifest_sha256,
        "--endpoint-attestation-sha256", cell.endpoint_attestation_sha256,
        "--checkpoint-sha256", cell.checkpoint_sha256,
        "--tokenizer-sha256", cell.tokenizer_sha256,
        "--render-contract-sha256", cell.render_contract_sha256,
        "--output-dir", cell.run_dir,
        "--project-dir", plan.project_dir,
        "--username", cell.username,
        "--server-port", str(cell.server_port),
        "--sandbox", cell.sandbox,
        "--inference-seed", str(cell.inference_seed),
        "--factorial-schedule-algorithm", plan.schedule_algorithm,
        "--factorial-schedule-seed", str(plan.schedule_seed),
        "--factorial-schedule-index", str(cell.schedule_index),
        "--factorial-batch-index", str(cell.batch_index),
        "--factorial-cluster-id", cell.cluster_id,
        "--factorial-pair-id", cell.pair_id,
        "--environment-seed-mechanism", plan.environment_seed_mechanism,
        "--environment-seed", str(cell.environment_seed),
        "--environment-rng-algorithm", plan.environment_rng_algorithm,
        "--environment-game-revision", plan.environment_game_revision,
        "--environment-game-bundle-sha256", plan.environment_game_bundle_sha256,
        "--environment-seed-reason", plan.environment_seed_reason,
    ]
    cmd.extend(["--personality", cell.personality])
    if plan.omit_game_knowledge:
        cmd.append("--omit-game-knowledge")
    if plan.held_out_quest:
        cmd.extend([
            "--held-out-quest", plan.held_out_quest,
            "--held-out-registration", plan.held_out_registration,
        ])
    return cmd


def plan_dict(plan: ExperimentPlan) -> dict[str, Any]:
    payload = asdict(plan)
    payload["mode"] = "preflight_only"
    payload["factorial_validation"] = "passed"
    payload["launchability"] = "attested_environment_rng_configured"
    payload["commands"] = [
        cell_command(plan, cell)
        for cell in plan.cells
    ]
    return payload


def validate_cell_result(plan: ExperimentPlan, cell: Cell) -> None:
    """Require one complete, correctly attributed artifact for a launched cell."""
    path = Path(cell.run_dir) / cell.cell_id / "results.json"
    try:
        results = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cell {cell.cell_id} has no valid results artifact: {exc}") from exc
    if not isinstance(results, dict):
        raise ManifestError(f"cell {cell.cell_id} results root must be an object")
    episodes = results.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != plan.episodes:
        raise ManifestError(
            f"cell {cell.cell_id} recorded {len(episodes) if isinstance(episodes, list) else 0} "
            f"episodes; expected {plan.episodes}"
        )
    expected_ids = list(range(1, plan.episodes + 1))
    if not all(isinstance(episode, dict) for episode in episodes):
        raise ManifestError(f"cell {cell.cell_id} episodes must be objects")
    if [episode.get("episode") for episode in episodes] != expected_ids:
        raise ManifestError(f"cell {cell.cell_id} episode IDs are incomplete or duplicated")
    if any(episode.get("status") != "ok" for episode in episodes):
        raise ManifestError(f"cell {cell.cell_id} contains a failed episode")
    if any(episode.get("returncode") != 0 for episode in episodes):
        raise ManifestError(f"cell {cell.cell_id} contains a nonzero episode return code")
    if any(
        isinstance(episode.get("duration_seconds"), bool)
        or not isinstance(episode.get("duration_seconds"), (int, float))
        or episode["duration_seconds"] < plan.duration_seconds
        for episode in episodes
    ):
        raise ManifestError(
            f"cell {cell.cell_id} contains an episode shorter than the registered duration"
        )

    meta = results.get("meta")
    if not isinstance(meta, dict):
        raise ManifestError(f"cell {cell.cell_id} results meta must be an object")
    expected_meta = {
        "model": cell.cell_id,
        "scenario": plan.scenario,
        "duration_seconds_budget": plan.duration_seconds,
        "protocol_id": plan.protocol_id,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "endpoint_attestation_sha256": cell.endpoint_attestation_sha256,
        "checkpoint_sha256": cell.checkpoint_sha256,
        "tokenizer_sha256": cell.tokenizer_sha256,
        "render_contract_sha256": cell.render_contract_sha256,
        "total_episodes": plan.episodes,
        "ok_episodes": plan.episodes,
        "tool_schema_source": plan.tool_schema_source,
        "include_game_knowledge": not plan.omit_game_knowledge,
        "held_out_quest": plan.held_out_quest,
        "inference_seed": cell.inference_seed,
        "factorial_schedule_algorithm": plan.schedule_algorithm,
        "factorial_schedule_seed": plan.schedule_seed,
        "factorial_schedule_index": cell.schedule_index,
        "factorial_batch_index": cell.batch_index,
        "factorial_cluster_id": cell.cluster_id,
        "factorial_pair_id": cell.pair_id,
        "environment_seed_mechanism": plan.environment_seed_mechanism,
        "environment_seed": cell.environment_seed,
        "environment_rng_algorithm": plan.environment_rng_algorithm,
        "environment_game_revision": plan.environment_game_revision,
        "environment_game_bundle_sha256": plan.environment_game_bundle_sha256,
        "environment_seed_reason": plan.environment_seed_reason,
            "environment_rng_attestation": {
            "schema": plan.environment_seed_mechanism,
            "algorithm": plan.environment_rng_algorithm,
            "seedSha256": hashlib.sha256(str(cell.environment_seed).encode()).hexdigest(),
                "gameRevision": plan.environment_game_revision,
                "serverBundleSha256": plan.environment_game_bundle_sha256,
                "drawsAtAttestation": 0,
        },
    }
    mismatches = {
        key: {"expected": expected, "actual": meta.get(key)}
        for key, expected in expected_meta.items()
        if meta.get(key) != expected
    }
    if mismatches:
        raise ManifestError(f"cell {cell.cell_id} result metadata mismatch: {mismatches}")


def require_environment_seed_capability(
    plan: ExperimentPlan, environ: dict[str, str]
) -> dict[str, str]:
    if plan.environment_seed_mechanism != ENVIRONMENT_RNG_MECHANISM:
        raise ManifestError(
            "launch blocked: unsupported Kaetram environment RNG attestation mechanism"
        )
    game_dir = Path(
        environ.get("KAETRAM_GAME_DIR", "~/projects/Kaetram-Open")
    ).expanduser().resolve()
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(game_dir), "rev-parse", "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError(
            f"launch blocked: cannot attest Kaetram checkout at {game_dir}: {exc}"
        ) from exc
    if revision != plan.environment_game_revision:
        raise ManifestError(
            "launch blocked: Kaetram checkout revision mismatch: "
            f"expected {plan.environment_game_revision}, found {revision}"
        )
    try:
        dirty = subprocess.check_output(
            ["git", "-C", str(game_dir), "status", "--porcelain", "--untracked-files=no"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
        source_tree_oid = subprocess.check_output(
            ["git", "-C", str(game_dir), "rev-parse", "HEAD^{tree}"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError(f"launch blocked: cannot verify Kaetram source tree: {exc}") from exc
    if dirty:
        raise ManifestError("launch blocked: Kaetram tracked source tree is dirty")

    build_record_path = game_dir / "packages" / "server" / "dist" / "kaetram-build-attestation.json"
    try:
        build_record = json.loads(build_record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"launch blocked: invalid Kaetram build attestation: {exc}") from exc
    expected_build = {
        "schema": SERVER_BUILD_ATTESTATION_SCHEMA,
        "gameRevision": plan.environment_game_revision,
        "sourceTreeGitOid": source_tree_oid,
        "entrypoint": "packages/server/dist/main.js",
    }
    mismatches = {
        key: {"expected": value, "actual": build_record.get(key)}
        for key, value in expected_build.items()
        if build_record.get(key) != value
    }
    if mismatches:
        raise ManifestError(f"launch blocked: Kaetram build attribution mismatch: {mismatches}")
    bundle_sha256 = build_record.get("entrypointSha256")
    if not isinstance(bundle_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_sha256):
        raise ManifestError("launch blocked: Kaetram build attestation has no valid entrypoint SHA-256")
    entrypoint = game_dir / build_record["entrypoint"]
    if not entrypoint.is_file():
        raise ManifestError(
            f"launch blocked: attested Kaetram server build is missing under {game_dir}"
        )
    actual_bundle_sha256 = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    if actual_bundle_sha256 != bundle_sha256:
        raise ManifestError("launch blocked: Kaetram server bundle digest mismatch")
    return {
        "schema": SERVER_BUILD_ATTESTATION_SCHEMA,
        "game_revision": revision,
        "source_tree_git_oid": source_tree_oid,
        "entrypoint": build_record["entrypoint"],
        "entrypoint_sha256": bundle_sha256,
        "build_attestation_path": str(build_record_path),
        "build_attestation_sha256": hashlib.sha256(build_record_path.read_bytes()).hexdigest(),
    }



def seal_cell_bundle(plan: ExperimentPlan, cell: Cell) -> Path:
    """Hash and seal the complete model-visible and raw evidence for one cell."""
    cell_root = Path(cell.run_dir) / cell.cell_id
    raw_dirs = [cell_root / f"episode_{episode:03d}_raw" for episode in range(1, plan.episodes + 1)]
    required: list[tuple[str, Path]] = [
        ("results", cell_root / "results.json"),
        ("resolved_prompt", cell_root / "system_prompt.md"),
        ("launcher_log", Path(cell.run_dir) / "launcher.log"),
    ]
    for episode in range(1, plan.episodes + 1):
        required.extend([
            (f"episode_{episode:03d}_parsed_transcript", cell_root / f"episode_{episode:03d}.jsonl"),
            (f"episode_{episode:03d}_raw_sessions", raw_dirs[episode - 1]),
            (f"episode_{episode:03d}_state_boundary", cell_root / f"episode_{episode:03d}_state.json"),
        ])
    missing = [str(path) for _name, path in required if not path.exists()]
    if missing:
        raise ManifestError(
            f"cell {cell.cell_id} cannot be sealed; missing required artifacts: {missing}"
        )

    for raw_dir in raw_dirs:
        raw_logs = sorted(raw_dir.glob("session_*.log"))
        if not raw_logs:
            raise ManifestError(f"cell {cell.cell_id} has no preserved raw session log")
        has_raw_emission = False
        for raw_log in raw_logs:
            for line in raw_log.read_text().splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") == "raw_model_emission":
                    has_raw_emission = True
                    break
            if has_raw_emission:
                break
        if not has_raw_emission:
            raise ManifestError(
                f"cell {cell.cell_id} raw sessions omit pre-rewrite model emissions"
            )

    artifacts = []
    for name, path in required:
        descriptor = hash_path(path, root=Path(cell.run_dir).parent)
        descriptor["name"] = name
        artifacts.append(descriptor)
    record: dict[str, Any] = {
        "schema_version": CELL_BUNDLE_SCHEMA,
        "created_at_utc": utc_now(),
        "experiment_id": plan.experiment_id,
        "protocol_id": plan.protocol_id,
        "cell_id": cell.cell_id,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "endpoint_attestation_sha256": cell.endpoint_attestation_sha256,
        "checkpoint_sha256": cell.checkpoint_sha256,
        "tokenizer_sha256": cell.tokenizer_sha256,
        "render_contract_sha256": cell.render_contract_sha256,
        "artifacts": artifacts,
    }
    record["bundle_sha256"] = sha256_json(record)
    path = cell_root / "cell-bundle.json"
    atomic_write_json(path, record)
    return path


def validate_cell_bundle(plan: ExperimentPlan, cell: Cell) -> dict[str, Any]:
    """Revalidate bundle identity, attribution, and every sealed artifact."""
    bundle_path = Path(cell.run_dir) / cell.cell_id / "cell-bundle.json"
    bundle = load_json(bundle_path)
    if not isinstance(bundle, dict) or bundle.get("schema_version") != CELL_BUNDLE_SCHEMA:
        raise ManifestError(f"cell {cell.cell_id} has no valid sealed bundle")
    payload = dict(bundle)
    recorded_digest = payload.pop("bundle_sha256", None)
    if recorded_digest != sha256_json(payload):
        raise ManifestError(f"cell {cell.cell_id} bundle identity mismatch")
    expected = {
        "experiment_id": plan.experiment_id,
        "protocol_id": plan.protocol_id,
        "cell_id": cell.cell_id,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "endpoint_attestation_sha256": cell.endpoint_attestation_sha256,
        "checkpoint_sha256": cell.checkpoint_sha256,
        "tokenizer_sha256": cell.tokenizer_sha256,
        "render_contract_sha256": cell.render_contract_sha256,
    }
    mismatches = {
        key: {"expected": value, "actual": bundle.get(key)}
        for key, value in expected.items() if bundle.get(key) != value
    }
    if mismatches:
        raise ManifestError(f"cell {cell.cell_id} bundle attribution mismatch: {mismatches}")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ManifestError(f"cell {cell.cell_id} bundle has no artifacts")
    names = [artifact.get("name") for artifact in artifacts if isinstance(artifact, dict)]
    if len(names) != len(artifacts) or len(names) != len(set(names)):
        raise ManifestError(f"cell {cell.cell_id} bundle artifact names are invalid or duplicated")
    root = Path(cell.run_dir).parent
    errors = []
    for index, artifact in enumerate(artifacts):
        errors.extend(verify_descriptor(artifact, root, f"cell {cell.cell_id} artifacts[{index}]"))
    if errors:
        raise ManifestError("; ".join(errors))
    return bundle


def seal_completed_inventory(plan: ExperimentPlan) -> Path:
    """Seal the exact requested/completed-cell inventory after every cell passes."""
    cells = []
    for cell in plan.cells:
        bundle_path = Path(cell.run_dir) / cell.cell_id / "cell-bundle.json"
        bundle = validate_cell_bundle(plan, cell)
        recorded_digest = bundle["bundle_sha256"]
        cells.append({
            "cell_id": cell.cell_id,
            "bundle_sha256": recorded_digest,
            "bundle_file": hash_path(bundle_path, root=Path(cell.run_dir).parent),
        })
    record: dict[str, Any] = {
        "schema_version": COMPLETED_INVENTORY_SCHEMA,
        "created_at_utc": utc_now(),
        "experiment_id": plan.experiment_id,
        "protocol_id": plan.protocol_id,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "requested_cell_ids": [cell.cell_id for cell in plan.cells],
        "completed_cells": cells,
    }
    record["inventory_sha256"] = sha256_json(record)
    path = Path(plan.cells[0].run_dir).parent / "completed.json"
    atomic_write_json(path, record)
    return path

def validate_completed_inventory(plan: ExperimentPlan) -> dict[str, Any]:
    """Require an exact, untampered inventory and revalidate every cell bundle."""
    path = Path(plan.cells[0].run_dir).parent / "completed.json"
    try:
        inventory = load_json(path)
    except ManifestError as exc:
        raise ManifestError(f"completed factorial inventory is missing or unreadable: {exc}") from exc
    if not isinstance(inventory, dict) or inventory.get("schema_version") != COMPLETED_INVENTORY_SCHEMA:
        raise ManifestError("completed factorial inventory is missing or invalid")
    payload = dict(inventory)
    identity = payload.pop("inventory_sha256", None)
    if identity != sha256_json(payload):
        raise ManifestError("completed factorial inventory identity mismatch")
    expected_ids = [cell.cell_id for cell in plan.cells]
    if inventory.get("experiment_id") != plan.experiment_id \
            or inventory.get("protocol_id") != plan.protocol_id \
            or inventory.get("experiment_manifest_sha256") != plan.manifest_sha256 \
            or inventory.get("requested_cell_ids") != expected_ids:
        raise ManifestError("completed factorial inventory attribution or requested cells mismatch")
    completed = inventory.get("completed_cells")
    if not isinstance(completed, list) or [row.get("cell_id") for row in completed if isinstance(row, dict)] != expected_ids:
        raise ManifestError("completed factorial inventory is incomplete, duplicated, or reordered")
    for cell, row in zip(plan.cells, completed):
        bundle = validate_cell_bundle(plan, cell)
        if row.get("bundle_sha256") != bundle.get("bundle_sha256"):
            raise ManifestError(f"completed inventory bundle digest mismatch for {cell.cell_id}")
        errors = verify_descriptor(
            row.get("bundle_file"), Path(cell.run_dir).parent,
            f"completed inventory bundle {cell.cell_id}",
        )
        if errors:
            raise ManifestError("; ".join(errors))
    return inventory


def _health_url(endpoint: str, health_path: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ManifestError("endpoint environment variable must contain an HTTP(S) URL")
    base_path = parts.path.rstrip("/")
    if base_path.endswith("/v1"):
        base_path = base_path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, base_path + health_path, "", ""))


def validate_live_endpoint_attestations(
    plan: ExperimentPlan,
    env_source: dict[str, str],
    *,
    timeout_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Query every distinct deployment and require its frozen identity payload."""
    verified: list[dict[str, Any]] = []
    for model in plan.model_provenance:
        weight = model["weight"]
        if model["attestation_status"] != "attested":
            raise ManifestError(
                f"launch blocked: models.{weight}.endpoint_attestation is "
                f"{model['attestation_status']!r}; replace the example with an attested deployment"
            )
        if model["checkpoint_sha256"] == "0" * 64:
            raise ManifestError(
                f"launch blocked: models.{weight} still has an unresolved checkpoint digest"
            )
        endpoint_env = model["endpoint_env"]
        endpoint = env_source.get(endpoint_env, "")
        try:
            url = _health_url(endpoint, model["health_path"])
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ManifestError(
                f"launch blocked: {endpoint_env} did not return a valid endpoint attestation: "
                f"{type(exc).__name__}"
            ) from exc
        actual = payload.get("attestation") if isinstance(payload, dict) else None
        expected = model["expected_health"]
        if not isinstance(actual, dict):
            raise ManifestError(
                f"launch blocked: {endpoint_env} /health has no attestation object"
            )
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in expected.items()
            if actual.get(key) != value
        }
        if mismatches:
            raise ManifestError(
                f"launch blocked: {endpoint_env} identity attestation mismatch: {mismatches}"
            )
        verified.append({
            "weight": weight,
            "endpoint_env": endpoint_env,
            "endpoint_attestation_sha256": model["endpoint_attestation_sha256"],
            "expected_health": expected,
        })
    return verified


def seal_prelaunch_record(
    plan: ExperimentPlan,
    endpoint_attestations: list[dict[str, Any]],
    game_build_attestation: dict[str, str] | None = None,
) -> Path:
    """Create the immutable provenance ledger before any cell process starts."""
    current_manifest_sha = hash_path(plan.manifest, root=REPO)["sha256"]
    if current_manifest_sha != plan.manifest_sha256:
        raise ManifestError(
            "launch blocked: experiment manifest changed after preflight validation"
        )
    git = capture_git_state(REPO)
    if plan.source_git_commit.startswith(UNRESOLVED_MARKER):
        raise ManifestError(
            "launch blocked: protocol.source_git_commit is unresolved in the example manifest"
        )
    if git["commit"] != plan.source_git_commit:
        raise ManifestError(
            "launch blocked: current git commit does not match protocol.source_git_commit"
        )
    if git["dirty"]:
        raise ManifestError(
            "launch blocked: immutable provenance requires a clean worktree: "
            + ", ".join(git["dirty_paths"])
        )
    experiment_root = Path(plan.cells[0].run_dir).parent
    record: dict[str, Any] = {
        "schema_version": "kaetram.factorial-prelaunch.v1",
        "experiment_id": plan.experiment_id,
        "protocol_id": plan.protocol_id,
        "source_git": git,
        "experiment_manifest": {
            "path": plan.manifest,
            "sha256": plan.manifest_sha256,
        },
        "tool_schema": tool_schema_record(),
        "prompt_inputs": list(plan.prompt_inputs),
        "model_provenance": list(plan.model_provenance),
        "verified_endpoint_attestations": endpoint_attestations,
        "held_out": {
            "quest": plan.held_out_quest,
            "registration": plan.held_out_registration,
            "registration_sha256": plan.held_out_registration_sha256,
        },
        "analysis": {
            "primary_metric": plan.primary_metric,
            "primary_estimands": list(plan.primary_estimands),
            "familywise_alpha": plan.familywise_alpha,
            "planned_replicates": plan.planned_replicates,
            "power_analysis_artifact": plan.power_analysis_artifact,
            "power_analysis_sha256": plan.power_analysis_sha256,
        },
        "environment_rng": {
            "mechanism": plan.environment_seed_mechanism,
            "algorithm": plan.environment_rng_algorithm,
            "game_revision": plan.environment_game_revision,
            "replicate_seeds": list(plan.environment_seeds),
            "residual_nondeterminism": plan.environment_seed_reason,
            "server_build": game_build_attestation,
        },
    }
    record["prelaunch_sha256"] = sha256_json(record)
    path = experiment_root / "prelaunch.json"
    atomic_write_json(path, record)
    return path


def _cleanup_processes(processes: list[tuple[Cell, subprocess.Popen, Any]]) -> None:
    """Best-effort cleanup for every owned child process group and launcher log."""
    for _cell, proc, log_handle in processes:
        try:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        except BaseException:
            pass
        try:
            log_handle.close()
        except BaseException:
            pass


def launch(plan: ExperimentPlan, *, confirmation: str, environ: dict[str, str] | None = None) -> int:
    if not plan.allow_launch:
        raise ManifestError("launch blocked: set execution.allow_launch=true in the reviewed manifest")
    if confirmation != plan.experiment_id:
        raise ManifestError("launch blocked: --confirm-launch must exactly match experiment_id")
    env_source = dict(os.environ if environ is None else environ)
    game_build_attestation = require_environment_seed_capability(plan, env_source)
    plan = replace(
        plan,
        environment_game_bundle_sha256=game_build_attestation["entrypoint_sha256"],
    )
    env_source["KAETRAM_GAME_BUNDLE_SHA256"] = plan.environment_game_bundle_sha256
    missing = sorted({c.endpoint_env for c in plan.cells if not env_source.get(c.endpoint_env)})
    if missing:
        raise ManifestError(f"launch blocked: missing endpoint environment variables: {', '.join(missing)}")

    existing = [cell.run_dir for cell in plan.cells if Path(cell.run_dir).exists()]
    if existing:
        raise ManifestError(
            "launch blocked: refusing to reuse existing run directories: " + ", ".join(existing)
        )
    verified_attestations = validate_live_endpoint_attestations(plan, dict(env_source))
    seal_prelaunch_record(plan, verified_attestations, game_build_attestation)
    return_code = 0
    for start in range(0, len(plan.cells), plan.max_parallel):
        batch_cells = plan.cells[start:start + plan.max_parallel]
        processes: list[tuple[Cell, subprocess.Popen, Any]] = []
        try:
            for cell in batch_cells:
                run_dir = Path(cell.run_dir)
                run_dir.mkdir(parents=True, exist_ok=False)
                log_handle = (run_dir / "launcher.log").open("w", encoding="utf-8")
                try:
                    child_env = dict(env_source)
                    child_env["KAETRAM_TOOL_SCHEMA_SOURCE"] = plan.tool_schema_source
                    if cell.recovery:
                        child_env["KAETRAM_TOOL_RECOVERY"] = "1"
                    else:
                        child_env.pop("KAETRAM_TOOL_RECOVERY", None)
                    proc = subprocess.Popen(
                        cell_command(plan, cell),
                        cwd=REPO,
                        env=child_env,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except BaseException:
                    log_handle.close()
                    raise
                processes.append((cell, proc, log_handle))
        except BaseException:
            _cleanup_processes(processes)
            raise

        validation_errors = []
        for cell, proc, log_handle in processes:
            rc = proc.wait()
            log_handle.close()
            if rc != 0 and return_code == 0:
                return_code = rc
            elif rc == 0:
                try:
                    validate_cell_result(plan, cell)
                    seal_cell_bundle(plan, cell)
                except ManifestError as exc:
                    validation_errors.append(str(exc))
        if validation_errors:
            raise ManifestError(
                "launch blocked after incomplete cell results: " + "; ".join(validation_errors)
            )
        if return_code != 0:
            break
    if return_code == 0:
        seal_completed_inventory(plan)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--write-plan", type=Path, help="write the validated preflight plan as JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="validate and print every cell/command without resolving endpoints or launching (default)",
    )
    mode.add_argument("--execute", action="store_true", help="launch all cells after safety checks")
    parser.add_argument("--confirm-launch", default="", help="must exactly match experiment_id with --execute")
    args = parser.parse_args()
    try:
        plan = build_plan(args.manifest)
        payload = plan_dict(plan)
        print(json.dumps(payload, indent=2))
        if args.write_plan:
            atomic_write_json(args.write_plan, payload)
        if not args.execute:
            print("\nPreflight passed. Nothing was launched.")
            return 0
        def interrupt_launch(signum, _frame):
            raise ManifestError(f"launch interrupted by signal {signum}")

        previous_sigterm = signal.signal(signal.SIGTERM, interrupt_launch)
        try:
            return launch(plan, confirmation=args.confirm_launch)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    except ManifestError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
