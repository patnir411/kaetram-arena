#!/usr/bin/env python3
"""Semantically verify and export a trigger-incidence evidence bundle."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterator


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import trigger_incidence_probe as probe  # noqa: E402


RUN_FILES = (
    "prelaunch.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
    "artifact-index.json",
)
ANALYSIS_FILES = (
    "analysis-summary.json",
    "cells.csv",
    "contrasts.csv",
    "artifact-index.json",
)
EXPORT_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v1"
PUBLIC_ATTESTATION_EXTRAS = {
    "deployment_id",
    "runtime_environment_receipt_sha256",
    "snapshot_lock_sha256",
    "snapshot_tree_sha256",
    "tokenizer_source_revision",
}
GENERIC_IDENTITY_PATTERNS = (
    re.compile(r"(?:^|[\"'\s])(?:/Users/|/home/|[A-Za-z]:\\\\Users\\\\)"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:git@github\.com:|https?://github\.com/)", re.IGNORECASE),
)
SAFE_SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ExportError(RuntimeError):
    """Raised when the copied bundle is incomplete, inconsistent, or unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"JSON root must be an object: {path}")
    return value


def _require_regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"input must be a regular file, not a symlink: {path}")


def _require_regular_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ExportError(f"input must be a regular directory, not a symlink: {path}")


def _paths_overlap(first: Path, second: Path) -> bool:
    first_resolved = first.resolve(strict=False)
    second_resolved = second.resolve(strict=False)
    return (
        first_resolved == second_resolved
        or first_resolved.is_relative_to(second_resolved)
        or second_resolved.is_relative_to(first_resolved)
    )


def _reject_output_overlap(output_dir: Path, sources: list[Path]) -> None:
    for source in sources:
        if _paths_overlap(output_dir, source):
            raise ExportError(f"output overlaps sealed input: {source}")


