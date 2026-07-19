#!/usr/bin/env python3
"""Materialize one matched-training cell into a hash-pinned record bundle.

This adapter performs local data construction and emits an immutable trainer
plan.  It never invokes Modal or claims that weights were trained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.opd.matched_training import (  # noqa: E402
    EXPECTED_CONSTRUCTORS,
    FIRST_ERROR_ARMS,
    HISTORY_ABLATION_CONSTRUCTORS,
    HISTORY_ABLATION_IDS,
    INTERFACE_CONTRACT,
    PREFIX_ARMS,
    ProtocolError,
    REACHABILITY_ARMS,
    REGISTRY_SCHEMA,
    UNRESOLVED,
)


CELL_SCHEMA = "kaetram.matched-training-cell.v1"
SOURCE_SCHEMA = "kaetram.arm-source-record.v1"
NORMALIZED_SCHEMA = "kaetram.normalized-training-record.v1"
BACKEND_PLAN_SCHEMA = "kaetram.matched-training-backend-plan.v1"
BACKEND_RESULT_SCHEMA = "kaetram.matched-training-result.v2"
OPD_TRAINER = "finetune/train_opd_2b.py"
SFT_TRAINER = "finetune/train_modal.py"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ProtocolError(f"{label} fields must be exactly {sorted(expected)}")


def _digest(value: Any, *, label: str, nonzero: bool = True) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256")
    if nonzero and value == "0" * 64:
        raise ProtocolError(f"{label} must not be an unresolved zero digest")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProtocolError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{label} must be a nonnegative integer")
    return value


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"{label} must be finite numeric")
    return float(value)


def _inside_repo(path: Path, *, label: str) -> Path:
    path = path.resolve()
    try:
        path.relative_to(REPO)
    except ValueError as exc:
        raise ProtocolError(f"{label} must resolve inside the repository") from exc
    return path


def _artifact_root(value: Any) -> Path:
    if not isinstance(value, str) or not value or value.startswith(UNRESOLVED):
        raise ProtocolError("shared_contract.artifact_root must be a resolved absolute path")
    declared = Path(value)
    if not declared.is_absolute():
        raise ProtocolError("shared_contract.artifact_root must be absolute")
    if declared.is_symlink():
        raise ProtocolError("shared_contract.artifact_root must not be a symlink")
    root = declared.resolve()
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise ProtocolError("shared_contract.artifact_root must be a specific non-root directory")
    return root


def _material_path(uri: Any, *, artifact_root: Path, label: str) -> Path:
    if not isinstance(uri, str) or not uri.startswith("file:"):
        raise ProtocolError(f"{label} must be a file: URI for locally available material")
    raw = uri[len("file:"):]
    if raw.startswith("//"):
        raw = raw[2:]
    if not raw:
        raise ProtocolError(f"{label} is empty")
    candidate = Path(raw)
    unresolved = candidate if candidate.is_absolute() else artifact_root / candidate
    try:
        relative = unresolved.relative_to(artifact_root)
    except ValueError as exc:
        raise ProtocolError(f"{label} must resolve inside the declared artifact root") from exc
    if ".." in relative.parts:
        raise ProtocolError(f"{label} must not contain parent traversal")
    cursor = artifact_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ProtocolError(f"{label} must not traverse a symlink")
    path = unresolved.resolve()
    try:
        path.relative_to(artifact_root)
    except ValueError as exc:
        raise ProtocolError(f"{label} must resolve inside the declared artifact root") from exc
    if not path.exists():
        raise ProtocolError(f"{label} does not exist: {path}")
    return path


def _hash_material(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    if not path.is_dir():
        raise ProtocolError(f"unsupported artifact material: {path}")
    records = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ProtocolError(f"artifact directories must not contain symlinks: {item}")
        if item.is_file():
            records.append({"path": item.relative_to(path).as_posix(), "sha256": _sha256(item)})
    if not records:
        raise ProtocolError(f"artifact directory is empty: {path}")
    return _sha256_json(records)


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load {label} {path}: {exc}") from exc


def _verified_material(
    artifacts: dict[str, Any], artifact_id: str, *, artifact_root: Path,
    expected_kind: str | None = None,
) -> tuple[dict[str, Any], Path, str]:
    record = _mapping(artifacts.get(artifact_id), label=f"artifact {artifact_id}")
    if record.get("status") != "verified":
        raise ProtocolError(f"artifact {artifact_id} is not verified")
    if expected_kind is not None and record.get("kind") != expected_kind:
        raise ProtocolError(f"artifact {artifact_id} kind must be {expected_kind}")
    payload = _mapping(record.get("payload"), label=f"artifact {artifact_id}.payload")
    _exact_keys(payload, {"uri", "sha256"}, label=f"artifact {artifact_id}.payload")
    expected = _digest(payload.get("sha256"), label=f"artifact {artifact_id}.payload.sha256")
    path = _material_path(
        payload.get("uri"), artifact_root=artifact_root,
        label=f"artifact {artifact_id}.payload.uri",
    )
    actual = _hash_material(path)
    if actual != expected:
        raise ProtocolError(
            f"artifact {artifact_id} material SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return record, path, actual


def _interface_digest(shared: dict[str, Any]) -> str:
    if shared.get("interface_contract_id") != INTERFACE_CONTRACT:
        raise ProtocolError(f"interface_contract_id must be {INTERFACE_CONTRACT}")
    files = shared.get("frozen_interfaces")
    if not isinstance(files, list) or not files:
        raise ProtocolError("shared_contract.frozen_interfaces must be a non-empty list")
    normalized = []
    for index, raw in enumerate(files):
        item = _mapping(raw, label=f"frozen_interfaces[{index}]")
        _exact_keys(item, {"path", "sha256"}, label=f"frozen_interfaces[{index}]")
        candidate = Path(item["path"])
        path = _inside_repo(
            candidate if candidate.is_absolute() else REPO / candidate,
            label=f"frozen_interfaces[{index}].path",
        )
        expected = _digest(item["sha256"], label=f"frozen_interfaces[{index}].sha256")
        if not path.is_file() or _sha256(path) != expected:
            raise ProtocolError(f"frozen rendered interface drift: {path}")
        normalized.append({"path": path.relative_to(REPO).as_posix(), "sha256": expected})
    return _sha256_json({"contract_id": INTERFACE_CONTRACT, "files": normalized})


def _history_alias_scan(record: dict[str, Any], aliases: list[str], *, record_id: str) -> None:
    def string_values(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from string_values(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from string_values(nested)

    visible = tuple(string_values({
        "state": record["state"]["content"],
        "history": record["history"]["content"],
    }))
    leaked = sorted({
        alias
        for alias in aliases
        if any(alias.casefold() in value.casefold() for value in visible)
    })
    if leaked:
        raise ProtocolError(f"source record {record_id} leaks held-out alias(es): {leaked}")


def _validate_arrays(
    supervision: dict[str, Any],
    *,
    objective: str,
    record_id: str,
    tokenizer_vocab_size: int,
    forbidden_token_sequences: list[list[int]],
) -> int:
    _exact_keys(
        supervision,
        {"input_ids", "labels", "advantages", "behavior_logprobs", "step_weight"},
        label=f"record {record_id}.supervision",
    )
    input_ids = supervision["input_ids"]
    labels = supervision["labels"]
    if not isinstance(input_ids, list) or not input_ids or not all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < tokenizer_vocab_size
        for value in input_ids
    ):
        raise ProtocolError(
            f"record {record_id} input_ids must be non-empty in-vocabulary token IDs"
        )
    if not isinstance(labels, list) or len(labels) != len(input_ids) or not all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and (value == -100 or 0 <= value < tokenizer_vocab_size)
        for value in labels
    ):
        raise ProtocolError(
            f"record {record_id} labels must align and contain only -100 or in-vocabulary token IDs"
        )
    for sequence in forbidden_token_sequences:
        width = len(sequence)
        if any(input_ids[index:index + width] == sequence for index in range(len(input_ids) - width + 1)):
            raise ProtocolError(f"record {record_id} input_ids leak a held-out token sequence")
    action_tokens = sum(label != -100 for label in labels)
    if action_tokens < 1:
        raise ProtocolError(f"record {record_id} has no supervised action tokens")
    step_weight = _finite(supervision["step_weight"], label=f"record {record_id}.step_weight")
    if step_weight <= 0:
        raise ProtocolError(f"record {record_id}.step_weight must be positive")
    advantages = supervision["advantages"]
    behavior = supervision["behavior_logprobs"]
    if objective == "opd":
        for name, values in (("advantages", advantages), ("behavior_logprobs", behavior)):
            if not isinstance(values, list) or len(values) != len(input_ids) or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in values
            ):
                raise ProtocolError(f"record {record_id} {name} must align with input_ids")
    elif objective in {"sft", "score"}:
        if advantages is not None or behavior is not None:
            raise ProtocolError(
                f"{objective.upper()} record {record_id} must set OPD-only arrays to null"
            )
    else:
        raise ProtocolError(f"record {record_id} uses unsupported objective {objective!r}")
    return action_tokens


def _validate_semantics(
    arm: dict[str, Any], record: dict[str, Any], *, record_id: str
) -> dict[str, Any]:
    arm_id = arm["arm_id"]
    semantics = _mapping(record["semantics"], label=f"record {record_id}.semantics")
    mode = semantics.get("mode")
    digest_fields: tuple[str, ...] = ()
    required: set[str]
    if arm_id == "natural_opd":
        required = {"mode", "world_initialization", "rollout_id"}
        if mode != "natural" or semantics.get("world_initialization") != "canonical_unseeded":
            raise ProtocolError(f"record {record_id} is not a canonical natural rollout")
    elif arm_id == "targeted_persistent_state":
        required = {"mode", "snapshot_sha256", "reachability_evidence_sha256", "selector"}
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        if mode != "targeted" or semantics.get("selector") != "preregistered_targeted":
            raise ProtocolError(f"record {record_id} is not targeted-state evidence")
    elif arm_id == "random_valid_state":
        required = {
            "mode", "snapshot_sha256", "reachability_evidence_sha256", "pool_size",
            "selection_probability",
        }
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        if mode != "random_valid":
            raise ProtocolError(f"record {record_id} is not random-valid evidence")
        pool_size = _positive_int(semantics.get("pool_size"), label=f"record {record_id}.pool_size")
        probability = _finite(
            semantics.get("selection_probability"),
            label=f"record {record_id}.selection_probability",
        )
        if not math.isclose(probability, 1 / pool_size, rel_tol=0, abs_tol=1e-12):
            raise ProtocolError(f"record {record_id} random-valid selection is not uniform")
    elif arm_id == "progress_matched_state":
        required = {
            "mode", "snapshot_sha256", "reachability_evidence_sha256", "progress_vector",
            "matched_stratum", "match_distance",
        }
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        if mode != "progress_matched":
            raise ProtocolError(f"record {record_id} is not progress-matched evidence")
        vector = _mapping(semantics.get("progress_vector"), label=f"record {record_id}.progress_vector")
        if not vector or not all(
            isinstance(key, str) and key and isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value)
            for key, value in vector.items()
        ):
            raise ProtocolError(f"record {record_id} progress_vector is invalid")
        if not isinstance(semantics.get("matched_stratum"), str) or not semantics["matched_stratum"]:
            raise ProtocolError(f"record {record_id} matched_stratum is missing")
        if _finite(semantics.get("match_distance"), label=f"record {record_id}.match_distance") < 0:
            raise ProtocolError(f"record {record_id} match_distance must be nonnegative")
    elif arm_id in PREFIX_ARMS:
        required = {
            "mode", "prefix_id", "teacher_success_evidence_sha256", "distance_to_success",
        }
        digest_fields = ("teacher_success_evidence_sha256",)
        if mode != "teacher_success_prefix":
            raise ProtocolError(f"record {record_id} is not a teacher-success prefix")
        _nonnegative_int(
            semantics.get("distance_to_success"),
            label=f"record {record_id}.distance_to_success",
        )
    elif arm_id in {"visitation_only", "teacher_advantage_only"}:
        required = {
            "mode", "snapshot_sha256", "reachability_evidence_sha256", "selector",
        }
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        expected_mode = arm_id
        expected_selector = "preregistered_visitation_only" if arm_id == "visitation_only" \
            else "preregistered_teacher_advantage_only"
        if mode != expected_mode or semantics.get("selector") != expected_selector:
            raise ProtocolError(f"record {record_id} does not implement {arm_id}")
    elif arm_id == "corrected_interface_sft":
        required = {
            "mode", "teacher_trajectory_id", "teacher_action_evidence_sha256",
            "corrected_interface_contract_sha256",
        }
        digest_fields = (
            "teacher_action_evidence_sha256", "corrected_interface_contract_sha256",
        )
        if mode != "corrected_interface_teacher_trajectory":
            raise ProtocolError(f"record {record_id} is not a corrected-interface trajectory")
    elif arm_id == "score_first_error_prefixes":
        required = {
            "mode", "student_trajectory_id", "first_error_index",
            "first_error_evidence_sha256", "prefix_verifier_sha256",
        }
        digest_fields = ("first_error_evidence_sha256", "prefix_verifier_sha256")
        if mode != "verified_first_model_visible_error_prefix":
            raise ProtocolError(f"record {record_id} is not a verified first-error prefix")
        _nonnegative_int(
            semantics.get("first_error_index"), label=f"record {record_id}.first_error_index"
        )
    elif arm_id == "snapshot_minimal_history":
        required = {
            "mode", "snapshot_sha256", "reachability_evidence_sha256", "observation_count",
        }
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        if mode != "snapshot_minimal_history" or semantics.get("observation_count") != 1:
            raise ProtocolError(f"record {record_id} is not the minimal-history control")
    elif arm_id == "teacher_replay_authentic_prefix":
        required = {
            "mode", "reachability_evidence_sha256", "witness_trajectory_sha256",
            "terminal_state_sha256",
        }
        digest_fields = (
            "reachability_evidence_sha256", "witness_trajectory_sha256",
            "terminal_state_sha256",
        )
        if mode != "teacher_replay_authentic_prefix":
            raise ProtocolError(f"record {record_id} is not the teacher-replay history control")
        if semantics.get("terminal_state_sha256") != record["state"]["content_sha256"]:
            raise ProtocolError(f"record {record_id} authentic history does not terminate at its state")
    elif arm_id == "snapshot_matched_reconstructed_history":
        required = {"mode", "snapshot_sha256", "reachability_evidence_sha256"}
        digest_fields = ("snapshot_sha256", "reachability_evidence_sha256")
        if mode != "snapshot_matched_reconstructed_history":
            raise ProtocolError(f"record {record_id} is not the reconstructed-history control")
    elif arm_id == "backplay_witness_annealing":
        required = {
            "mode", "reachability_evidence_sha256", "witness_trajectory_sha256",
            "distance_to_success",
        }
        digest_fields = ("reachability_evidence_sha256", "witness_trajectory_sha256")
        if mode != "backplay_witness":
            raise ProtocolError(f"record {record_id} is not a Backplay witness state")
        _nonnegative_int(
            semantics.get("distance_to_success"),
            label=f"record {record_id}.distance_to_success",
        )
    else:  # pragma: no cover - arm IDs are frozen by the launcher
        raise ProtocolError(f"unsupported arm {arm_id}")
    _exact_keys(semantics, required, label=f"record {record_id}.semantics")
    for field in digest_fields:
        _digest(semantics[field], label=f"record {record_id}.semantics.{field}")
    return semantics


def _validate_source_record(
    raw: Any,
    *,
    cell: dict[str, Any],
    arm: dict[str, Any],
    aliases: list[str],
    tokenizer_vocab_size: int,
    forbidden_token_sequences: list[list[int]],
    render_sha: str,
    source_artifact_id: str,
    source_payload_sha: str,
) -> dict[str, Any]:
    record = _mapping(raw, label="source record")
    _exact_keys(
        record,
        {
            "schema_version", "record_id", "identities", "state", "history",
            "supervision", "budget_usage", "semantics",
        },
        label="source record",
    )
    if record.get("schema_version") != SOURCE_SCHEMA:
        raise ProtocolError(f"source record schema_version must be {SOURCE_SCHEMA}")
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ProtocolError("source record_id must be non-empty")
    identities = _mapping(record["identities"], label=f"record {record_id}.identities")
    expected_identities = {
        "base_checkpoint_artifact_id": cell["shared_contract"]["base_checkpoint_artifact_id"],
        "teacher_artifact_id": cell["shared_contract"]["teacher_artifact_id"],
        "render_contract_sha256": render_sha,
        "held_out_registration_artifact_id": cell["shared_contract"][
            "held_out_registration_artifact_id"
        ],
    }
    if identities != expected_identities:
        raise ProtocolError(f"source record {record_id} identity mismatch")

    state = _mapping(record["state"], label=f"record {record_id}.state")
    history = _mapping(record["history"], label=f"record {record_id}.history")
    _exact_keys(state, {"kind", "constructor", "content", "content_sha256"}, label=f"record {record_id}.state")
    _exact_keys(history, {"kind", "source", "content", "content_sha256"}, label=f"record {record_id}.history")
    constructors = {**EXPECTED_CONSTRUCTORS, **HISTORY_ABLATION_CONSTRUCTORS}
    expected = constructors.get(arm["arm_id"])
    if expected is None:
        raise ProtocolError(f"unsupported arm {arm['arm_id']}")
    if (state["kind"], state["constructor"], history["kind"], history["source"]) != expected:
        raise ProtocolError(f"source record {record_id} state/history constructor mismatch")
    for name, container in (("state", state), ("history", history)):
        actual = _sha256_json(container["content"])
        if container["content_sha256"] != actual:
            raise ProtocolError(f"source record {record_id} {name} content digest mismatch")
    _history_alias_scan(record, aliases, record_id=record_id)

    supervision = _mapping(record["supervision"], label=f"record {record_id}.supervision")
    action_tokens = _validate_arrays(
        supervision,
        objective=arm["objective"],
        record_id=record_id,
        tokenizer_vocab_size=tokenizer_vocab_size,
        forbidden_token_sequences=forbidden_token_sequences,
    )
    usage = _mapping(record["budget_usage"], label=f"record {record_id}.budget_usage")
    _exact_keys(
        usage,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label=f"record {record_id}.budget_usage",
    )
    normalized_usage = {
        "action_tokens": _nonnegative_int(usage["action_tokens"], label=f"record {record_id}.action_tokens"),
        "teacher_scoring_tokens": _nonnegative_int(
            usage["teacher_scoring_tokens"], label=f"record {record_id}.teacher_scoring_tokens"
        ),
        "environment_interactions": _nonnegative_int(
            usage["environment_interactions"], label=f"record {record_id}.environment_interactions"
        ),
    }
    if normalized_usage["action_tokens"] != action_tokens:
        raise ProtocolError(f"source record {record_id} action-token accounting mismatch")
    semantics = _validate_semantics(arm, record, record_id=record_id)
    source_record_sha = _sha256_json(record)
    return {
        "schema_version": NORMALIZED_SCHEMA,
        "record_id": record_id,
        "cell_id": cell["cell_id"],
        "arm_id": arm["arm_id"],
        "role": arm["role"],
        "objective": arm["objective"],
        "training_seed": cell["training_seed"],
        "recovery": arm["recovery"],
        "identities": identities,
        "state": state,
        "history": history,
        "semantics": semantics,
        "input_ids": supervision["input_ids"],
        "labels": supervision["labels"],
        "advantages": supervision["advantages"],
        "behavior_logprobs": supervision["behavior_logprobs"],
        "step_weight": supervision["step_weight"],
        "budget_usage": normalized_usage,
        "source": {
            "artifact_id": source_artifact_id,
            "payload_sha256": source_payload_sha,
            "source_record_sha256": source_record_sha,
        },
        "curriculum": {},
    }


def _load_jsonl(path: Path) -> list[Any]:
    if not path.is_file():
        raise ProtocolError("selected training artifact must materialize as a JSONL file")
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    if not records:
        raise ProtocolError(f"selected training artifact is empty: {path}")
    return records


def _rank(seed: int, record_id: str) -> bytes:
    return hashlib.sha256(f"{seed}:{record_id}".encode()).digest()


def _order_records(records: list[dict[str, Any]], arm: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    arm_id = arm["arm_id"]
    if arm_id in {"tcod_b2f_prefixes", "backplay_witness_annealing"}:
        ordered = sorted(
            records,
            key=lambda record: (
                record["semantics"]["distance_to_success"],
                _rank(seed, record["record_id"]),
            ),
        )
    elif arm_id == "progress_matched_state":
        groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            groups.setdefault(record["semantics"]["matched_stratum"], []).append(record)
        for group in groups.values():
            group.sort(key=lambda record: _rank(seed, record["record_id"]))
        ordered = []
        strata = sorted(groups)
        while any(groups.values()):
            for stratum in strata:
                if groups[stratum]:
                    ordered.append(groups[stratum].pop(0))
    else:
        ordered = sorted(records, key=lambda record: _rank(seed, record["record_id"]))
    if arm_id == "tcod_b2f_prefixes":
        cfg = arm["tcod_b2f"]
        cumulative = 0
        total = sum(record["budget_usage"]["action_tokens"] for record in ordered)
        for record in ordered:
            progress = cumulative / total
            fraction = cfg["initial_success_fraction"] + progress * (
                cfg["final_success_fraction"] - cfg["initial_success_fraction"]
            )
            record["curriculum"] = {
                "kind": "tcod_b2f",
                "success_fraction": fraction,
                "distance_to_success": record["semantics"]["distance_to_success"],
            }
            cumulative += record["budget_usage"]["action_tokens"]
    elif arm_id == "guided_opd":
        cfg = arm["guided_annealing"]
        cumulative = 0
        for record in ordered:
            progress = cumulative / cfg["anneal_action_tokens"]
            probability = max(0.0, cfg["start_teacher_prefix_probability"] + progress * (
                cfg["end_teacher_prefix_probability"]
                - cfg["start_teacher_prefix_probability"]
            ))
            record["curriculum"] = {
                "kind": "guided_opd",
                "teacher_prefix_probability": probability,
                "schedule_position_action_tokens": cumulative,
            }
            cumulative += record["budget_usage"]["action_tokens"]
    elif arm_id == "backplay_witness_annealing":
        cfg = arm["backplay_annealing"]
        cumulative = 0
        for record in ordered:
            progress = cumulative / cfg["anneal_action_tokens"]
            fraction = min(1.0, cfg["start_distance_fraction"] + progress * (
                cfg["end_distance_fraction"] - cfg["start_distance_fraction"]
            ))
            record["curriculum"] = {
                "kind": "backplay_witness_annealing",
                "distance_fraction": fraction,
                "distance_to_success": record["semantics"]["distance_to_success"],
                "schedule_position_action_tokens": cumulative,
            }
            cumulative += record["budget_usage"]["action_tokens"]
    return ordered


def _budget_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(record["budget_usage"][key] for record in records)
        for key in ("action_tokens", "teacher_scoring_tokens", "environment_interactions")
    }


def _trainer_route(arm: dict[str, Any]) -> dict[str, Any]:
    arm_id = arm["arm_id"]
    if arm["objective"] == "sft":
        return {
            "entrypoint": SFT_TRAINER,
            "compatibility": "requires_sft_pretokenized_adapter",
            "reason": "existing SFT trainer consumes conversation records, not this normalized token contract",
        }
    if arm["objective"] == "score":
        return {
            "entrypoint": OPD_TRAINER,
            "compatibility": "requires_score_first_error_objective_extension",
            "reason": "existing trainers do not implement the SCoRe first-error-prefix objective",
        }
    if arm_id == "guided_opd":
        return {
            "entrypoint": OPD_TRAINER,
            "compatibility": "requires_guided_sampling_extension",
            "reason": "existing OPD trainer does not consume per-record teacher-prefix probabilities",
        }
    return {
        "entrypoint": OPD_TRAINER,
        "compatibility": "record_schema_compatible_not_executed",
        "reason": "normalized OPD arrays match the existing collator; Modal execution remains a separate reviewed step",
    }


def build_backend_plan(cell_config_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config_path = _inside_repo(Path(cell_config_path), label="cell config")
    cell = _mapping(_load_json(config_path, label="cell config"), label="cell config")
    _exact_keys(
        cell,
        {"schema_version", "experiment_id", "cell_id", "arm", "training_seed", "shared_contract"},
        label="cell config",
    )
    if cell.get("schema_version") != CELL_SCHEMA:
        raise ProtocolError(f"cell config schema_version must be {CELL_SCHEMA}")
    if not isinstance(cell.get("cell_id"), str) or not cell["cell_id"]:
        raise ProtocolError("cell_id must be non-empty")
    _positive_int(cell.get("training_seed"), label="training_seed")
    arm = _mapping(cell.get("arm"), label="cell arm")
    if arm.get("arm_id") == "guided_opd":
        raise ProtocolError(
            "Guided-OPD materialization is blocked until the reviewed live "
            "mixed-rollout collector and actor-conditional reverse-KL/forward-KL "
            "trainer are available"
        )
    shared = _mapping(cell.get("shared_contract"), label="shared_contract")
    _exact_keys(
        shared,
        {
            "source_git_commit", "experiment_manifest_sha256",
            "base_checkpoint_artifact_id", "teacher_artifact_id", "teacher_endpoint_env",
            "held_out_registration_artifact_id", "interface_contract_id", "frozen_interfaces",
            "optimizer", "budgets", "artifact_registry", "artifact_root",
        },
        label="shared_contract",
    )
    source_git = shared.get("source_git_commit")
    if not isinstance(source_git, str) or not (
        source_git.startswith(UNRESOLVED)
        or (len(source_git) == 40 and all(char in "0123456789abcdef" for char in source_git))
    ):
        raise ProtocolError("shared_contract.source_git_commit must be a full Git SHA")
    manifest_sha = _digest(
        shared.get("experiment_manifest_sha256"),
        label="shared_contract.experiment_manifest_sha256",
    )
    material_root = _artifact_root(shared.get("artifact_root"))
    render_sha = _interface_digest(shared)
    registry_ref = _mapping(shared["artifact_registry"], label="artifact_registry")
    _exact_keys(registry_ref, {"path", "sha256"}, label="artifact_registry")
    registry_path = _inside_repo(Path(registry_ref["path"]), label="artifact registry")
    expected_registry_sha = _digest(registry_ref["sha256"], label="artifact_registry.sha256")
    if not registry_path.is_file() or _sha256(registry_path) != expected_registry_sha:
        raise ProtocolError("artifact registry material SHA-256 mismatch")
    registry = _mapping(_load_json(registry_path, label="artifact registry"), label="artifact registry")
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ProtocolError(f"artifact registry schema_version must be {REGISTRY_SCHEMA}")
    artifacts = _mapping(registry.get("artifacts"), label="artifact registry.artifacts")

    base_id = shared["base_checkpoint_artifact_id"]
    teacher_id = shared["teacher_artifact_id"]
    heldout_id = shared["held_out_registration_artifact_id"]
    _verified_material(
        artifacts, base_id, artifact_root=material_root, expected_kind="checkpoint"
    )
    _verified_material(
        artifacts, teacher_id, artifact_root=material_root, expected_kind="teacher_attestation"
    )
    heldout = _mapping(artifacts.get(heldout_id), label=f"artifact {heldout_id}")
    if heldout.get("status") != "verified" or heldout.get("kind") != "heldout_registration":
        raise ProtocolError("held-out registration artifact must be verified")
    aliases = heldout.get("aliases")
    if not isinstance(aliases, list) or not aliases or not all(isinstance(alias, str) and alias for alias in aliases):
        raise ProtocolError("held-out registration aliases are invalid")
    tokenizer_vocab_size = heldout.get("tokenizer_vocab_size")
    if not isinstance(tokenizer_vocab_size, int) or isinstance(tokenizer_vocab_size, bool) \
            or tokenizer_vocab_size < 1:
        raise ProtocolError("held-out registration tokenizer_vocab_size is invalid")
    forbidden_token_sequences = heldout.get("forbidden_token_sequences")
    if not isinstance(forbidden_token_sequences, list) or not forbidden_token_sequences:
        raise ProtocolError("held-out registration must include forbidden token sequences")
    if not all(
        isinstance(sequence, list)
        and sequence
        and all(
            isinstance(token, int)
            and not isinstance(token, bool)
            and 0 <= token < tokenizer_vocab_size
            for token in sequence
        )
        for sequence in forbidden_token_sequences
    ):
        raise ProtocolError("held-out registration forbidden token sequences are invalid")

    training_artifact_id = arm.get("training_artifact_id")
    artifact, source_path, source_payload_sha = _verified_material(
        artifacts, training_artifact_id, artifact_root=material_root
    )
    exclusion = _mapping(
        artifact.get("held_out_exclusion"),
        label=f"artifact {training_artifact_id}.held_out_exclusion",
    )
    if exclusion.get("registration_artifact_id") != heldout_id \
            or exclusion.get("status") != "pass" \
            or not isinstance(exclusion.get("scanned_records"), int) \
            or exclusion["scanned_records"] < 1:
        raise ProtocolError("selected training artifact lacks a passing held-out exclusion record")
    if arm["arm_id"] in PREFIX_ARMS:
        evidence = _mapping(
            artifact.get("teacher_success_evidence"),
            label=f"artifact {training_artifact_id}.teacher_success_evidence",
        )
        if evidence.get("status") != "pass" \
                or evidence.get("metric") != "db_authoritative_quest_completion":
            raise ProtocolError("prefix artifact lacks DB-authoritative teacher-success evidence")
        _digest(evidence.get("evidence_sha256"), label="teacher-success evidence SHA-256")
    if arm["arm_id"] in FIRST_ERROR_ARMS:
        evidence = _mapping(
            artifact.get("first_error_evidence"),
            label=f"artifact {training_artifact_id}.first_error_evidence",
        )
        if evidence.get("status") != "pass" \
                or evidence.get("metric") != "first_model_visible_student_error":
            raise ProtocolError("SCoRe artifact lacks verified first-model-visible-error evidence")
        _digest(evidence.get("evidence_sha256"), label="first-error evidence SHA-256")
        _digest(evidence.get("prefix_verifier_sha256"), label="prefix verifier SHA-256")
    reachability_sha: str | None = None
    if arm["arm_id"] in REACHABILITY_ARMS or arm["arm_id"] in HISTORY_ABLATION_IDS:
        reachability = _mapping(
            artifact.get("reachability_evidence"),
            label=f"artifact {training_artifact_id}.reachability_evidence",
        )
        if reachability.get("status") != "pass" \
                or reachability.get("method") != "witness_trajectory_or_invariant_certificate":
            raise ProtocolError("state artifact lacks passing legal-reachability evidence")
        reachability_sha = _digest(
            reachability.get("evidence_sha256"), label="reachability evidence SHA-256"
        )

    raw_records = _load_jsonl(source_path)
    records = [
        _validate_source_record(
            raw,
            cell=cell,
            arm=arm,
            aliases=aliases,
            tokenizer_vocab_size=tokenizer_vocab_size,
            forbidden_token_sequences=forbidden_token_sequences,
            render_sha=render_sha,
            source_artifact_id=training_artifact_id,
            source_payload_sha=source_payload_sha,
        )
        for raw in raw_records
    ]
    if len({record["record_id"] for record in records}) != len(records):
        raise ProtocolError("source artifact contains duplicate record_id values")
    if arm["arm_id"] in PREFIX_ARMS:
        expected = artifact["teacher_success_evidence"]["evidence_sha256"]
        if any(record["semantics"]["teacher_success_evidence_sha256"] != expected for record in records):
            raise ProtocolError("source prefix record is not bound to registry teacher-success evidence")
    if arm["arm_id"] in FIRST_ERROR_ARMS:
        evidence = artifact["first_error_evidence"]
        if any(
            record["semantics"]["first_error_evidence_sha256"] != evidence["evidence_sha256"]
            or record["semantics"]["prefix_verifier_sha256"] != evidence["prefix_verifier_sha256"]
            for record in records
        ):
            raise ProtocolError("source SCoRe record is not bound to registry first-error evidence")
    if reachability_sha is not None and any(
        record["semantics"].get("reachability_evidence_sha256") != reachability_sha
        for record in records
    ):
        raise ProtocolError("source state record is not bound to registry reachability evidence")
    if arm["arm_id"] == "corrected_interface_sft" and any(
        record["semantics"]["corrected_interface_contract_sha256"] != render_sha
        for record in records
    ):
        raise ProtocolError("corrected-interface SFT record does not match the frozen render contract")
    records = _order_records(records, arm, cell["training_seed"])
    budgets = _mapping(shared.get("budgets"), label="shared_contract.budgets")
    _exact_keys(
        budgets,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label="shared_contract.budgets",
    )
    registered_budgets = {
        key: _positive_int(value, label=f"budgets.{key}") for key, value in budgets.items()
    }
    observed_budgets = _budget_totals(records)
    if observed_budgets != registered_budgets:
        raise ProtocolError(
            f"source artifact does not exactly fill the matched budget: "
            f"registered={registered_budgets}, observed={observed_budgets}"
        )
    optimizer = _mapping(shared.get("optimizer"), label="shared_contract.optimizer")
    route = _trainer_route(arm)
    plan = {
        "schema_version": BACKEND_PLAN_SCHEMA,
        "experiment_id": cell["experiment_id"],
        "cell_id": cell["cell_id"],
        "arm_id": arm["arm_id"],
        "role": arm["role"],
        "objective": arm["objective"],
        "training_seed": cell["training_seed"],
        "source_git_commit": source_git,
        "experiment_manifest_sha256": manifest_sha,
        "cell_config": {"path": str(config_path), "sha256": _sha256(config_path)},
        "artifact_registry": {"path": str(registry_path), "sha256": expected_registry_sha},
        "artifact_root": str(material_root),
        "source_artifact": {
            "artifact_id": training_artifact_id,
            "material_path": str(source_path),
            "sha256": source_payload_sha,
            "records": len(records),
        },
        "identities": {
            "base_checkpoint_artifact_id": base_id,
            "teacher_artifact_id": teacher_id,
            "render_contract_sha256": render_sha,
            "held_out_registration_artifact_id": heldout_id,
        },
        "optimizer": optimizer,
        "budgets": registered_budgets,
        "trainer_route": route,
        "execution_status": "not_run",
    }
    return plan, records


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(content)


def materialize(cell_config_path: str | Path) -> dict[str, Any]:
    plan, records = build_backend_plan(cell_config_path)
    output_dir = _inside_repo(Path(cell_config_path).resolve().parent, label="cell output directory")
    plan_path = output_dir / "backend-plan.json"
    records_path = output_dir / "normalized-records.jsonl"
    result_path = output_dir / "result.json"
    records_content = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records
    )
    records_sha = _sha256_bytes(records_content.encode())
    final_plan = {
        **plan,
        "normalized_records": {
            "path": str(records_path),
            "sha256": records_sha,
            "schema_version": NORMALIZED_SCHEMA,
            "records": len(records),
        },
    }
    plan_content = json.dumps(final_plan, indent=2, sort_keys=True) + "\n"
    plan_sha = _sha256_bytes(plan_content.encode())
    _write_new(records_path, records_content)
    _write_new(plan_path, plan_content)
    result = {
        "schema_version": BACKEND_RESULT_SCHEMA,
        "experiment_id": plan["experiment_id"],
        "cell_id": plan["cell_id"],
        "status": "prepared_not_trained",
        "source_git_commit": plan["source_git_commit"],
        "experiment_manifest_sha256": plan["experiment_manifest_sha256"],
        "base_checkpoint_artifact_id": plan["identities"]["base_checkpoint_artifact_id"],
        "teacher_artifact_id": plan["identities"]["teacher_artifact_id"],
        "training_seed": plan["training_seed"],
        "allocated_budgets": plan["budgets"],
        "backend_plan": {"path": str(plan_path), "sha256": plan_sha},
        "output_artifact": {
            "kind": "normalized_training_records",
            "uri": f"file:{records_path}",
            "sha256": records_sha,
        },
        "trainer_execution_status": "not_run",
        "trainer_compatibility": plan["trainer_route"]["compatibility"],
    }
    _write_new(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.dry_run:
            plan, records = build_backend_plan(args.cell_config)
            print(json.dumps({**plan, "normalized_record_count": len(records)}, indent=2, sort_keys=True))
            print("No files were written and no trainer was run.")
        else:
            print(json.dumps(materialize(args.cell_config), indent=2, sort_keys=True))
    except (OSError, ProtocolError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
