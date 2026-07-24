#!/usr/bin/env python3
"""Train corrected-interface SFT directly from frozen token arrays.

The default invocation is a read-only preflight. Accelerator execution requires
three independent signals: ``--execute``, an exact cell confirmation string,
and ``KAETRAM_ENABLE_ACCELERATOR_TRAINING=1``. No tokenizer or conversation
renderer is used; the model receives only already-tokenized ``input_ids`` and
``labels``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from scripts.opd import corrected_interface_sft as adapter
from scripts.opd import matched_training_backend as backend
from scripts.opd.matched_training import ProtocolError


REPO = Path(__file__).resolve().parents[1]
ENABLE_ENV = "KAETRAM_ENABLE_ACCELERATOR_TRAINING"
TRAIN_RESULT_SCHEMA = "kaetram.corrected-interface-sft-training-result.v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(char not in "0123456789abcdef" for char in value) \
            or value == "0" * 64:
        raise ProtocolError(f"{label} must be a resolved lowercase SHA-256")
    return value


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ProtocolError(f"{label} fields must be exactly {sorted(expected)}")


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load {label} {path}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid pretokenized JSONL line {line_number}: {exc}") from exc
        record = _mapping(raw, label=f"pretokenized record {line_number}")
        _exact_keys(
            record,
            {
                "schema_version", "record_id", "input_ids", "labels", "step_weight",
                "budget_usage", "identities", "source_normalized_record_sha256",
            },
            label=f"pretokenized record {line_number}",
        )
        if record.get("schema_version") != adapter.RECORD_SCHEMA:
            raise ProtocolError(f"pretokenized record {line_number} schema mismatch")
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ProtocolError(f"pretokenized record {line_number} record_id is invalid")
        input_ids = record.get("input_ids")
        labels = record.get("labels")
        if not isinstance(input_ids, list) or not input_ids or not all(
            isinstance(token, int) and not isinstance(token, bool) and token >= 0
            for token in input_ids
        ):
            raise ProtocolError(f"record {record_id} input_ids are invalid")
        if not isinstance(labels, list) or len(labels) != len(input_ids):
            raise ProtocolError(f"record {record_id} labels do not align with input_ids")
        for index, (token, label) in enumerate(zip(input_ids, labels, strict=True)):
            if isinstance(label, bool) or not isinstance(label, int) \
                    or (label != -100 and label != token):
                raise ProtocolError(f"record {record_id} causal label {index} is invalid")
        usage = _mapping(record.get("budget_usage"), label=f"record {record_id}.budget_usage")
        _exact_keys(
            usage,
            {"action_tokens", "teacher_scoring_tokens", "environment_interactions"},
            label=f"record {record_id}.budget_usage",
        )
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in usage.values()
        ):
            raise ProtocolError(f"record {record_id} budget usage is invalid")
        if usage["action_tokens"] != sum(label != -100 for label in labels):
            raise ProtocolError(f"record {record_id} action-token accounting mismatch")
        _digest(
            record.get("source_normalized_record_sha256"),
            label=f"record {record_id} normalized-source SHA-256",
        )
        records.append(record)
    if not records:
        raise ProtocolError("pretokenized SFT bundle is empty")
    return records


def _budget_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(record["budget_usage"][key] for record in records)
        for key in ("action_tokens", "teacher_scoring_tokens", "environment_interactions")
    }


def collate_as_lists(records: list[dict[str, Any]], *, pad_token_id: int) -> dict[str, list[list[Any]]]:
    """Pad a batch without tokenization and preserve the registered loss mask."""
    if isinstance(pad_token_id, bool) or not isinstance(pad_token_id, int) or pad_token_id < 0:
        raise ProtocolError("pad_token_id must be a nonnegative integer")
    if not records:
        raise ProtocolError("cannot collate an empty batch")
    width = max(len(record["input_ids"]) for record in records)
    batch = {"input_ids": [], "labels": [], "attention_mask": [], "step_weight": []}
    for record in records:
        input_ids = record["input_ids"]
        labels = record["labels"]
        if len(input_ids) != len(labels) or not input_ids:
            raise ProtocolError(f"record {record.get('record_id')} token arrays are not aligned")
        padding = width - len(input_ids)
        batch["input_ids"].append(input_ids + [pad_token_id] * padding)
        batch["labels"].append(labels + [-100] * padding)
        batch["attention_mask"].append([1] * len(input_ids) + [0] * padding)
        batch["step_weight"].append(record["step_weight"])
    causal_targets(batch["labels"])
    return batch


def causal_targets(labels: list[list[int]]) -> list[list[int]]:
    """Return next-token targets and fail if any row would contribute zero loss."""
    shifted = [row[1:] for row in labels]
    if any(not row or all(label == -100 for label in row) for row in shifted):
        raise ProtocolError("every pretokenized SFT row must retain a causal supervised target")
    return shifted


class PretokenizedCollator:
    """Torch collator that performs padding only; it never owns a tokenizer."""

    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        lists = collate_as_lists(records, pad_token_id=self.pad_token_id)
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - execution environment boundary
            raise RuntimeError("PyTorch is required only for an approved training execution") from exc
        return {
            "input_ids": torch.tensor(lists["input_ids"], dtype=torch.long),
            "labels": torch.tensor(lists["labels"], dtype=torch.long),
            "attention_mask": torch.tensor(lists["attention_mask"], dtype=torch.long),
            "step_weight": torch.tensor(lists["step_weight"], dtype=torch.float32),
        }


def causal_sft_loss(logits: Any, labels: Any, step_weight: Any) -> Any:
    """Differentiable shifted causal-LM loss with per-record registered weights."""
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:  # pragma: no cover - execution environment boundary
        raise RuntimeError("PyTorch is required only for an approved training execution") from exc
    shift_logits = logits[:, :-1, :].contiguous()
    shift_labels = labels[:, 1:].contiguous()
    valid = shift_labels.ne(-100)
    if not bool(valid.any(dim=1).all().item()):
        raise RuntimeError("causal SFT loss received a row with no supervised target")
    token_loss = functional.cross_entropy(
        shift_logits.view(-1, shift_logits.size(-1)),
        shift_labels.view(-1),
        reduction="none",
        ignore_index=-100,
    ).view_as(shift_labels)
    row_loss = (token_loss * valid).sum(dim=1) / valid.sum(dim=1).to(token_loss.dtype)
    weights = step_weight.to(row_loss.dtype)
    return (row_loss * weights).sum() / weights.sum()


def load_training_contract(plan_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(plan_path).resolve()
    plan = _mapping(_load_json(path, label="corrected-interface SFT plan"), label="SFT plan")
    if plan.get("schema_version") != adapter.PLAN_SCHEMA:
        raise ProtocolError(f"SFT plan schema_version must be {adapter.PLAN_SCHEMA}")
    if plan.get("execution_status") != "not_run" \
            or plan.get("rendering_status") != "not_performed_and_forbidden" \
            or plan.get("input_contract") != "pretokenized_input_ids_and_labels_only":
        raise ProtocolError("SFT plan does not preserve the direct-token not-run boundary")
    route = _mapping(plan.get("trainer_route"), label="trainer_route")
    if route.get("compatibility") != "executable_pending_compute" \
            or Path(route.get("entrypoint", "")).resolve() != Path(__file__).resolve() \
            or route.get("entrypoint_sha256") != _sha256(Path(__file__).resolve()):
        raise ProtocolError("SFT plan is not bound to this reviewed trainer entrypoint")
    optimizer = adapter.validate_optimizer(plan.get("optimizer"))
    if optimizer != plan.get("optimizer"):
        raise ProtocolError("SFT optimizer contract is not canonical")
    parameterization, parameterization_sha = backend._validate_parameterization(
        plan.get("parameterization")
    )
    if parameterization != plan.get("parameterization") \
            or parameterization_sha != plan.get("parameterization_sha256"):
        raise ProtocolError("SFT LoRA parameterization contract is not canonical")

    checkpoint = _mapping(plan.get("base_checkpoint"), label="base_checkpoint")
    _exact_keys(checkpoint, {"artifact_id", "path", "sha256"}, label="base_checkpoint")
    checkpoint_path = Path(checkpoint["path"]).resolve()
    if not checkpoint_path.exists() or backend._hash_material(checkpoint_path) != checkpoint["sha256"]:
        raise ProtocolError("base checkpoint material hash mismatch")

    record_ref = _mapping(plan.get("pretokenized_records"), label="pretokenized_records")
    _exact_keys(
        record_ref, {"path", "sha256", "schema_version", "records"}, label="pretokenized_records"
    )
    records_path = Path(record_ref["path"]).resolve()
    if record_ref.get("schema_version") != adapter.RECORD_SCHEMA \
            or not records_path.is_file() or _sha256(records_path) != record_ref.get("sha256"):
        raise ProtocolError("pretokenized SFT material hash or schema mismatch")
    records = _load_jsonl(records_path)
    if len(records) != record_ref.get("records"):
        raise ProtocolError("pretokenized SFT record count mismatch")
    if any(record["identities"] != plan.get("identities") for record in records):
        raise ProtocolError("pretokenized SFT record identity mismatch")
    if _budget_totals(records) != plan.get("budgets"):
        raise ProtocolError("pretokenized SFT records do not fill the frozen budgets")
    for record in records:
        collate_as_lists([record], pad_token_id=0)
        if not isinstance(record["step_weight"], (int, float)) \
                or isinstance(record["step_weight"], bool) \
                or not math.isfinite(record["step_weight"]) or record["step_weight"] <= 0:
            raise ProtocolError(f"record {record.get('record_id')} step_weight is invalid")
    return plan, records


def _training_arguments(plan: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    optimizer = adapter.validate_optimizer(plan["optimizer"])
    return {
        "output_dir": str(output_dir),
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": optimizer["effective_batch_size"],
        "num_train_epochs": optimizer["epochs"],
        "learning_rate": optimizer["learning_rate"],
        "adam_beta1": optimizer["betas"][0],
        "adam_beta2": optimizer["betas"][1],
        "weight_decay": optimizer["weight_decay"],
        "lr_scheduler_type": optimizer["scheduler"],
        "warmup_ratio": optimizer["warmup_ratio"],
        "max_grad_norm": optimizer["gradient_clip_norm"],
        "optim": "adamw_bnb_8bit",
        "seed": plan["training_seed"],
        "save_strategy": "no",
        "report_to": [],
        "remove_unused_columns": False,
        "bf16": True,
    }


def lora_config_kwargs(parameterization: Any) -> dict[str, Any]:
    value, _ = backend._validate_parameterization(parameterization)
    return {
        "r": value["rank"],
        "lora_alpha": value["alpha"],
        "lora_dropout": value["dropout"],
        "bias": value["bias"],
        "target_modules": value["target_modules"],
        "task_type": value["task_type"],
        "init_lora_weights": value["init_lora_weights"],
    }


def verify_trainable_parameter_names(names: list[str]) -> None:
    if not names or any("lora_" not in name for name in names):
        raise ProtocolError("fresh LoRA contract violated: non-LoRA parameters are trainable")


def execute_training(plan_path: Path, confirmation: str) -> dict[str, Any]:
    plan, records = load_training_contract(plan_path)
    expected = f"{plan['experiment_id']}:{plan['cell_id']}"
    if confirmation != expected:
        raise ProtocolError(f"--confirm-execute must exactly equal {expected}")
    if os.environ.get(ENABLE_ENV) != "1":
        raise ProtocolError(f"{ENABLE_ENV}=1 is required for accelerator execution")
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from torch.utils.data import Dataset
        from transformers import AutoModelForCausalLM, Trainer, TrainingArguments
    except ImportError as exc:  # pragma: no cover - execution environment boundary
        raise RuntimeError(
            "approved execution requires torch, transformers, peft, and bitsandbytes"
        ) from exc

    class TokenDataset(Dataset):
        def __len__(self) -> int:
            return len(records)

        def __getitem__(self, index: int) -> dict[str, Any]:
            return records[index]

    class DirectTokenTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            step_weight = inputs["step_weight"]
            labels = inputs["labels"]
            model_inputs = {
                key: value
                for key, value in inputs.items()
                if key not in {"step_weight", "labels"}
            }
            outputs = model(**model_inputs, use_cache=False)
            loss = causal_sft_loss(outputs.logits, labels, step_weight)
            return (loss, outputs) if return_outputs else loss

    output_root = plan_path.resolve().parent / "corrected-interface-sft-training"
    if output_root.exists():
        raise ProtocolError(f"training output already exists: {output_root}")
    model = AutoModelForCausalLM.from_pretrained(
        plan["base_checkpoint"]["path"],
        local_files_only=True,
        trust_remote_code=False,
        torch_dtype=torch.bfloat16,
    )
    model = get_peft_model(model, LoraConfig(**lora_config_kwargs(plan["parameterization"])))
    trainable = [name for name, parameter in model.named_parameters() if parameter.requires_grad]
    verify_trainable_parameter_names(trainable)
    pad_token_id = model.config.pad_token_id
    if isinstance(pad_token_id, bool) or not isinstance(pad_token_id, int) or pad_token_id < 0:
        raise ProtocolError("hash-pinned base checkpoint must declare a valid pad_token_id")
    args = TrainingArguments(**_training_arguments(plan, output_root / "trainer-state"))
    trainer = DirectTokenTrainer(
        model=model,
        args=args,
        train_dataset=TokenDataset(),
        data_collator=PretokenizedCollator(pad_token_id),
    )
    trainer.train()
    checkpoint_dir = output_root / "checkpoint"
    checkpoint_dir.mkdir(parents=True, exist_ok=False)
    trainer.save_model(str(checkpoint_dir))
    checkpoint_sha = backend._hash_material(checkpoint_dir)
    result = {
        "schema_version": TRAIN_RESULT_SCHEMA,
        "experiment_id": plan["experiment_id"],
        "cell_id": plan["cell_id"],
        "status": "trained",
        "trainer_execution_status": "completed",
        "source_plan_sha256": _sha256(plan_path),
        "output_artifact": {
            "kind": "corrected_interface_sft_lora_adapter",
            "uri": f"file:{checkpoint_dir}",
            "sha256": checkpoint_sha,
        },
    }
    result_path = output_root / "training-result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute")
    args = parser.parse_args()
    try:
        if not args.execute:
            plan, records = load_training_contract(args.plan)
            print(json.dumps({
                "status": "not_run",
                "compatibility": "executable_pending_compute",
                "experiment_id": plan["experiment_id"],
                "cell_id": plan["cell_id"],
                "records": len(records),
                "training_arguments": _training_arguments(plan, Path("<execution-output>")),
            }, indent=2, sort_keys=True))
            print("No tokenizer, renderer, model, accelerator, or trainer was invoked.")
        else:
            if args.confirm_execute is None:
                raise ProtocolError("--confirm-execute is required with --execute")
            print(json.dumps(execute_training(args.plan, args.confirm_execute), indent=2, sort_keys=True))
    except (OSError, ProtocolError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
