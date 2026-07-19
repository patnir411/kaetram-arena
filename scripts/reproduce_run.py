#!/usr/bin/env python3
"""Fail-closed clean-clone preflight and optional run reproduction."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from run_manifest import (  # noqa: E402
    ManifestError,
    load_json,
    validate_git_constraints,
    validate_manifest_files,
    validate_manifest_shape,
)


def expand_reproduction_argv(
    argv: list[str], *, repo_root: Path, artifact_root: Path
) -> list[str]:
    """Expand only documented literal path placeholders, without a shell."""
    replacements = {
        "{repo_root}": str(repo_root.resolve()),
        "{artifact_root}": str(artifact_root.resolve()),
    }
    expanded = []
    for arg in argv:
        for placeholder, value in replacements.items():
            arg = arg.replace(placeholder, value)
        expanded.append(arg)
    return expanded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--artifact-root", type=Path,
        help="root of the external artifact bundle (defaults to --root)",
    )
    parser.add_argument("--execute", action="store_true", help="execute reproduction.argv after preflight")
    parser.add_argument("--allow-dirty", action="store_true", help="diagnostic escape hatch")
    parser.add_argument("--allow-commit-mismatch", action="store_true", help="diagnostic escape hatch")
    args = parser.parse_args(argv)
    artifact_root = args.artifact_root or args.root

    try:
        manifest = load_json(args.manifest)
    except ManifestError as exc:
        print(f"preflight FAILED: {exc}", file=sys.stderr)
        return 2
    errors = validate_manifest_shape(manifest)
    if not errors:
        errors.extend(validate_manifest_files(manifest, artifact_root))
        errors.extend(validate_git_constraints(
            manifest,
            args.root,
            require_commit_match=not args.allow_commit_mismatch,
            require_clean=not args.allow_dirty,
        ))
    if errors:
        print("preflight FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    command = expand_reproduction_argv(
        manifest["reproduction"]["argv"],
        repo_root=args.root,
        artifact_root=artifact_root,
    )
    print(f"preflight passed: {args.manifest}")
    print("reproduction argv:", repr(command))
    if not args.execute:
        print("validation only; pass --execute to run the recorded command")
        return 0
    return subprocess.run(command, cwd=args.root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
