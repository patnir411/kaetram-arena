"""Fail-closed verification of trainer-facing OPD records and their receipt."""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from . import make_uniform_advantages, opd_data_manifest, resample_records
from .receipt_chain import (
    SUPPORTED_MANIFESTS,
    ReceiptChainError,
    validate_receipt_chain,
)
from .record_schema import (
    RecordSchemaError,
    validate_opd_train_record,
)


class TrainingRecordBundleError(ValueError):
    """Raised when records are not byte- and schema-bound to their receipt."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: object) -> bool:
    return _nonnegative_int(value) and value > 0


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TrainingRecordBundleError(message)


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            _require(bool(line.strip()), f"blank JSONL record at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TrainingRecordBundleError(
                    f"invalid JSON at record {line_number}: {exc}"
                ) from exc
            try:
                records.append(validate_opd_train_record(value, line_number=line_number))
            except RecordSchemaError as exc:
                raise TrainingRecordBundleError(str(exc)) from exc
    _require(bool(records), "records file contains no records")
    return records


def _verify_uniform(manifest: dict, records: list[dict]) -> None:
    _require(
        manifest.get("control") == "uniform-clipped-self-imitation",
        "uniform manifest has the wrong control",
    )
    c = manifest.get("c")
    _require(
        isinstance(c, (int, float))
        and not isinstance(c, bool)
        and math.isfinite(float(c))
        and float(c) > 0,
        "uniform manifest c must be finite and positive",
    )
    _require(
        manifest.get("c_rule") == "corpus mean |advantage| over nonzero tokens",
        "uniform manifest has the wrong c_rule",
    )
    nonzero = 0
    zero = 0
    for record in records:
        for advantage in record["advantages"]:
            if float(advantage) == 0.0:
                zero += 1
            else:
                nonzero += 1
                _require(
                    float(advantage) == float(c),
                    "uniform record contains a nonzero advantage different from c",
                )
    _require(manifest.get("n_records") == len(records), "uniform n_records mismatch")
    _require(
        manifest.get("n_nonzero_tokens") == nonzero,
        "uniform n_nonzero_tokens mismatch",
    )
    _require(
        manifest.get("n_zero_tokens_kept") == zero,
        "uniform n_zero_tokens_kept mismatch",
    )


def _verify_resample(manifest: dict, records: list[dict]) -> None:
    original = manifest.get("original_records")
    resampled = manifest.get("resampled_records")
    target = manifest.get("target_records")
    _require(_positive_int(original), "resample original_records must be positive")
    _require(_positive_int(resampled), "resample resampled_records must be positive")
    _require(_positive_int(target), "resample target_records must be positive")
    _require(original + resampled == target, "resample record counts do not add up")
    _require(target == len(records), "resample target_records mismatch")
    _require(
        isinstance(manifest.get("seed"), int)
        and not isinstance(manifest.get("seed"), bool),
        "resample seed must be an integer",
    )
    _require(
        manifest.get("sampling") == "uniform-with-replacement-after-originals",
        "resample manifest has the wrong sampling rule",
    )
    _require(
        _digest(manifest.get("sampled_indices_sha256")),
        "resample sampled_indices_sha256 is invalid",
    )
    rng = random.Random(manifest["seed"])
    sampled_indices = [
        rng.randrange(original) for _ in range(resampled)
    ]
    sampled_payload = json.dumps(
        sampled_indices, separators=(",", ":")
    ).encode("ascii")
    _require(
        hashlib.sha256(sampled_payload).hexdigest()
        == manifest["sampled_indices_sha256"],
        "resample sampled-index digest mismatch",
    )
    expected_tail = [records[index] for index in sampled_indices]
    _require(
        records[original:] == expected_tail,
        "resample records do not match the declared deterministic sample",
    )


def _verify_generated(manifest: dict, records: list[dict]) -> None:
    _require(
        manifest.get("n_records") == len(records),
        "generated-record n_records mismatch",
    )


def _verify_semantic_chain(manifest: dict, records: list[dict]) -> None:
    """Verify every transform against the exact records it claims to emit.

    Resampling retains its complete parent corpus as an ordered prefix, so the
    parent can be checked recursively without trusting path fields or requiring
    a separately mounted ancestor file.
    """
    schema = manifest["schema_version"]
    if schema == opd_data_manifest.MANIFEST_SCHEMA_VERSION:
        _verify_generated(manifest, records)
        return
    if schema == make_uniform_advantages.MANIFEST_SCHEMA_VERSION:
        _verify_uniform(manifest, records)
        parent = manifest["parent_manifest"]
        _require(
            parent["schema_version"]
            != make_uniform_advantages.MANIFEST_SCHEMA_VERSION,
            "multiple uniform transformations are not supported",
        )
        # A uniform transform preserves count/order but overwrites nonzero
        # advantages. All non-uniform ancestors remain replayable from the
        # preserved order/equality relationships; rejecting a second uniform
        # prevents one rewrite from erasing the other's evidence.
        _verify_semantic_chain(parent, records)
        return

    _verify_resample(manifest, records)
    original = manifest["original_records"]
    _verify_semantic_chain(manifest["parent_manifest"], records[:original])


def load_verified_training_records(
    records_path: str | Path,
    manifest_path: str | Path,
) -> list[dict]:
    """Return records only after their immutable transformation receipt verifies."""
    records = Path(records_path)
    receipt = Path(manifest_path) if str(manifest_path) else None
    _require(records.is_file(), f"records path is not a regular file: {records}")
    _require(receipt is not None, "--records-manifest-path is required")
    _require(receipt.is_file(), f"records manifest is not a regular file: {receipt}")
    try:
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingRecordBundleError(f"invalid records manifest: {exc}") from exc
    _require(isinstance(manifest, dict), "records manifest must be a JSON object")

    schema = manifest.get("schema_version")
    _require(schema in SUPPORTED_MANIFESTS, f"unsupported records manifest: {schema!r}")
    _require(_digest(manifest.get("source_sha256")), "source_sha256 is invalid")
    _require(_digest(manifest.get("output_sha256")), "output_sha256 is invalid")
    _require(
        manifest["output_sha256"] == _sha256(records),
        "records SHA-256 does not match the manifest",
    )
    try:
        validate_receipt_chain(
            manifest,
            expected_output_sha256=manifest["output_sha256"],
            repo_root=Path(__file__).resolve().parents[2],
        )
    except ReceiptChainError as exc:
        raise TrainingRecordBundleError(str(exc)) from exc

    loaded = _load_records(records)
    _verify_semantic_chain(manifest, loaded)
    return loaded
