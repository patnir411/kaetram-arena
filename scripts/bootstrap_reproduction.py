#!/usr/bin/env python3
"""Clone the manifest's exact source commit and run clean-clone preflight."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from run_manifest import ManifestError, load_json, validate_manifest_shape  # noqa: E402


def clone_checkout(repository: str, commit: str, destination: Path) -> None:
    """Create a new detached checkout, refusing any existing destination."""
    if destination.exists():
        raise ManifestError(f"refusing to overwrite clone destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "--no-checkout", "--", repository, str(destination)],
        check=False,
    )
    if clone.returncode != 0:
        raise ManifestError(f"git clone failed with exit code {clone.returncode}")
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", commit],
        check=False,
    )
    if checkout.returncode != 0:
        raise ManifestError(f"git checkout failed with exit code {checkout.returncode}")


def reproduction_command(
    manifest: Path,
    bundle_root: Path,
    clone_root: Path,
    *,
    execute: bool = False,
) -> list[str]:
    """Build the preflight command from the exact checked-out source tree."""
    command = [
        sys.executable,
        str(clone_root.resolve() / "scripts" / "reproduce_run.py"),
        str(manifest.resolve()),
        "--root",
        str(clone_root.resolve()),
        "--artifact-root",
        str(bundle_root.resolve()),
    ]
    if execute:
        command.append("--execute")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--clone-to", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_json(args.manifest)
        errors = validate_manifest_shape(manifest)
        if errors:
            raise ManifestError("; ".join(errors))
        source = manifest["source_git"]
        clone_checkout(source["repository"], source["commit"], args.clone_to.resolve())
    except ManifestError as exc:
        print(f"bootstrap FAILED: {exc}", file=sys.stderr)
        return 1

    command = reproduction_command(
        args.manifest,
        args.bundle_root,
        args.clone_to,
        execute=args.execute,
    )
    try:
        return subprocess.run(command, check=False).returncode
    except FileNotFoundError as exc:
        print(f"bootstrap FAILED: executable not found: {command[0]}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
