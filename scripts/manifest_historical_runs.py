#!/usr/bin/env python3
"""Build compact, self-identifying digests for recovered historical runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import ManifestError, hash_path, sha256_json
from scripts.audit_historical_artifacts import AGENTS, CLAIM_RUNS


SCHEMA_VERSION = "kaetram-historical-run-digests-v1"
_SHA256SUM_RE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")
HISTORICAL_RUNS = {
    **CLAIM_RUNS,
    "r10_source_corpus": (
        "run_20260504_140418",
        "run_20260504_172157",
        "run_20260504_221206",
        "run_20260505_150033",
        "run_20260505_214542",
    ),
}


def _source_manifest_record(path: Path) -> dict:
    return {
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _validate_output_path(
    output: Path,
    *,
    raw_root: Path,
    source_manifest: Path,
) -> None:
    if output.is_symlink():
        raise ManifestError(f"output must not be a symlink: {output}")
    resolved = output.resolve()
    raw_resolved = raw_root.resolve()
    source_resolved = source_manifest.resolve()
    if resolved == source_resolved:
        raise ManifestError("output must not overwrite the source manifest")
    if resolved == raw_resolved or resolved.is_relative_to(raw_resolved):
        raise ManifestError("output must not be inside the hashed raw root")


def _write_identical_or_new(output: Path, rendered: bytes) -> None:
    if output.exists():
        if not output.is_file() or output.read_bytes() != rendered:
            raise ManifestError(f"refusing to overwrite different output: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(rendered)


def _logical_source_path(name: str) -> str:
    prefix = "dataset/raw/"
    return name[len(prefix):] if name.startswith(prefix) else name


def _source_manifest_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read source manifest: {path}") from exc
    for line_no, line in enumerate(lines, 1):
        match = _SHA256SUM_RE.fullmatch(line)
        if not match:
            raise ManifestError(f"invalid source-manifest line {line_no}: {line!r}")
        digest, name = match.groups()
        relative = Path(name)
        logical_name = _logical_source_path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not logical_name
            or logical_name in entries
        ):
            raise ManifestError(f"unsafe or duplicate source-manifest path: {name!r}")
        entries[logical_name] = digest
    if not entries:
        raise ManifestError("source manifest is empty")
    return entries


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_bundle_source_entries(
    run_dir: Path,
    *,
    raw_root: Path,
    entries: Mapping[str, str],
) -> set[str]:
    verified: set[str] = set()
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ManifestError(f"bundle contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(raw_root).as_posix()
        if relative not in entries:
            raise ManifestError(f"source manifest does not bind {relative}")
        actual = _file_sha256(path)
        if actual != entries[relative]:
            raise ManifestError(
                f"source-manifest digest mismatch for {relative}: "
                f"expected {entries[relative]}, got {actual}"
            )
        verified.add(relative)
    return verified


def build_historical_run_digests(
    raw_root: Path,
    *,
    source_manifest: Path,
    raw_root_label: str | None = None,
    groups: Iterable[str] | None = None,
    claim_runs: Mapping[str, Iterable[str]] = HISTORICAL_RUNS,
    agents: Iterable[str] = AGENTS,
) -> dict:
    """Hash every selected agent/run directory and its relative file tree."""
    selected_groups = tuple(groups) if groups is not None else tuple(sorted(claim_runs))
    unknown = sorted(set(selected_groups) - set(claim_runs))
    if unknown:
        raise ManifestError(f"unknown claim group(s): {', '.join(unknown)}")
    if not source_manifest.is_file():
        raise ManifestError(f"source manifest does not exist: {source_manifest}")
    if source_manifest.is_symlink():
        raise ManifestError("source manifest must not be a symlink")
    if raw_root.is_symlink():
        raise ManifestError("raw root must not be a symlink")
    source_entries = _source_manifest_entries(source_manifest)

    bundles: list[dict] = []
    missing: list[str] = []
    verified_source_files = 0
    verified_source_paths: set[str] = set()
    selected_prefixes: set[str] = set()
    for group in selected_groups:
        for run_id in claim_runs[group]:
            for agent in sorted(set(agents)):
                selected_prefixes.add(f"{agent}/runs/{run_id}/")
                run_dir = raw_root / agent / "runs" / run_id
                if not run_dir.is_dir():
                    missing.append(str(run_dir))
                    continue
                verified = _verify_bundle_source_entries(
                    run_dir,
                    raw_root=raw_root,
                    entries=source_entries,
                )
                verified_source_files += len(verified)
                verified_source_paths.update(verified)
                descriptor = hash_path(run_dir, root=raw_root)
                bundles.append({
                    "claim_group": group,
                    "run_id": run_id,
                    "agent": agent,
                    "content": descriptor,
                })

    if not missing:
        expected_source_paths = {
            name
            for name in source_entries
            if any(name.startswith(prefix) for prefix in selected_prefixes)
        }
        omitted = sorted(expected_source_paths - verified_source_paths)
        if omitted:
            preview = ", ".join(omitted[:3])
            suffix = "" if len(omitted) <= 3 else f" (+{len(omitted) - 3} more)"
            raise ManifestError(
                "source manifest names selected files absent from the recovered "
                f"bundle: {preview}{suffix}"
            )

    report = {
        "schema_version": SCHEMA_VERSION,
        "raw_root": raw_root_label if raw_root_label is not None else str(raw_root),
        "source_manifest": _source_manifest_record(source_manifest),
        "source_manifest_verified_file_count": verified_source_files,
        "claim_groups": {
            group: list(claim_runs[group])
            for group in selected_groups
        },
        "bundle_count": len(bundles),
        "bundles": bundles,
        "missing": missing,
        "complete": not missing,
    }
    report["manifest_sha256"] = sha256_json(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=Path("dataset/raw"))
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument(
        "--raw-root-label",
        help=(
            "portable logical path recorded in the report (hashing still reads "
            "from --raw-root)"
        ),
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        choices=sorted(HISTORICAL_RUNS),
        help="claim groups to include (default: all)",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="return success even when a required run directory is missing",
    )
    args = parser.parse_args(argv)

    try:
        report = build_historical_run_digests(
            args.raw_root,
            source_manifest=args.source_manifest,
            raw_root_label=args.raw_root_label,
            groups=args.groups,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rendered = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        if args.out:
            _validate_output_path(
                args.out,
                raw_root=args.raw_root,
                source_manifest=args.source_manifest,
            )
            _write_identical_or_new(args.out, rendered)
            print(args.out)
        else:
            print(rendered.decode("utf-8"), end="")
    except (ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
