#!/usr/bin/env python3
"""Prepare corrected-interface SFT tokens without rendering or training.

The matched-training backend has already validated source trajectories and
normalized their token arrays.  This adapter consumes that normalized output,
revalidates its immutable identities and budgets, and emits a minimal
pretokenized SFT bundle.  It intentionally has no tokenizer, renderer, Modal,
or trainer dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from scripts.opd import matched_training_backend as backend
from scripts.opd.matched_training import ProtocolError, _validate_parameterization


REPO = Path(__file__).resolve().parents[2]
PLAN_SCHEMA = "kaetram.corrected-interface-sft-plan.v1"
RECORD_SCHEMA = "kaetram.pretokenized-sft-record.v1"
RESULT_SCHEMA = "kaetram.corrected-interface-sft-result.v1"
EXPECTED_ARM = "corrected_interface_sft"
EXPECTED_OBJECTIVE = "sft"
TRAINER_ENTRYPOINT = Path(__file__).resolve().parents[2] / "finetune/train_corrected_interface_sft.py"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _inside_repo(path: Path, *, label: str) -> Path:
    path = path.resolve()
    try:
        path.relative_to(REPO)
    except ValueError as exc:
        raise ProtocolError(f"{label} must resolve inside the repository") from exc
    return path


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ProtocolError(f"{label} fields must be exactly {sorted(expected)}")


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(char not in "0123456789abcdef" for char in value) \
            or value == "0" * 64:
        raise ProtocolError(f"{label} must be a resolved lowercase SHA-256")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{label} must be a nonnegative integer")
    return value


def _finite_positive(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise ProtocolError(f"{label} must be finite and positive")
    return float(value)


def validate_optimizer(value: Any) -> dict[str, Any]:
    optimizer = _mapping(value, label="optimizer")
    _exact_keys(
        optimizer,
        {
            "name", "learning_rate", "betas", "weight_decay", "scheduler",
            "warmup_ratio", "gradient_clip_norm", "effective_batch_size", "epochs",
        },
        label="optimizer",
    )
    if optimizer.get("name") != "adamw_8bit" or optimizer.get("scheduler") != "cosine":
        raise ProtocolError("corrected-interface SFT requires registered adamw_8bit/cosine")
    learning_rate = _finite_positive(optimizer.get("learning_rate"), label="optimizer.learning_rate")
    weight_decay = optimizer.get("weight_decay")
    if isinstance(weight_decay, bool) or not isinstance(weight_decay, (int, float)) \
            or not math.isfinite(weight_decay) or weight_decay < 0:
        raise ProtocolError("optimizer.weight_decay must be finite and nonnegative")
    warmup_ratio = optimizer.get("warmup_ratio")
    if isinstance(warmup_ratio, bool) or not isinstance(warmup_ratio, (int, float)) \
            or not math.isfinite(warmup_ratio) or not 0 <= warmup_ratio < 1:
        raise ProtocolError("optimizer.warmup_ratio must be in [0, 1)")
    betas = optimizer.get("betas")
    if not isinstance(betas, list) or len(betas) != 2 or not all(
        isinstance(beta, (int, float)) and not isinstance(beta, bool) and 0 <= beta < 1
        for beta in betas
    ):
        raise ProtocolError("optimizer.betas must contain two values in [0, 1)")
    effective_batch = optimizer.get("effective_batch_size")
    epochs = optimizer.get("epochs")
    if isinstance(effective_batch, bool) or not isinstance(effective_batch, int) \
            or effective_batch < 1:
        raise ProtocolError("optimizer.effective_batch_size must be a positive integer")
    if isinstance(epochs, bool) or not isinstance(epochs, int) or epochs < 1:
        raise ProtocolError("optimizer.epochs must be a positive integer")
    return {
        **optimizer,
        "learning_rate": learning_rate,
        "weight_decay": float(weight_decay),
        "warmup_ratio": float(warmup_ratio),
        "gradient_clip_norm": _finite_positive(
            optimizer.get("gradient_clip_norm"), label="optimizer.gradient_clip_norm"
        ),
        "betas": [float(beta) for beta in betas],
    }


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load {label} {path}: {exc}") from exc


def _load_jsonl(path: Path) -> list[Any]:
    if not path.is_file():
        raise ProtocolError("normalized SFT artifact must be a JSONL file")
    records: list[Any] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    if not records:
        raise ProtocolError("normalized SFT artifact is empty")
    return records


def _validate_record(
    raw: Any,
    *,
    plan: dict[str, Any],
    render_sha: str,
) -> dict[str, Any]:
    record = _mapping(raw, label="normalized SFT record")
    _exact_keys(
        record,
        {
            "schema_version", "record_id", "cell_id", "arm_id", "role", "objective",
            "training_seed", "recovery", "identities", "state", "history", "semantics",
            "input_ids", "labels", "advantages", "behavior_logprobs", "step_weight",
            "budget_usage", "source", "curriculum",
        },
        label="normalized SFT record",
    )
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ProtocolError("normalized SFT record_id must be non-empty")
    expected_header = {
        "schema_version": backend.NORMALIZED_SCHEMA,
        "cell_id": plan["cell_id"],
        "arm_id": EXPECTED_ARM,
        "role": plan["role"],
        "objective": EXPECTED_OBJECTIVE,
        "training_seed": plan["training_seed"],
    }
    for key, expected in expected_header.items():
        if record.get(key) != expected:
            raise ProtocolError(f"record {record_id} {key} does not match the backend plan")
    identities = _mapping(record.get("identities"), label=f"record {record_id}.identities")
    if identities != plan["identities"] or identities.get("render_contract_sha256") != render_sha:
        raise ProtocolError(f"record {record_id} frozen identity mismatch")
    semantics = _mapping(record.get("semantics"), label=f"record {record_id}.semantics")
    if semantics.get("corrected_interface_contract_sha256") != render_sha:
        raise ProtocolError(f"record {record_id} corrected-interface digest mismatch")

    input_ids = record.get("input_ids")
    labels = record.get("labels")
    if not isinstance(input_ids, list) or not input_ids or not all(
        isinstance(token, int) and not isinstance(token, bool) and token >= 0 for token in input_ids
    ):
        raise ProtocolError(f"record {record_id} input_ids must be non-empty token IDs")
    if not isinstance(labels, list) or len(labels) != len(input_ids):
        raise ProtocolError(f"record {record_id} labels must align with input_ids")
    for index, (token, label) in enumerate(zip(input_ids, labels, strict=True)):
        if isinstance(label, bool) or not isinstance(label, int) \
                or (label != -100 and label < 0):
            raise ProtocolError(f"record {record_id} label {index} is invalid")
        if label != -100 and label != token:
            raise ProtocolError(
                f"record {record_id} supervised label {index} does not equal its input token"
            )
    supervised_tokens = sum(label != -100 for label in labels)
    if supervised_tokens < 1:
        raise ProtocolError(f"record {record_id} has no supervised tokens")
    if record.get("advantages") is not None or record.get("behavior_logprobs") is not None:
        raise ProtocolError(f"record {record_id} carries OPD-only arrays")
    step_weight = _finite_positive(record.get("step_weight"), label=f"record {record_id}.step_weight")
    usage = _mapping(record.get("budget_usage"), label=f"record {record_id}.budget_usage")
    _exact_keys(
        usage,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label=f"record {record_id}.budget_usage",
    )
    normalized_usage = {
        key: _nonnegative_int(value, label=f"record {record_id}.{key}")
        for key, value in usage.items()
    }
    if normalized_usage["action_tokens"] != supervised_tokens:
        raise ProtocolError(f"record {record_id} action-token accounting mismatch")

    # Deliberately do not read or transform state/history.  Their provenance was
    # checked by the backend; this boundary consumes only frozen token arrays.
    return {
        "schema_version": RECORD_SCHEMA,
        "record_id": record_id,
        "input_ids": input_ids,
        "labels": labels,
        "step_weight": step_weight,
        "budget_usage": normalized_usage,
        "identities": identities,
        "source_normalized_record_sha256": backend._sha256_json(record),
    }


def _budget_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(record["budget_usage"][key] for record in records)
        for key in ("action_tokens", "teacher_scoring_tokens", "environment_interactions")
    }


def build_adapter_plan(
    backend_plan_path: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_path = _inside_repo(Path(backend_plan_path), label="backend plan")
    plan = _mapping(_load_json(plan_path, label="backend plan"), label="backend plan")
    if plan.get("schema_version") != backend.BACKEND_PLAN_SCHEMA:
        raise ProtocolError(f"backend plan schema_version must be {backend.BACKEND_PLAN_SCHEMA}")
    if plan.get("arm_id") != EXPECTED_ARM or plan.get("objective") != EXPECTED_OBJECTIVE:
        raise ProtocolError("adapter accepts only the corrected_interface_sft arm")
    if plan.get("execution_status") != "not_run":
        raise ProtocolError("backend plan must enter the adapter with execution_status not_run")
    backend_route = _mapping(plan.get("trainer_route"), label="backend plan.trainer_route")
    if backend_route.get("entrypoint") != backend.SFT_ADAPTER \
            or backend_route.get("entrypoint_sha256") != _sha256(Path(__file__).resolve()):
        raise ProtocolError("backend plan is not bound to this corrected-interface SFT adapter")

    config_ref = _mapping(plan.get("cell_config"), label="backend plan.cell_config")
    _exact_keys(config_ref, {"path", "sha256"}, label="backend plan.cell_config")
    config_path = _inside_repo(Path(config_ref["path"]), label="cell config")
    if _sha256(config_path) != _digest(config_ref["sha256"], label="cell config SHA-256"):
        raise ProtocolError("cell config SHA-256 mismatch")
    cell = _mapping(_load_json(config_path, label="cell config"), label="cell config")
    shared = _mapping(cell.get("shared_contract"), label="cell shared_contract")
    render_sha = backend._interface_digest(shared)
    identities = _mapping(plan.get("identities"), label="backend plan.identities")
    if identities.get("render_contract_sha256") != render_sha:
        raise ProtocolError("backend plan does not match the frozen render/interface contract")
    if identities.get("base_checkpoint_artifact_id") != shared.get("base_checkpoint_artifact_id") \
            or identities.get("teacher_artifact_id") != shared.get("teacher_artifact_id") \
            or identities.get("held_out_registration_artifact_id") \
            != shared.get("held_out_registration_artifact_id"):
        raise ProtocolError("backend plan identity does not match the cell contract")

    registry_ref = _mapping(plan.get("artifact_registry"), label="backend plan.artifact_registry")
    _exact_keys(registry_ref, {"path", "sha256"}, label="backend plan.artifact_registry")
    registry_path = _inside_repo(Path(registry_ref["path"]), label="artifact registry")
    expected_registry_sha = _digest(registry_ref["sha256"], label="artifact registry SHA-256")
    if _sha256(registry_path) != expected_registry_sha:
        raise ProtocolError("artifact registry SHA-256 mismatch")
    registry = _mapping(_load_json(registry_path, label="artifact registry"), label="artifact registry")
    artifacts = _mapping(registry.get("artifacts"), label="artifact registry.artifacts")
    artifact_root = backend._artifact_root(plan.get("artifact_root"))
    _, checkpoint_path, checkpoint_sha = backend._verified_material(
        artifacts,
        identities["base_checkpoint_artifact_id"],
        artifact_root=artifact_root,
        expected_kind="checkpoint",
    )

    normalized_ref = _mapping(plan.get("normalized_records"), label="normalized_records")
    _exact_keys(
        normalized_ref,
        {"path", "sha256", "schema_version", "records"},
        label="normalized_records",
    )
    if normalized_ref.get("schema_version") != backend.NORMALIZED_SCHEMA:
        raise ProtocolError("backend plan does not reference normalized token records")
    normalized_path = _inside_repo(Path(normalized_ref["path"]), label="normalized records")
    expected_records_sha = _digest(normalized_ref["sha256"], label="normalized records SHA-256")
    if _sha256(normalized_path) != expected_records_sha:
        raise ProtocolError("normalized token record SHA-256 mismatch")
    raw_records = _load_jsonl(normalized_path)
    if normalized_ref.get("records") != len(raw_records):
        raise ProtocolError("normalized token record count mismatch")
    records = [_validate_record(raw, plan=plan, render_sha=render_sha) for raw in raw_records]
    if len({record["record_id"] for record in records}) != len(records):
        raise ProtocolError("normalized SFT artifact contains duplicate record IDs")

    budgets = _mapping(plan.get("budgets"), label="backend plan.budgets")
    _exact_keys(
        budgets,
        {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
        label="backend plan.budgets",
    )
    registered_budgets = {
        key: _nonnegative_int(value, label=f"backend plan.budgets.{key}")
        for key, value in budgets.items()
    }
    if registered_budgets != shared.get("budgets"):
        raise ProtocolError("backend plan budgets do not match the frozen cell contract")
    observed_budgets = _budget_totals(records)
    if observed_budgets != registered_budgets:
        raise ProtocolError(
            "pretokenized SFT records do not exactly fill the matched budget: "
            f"registered={registered_budgets}, observed={observed_budgets}"
        )
    optimizer = validate_optimizer(plan.get("optimizer"))
    if optimizer != shared.get("optimizer"):
        raise ProtocolError("backend plan optimizer does not match the frozen cell contract")
    parameterization, parameterization_sha = _validate_parameterization(
        plan.get("parameterization")
    )
    if parameterization != shared.get("parameterization") \
            or parameterization_sha != shared.get("parameterization_sha256") \
            or parameterization_sha != plan.get("parameterization_sha256"):
        raise ProtocolError("backend plan parameterization does not match the frozen cell contract")
    if not TRAINER_ENTRYPOINT.is_file():
        raise ProtocolError(f"reviewed SFT trainer entrypoint is missing: {TRAINER_ENTRYPOINT}")

    adapter_plan = {
        "schema_version": PLAN_SCHEMA,
        "experiment_id": plan["experiment_id"],
        "cell_id": plan["cell_id"],
        "arm_id": EXPECTED_ARM,
        "objective": EXPECTED_OBJECTIVE,
        "training_seed": plan["training_seed"],
        "backend_plan": {"path": str(plan_path), "sha256": _sha256(plan_path)},
        "normalized_records": {
            "path": str(normalized_path),
            "sha256": expected_records_sha,
            "records": len(records),
        },
        "identities": identities,
        "budgets": registered_budgets,
        "optimizer": optimizer,
        "parameterization": parameterization,
        "parameterization_sha256": parameterization_sha,
        "base_checkpoint": {
            "artifact_id": identities["base_checkpoint_artifact_id"],
            "path": str(checkpoint_path),
            "sha256": checkpoint_sha,
        },
        "input_contract": "pretokenized_input_ids_and_labels_only",
        "rendering_status": "not_performed_and_forbidden",
        "trainer_route": {
            "entrypoint": str(TRAINER_ENTRYPOINT),
            "entrypoint_sha256": _sha256(TRAINER_ENTRYPOINT),
            "compatibility": "executable_pending_compute",
            "reason": (
                "reviewed direct-token trainer is available but has not been launched"
            ),
        },
        "execution_status": "not_run",
    }
    return adapter_plan, records


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(content)


def materialize(backend_plan_path: str | Path) -> dict[str, Any]:
    plan, records = build_adapter_plan(backend_plan_path)
    output_dir = _inside_repo(Path(backend_plan_path).resolve().parent, label="SFT output directory")
    records_path = output_dir / "pretokenized-sft-records.jsonl"
    plan_path = output_dir / "corrected-interface-sft-plan.json"
    result_path = output_dir / "corrected-interface-sft-result.json"
    records_content = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records
    )
    records_sha = _sha256_bytes(records_content.encode())
    final_plan = {
        **plan,
        "pretokenized_records": {
            "path": str(records_path),
            "sha256": records_sha,
            "schema_version": RECORD_SCHEMA,
            "records": len(records),
        },
    }
    plan_content = json.dumps(final_plan, indent=2, sort_keys=True) + "\n"
    plan_sha = _sha256_bytes(plan_content.encode())
    _write_new(records_path, records_content)
    _write_new(plan_path, plan_content)
    result = {
        "schema_version": RESULT_SCHEMA,
        "experiment_id": plan["experiment_id"],
        "cell_id": plan["cell_id"],
        "status": "prepared_not_trained",
        "training_seed": plan["training_seed"],
        "allocated_budgets": plan["budgets"],
        "render_contract_sha256": plan["identities"]["render_contract_sha256"],
        "adapter_plan": {"path": str(plan_path), "sha256": plan_sha},
        "output_artifact": {
            "kind": "pretokenized_corrected_interface_sft_records",
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
    parser.add_argument("--backend-plan", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.dry_run:
            plan, records = build_adapter_plan(args.backend_plan)
            print(json.dumps({**plan, "pretokenized_record_count": len(records)}, indent=2, sort_keys=True))
            print("No files were written, no conversations were rendered, and no trainer was run.")
        else:
            print(json.dumps(materialize(args.backend_plan), indent=2, sort_keys=True))
    except (OSError, ProtocolError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
