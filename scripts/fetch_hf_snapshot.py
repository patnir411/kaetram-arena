#!/usr/bin/env python3
"""Download or verify a public Hugging Face snapshot from the checked-in lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import sha256_json
from scripts.build_hf_snapshot_lock import SCHEMA_VERSION


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}")


class SnapshotError(RuntimeError):
    """Raised when a lock or downloaded snapshot fails verification."""


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def load_lock(path: Path) -> dict:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot lock {path}: {exc}") from exc
    if lock.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError(f"snapshot lock schema must be {SCHEMA_VERSION}")
    identity = lock.get("lock_sha256")
    unsigned = dict(lock)
    unsigned.pop("lock_sha256", None)
    if identity != sha256_json(unsigned):
        raise SnapshotError("snapshot lock identity does not match its contents")

    snapshots = lock.get("snapshots")
    if not isinstance(snapshots, dict) or not snapshots:
        raise SnapshotError("snapshot lock has no snapshots")
    for name, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            raise SnapshotError(f"{name}: snapshot record must be an object")
        files = snapshot.get("files")
        if not isinstance(files, list) or not files:
            raise SnapshotError(f"{name}: snapshot has no files")
        seen: set[str] = set()
        for record in files:
            value = record.get("path") if isinstance(record, dict) else None
            if not isinstance(value, str) or not _safe_relative_path(value):
                raise SnapshotError(f"{name}: unsafe locked path {value!r}")
            if value in seen:
                raise SnapshotError(f"{name}: duplicate locked path {value}")
            seen.add(value)
            size = record.get("size_bytes")
            if not isinstance(size, int) or size < 0:
                raise SnapshotError(f"{name}/{value}: invalid size")
            sha256 = record.get("sha256")
            git_sha1 = record.get("git_blob_sha1")
            valid_sha256 = isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256)
            valid_git = isinstance(git_sha1, str) and _GIT_SHA1_RE.fullmatch(git_sha1)
            if bool(valid_sha256) == bool(valid_git):
                raise SnapshotError(
                    f"{name}/{value}: require exactly one valid content identity"
                )
    return lock


def snapshot_url(snapshot: dict, relative_path: str) -> str:
    repo_id = quote(snapshot["repo_id"], safe="/")
    revision = quote(snapshot["revision"], safe="")
    path = quote(relative_path, safe="/")
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{path}?download=true"


def file_identity(path: Path, record: dict) -> tuple[bool, str]:
    """Verify size plus the LFS SHA-256 or canonical Git blob SHA-1."""
    if not path.is_file() or path.is_symlink():
        return False, "missing or non-regular file"
    expected_size = record["size_bytes"]
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        return False, f"size {actual_size}, expected {expected_size}"

    sha256 = hashlib.sha256() if "sha256" in record else None
    git_sha1 = hashlib.sha1()
    git_sha1.update(f"blob {actual_size}\0".encode())
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            git_sha1.update(chunk)
            if sha256 is not None:
                sha256.update(chunk)
    if sha256 is not None:
        actual = sha256.hexdigest()
        expected = record["sha256"]
        return actual == expected, f"sha256 {actual}, expected {expected}"
    actual = git_sha1.hexdigest()
    expected = record["git_blob_sha1"]
    return actual == expected, f"git blob {actual}, expected {expected}"


def _reject_symlink_parents(root: Path, target: Path) -> None:
    current = target
    while current != root:
        if current.is_symlink():
            raise SnapshotError(f"refusing symlinked snapshot path: {current}")
        current = current.parent


def locked_snapshot_tree_sha256(snapshot: dict) -> str:
    """Identify the complete locked snapshot tree, not only its weights file."""
    files = []
    for record in sorted(snapshot["files"], key=lambda item: item["path"]):
        files.append({
            "path": record["path"],
            "size_bytes": record["size_bytes"],
            "identity": record.get("sha256") or record["git_blob_sha1"],
        })
    return sha256_json({
        "repo_id": snapshot["repo_id"],
        "revision": snapshot["revision"],
        "files": files,
    })


def _verify_snapshot_closure(snapshot: dict, destination: Path) -> None:
    """Reject runtime paths absent from the content-addressed checked-in lock."""
    expected_files = {record["path"] for record in snapshot["files"]}
    expected_dirs: set[str] = set()
    for relative in expected_files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            expected_dirs.add(parent.as_posix())
            parent = parent.parent

    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for path in destination.rglob("*"):
        relative = path.relative_to(destination).as_posix()
        if path.is_symlink():
            raise SnapshotError(f"refusing symlinked snapshot path: {relative}")
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_dirs.add(relative)
        else:
            raise SnapshotError(f"refusing non-regular snapshot path: {relative}")

    unexpected_files = sorted(actual_files - expected_files)
    unexpected_dirs = sorted(actual_dirs - expected_dirs)
    if unexpected_files or unexpected_dirs:
        detail = []
        if unexpected_files:
            detail.append(f"files={unexpected_files}")
        if unexpected_dirs:
            detail.append(f"directories={unexpected_dirs}")
        raise SnapshotError(
            "snapshot contains unlocked runtime paths: " + ", ".join(detail)
        )


def fetch_snapshot(snapshot: dict, destination: Path, *, verify_only: bool) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise SnapshotError(f"refusing symlinked destination: {destination}")
    curl = shutil.which("curl")
    if not verify_only and not curl:
        raise SnapshotError("curl is required to download snapshots")

    verified: list[dict] = []
    for record in snapshot["files"]:
        relative = record["path"]
        target = destination.joinpath(*PurePosixPath(relative).parts)
        _reject_symlink_parents(destination, target)
        ok, detail = file_identity(target, record)
        if not ok:
            if verify_only:
                raise SnapshotError(f"{relative}: {detail}")
            if target.exists():
                raise SnapshotError(
                    f"{relative}: existing file failed verification ({detail}); "
                    "remove it explicitly before retrying"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            command = [
                curl,
                "--location",
                "--fail",
                "--show-error",
                "--retry",
                "4",
                "--retry-all-errors",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                snapshot_url(snapshot, relative),
            ]
            result = subprocess.run(command, check=False)
            if result.returncode != 0:
                raise SnapshotError(f"{relative}: curl exited {result.returncode}")
            ok, detail = file_identity(partial, record)
            if not ok:
                raise SnapshotError(f"{relative}: downloaded file failed verification ({detail})")
            os.replace(partial, target)
        verified.append({
            "path": relative,
            "size_bytes": record["size_bytes"],
            "identity": record.get("sha256") or record["git_blob_sha1"],
        })
    _verify_snapshot_closure(snapshot, destination)
    return {
        "repo_id": snapshot["repo_id"],
        "revision": snapshot["revision"],
        "destination": str(destination),
        "file_count": len(verified),
        "size_bytes": sum(record["size_bytes"] for record in snapshot["files"]),
        "snapshot_tree_sha256": locked_snapshot_tree_sha256(snapshot),
        "files": verified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(
            "research/experiments/provenance/public-hf-snapshots.lock.json"
        ),
    )
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--snapshot", nargs="+", required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    try:
        lock = load_lock(args.lock)
        unknown = sorted(set(args.snapshot) - set(lock["snapshots"]))
        if unknown:
            raise SnapshotError(f"unknown snapshot(s): {', '.join(unknown)}")
        snapshots = [
            fetch_snapshot(
                lock["snapshots"][name],
                args.dest / name,
                verify_only=args.verify_only,
            )
            for name in args.snapshot
        ]
    except SnapshotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    receipt = {
        "schema_version": "kaetram-hf-snapshot-receipt-v1",
        "lock_sha256": lock["lock_sha256"],
        "snapshots": snapshots,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(rendered, encoding="utf-8")
        print(args.receipt)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
