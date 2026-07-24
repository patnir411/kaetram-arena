#!/usr/bin/env python3
"""Capture and verify a clean, content-bound analysis implementation receipt."""
from __future__ import annotations

import argparse
import copy
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import (  # noqa: E402
    ManifestError,
    capture_git_state,
    hash_path,
    sha256_json,
)


SCHEMA_VERSION = "kaetram.analysis-provenance.v1"
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class AnalysisProvenanceError(RuntimeError):
    """Raised when an analysis implementation cannot be bound exactly."""


def _relative_implementation_paths(
    repo_root: Path,
    implementation_files: Iterable[Path],
) -> list[Path]:
    root = repo_root.resolve()
    relative: set[Path] = set()
    for supplied in implementation_files:
        path = supplied if supplied.is_absolute() else root / supplied
        if path.is_symlink() or not path.is_file():
            raise AnalysisProvenanceError(
                f"implementation input must be a regular non-symlink file: {path}"
            )
        try:
            logical = path.resolve().relative_to(root)
        except ValueError as exc:
            raise AnalysisProvenanceError(
                f"implementation input is outside the repository: {path}"
            ) from exc
        relative.add(logical)
    if not relative:
        raise AnalysisProvenanceError("at least one implementation file is required")
    return sorted(relative, key=lambda value: value.as_posix())


def build_analysis_provenance(
    repo_root: Path,
    implementation_files: Iterable[Path],
) -> dict[str, Any]:
    """Bind exact analysis files from a clean repository checkout."""
    root = repo_root.resolve()
    relative = _relative_implementation_paths(root, implementation_files)
    try:
        git = capture_git_state(root)
    except ManifestError as exc:
        raise AnalysisProvenanceError(str(exc)) from exc
    if git.get("dirty"):
        raise AnalysisProvenanceError(
            "analysis provenance requires a clean worktree; dirty paths: "
            + ", ".join(git.get("dirty_paths") or [])
        )
    descriptors = [hash_path(root / path, root=root) for path in relative]
    report = {
        "schema_version": SCHEMA_VERSION,
        "source_git_commit": git["commit"],
        "generation_worktree_clean": True,
        "python_version": platform.python_version(),
        "implementation_files": descriptors,
        "implementation_sha256": sha256_json(descriptors),
    }
    report["manifest_sha256"] = sha256_json(report)
    return report


def load_and_verify_analysis_provenance(
    path: Path,
    *,
    repo_root: Path,
    expected_files: Iterable[Path],
) -> dict[str, Any]:
    """Verify a receipt against the exact current implementation and runtime."""
    if path.is_symlink():
        raise AnalysisProvenanceError("analysis provenance must not be a symlink")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisProvenanceError(f"cannot read analysis provenance: {path}") from exc
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise AnalysisProvenanceError("unexpected analysis-provenance schema")
    identity = report.get("manifest_sha256")
    unsigned = copy.deepcopy(report)
    unsigned.pop("manifest_sha256", None)
    if not isinstance(identity, str) or identity != sha256_json(unsigned):
        raise AnalysisProvenanceError("analysis provenance has an invalid self-identity")
    if (
        report.get("generation_worktree_clean") is not True
        or not _GIT_SHA_RE.fullmatch(str(report.get("source_git_commit", "")))
        or report.get("python_version") != platform.python_version()
    ):
        raise AnalysisProvenanceError(
            "analysis provenance does not match a clean source revision and runtime"
        )

    root = repo_root.resolve()
    relative = _relative_implementation_paths(root, expected_files)
    actual = [hash_path(root / item, root=root) for item in relative]
    recorded = report.get("implementation_files")
    if recorded != actual or report.get("implementation_sha256") != sha256_json(actual):
        raise AnalysisProvenanceError(
            "analysis implementation bytes do not match the provenance receipt"
        )
    try:
        current_git = capture_git_state(root)
    except ManifestError as exc:
        raise AnalysisProvenanceError(str(exc)) from exc
    expected_names = {item.as_posix() for item in relative}
    dirty_implementation = sorted(
        name for name in current_git.get("dirty_paths") or [] if name in expected_names
    )
    if dirty_implementation:
        raise AnalysisProvenanceError(
            "analysis implementation has dirty paths: "
            + ", ".join(dirty_implementation)
        )
    return report


def _write_identical_or_new(
    output: Path,
    rendered: bytes,
    *,
    forbidden_files: Iterable[Path],
) -> None:
    if output.is_symlink():
        raise AnalysisProvenanceError(f"output must not be a symlink: {output}")
    resolved = output.resolve()
    if any(resolved == path.resolve() for path in forbidden_files):
        raise AnalysisProvenanceError("output must not overwrite an implementation input")
    if output.exists():
        if not output.is_file() or output.read_bytes() != rendered:
            raise AnalysisProvenanceError(
                f"refusing to overwrite different provenance: {output}"
            )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument(
        "--implementation-file",
        action="append",
        type=Path,
        required=True,
        dest="implementation_files",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_analysis_provenance(
            args.repo_root,
            args.implementation_files,
        )
        rendered = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_identical_or_new(
            args.out,
            rendered,
            forbidden_files=args.implementation_files,
        )
    except (AnalysisProvenanceError, ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(args.out)
    print(f"manifest_sha256={report['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
