"""Canonical core schema for OPD training JSONL records."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


OPD_TRAIN_RECORD_SCHEMA_VERSION = "kaetram-opd-train-record-v2"
OPD_TRAIN_RECORD_SCHEMA = {
    "version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
    "required": {
        "input_ids": "list[int>=0] of length >= 2",
        "labels": "aligned list[int], position 0 ignored, with a supervised token after it",
        "advantages": "aligned list[finite number]",
        "behavior_logprobs": "aligned list[finite number]",
        "step_weight": "finite number > 0",
    },
    "alignment": [
        "input_ids",
        "labels",
        "advantages",
        "behavior_logprobs",
    ],
    "ignored_label": -100,
    "semantics": [
        "supervised labels equal corresponding input_ids",
        "ignored labels form a contiguous context prefix",
        "ignored positions have zero advantage and behavior_logprob",
        "behavior log-probabilities are non-positive",
        "n_action counts supervised targets after the causal shift",
    ],
}
OPD_TRAIN_RECORD_VALIDATOR_SHA256 = hashlib.sha256(
    Path(__file__).read_bytes()
).hexdigest()
OPD_TRAIN_RECORD_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "schema": OPD_TRAIN_RECORD_SCHEMA,
            "validator_file_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


class RecordSchemaError(ValueError):
    """Raised when a record is not a canonical OPD training record."""


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def validate_opd_train_record(record: object, *, line_number: int) -> dict:
    """Validate the canonical trainer-facing fields while permitting metadata."""
    if not isinstance(record, dict):
        raise RecordSchemaError(f"record {line_number} is not a JSON object")

    missing = [
        field
        for field in OPD_TRAIN_RECORD_SCHEMA["required"]
        if field not in record
    ]
    if missing:
        raise RecordSchemaError(
            f"record {line_number} is missing required OPD field(s): "
            + ", ".join(missing)
        )

    input_ids = record["input_ids"]
    if (
        not isinstance(input_ids, list)
        or len(input_ids) < 2
        or any(not _integer(value) or value < 0 for value in input_ids)
    ):
        raise RecordSchemaError(
            f"record {line_number} input_ids must contain at least two nonnegative integers"
        )

    expected = len(input_ids)
    labels = record["labels"]
    if (
        not isinstance(labels, list)
        or len(labels) != expected
        or any(not _integer(value) for value in labels)
    ):
        raise RecordSchemaError(
            f"record {line_number} labels must be an integer list aligned with input_ids"
        )
    if labels[0] != -100:
        raise RecordSchemaError(
            f"record {line_number} label position 0 must be ignored because the "
            "causal trainer drops it"
        )
    if all(value == -100 for value in labels[1:]):
        raise RecordSchemaError(
            f"record {line_number} has no supervised label token after the causal shift"
        )

    for field in ("advantages", "behavior_logprobs"):
        values = record[field]
        if (
            not isinstance(values, list)
            or len(values) != expected
            or any(not _finite_number(value) for value in values)
        ):
            raise RecordSchemaError(
                f"record {line_number} {field} must be a finite numeric list "
                "aligned with input_ids"
            )
    if any(float(value) > 0.0 for value in record["behavior_logprobs"]):
        raise RecordSchemaError(
            f"record {line_number} behavior_logprobs must be non-positive log-probabilities"
        )

    saw_supervised = False
    for index, (input_id, label, advantage, behavior_logprob) in enumerate(
        zip(
            input_ids,
            labels,
            record["advantages"],
            record["behavior_logprobs"],
        )
    ):
        if label == -100:
            if saw_supervised:
                raise RecordSchemaError(
                    f"record {line_number} ignored labels must be a contiguous prefix"
                )
            if float(advantage) != 0.0 or float(behavior_logprob) != 0.0:
                raise RecordSchemaError(
                    f"record {line_number} ignored position {index} must have zero "
                    "advantage and behavior_logprob"
                )
        else:
            saw_supervised = True
            if label != input_id:
                raise RecordSchemaError(
                    f"record {line_number} supervised label {index} must equal input_id"
                )

    step_weight = record["step_weight"]
    if not _finite_number(step_weight) or float(step_weight) <= 0:
        raise RecordSchemaError(
            f"record {line_number} step_weight must be finite and positive"
        )
    n_action = record.get("n_action")
    if n_action is not None and (
        not _integer(n_action)
        or n_action != sum(label != -100 for label in labels[1:])
    ):
        raise RecordSchemaError(
            f"record {line_number} n_action must equal the post-shift supervised-token count"
        )
    return record
