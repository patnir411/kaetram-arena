"""Build the Arm-C control corpus: uniform clipped self-imitation.

Every finite, nonzero advantage is replaced with the corpus mean absolute
nonzero advantage. Zero-valued mask positions remain zero. Zeros in the source
records mark exactly the tokens that must not train (context positions,
unscorable Nones, and the malformed-span abstention mask), so replacing only
nonzero values preserves the trained-token support and the mask geometry while
erasing all teacher-derived per-token structure.

Trained with the unchanged IS-clipped trainer this yields *uniform clipped
self-imitation of behavior-policy tokens* (NOT plain SFT — the PPO-style ratio
clip still bounds the update; that is deliberate: the control changes only the
advantage pattern and preserves init, records, behavior logprobs, clipping,
step weighting, and optimizer). If it matches seeded-OPD's eval, teacher
grading was unnecessary for that lift; if it returns to the natural-only arm's
level while OPD replicates, teacher weighting contributes beyond state
coverage.

c is pre-registered as the corpus mean |advantage| over nonzero tokens: at
init the importance ratio is ~1, so the first-step gradient magnitude is
proportional to the advantage scale, and matching mean |adv| matches the
initial update magnitude without post-hoc tuning.

The transformer is fail-closed: it validates record geometry, refuses in-place
or accidental overwrite, detects source mutation between passes, writes
atomically, and records byte-level input/output/script hashes beside the
result. The source must have an adjacent ``.manifest.json`` receipt; the
complete validated parent chain is embedded in the derived receipt.

Usage:
  python3 scripts/opd/make_uniform_advantages.py \
      --in dataset/opd_2b/round2_uniform/records_r2_original.jsonl \
      --out dataset/opd_2b/round2_uniform/records.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

try:
    from .receipt_chain import (
        UNIFORM_MANIFEST_SCHEMA_VERSION,
        ReceiptChainError,
        canonical_sha256,
        receipt_chain_contains,
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
        UNIFORM_MANIFEST_SCHEMA_VERSION,
        ReceiptChainError,
        canonical_sha256,
        receipt_chain_contains,
        validate_receipt_chain,
    )
    from record_schema import (  # type: ignore[no-redef]
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        RecordSchemaError,
        validate_opd_train_record,
    )


MANIFEST_SCHEMA_VERSION = UNIFORM_MANIFEST_SCHEMA_VERSION


class ArtifactBuildError(ValueError):
    """Raised when an immutable derived-artifact contract is violated."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(raw: bytes, *, line_number: int) -> dict:
    if not raw.strip():
        raise ArtifactBuildError(f"blank JSONL record at line {line_number}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactBuildError(
            f"invalid UTF-8 JSON at line {line_number}: {exc}"
        ) from exc
    try:
        return validate_opd_train_record(value, line_number=line_number)
    except RecordSchemaError as exc:
        raise ArtifactBuildError(str(exc)) from exc


def _temporary(parent: Path, stem: str) -> tuple[BinaryIO, Path]:
    handle = tempfile.NamedTemporaryFile(
        mode="w+b",
        dir=parent,
        prefix=f".{stem}.",
        suffix=".tmp",
        delete=False,
    )
    return handle, Path(handle.name)


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ArtifactBuildError(
            f"refusing to replace a concurrently created artifact: {destination}"
        ) from exc
    temporary.unlink()


def build_uniform_advantages(src: Path, dst: Path) -> dict:
    """Create and attest a uniform-advantage corpus."""
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
    script_path = Path(__file__).resolve()
    script_sha256 = _sha256(script_path)
    source_manifest_path = src.with_suffix(".manifest.json")
    if not source_manifest_path.is_file():
        raise ArtifactBuildError(
            f"source provenance manifest is required: {source_manifest_path}"
        )

    source_sha256 = _sha256(src)
    try:
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        validate_receipt_chain(
            source_manifest,
            expected_output_sha256=source_sha256,
            repo_root=Path(__file__).resolve().parents[2],
        )
        if receipt_chain_contains(source_manifest, MANIFEST_SCHEMA_VERSION):
            raise ReceiptChainError(
                "multiple uniform transformations are not supported"
            )
    except (OSError, json.JSONDecodeError, ReceiptChainError) as exc:
        raise ArtifactBuildError(f"invalid source provenance chain: {exc}") from exc
    total_abs = 0.0
    n_nonzero = n_zero = n_records = 0
    source_digest_first_pass = hashlib.sha256()
    with src.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            source_digest_first_pass.update(raw)
            rec = _record(raw, line_number=line_number)
            n_records += 1
            for advantage in rec["advantages"]:
                value = float(advantage)
                if value != 0.0:
                    total_abs += abs(value)
                    n_nonzero += 1
                else:
                    n_zero += 1
    if source_digest_first_pass.hexdigest() != source_sha256:
        raise ArtifactBuildError("source changed during the statistics pass")
    if n_records == 0:
        raise ArtifactBuildError("source contains no records")
    if n_nonzero == 0:
        raise ArtifactBuildError("source contains no nonzero advantages")
    c = total_abs / n_nonzero
    if not math.isfinite(c) or c <= 0:
        raise ArtifactBuildError("derived uniform advantage is not finite and positive")

    output_handle, output_tmp = _temporary(dst.parent, dst.name)
    source_digest_second_pass = hashlib.sha256()
    output_digest = hashlib.sha256()
    try:
        with output_handle:
            with src.open("rb") as source_handle:
                for line_number, raw in enumerate(source_handle, start=1):
                    source_digest_second_pass.update(raw)
                    rec = _record(raw, line_number=line_number)
                    rec["advantages"] = [
                        c if float(value) != 0.0 else 0.0
                        for value in rec["advantages"]
                    ]
                    encoded = (json.dumps(rec, sort_keys=True) + "\n").encode("utf-8")
                    output_handle.write(encoded)
                    output_digest.update(encoded)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if source_digest_second_pass.hexdigest() != source_sha256:
            raise ArtifactBuildError("source changed between validation and rewrite")
        if _sha256(script_path) != script_sha256:
            raise ArtifactBuildError("transformer source changed during the build")

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "control": "uniform-clipped-self-imitation",
            "source": str(src),
            "source_sha256": source_sha256,
            "parent_manifest": source_manifest,
            "parent_manifest_sha256": canonical_sha256(source_manifest),
            "output": str(dst),
            "output_sha256": output_digest.hexdigest(),
            "script_sha256": script_sha256,
            "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
            "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
            "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
            "c": c,
            "c_rule": "corpus mean |advantage| over nonzero tokens",
            "n_records": n_records,
            "n_nonzero_tokens": n_nonzero,
            "n_zero_tokens_kept": n_zero,
        }
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest_handle, manifest_tmp = _temporary(
            manifest_path.parent, manifest_path.name
        )
        try:
            with manifest_handle:
                manifest_handle.write(manifest_bytes)
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            _publish_create_only(output_tmp, dst)
            _publish_create_only(manifest_tmp, manifest_path)
        finally:
            manifest_tmp.unlink(missing_ok=True)
        return manifest
    finally:
        output_tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", dest="out", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_uniform_advantages(Path(args.inp), Path(args.out))
    except ArtifactBuildError as exc:
        parser.error(str(exc))
    print(
        f"records={manifest['n_records']}  "
        f"nonzero_adv_tokens={manifest['n_nonzero_tokens']}  "
        f"zero(kept)={manifest['n_zero_tokens_kept']}"
    )
    print(f"pre-registered c = corpus mean |advantage| = {manifest['c']:.6f}")
    print(f"wrote {manifest['output']} + {Path(manifest['output']).with_suffix('.manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
