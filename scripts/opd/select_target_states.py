#!/usr/bin/env python3
"""Freeze targeted persistent player-state and matched-control curricula for OPD."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import subprocess
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, assert_text_not_reserved, load_registration  # noqa: E402


class SelectionError(ValueError):
    pass


REQUIRED_VALIDITY = ("legal_reachable", "internally_consistent", "e2e_seed_verified")
SNAPSHOT_FIELDS = {
    "position", "hit_points", "mana", "inventory", "bank", "equipment",
    "quests", "achievements", "skills", "statistics", "player_info_overrides",
}
SNAPSHOT_LIST_FIELDS = {
    "inventory", "bank", "equipment", "quests", "achievements", "skills",
}
FORBIDDEN_INFO_OVERRIDES = {
    "username", "password", "email", "x", "y", "hitPoints", "mana",
}
REQUIRED_COUNTS = (
    "student_visits", "natural_student_rollouts",
    "teacher_successes", "teacher_trials",
    "student_successes", "student_trials",
    "recoveries", "recovery_trials",
)
TRIAL_SPECS = {
    "visitation": ("natural_student_visitation", "student", "student_visits", "natural_student_rollouts"),
    "teacher_success": ("conditional_task_success", "teacher", "teacher_successes", "teacher_trials"),
    "student_success": ("conditional_task_success", "student", "student_successes", "student_trials"),
    "recovery": ("player_state_recovery", "student", "recoveries", "recovery_trials"),
}
REACHABILITY_METHODS = {"witness_trajectory", "invariant_certificate"}
REACHABILITY_RESULT_SCHEMA = "kaetram-player-state-reachability-check-v1"
LIVE_REPLAY_PROTOCOL = "kaetram-live-player-state-replay-v1"
TRIAL_SCHEMA = "kaetram-player-state-trials-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")

# This predicate intentionally covers only persistent, task-facing player state.
# It is not an equivalence relation over NPCs, resource nodes, shared maps, or
# other server/world state, none of which seed_player restores.
STATE_EQUIVALENCE_SPEC = {
    "id": "kaetram-persistent-player-state-equivalence-v1",
    "position_cell_size": 32,
    "included": [
        "position_cell", "inventory", "bank", "equipment", "quests",
        "achievements", "skills",
    ],
    "excluded": ["hit_points", "mana", "statistics", "player_info_overrides"],
    "normalization": "canonical-json-sort-lists-by-canonical-form",
}
STATE_EQUIVALENCE_REVISION = hashlib.sha256(
    json.dumps(STATE_EQUIVALENCE_SPEC, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()
PROGRESS_SPEC = {
    "id": "kaetram-persistent-player-progress-v1",
    "included": ["quests", "achievements", "skills"],
    "normalization": "canonical-json-sort-lists-by-canonical-form",
}
PROGRESS_REVISION = hashlib.sha256(
    json.dumps(PROGRESS_SPEC, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_list(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(value, key=lambda item: _canonical_bytes(item))


def state_equivalence(snapshot: dict[str, Any]) -> dict[str, str]:
    """Return the frozen player-state equivalence identity used by visitation trials."""
    projection = {
        "position_cell": [
            math.floor(snapshot["position"][0] / STATE_EQUIVALENCE_SPEC["position_cell_size"]),
            math.floor(snapshot["position"][1] / STATE_EQUIVALENCE_SPEC["position_cell_size"]),
        ],
        **{
            key: _canonical_list(snapshot[key])
            for key in ("inventory", "bank", "equipment", "quests", "achievements", "skills")
        },
    }
    return {
        "predicate_id": STATE_EQUIVALENCE_SPEC["id"],
        "predicate_revision": STATE_EQUIVALENCE_REVISION,
        "key": _sha256_bytes(_canonical_bytes(projection)),
    }


def progress_bin(snapshot: dict[str, Any]) -> dict[str, str]:
    """Return the frozen progress stratum; caller-supplied labels are not accepted."""
    projection = {
        key: _canonical_list(snapshot[key])
        for key in ("quests", "achievements", "skills")
    }
    return {
        "predicate_id": PROGRESS_SPEC["id"],
        "predicate_revision": PROGRESS_REVISION,
        "key": _sha256_bytes(_canonical_bytes(projection)),
    }


def _rate(numerator: int, denominator: int, label: str) -> float:
    if isinstance(numerator, bool) or not isinstance(numerator, int) or numerator < 0:
        raise SelectionError(f"{label} numerator must be a nonnegative integer")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator < 1:
        raise SelectionError(f"{label} denominator must be a positive integer")
    if numerator > denominator:
        raise SelectionError(f"{label} numerator cannot exceed denominator")
    return numerator / denominator


def _wilson_interval(successes: int, trials: int, confidence_level: float) -> tuple[float, float]:
    rate = _rate(successes, trials, "Wilson interval")
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2)
    denominator = 1 + z * z / trials
    centre = (rate + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _validate_snapshot(snapshot: dict[str, Any], state_id: str) -> None:
    position = snapshot["position"]
    if (
        not isinstance(position, list) or len(position) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in position)
    ):
        raise SelectionError(f"candidate {state_id} snapshot.position must be two integers")
    for key in ("hit_points", "mana"):
        value = snapshot[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SelectionError(f"candidate {state_id} snapshot.{key} must be nonnegative integer")
    for key in SNAPSHOT_LIST_FIELDS:
        value = snapshot[key]
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise SelectionError(f"candidate {state_id} snapshot.{key} must be a list of objects")
    if not isinstance(snapshot["statistics"], dict):
        raise SelectionError(f"candidate {state_id} snapshot.statistics must be an object")
    overrides = snapshot["player_info_overrides"]
    if not isinstance(overrides, dict):
        raise SelectionError(f"candidate {state_id} snapshot.player_info_overrides must be an object")
    forbidden = sorted(FORBIDDEN_INFO_OVERRIDES & overrides.keys())
    if forbidden:
        raise SelectionError(
            f"candidate {state_id} player_info_overrides cannot replace authoritative fields: {forbidden}"
        )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SelectionError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _read_json_artifact(path: Path, label: str) -> dict[str, Any]:
    try:
        artifact = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelectionError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(artifact, dict):
        raise SelectionError(f"{label} must contain a JSON object")
    return artifact


def _execute_reachability_checker(
    checker: dict[str, Any], evidence_path: Path, *, method: str,
    canonical_start: str, snapshot_sha256: str, state_id: str,
) -> dict[str, Any]:
    command = [
        sys.executable, str(checker["_path"]),
        "--artifact", str(evidence_path),
        "--method", method,
        "--canonical-start-sha256", canonical_start,
        "--target-snapshot-sha256", snapshot_sha256,
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=checker["timeout_seconds"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelectionError(f"candidate {state_id} reachability checker could not execute: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no checker diagnostic"
        raise SelectionError(
            f"candidate {state_id} reachability checker rejected evidence: {detail[:500]}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SelectionError(f"candidate {state_id} reachability checker returned invalid JSON") from exc
    expected = {
        "schema_version": REACHABILITY_RESULT_SCHEMA,
        "status": "passed",
        "method": method,
        "checker_sha256": checker["sha256"],
        "artifact_sha256": _sha256_file(evidence_path),
        "canonical_start_sha256": canonical_start,
        "target_snapshot_sha256": snapshot_sha256,
        "execution_environment": "live-isolated-service",
    }
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
        raise SelectionError(f"candidate {state_id} reachability checker result is not provenance-bound")
    if method == "witness_trajectory":
        if result.get("verification_kind") != "transition_replay":
            raise SelectionError(f"candidate {state_id} checker did not replay witness transitions")
        replayed = result.get("replayed_transition_count")
        if isinstance(replayed, bool) or not isinstance(replayed, int) or replayed < 1:
            raise SelectionError(f"candidate {state_id} checker replayed no witness transitions")
    else:
        if result.get("verification_kind") != "executed_invariant_checker":
            raise SelectionError(f"candidate {state_id} invariant checker was not executed")
        checked = result.get("checked_invariants")
        if not isinstance(checked, list) or not checked or not all(isinstance(item, str) and item for item in checked):
            raise SelectionError(f"candidate {state_id} checker executed no invariants")
    return result


def _validate_reachability_evidence(
    evidence_path: Path, evidence: dict[str, Any], *, snapshot_sha256: str,
    state_id: str, checkers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    method = evidence.get("method")
    if method not in REACHABILITY_METHODS:
        raise SelectionError(
            f"candidate {state_id} legal reachability method must be one of {sorted(REACHABILITY_METHODS)}"
        )
    checker = checkers.get(method)
    if checker is None:
        raise SelectionError(f"candidate {state_id} has no pinned executable checker for {method}")
    canonical_start = _require_sha256(
        evidence.get("canonical_start_sha256"), f"candidate {state_id} canonical_start_sha256",
    )
    artifact = _read_json_artifact(evidence_path, f"candidate {state_id} reachability artifact")
    if artifact.get("schema_version") != 2:
        raise SelectionError(f"candidate {state_id} reachability artifact schema_version must be 2")
    if artifact.get("method") != method:
        raise SelectionError(f"candidate {state_id} reachability method mismatch")
    if artifact.get("canonical_start_sha256") != canonical_start:
        raise SelectionError(f"candidate {state_id} canonical start digest mismatch")
    if artifact.get("target_snapshot_sha256") != snapshot_sha256:
        raise SelectionError(f"candidate {state_id} reachability target digest mismatch")
    path_digests = artifact.get("path_state_sha256s")
    if not isinstance(path_digests, list) or len(path_digests) < 2:
        raise SelectionError(f"candidate {state_id} reachability artifact requires a nontrivial path")
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in path_digests):
        raise SelectionError(f"candidate {state_id} reachability path digests are invalid")
    if path_digests[0] != canonical_start or path_digests[-1] != snapshot_sha256:
        raise SelectionError(f"candidate {state_id} reachability path must connect canonical start to target")
    live_replay = artifact.get("checker_protocol") == LIVE_REPLAY_PROTOCOL
    transitions = artifact.get("transitions")
    if method == "witness_trajectory" or live_replay:
        if not isinstance(transitions, list) or not transitions:
            raise SelectionError(f"candidate {state_id} witness requires executable transitions")
        if not live_replay and len(transitions) != len(path_digests) - 1:
            raise SelectionError(f"candidate {state_id} witness transitions must cover every path edge")
        for index, transition in enumerate(transitions):
            action = transition.get("action") if isinstance(transition, dict) else None
            if not isinstance(transition, dict) or not isinstance(action, (str, dict)):
                raise SelectionError(f"candidate {state_id} witness transition {index} requires an action")
            if live_replay:
                if (
                    not isinstance(action, dict) or set(action) != {"tool", "arguments"}
                    or not isinstance(action.get("tool"), str)
                    or not isinstance(action.get("arguments"), dict)
                ):
                    raise SelectionError(
                        f"candidate {state_id} live replay transition {index} action is invalid"
                    )
                for digest_key in (
                    "before_observation_sha256", "tool_result_sha256", "after_observation_sha256",
                ):
                    _require_sha256(
                        transition.get(digest_key),
                        f"candidate {state_id} transition {index} {digest_key}",
                    )
            elif (
                transition.get("before_state_sha256") != path_digests[index]
                or transition.get("after_state_sha256") != path_digests[index + 1]
            ):
                raise SelectionError(f"candidate {state_id} witness transition {index} breaks path continuity")
    if method == "invariant_certificate":
        invariants = artifact.get("invariants")
        if not isinstance(invariants, list) or not invariants or not all(
            isinstance(item, str) and item.strip() for item in invariants
        ):
            raise SelectionError(f"candidate {state_id} certificate requires invariants")
    result = _execute_reachability_checker(
        checker, evidence_path, method=method, canonical_start=canonical_start,
        snapshot_sha256=snapshot_sha256, state_id=state_id,
    )
    if method == "witness_trajectory" or live_replay:
        expected_replayed = len(transitions) if live_replay else len(path_digests) - 1
        if result.get("replayed_transition_count") != expected_replayed:
            raise SelectionError(f"candidate {state_id} checker did not replay every witness transition")
    if live_replay:
        if result.get("runtime") != artifact.get("runtime"):
            raise SelectionError(f"candidate {state_id} checker runtime attestation diverged")
        executed_trace = result.get("executed_trace")
        if not isinstance(executed_trace, list) or len(executed_trace) != len(transitions):
            raise SelectionError(f"candidate {state_id} checker did not return the complete executed trace")
        trace_keys = (
            "action", "before_observation_sha256", "tool_result_sha256",
            "after_observation_sha256",
        )
        for index, (expected, observed) in enumerate(zip(transitions, executed_trace, strict=True)):
            if not isinstance(observed, dict) or any(
                observed.get(key) != expected.get(key) for key in trace_keys
            ):
                raise SelectionError(f"candidate {state_id} executed trace {index} diverged")
        persistent = result.get("final_persistent_player_state")
        if (
            not isinstance(persistent, dict) or persistent.get("matches_target") is not True
            or persistent.get("actual_sha256") != persistent.get("expected_sha256")
        ):
            raise SelectionError(f"candidate {state_id} final persistent player state diverged")
    if method == "invariant_certificate" and sorted(result["checked_invariants"]) != sorted(artifact["invariants"]):
        raise SelectionError(f"candidate {state_id} checker did not execute every declared invariant")
    return result


def _resolve_hashed_file(raw_path: Any, digest: Any, *, base: Path, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SelectionError(f"{label}.path is required")
    expected = _require_sha256(digest, f"{label}.sha256")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (base / path).resolve()
    try:
        observed = _sha256_file(path)
    except OSError as exc:
        raise SelectionError(f"{label} is unavailable: {path}: {exc}") from exc
    if observed != expected:
        raise SelectionError(f"{label} digest mismatch")
    return path


def load_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionError(f"cannot load config {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise SelectionError("config schema_version must be 2")
    experiment_id = raw.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", experiment_id):
        raise SelectionError("config experiment_id is invalid")
    thresholds = raw.get("thresholds")
    if not isinstance(thresholds, dict):
        raise SelectionError("config thresholds must be an object")
    for key in (
        "max_student_visit_rate", "min_teacher_success_rate",
        "min_teacher_student_success_gap", "min_recovery_rate",
    ):
        value = thresholds.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise SelectionError(f"thresholds.{key} must be between zero and one")
    confidence_level = raw.get("confidence_level")
    if isinstance(confidence_level, bool) or not isinstance(confidence_level, (int, float)) or not 0.5 < confidence_level < 1:
        raise SelectionError("config confidence_level must be between 0.5 and 1")
    minimum_trials = raw.get("minimum_trials")
    if not isinstance(minimum_trials, dict):
        raise SelectionError("config minimum_trials must be an object")
    for key in ("natural_student_rollouts", "teacher_trials", "student_trials", "recovery_trials"):
        value = minimum_trials.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 20:
            raise SelectionError(f"minimum_trials.{key} must be an integer of at least 20")
    max_states = raw.get("max_states")
    if isinstance(max_states, bool) or not isinstance(max_states, int) or max_states < 1:
        raise SelectionError("config max_states must be a positive integer")
    seed = raw.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SelectionError("config random_seed must be an integer")
    registration_raw = raw.get("held_out_registration")
    if not isinstance(registration_raw, str) or not registration_raw:
        raise SelectionError("config held_out_registration is required")
    registration = Path(registration_raw)
    if not registration.is_absolute():
        registration = (REPO / registration).resolve()
    try:
        load_registration(registration)
    except HeldOutGuardError as exc:
        raise SelectionError(str(exc)) from exc
    raw_checkers = raw.get("reachability_checkers")
    if not isinstance(raw_checkers, dict):
        raise SelectionError("config reachability_checkers must be an object")
    checkers: dict[str, dict[str, Any]] = {}
    for method, checker in raw_checkers.items():
        if method not in REACHABILITY_METHODS or not isinstance(checker, dict):
            raise SelectionError(f"invalid reachability checker entry: {method}")
        timeout = checker.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
            raise SelectionError(f"reachability_checkers.{method}.timeout_seconds must be 1..300")
        checker_path = _resolve_hashed_file(
            checker.get("path"), checker.get("sha256"), base=path.parent,
            label=f"reachability_checkers.{method}",
        )
        checkers[method] = {**checker, "_path": checker_path}
    return {**raw, "_registration_path": registration, "_reachability_checkers": checkers}


def _validate_trial_artifact(
    artifact_path: Path, *, state_id: str, snapshot_sha256: str,
    equivalence: dict[str, str], evidence_name: str,
) -> tuple[int, int, dict[str, Any]]:
    expected_kind, expected_role, _, _ = TRIAL_SPECS[evidence_name]
    artifact = _read_json_artifact(artifact_path, f"candidate {state_id} {evidence_name} trial artifact")
    expected = {
        "schema_version": TRIAL_SCHEMA,
        "kind": expected_kind,
        "state_id": state_id,
        "snapshot_sha256": snapshot_sha256,
        "state_equivalence": equivalence,
    }
    if any(artifact.get(key) != value for key, value in expected.items()):
        raise SelectionError(f"candidate {state_id} {evidence_name} trial artifact is not state-bound")
    policy = artifact.get("policy")
    if not isinstance(policy, dict) or policy.get("role") != expected_role:
        raise SelectionError(f"candidate {state_id} {evidence_name} policy role mismatch")
    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"].strip():
        raise SelectionError(f"candidate {state_id} {evidence_name} policy_id is required")
    _require_sha256(policy.get("checkpoint_sha256"), f"candidate {state_id} {evidence_name} checkpoint")
    history = artifact.get("history_constructor")
    if not isinstance(history, dict) or not isinstance(history.get("id"), str) or not history["id"].strip():
        raise SelectionError(f"candidate {state_id} {evidence_name} history constructor is required")
    _require_sha256(history.get("revision"), f"candidate {state_id} {evidence_name} history revision")
    horizon = artifact.get("horizon")
    if (
        not isinstance(horizon, dict) or isinstance(horizon.get("value"), bool)
        or not isinstance(horizon.get("value"), int) or horizon["value"] < 1
        or horizon.get("unit") not in {"turns", "environment_steps"}
    ):
        raise SelectionError(f"candidate {state_id} {evidence_name} horizon is invalid")
    success = artifact.get("success_definition")
    if not isinstance(success, dict) or not isinstance(success.get("id"), str) or not success["id"].strip():
        raise SelectionError(f"candidate {state_id} {evidence_name} success definition is required")
    _require_sha256(success.get("revision"), f"candidate {state_id} {evidence_name} success revision")
    seeds = artifact.get("seeds")
    outcomes = artifact.get("outcomes")
    if (
        not isinstance(seeds, list) or not seeds
        or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise SelectionError(f"candidate {state_id} {evidence_name} seeds must be unique integers")
    if not isinstance(outcomes, list) or len(outcomes) != len(seeds) or not all(isinstance(item, bool) for item in outcomes):
        raise SelectionError(f"candidate {state_id} {evidence_name} outcomes must align one-to-one with seeds")
    return sum(outcomes), len(outcomes), artifact


def load_candidates(
    path: Path, *, registration_path: Path, checkers: dict[str, dict[str, Any]],
    minimum_trials: dict[str, int], confidence_level: float,
) -> list[dict[str, Any]]:
    path = path.resolve()
    candidates = []
    seen_ids: set[str] = set()
    seen_snapshots: set[str] = set()
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise SelectionError(f"cannot read candidates {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionError(f"candidate line {line_number} is invalid JSON: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 2:
            raise SelectionError(f"candidate line {line_number} schema_version must be 2")
        state_id = raw.get("state_id")
        if not isinstance(state_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,95}", state_id):
            raise SelectionError(f"candidate line {line_number} has invalid state_id")
        if state_id in seen_ids:
            raise SelectionError(f"duplicate state_id: {state_id}")
        seen_ids.add(state_id)
        snapshot = raw.get("snapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            raise SelectionError(f"candidate {state_id} snapshot must be a nonempty object")
        missing_snapshot_fields = SNAPSHOT_FIELDS - snapshot.keys()
        unsupported_snapshot_fields = snapshot.keys() - SNAPSHOT_FIELDS
        if missing_snapshot_fields or unsupported_snapshot_fields:
            details = []
            if missing_snapshot_fields:
                details.append(f"missing {sorted(missing_snapshot_fields)}")
            if unsupported_snapshot_fields:
                details.append(f"unsupported {sorted(unsupported_snapshot_fields)}")
            raise SelectionError(
                f"candidate {state_id} snapshot must be a complete seed_player record: " + "; ".join(details)
            )
        _validate_snapshot(snapshot, state_id)
        try:
            assert_text_not_reserved(
                json.dumps(raw, sort_keys=True), use="training_seed", source=f"candidate {state_id}",
                path=registration_path,
            )
        except HeldOutGuardError as exc:
            raise SelectionError(str(exc)) from exc
        snapshot_sha256 = _sha256_bytes(_canonical_bytes(snapshot))
        if snapshot_sha256 in seen_snapshots:
            raise SelectionError(f"duplicate persistent player snapshot under multiple IDs: {state_id}")
        seen_snapshots.add(snapshot_sha256)
        equivalence = state_equivalence(snapshot)
        if raw.get("state_equivalence") != equivalence:
            raise SelectionError(f"candidate {state_id} state_equivalence does not match the versioned predicate")
        computed_progress = progress_bin(snapshot)
        if raw.get("progress_bin") != computed_progress:
            raise SelectionError(f"candidate {state_id} progress_bin does not match the versioned calculation")
        validity = raw.get("validity")
        if not isinstance(validity, dict) or any(validity.get(key) is not True for key in REQUIRED_VALIDITY):
            raise SelectionError(f"candidate {state_id} must pass validity checks: {', '.join(REQUIRED_VALIDITY)}")
        validity_evidence = raw.get("validity_evidence")
        if not isinstance(validity_evidence, dict):
            raise SelectionError(f"candidate {state_id} requires validity_evidence")
        checker_result = None
        for key in REQUIRED_VALIDITY:
            evidence = validity_evidence.get(key)
            if not isinstance(evidence, dict):
                raise SelectionError(f"candidate {state_id} requires validity_evidence.{key}")
            evidence_path = _resolve_hashed_file(
                evidence.get("artifact_path"), evidence.get("artifact_sha256"), base=path.parent,
                label=f"candidate {state_id} validity_evidence.{key}",
            )
            if key == "legal_reachable":
                checker_result = _validate_reachability_evidence(
                    evidence_path, evidence, snapshot_sha256=snapshot_sha256,
                    state_id=state_id, checkers=checkers,
                )
        counts = raw.get("counts")
        if not isinstance(counts, dict) or any(key not in counts for key in REQUIRED_COUNTS):
            raise SelectionError(f"candidate {state_id} is missing required repeated-trial counts")
        trial_evidence = raw.get("trial_evidence")
        if not isinstance(trial_evidence, dict):
            raise SelectionError(f"candidate {state_id} requires trial_evidence")
        trial_artifacts: dict[str, dict[str, Any]] = {}
        verified_counts: dict[str, int] = {}
        for name, (_, _, numerator_key, denominator_key) in TRIAL_SPECS.items():
            evidence = trial_evidence.get(name)
            if not isinstance(evidence, dict):
                raise SelectionError(f"candidate {state_id} requires trial_evidence.{name}")
            artifact_path = _resolve_hashed_file(
                evidence.get("artifact_path"), evidence.get("artifact_sha256"), base=path.parent,
                label=f"candidate {state_id} trial_evidence.{name}",
            )
            numerator, denominator, artifact = _validate_trial_artifact(
                artifact_path, state_id=state_id, snapshot_sha256=snapshot_sha256,
                equivalence=equivalence, evidence_name=name,
            )
            if denominator < minimum_trials[denominator_key]:
                raise SelectionError(
                    f"candidate {state_id} {denominator_key} has {denominator} trials; "
                    f"minimum is {minimum_trials[denominator_key]}"
                )
            if counts.get(numerator_key) != numerator or counts.get(denominator_key) != denominator:
                raise SelectionError(f"candidate {state_id} {name} counts do not match hashed trial outcomes")
            verified_counts[numerator_key] = numerator
            verified_counts[denominator_key] = denominator
            trial_artifacts[name] = artifact
        teacher = trial_artifacts["teacher_success"]
        student = trial_artifacts["student_success"]
        for field in ("history_constructor", "horizon", "success_definition"):
            if teacher[field] != student[field]:
                raise SelectionError(f"candidate {state_id} teacher/student {field} mismatch")
        visit_rate = _rate(verified_counts["student_visits"], verified_counts["natural_student_rollouts"], f"{state_id} visitation")
        teacher_rate = _rate(verified_counts["teacher_successes"], verified_counts["teacher_trials"], f"{state_id} teacher success")
        student_rate = _rate(verified_counts["student_successes"], verified_counts["student_trials"], f"{state_id} student success")
        recovery_rate = _rate(verified_counts["recoveries"], verified_counts["recovery_trials"], f"{state_id} recovery")
        visit_lower, visit_upper = _wilson_interval(verified_counts["student_visits"], verified_counts["natural_student_rollouts"], confidence_level)
        teacher_lower, teacher_upper = _wilson_interval(verified_counts["teacher_successes"], verified_counts["teacher_trials"], confidence_level)
        student_lower, student_upper = _wilson_interval(verified_counts["student_successes"], verified_counts["student_trials"], confidence_level)
        recovery_lower, recovery_upper = _wilson_interval(verified_counts["recoveries"], verified_counts["recovery_trials"], confidence_level)
        for flag in ("task_relevant", "endpoint_already_completed"):
            if not isinstance(raw.get(flag), bool):
                raise SelectionError(f"candidate {state_id} {flag} must be boolean")
        source_kind = raw.get("source_kind")
        if source_kind not in {"direct_snapshot", "teacher_success_prefix", "student_failure", "valid_state_pool"}:
            raise SelectionError(f"candidate {state_id} has unsupported source_kind")
        source_run_ids = raw.get("source_run_ids")
        if not isinstance(source_run_ids, list) or not source_run_ids or not all(
            isinstance(item, str) and item for item in source_run_ids
        ):
            raise SelectionError(f"candidate {state_id} requires source_run_ids")
        candidates.append({
            **raw,
            "snapshot_sha256": snapshot_sha256,
            "reachability_checker_result": checker_result,
            "derived": {
                "confidence_level": confidence_level,
                "student_visit_rate": visit_rate,
                "student_visit_interval": [visit_lower, visit_upper],
                "teacher_success_rate": teacher_rate,
                "teacher_success_interval": [teacher_lower, teacher_upper],
                "student_success_rate": student_rate,
                "student_success_interval": [student_lower, student_upper],
                "teacher_student_success_gap": teacher_rate - student_rate,
                "conservative_teacher_student_success_gap": teacher_lower - student_upper,
                "recovery_rate": recovery_rate,
                "recovery_interval": [recovery_lower, recovery_upper],
            },
        })
    if not candidates:
        raise SelectionError("candidate file is empty")
    return candidates


def _rank_target(candidate: dict[str, Any]) -> tuple[Any, ...]:
    d = candidate["derived"]
    return (
        d["student_visit_interval"][1],
        -d["conservative_teacher_student_success_gap"],
        -d["teacher_success_interval"][0],
        candidate["state_id"],
    )


def _valid(candidate: dict[str, Any]) -> bool:
    return (
        candidate["task_relevant"] and not candidate["endpoint_already_completed"]
        and all(candidate["validity"].get(key) is True for key in REQUIRED_VALIDITY)
    )


def select_arms(candidates: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    thresholds = config["thresholds"]
    n = config["max_states"]
    valid = [candidate for candidate in candidates if _valid(candidate)]

    def low_visitation(candidate: dict[str, Any]) -> bool:
        return candidate["derived"]["student_visit_interval"][1] <= thresholds["max_student_visit_rate"]

    def teacher_advantage(candidate: dict[str, Any]) -> bool:
        return (
            candidate["derived"]["teacher_success_interval"][0] >= thresholds["min_teacher_success_rate"]
            and candidate["derived"]["conservative_teacher_student_success_gap"]
            >= thresholds["min_teacher_student_success_gap"]
        )

    targeted_pool = [
        candidate for candidate in valid
        if low_visitation(candidate) and teacher_advantage(candidate)
        and candidate["derived"]["recovery_interval"][0] >= thresholds["min_recovery_rate"]
    ]
    targeted = sorted(targeted_pool, key=_rank_target)[:n]
    if len(targeted) != n:
        raise SelectionError(f"targeted rule selected {len(targeted)} states; config requires {n}")

    rng = random.Random(config["random_seed"])
    target_ids = {candidate["state_id"] for candidate in targeted}
    control_pool = [candidate for candidate in valid if candidate["state_id"] not in target_ids]
    if len(control_pool) < n:
        raise SelectionError("random-valid control pool is smaller than the targeted arm")
    random_valid = rng.sample(sorted(control_pool, key=lambda candidate: candidate["state_id"]), n)

    visitation = sorted(
        [candidate for candidate in control_pool if low_visitation(candidate)],
        key=lambda candidate: (candidate["derived"]["student_visit_interval"][1], candidate["state_id"]),
    )[:n]
    teacher_only = sorted(
        [candidate for candidate in control_pool if teacher_advantage(candidate)],
        key=lambda candidate: (-candidate["derived"]["conservative_teacher_student_success_gap"], candidate["state_id"]),
    )[:n]
    if len(visitation) != n or len(teacher_only) != n:
        raise SelectionError("non-target single-factor ablation pools do not contain max_states candidates")

    progress_matched = []
    used: set[str] = set()
    for target in targeted:
        choices = [
            candidate for candidate in control_pool
            if candidate["progress_bin"] == target["progress_bin"] and candidate["state_id"] not in used
        ]
        if not choices:
            raise SelectionError(
                f"no unused progress-matched control for target {target['state_id']} "
                f"in bin {target['progress_bin']['key']!r}"
            )
        choice = rng.choice(sorted(choices, key=lambda candidate: candidate["state_id"]))
        used.add(choice["state_id"])
        progress_matched.append(choice)

    arms = {
        "targeted": targeted,
        "random_valid": random_valid,
        "progress_matched": progress_matched,
        "visitation_only": visitation,
        "teacher_advantage_only": teacher_only,
    }
    targeted_signature = tuple(sorted(target_ids))
    for name, rows in arms.items():
        if name != "targeted" and tuple(sorted(row["state_id"] for row in rows)) == targeted_signature:
            raise SelectionError(f"control arm {name} is identical to targeted")
    return arms


def build_selection(candidate_path: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path.resolve())
    candidates = load_candidates(
        candidate_path.resolve(), registration_path=config["_registration_path"],
        checkers=config["_reachability_checkers"], minimum_trials=config["minimum_trials"],
        confidence_level=config["confidence_level"],
    )
    arms = select_arms(candidates, config)
    config_public = {key: value for key, value in config.items() if not key.startswith("_")}
    return {
        "schema_version": "kaetram-target-player-state-selection-v2",
        "experiment_id": config["experiment_id"],
        "selection_rule": (
            "valid and task-relevant persistent player state; upper confidence bound on natural "
            "student visitation; lower confidence bounds on teacher success and recoverability; "
            "teacher lower bound minus student upper bound for conditional success gap"
        ),
        "state_equivalence_spec": STATE_EQUIVALENCE_SPEC,
        "state_equivalence_revision": STATE_EQUIVALENCE_REVISION,
        "progress_spec": PROGRESS_SPEC,
        "progress_revision": PROGRESS_REVISION,
        "candidate_file": str(candidate_path.resolve()),
        "candidate_file_sha256": _sha256_file(candidate_path.resolve()),
        "config_file": str(config_path.resolve()),
        "config_file_sha256": _sha256_file(config_path.resolve()),
        "config": config_public,
        "candidate_count": len(candidates),
        "arms": arms,
        "warnings": [
            "Selection freezes a training initializer; it is not an outcome.",
            "Snapshots restore persistent player state only, not full server/world state.",
            "All headline evaluation must begin from the original unseeded state.",
            "Reachability is accepted only after the config-pinned checker executes and returns provenance-bound evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise SelectionError(f"refusing to overwrite frozen selection: {args.out}")
        selection = build_selection(args.candidates, args.config)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n")
    except SelectionError as exc:
        parser.error(str(exc))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
