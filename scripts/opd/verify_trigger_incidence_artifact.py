#!/usr/bin/env python3
"""Verify a published trigger-incidence artifact without modifying it."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import export_trigger_incidence_artifact as exporter  # noqa: E402


SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
INDEX_KEYS = {
    "schema_version",
    "study_id",
    "experiment_source_git_commit",
    "analysis_source_git_commit",
    "analysis_script_sha256",
    "export_script_sha256",
    "verifier_script_sha256",
    "registration_sha256",
    "design_sha256",
    "files",
    "tree_sha256",
}
FILE_KEYS = {"path", "size_bytes", "sha256"}


def _require_hash(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise exporter.ExportError(f"invalid {label}")
    return value


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise exporter.ExportError("artifact file path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise exporter.ExportError(f"unsafe artifact file path: {value!r}")
    if pure.as_posix() != value:
        raise exporter.ExportError(f"non-canonical artifact file path: {value!r}")
    return Path(*pure.parts)


def _verify_file_inventory(root: Path, index: dict) -> list[Path]:
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise exporter.ExportError("artifact file inventory must be a nonempty list")
    paths: list[Path] = []
    seen: set[str] = set()
    normalized_records = []
    for record in records:
        if not isinstance(record, dict) or set(record) != FILE_KEYS:
            raise exporter.ExportError("artifact file record has an invalid field set")
        relative = _safe_relative_path(record["path"])
        relative_text = relative.as_posix()
        if relative_text == "artifact-index.json" or relative_text in seen:
            raise exporter.ExportError(
                f"duplicate or recursive artifact file path: {relative_text}"
            )
        seen.add(relative_text)
        path = root / relative
        exporter._require_regular_file(path)
        size = record.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise exporter.ExportError(f"invalid size for {relative_text}")
        digest = _require_hash(
            record.get("sha256"),
            label=f"SHA-256 for {relative_text}",
            pattern=SHA256,
        )
        if path.stat().st_size != size or exporter.sha256_file(path) != digest:
            raise exporter.ExportError(f"artifact file mismatch: {relative_text}")
        paths.append(path)
        normalized_records.append(
            {"path": relative_text, "size_bytes": size, "sha256": digest}
        )
    if [record["path"] for record in normalized_records] != sorted(seen):
        raise exporter.ExportError("artifact file inventory is not canonically ordered")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected = {*seen, "artifact-index.json"}
    if actual != expected:
        raise exporter.ExportError("artifact tree differs from its file inventory")
    if exporter.sha256_json(normalized_records) != index.get("tree_sha256"):
        raise exporter.ExportError("artifact tree SHA-256 mismatch")
    return paths


def verify_bundle(
    artifact_dir: Path,
    *,
    forbidden_fragments: tuple[str, ...] = (),
) -> dict:
    exporter._require_regular_directory(artifact_dir)
    index_path = artifact_dir / "artifact-index.json"
    exporter._require_regular_file(index_path)
    index = exporter.load_json(index_path)
    if set(index) != INDEX_KEYS:
        raise exporter.ExportError("artifact index has an invalid field set")
    if index.get("schema_version") != exporter.EXPORT_SCHEMA:
        raise exporter.ExportError("artifact schema version mismatch")
    for label in (
        "analysis_script_sha256",
        "export_script_sha256",
        "verifier_script_sha256",
        "registration_sha256",
        "design_sha256",
        "tree_sha256",
    ):
        _require_hash(index.get(label), label=label, pattern=SHA256)
    for label in ("experiment_source_git_commit", "analysis_source_git_commit"):
        _require_hash(index.get(label), label=label, pattern=COMMIT)

    files = _verify_file_inventory(artifact_dir, index)
    registration = exporter.load_json(artifact_dir / "registration.json")
    snapshots = registration.get("snapshots")
    if not isinstance(snapshots, dict) or not snapshots:
        raise exporter.ExportError("registration snapshots are missing")
    run_root = artifact_dir / "runs"
    exporter._require_regular_directory(run_root)
    actual_run_names = {
        path.name for path in run_root.iterdir() if path.is_dir() or path.is_symlink()
    }
    if actual_run_names != set(snapshots):
        raise exporter.ExportError("public run directories are not canonical")

    verified = exporter._semantic_verify(artifact_dir)
    summary = verified["summary"]
    expected = {
        "schema_version": exporter.EXPORT_SCHEMA,
        "study_id": verified["registration"]["study_id"],
        "experiment_source_git_commit": verified["design"]["source_git_commit"],
        "analysis_source_git_commit": summary["analysis_code_provenance"][
            "source_git_commit"
        ],
        "analysis_script_sha256": verified["analysis_script_sha256"],
        "export_script_sha256": exporter.sha256_file(
            Path(exporter.__file__).resolve()
        ),
        "verifier_script_sha256": exporter.sha256_file(Path(__file__).resolve()),
        "registration_sha256": exporter.sha256_file(
            artifact_dir / "registration.json"
        ),
        "design_sha256": exporter.sha256_file(
            artifact_dir / "design" / "design.json"
        ),
        "files": index["files"],
        "tree_sha256": index["tree_sha256"],
    }
    if index != expected:
        raise exporter.ExportError("artifact index disagrees with semantic contents")

    normalized_forbidden = tuple(
        dict.fromkeys(fragment for fragment in forbidden_fragments if fragment)
    )
    exporter._scan_public_text([*files, index_path], normalized_forbidden)
    return {
        "schema_version": exporter.EXPORT_SCHEMA,
        "study_id": index["study_id"],
        "artifact_index_sha256": exporter.sha256_file(index_path),
        "tree_sha256": index["tree_sha256"],
        "scheduled_requests": summary["scheduled_requests"],
        "successful_requests": summary["successful_requests"],
        "failed_requests": summary["failed_requests"],
        "recovery_opportunities": summary["recovery_opportunities"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = (
        str(Path.home()),
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        "barath",
        "patnir",
    )
    result = verify_bundle(
        args.artifact_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
