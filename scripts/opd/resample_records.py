"""Resample an OPD JSONL corpus to a frozen train-record count.

The ±seeding ablation (E3': natural-only rebuild of the round-2 corpus) must
hold total records — and therefore optimizer steps at fixed batch size —
constant against the seeded arm, so a scoring or capability difference is not
just a training-budget difference. Duplicates are exact raw-line copies, so
per-token advantages/behavior logprobs stay byte-identical.

Original records are retained in order and exact raw records are sampled with
replacement using a fixed seed. The transformer validates every source line,
refuses in-place or accidental overwrite, writes atomically, and emits an
attested manifest with source, output, script, and sampled-index hashes.
The source must have an adjacent ``.manifest.json`` receipt; the complete
validated parent chain is embedded in the derived receipt.

Usage:
  python3 scripts/opd/resample_records.py \
      --in dataset/opd_2b/round2_noseed/records.jsonl \
      --target 7024 --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import tempfile
from pathlib import Path

try:
    from .receipt_chain import (
        RESAMPLE_MANIFEST_SCHEMA_VERSION,
        ReceiptChainError,
        canonical_sha256,
        validate_receipt_chain,
    )
    from .record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        RecordSchemaError,
        validate_opd_train_record,
    )
except ImportError:  # direct `python scripts/opd/...` execution
    from receipt_chain import (  # type: ignore[no-redef]
        RESAMPLE_MANIFEST_SCHEMA_VERSION,
        ReceiptChainError,
        canonical_sha256,
        validate_receipt_chain,
    )
    from record_schema import (  # type: ignore[no-redef]
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        RecordSchemaError,
        validate_opd_train_record,
    )


MANIFEST_SCHEMA_VERSION = RESAMPLE_MANIFEST_SCHEMA_VERSION


class ArtifactBuildError(ValueError):
    """Raised when a derived-corpus contract is violated."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _validate_lines(payload: bytes) -> list[bytes]:
    lines = payload.splitlines()
    if not lines:
        raise ArtifactBuildError("source contains no records")
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            raise ArtifactBuildError(f"blank JSONL record at line {line_number}")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactBuildError(
                f"invalid UTF-8 JSON at line {line_number}: {exc}"
            ) from exc
        try:
            validate_opd_train_record(value, line_number=line_number)
        except RecordSchemaError as exc:
            raise ArtifactBuildError(str(exc)) from exc
    return lines


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ArtifactBuildError(
            f"refusing to replace a concurrently created artifact: {destination}"
        ) from exc
    temporary.unlink()


def resample_records(
    src: Path,
    dst: Path,
    *,
    target: int,
    seed: int,
) -> dict:
    """Build and attest an immutable fixed-count resample."""
    src = src.resolve()
    dst = dst.resolve()
    manifest_path = dst.with_suffix(".manifest.json")
    if not src.is_file():
        raise ArtifactBuildError(f"source is not a regular file: {src}")
    if src == dst:
        raise ArtifactBuildError("source and output must be different files")
    if dst.exists() or manifest_path.exists():
        raise ArtifactBuildError(
            f"refusing to overwrite existing output or manifest: {dst}, {manifest_path}"
        )
    if not dst.parent.is_dir():
        raise ArtifactBuildError(f"output directory does not exist: {dst.parent}")
    if target <= 0:
        raise ArtifactBuildError("target must be positive")
    script_path = Path(__file__).resolve()
    script_sha256 = _sha256(script_path)

    source_payload = src.read_bytes()
    source_manifest_path = src.with_suffix(".manifest.json")
    if not source_manifest_path.is_file():
        raise ArtifactBuildError(
            f"source provenance manifest is required: {source_manifest_path}"
        )
    try:
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        validate_receipt_chain(
            source_manifest,
            expected_output_sha256=_sha256_bytes(source_payload),
            repo_root=Path(__file__).resolve().parents[2],
        )
    except (OSError, json.JSONDecodeError, ReceiptChainError) as exc:
        raise ArtifactBuildError(f"invalid source provenance chain: {exc}") from exc
    lines = _validate_lines(source_payload)
    n_original = len(lines)
    if n_original >= target:
        raise ArtifactBuildError(
            f"already at/above target ({n_original} >= {target})"
        )

    rng = random.Random(seed)
    sampled_indices = [
        rng.randrange(n_original) for _ in range(target - n_original)
    ]
    output_payload = b"\n".join(
        lines + [lines[index] for index in sampled_indices]
    ) + b"\n"
    sampled_index_payload = json.dumps(
        sampled_indices, separators=(",", ":")
    ).encode("ascii")
    if _sha256(script_path) != script_sha256:
        raise ArtifactBuildError("transformer source changed during the build")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": str(src),
        "source_sha256": _sha256_bytes(source_payload),
        "parent_manifest": source_manifest,
        "parent_manifest_sha256": canonical_sha256(source_manifest),
        "output": str(dst),
        "output_sha256": _sha256_bytes(output_payload),
        "script_sha256": script_sha256,
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "seed": seed,
        "target_records": target,
        "original_records": n_original,
        "resampled_records": len(sampled_indices),
        "sampled_indices_sha256": _sha256_bytes(sampled_index_payload),
        "sampling": "uniform-with-replacement-after-originals",
    }
    manifest_payload = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    tmp_paths: list[Path] = []
    try:
        for destination, payload in (
            (dst, output_payload),
            (manifest_path, manifest_payload),
        ):
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                tmp_paths.append(Path(handle.name))
        _publish_create_only(tmp_paths[0], dst)
        _publish_create_only(tmp_paths[1], manifest_path)
        return manifest
    finally:
        for path in tmp_paths:
            path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out")
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    src = Path(args.inp)
    dst = Path(args.out) if args.out else src.with_suffix(".resampled.jsonl")
    try:
        manifest = resample_records(
            src,
            dst,
            target=args.target,
            seed=args.seed,
        )
    except ArtifactBuildError as exc:
        parser.error(str(exc))
    print(
        f"{manifest['original_records']} originals + "
        f"{manifest['resampled_records']} resampled duplicates -> "
        f"{manifest['target_records']} (seed {manifest['seed']}) => "
        f"{manifest['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