def _copy_exclusive(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise ExportError(f"cannot open regular source without following links: {source}") from exc
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ExportError(f"input must be a regular file: {source}")
        with os.fdopen(source_fd, "rb", closefd=False) as input_handle, destination.open(
            "xb"
        ) as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    finally:
        os.close(source_fd)


def _nested_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from _nested_strings(key)
            yield from _nested_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_strings(nested)


def _decoded_text_fields(path: Path, raw_text: str) -> Iterator[str]:
    try:
        if path.suffix == ".json":
            yield from _nested_strings(json.loads(raw_text))
        elif path.suffix == ".jsonl":
            for line_number, line in enumerate(raw_text.splitlines(), start=1):
                if line.strip():
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ExportError(
                            f"invalid staged JSONL at {path}:{line_number}"
                        ) from exc
                    yield from _nested_strings(value)
        elif path.suffix == ".csv":
            for row in csv.reader(io.StringIO(raw_text)):
                yield from row
    except json.JSONDecodeError as exc:
        raise ExportError(f"invalid staged JSON: {path}") from exc


def _scan_identity_text(
    text: str,
    *,
    path: Path,
    forbidden: tuple[str, ...],
) -> None:
    normalized = unicodedata.normalize("NFKC", text)
    folded = normalized.casefold()
    for fragment in forbidden:
        if fragment in folded:
            raise ExportError(
                f"forbidden identity/path fragment in {path}: {fragment}"
            )
    for pattern in GENERIC_IDENTITY_PATTERNS:
        if match := pattern.search(normalized):
            raise ExportError(
                f"identity-bearing pattern in {path}: {match.group(0)!r}"
            )


def _scan_public_text(paths: list[Path], forbidden: tuple[str, ...]) -> None:
    normalized_forbidden = tuple(
        unicodedata.normalize("NFKC", item).casefold()
        for item in forbidden
        if item
    )
    for path in paths:
        try:
            raw_text = path.read_text(errors="strict")
        except UnicodeDecodeError as exc:
            raise ExportError(f"expected UTF-8 text artifact: {path}") from exc
        _scan_identity_text(
            raw_text,
            path=path,
            forbidden=normalized_forbidden,
        )
        for decoded in _decoded_text_fields(path, raw_text):
            _scan_identity_text(
                decoded,
                path=path,
                forbidden=normalized_forbidden,
            )


def _validate_health_allowlist(
    health: dict,
    registration: dict,
    snapshot: str,
) -> None:
    probe.validate_endpoint_health(health, registration, snapshot)
    if set(health) != {"status", "attestation"}:
        raise ExportError(f"{snapshot}: endpoint health has non-public fields")
    attestation = health.get("attestation")
    if not isinstance(attestation, dict):
        raise ExportError(f"{snapshot}: endpoint attestation is not an object")
    allowed = {
        "api_model",
        "checkpoint_sha256",
        *registration["endpoint_contract"].keys(),
        *PUBLIC_ATTESTATION_EXTRAS,
    }
    if set(attestation) != allowed:
        raise ExportError(f"{snapshot}: endpoint attestation field set is not public")


@contextlib.contextmanager
def _analysis_identity(provenance: dict) -> Iterator[None]:
    original = probe._git_identity
    identity = {
        "source_git_commit": provenance.get("source_git_commit"),
        "dirty_paths": provenance.get("dirty_paths"),
    }
    probe._git_identity = lambda: identity
    try:
        yield
    finally:
        probe._git_identity = original


def _semantic_verify(staged: Path) -> dict:
    registration_path = staged / "registration.json"
    design_path = staged / "design" / "design.json"
    try:
        registration, registration_sha256 = probe.load_registration(registration_path)
        unsafe_snapshots = [
            snapshot
            for snapshot in registration["snapshots"]
            if SAFE_SNAPSHOT_ID.fullmatch(snapshot) is None
        ]
        if unsafe_snapshots:
            raise ExportError(
                "registered snapshot IDs must be safe single path components"
            )
        design = probe.load_design(
            design_path,
            registration,
            registration_sha256,
        )
    except probe.ProbeError as exc:
        raise ExportError("producer registration/design verification failed") from exc
    snapshots = list(registration["snapshots"])
    staged_runs = sorted((staged / "runs").iterdir())
    if len(staged_runs) != len(snapshots):
        raise ExportError("staged bundle does not contain every registered run")

    by_snapshot: dict[str, Path] = {}
    for run_dir in staged_runs:
        try:
            prelaunch, postflight, _completed, _rows, _identity = (
                probe._verify_run_directory(run_dir, registration)
            )
        except probe.ProbeError as exc:
            raise ExportError(f"producer run verification failed: {run_dir}") from exc
        snapshot = prelaunch["snapshot"]
        if snapshot in by_snapshot:
            raise ExportError(f"duplicate staged snapshot: {snapshot}")
        _validate_health_allowlist(
            prelaunch["endpoint_health"], registration, snapshot
        )
        _validate_health_allowlist(
            postflight["endpoint_health"], registration, snapshot
        )
        by_snapshot[snapshot] = run_dir
    if set(by_snapshot) != set(snapshots):
        raise ExportError("staged runs do not match the registered checkpoints")

    summary_path = staged / "analysis" / "analysis-summary.json"
    summary = load_json(summary_path)
    provenance = summary.get("analysis_code_provenance")
    if not isinstance(provenance, dict):
        raise ExportError("analysis lacks code provenance")
    expected_script_sha256 = sha256_file(Path(probe.__file__).resolve())
    if (
        set(provenance)
        != {
            "source_git_commit",
            "dirty_paths",
            "analysis_script_sha256",
            "python_version",
        }
        or provenance.get("analysis_script_sha256") != expected_script_sha256
        or provenance.get("python_version") != sys.version.split()[0]
        or provenance.get("dirty_paths") != []
        or re.fullmatch(
            r"[0-9a-f]{40}", str(provenance.get("source_git_commit", ""))
        )
        is None
    ):
        raise ExportError("analysis implementation provenance does not match")

    with tempfile.TemporaryDirectory(prefix="kaetram-trigger-reanalysis-") as raw:
        regenerated = Path(raw) / "analysis"
        try:
            with _analysis_identity(provenance):
                probe.analyze(
                    registration_path,
                    design_path,
                    [by_snapshot[snapshot] for snapshot in snapshots],
                    regenerated,
                )
        except probe.ProbeError as exc:
            raise ExportError("producer semantic reanalysis failed") from exc
        for name in ANALYSIS_FILES:
            existing = staged / "analysis" / name
            reproduced = regenerated / name
            if existing.read_bytes() != reproduced.read_bytes():
                raise ExportError(
                    f"checked-in analysis differs from raw-data reanalysis: {name}"
                )

    for index, snapshot in enumerate(snapshots, start=1):
        current = by_snapshot[snapshot]
        target = staged / "runs" / snapshot
        if current != target:
            current.rename(target)
    return {
        "registration": registration,
        "design": design,
        "summary": summary,
        "analysis_script_sha256": expected_script_sha256,
    }


def _source_files(
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    analysis_dir: Path,
) -> list[tuple[Path, Path]]:
    sources = [
        (registration_path, Path("registration.json")),
        (design_dir / "design.json", Path("design/design.json")),
        (
            design_dir / "design.receipt.json",
            Path("design/design.receipt.json"),
        ),
    ]
    for index, run_dir in enumerate(run_dirs, start=1):
        for name in RUN_FILES:
            sources.append(
                (run_dir / name, Path("runs") / f"input-{index:02d}" / name)
            )
    for name in ANALYSIS_FILES:
        sources.append((analysis_dir / name, Path("analysis") / name))
    return sources


def export_bundle(
    *,
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    analysis_dir: Path,
    output_dir: Path,
    forbidden_fragments: tuple[str, ...],
) -> dict:
    if not run_dirs:
        raise ExportError("at least one run directory is required")
    source_roots = [registration_path, design_dir, *run_dirs, analysis_dir]
    _reject_output_overlap(output_dir, source_roots)
    for directory in [design_dir, *run_dirs, analysis_dir]:
        _require_regular_directory(directory)
    sources = _source_files(
        registration_path,
        design_dir,
        run_dirs,
        analysis_dir,
    )
    for source, _relative in sources:
        _require_regular_file(source)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ExportError(f"refusing to overwrite export directory: {output_dir}") from exc

    try:
        for source, relative in sources:
            _copy_exclusive(source, output_dir / relative)
        verified = _semantic_verify(output_dir)
        public_files = sorted(
            path
            for path in output_dir.rglob("*")
            if path.is_file()
        )
        _scan_public_text(public_files, forbidden_fragments)
        records = [
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in public_files
        ]
        summary = verified["summary"]
        manifest = {
            "schema_version": EXPORT_SCHEMA,
            "study_id": verified["registration"]["study_id"],
            "experiment_source_git_commit": verified["design"][
                "source_git_commit"
            ],
            "analysis_source_git_commit": summary["analysis_code_provenance"][
                "source_git_commit"
            ],
            "analysis_script_sha256": verified["analysis_script_sha256"],
            "export_script_sha256": sha256_file(Path(__file__).resolve()),
            "verifier_script_sha256": sha256_file(
                REPO / "scripts" / "opd" / "verify_trigger_incidence_artifact.py"
            ),
            "registration_sha256": sha256_file(output_dir / "registration.json"),
            "design_sha256": sha256_file(
                output_dir / "design" / "design.json"
            ),
            "files": records,
            "tree_sha256": sha256_json(records),
        }
        index_path = output_dir / "artifact-index.json"
        with index_path.open("x") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        from scripts.opd.verify_trigger_incidence_artifact import verify_bundle

        verified_manifest = verify_bundle(
            output_dir,
            forbidden_fragments=forbidden_fragments,
        )
        if verified_manifest["tree_sha256"] != manifest["tree_sha256"]:
            raise ExportError("final public-artifact verification disagrees")
        return manifest
    except Exception:
        shutil.rmtree(output_dir)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
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
    manifest = export_bundle(
        registration_path=args.registration,
        design_dir=args.design_dir,
        run_dirs=args.run_dir,
        analysis_dir=args.analysis_dir,
        output_dir=args.out_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
