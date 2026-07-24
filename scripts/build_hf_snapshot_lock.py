#!/usr/bin/env python3
"""Build an immutable lock for the public Kaetram Hugging Face snapshots."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import sha256_json


SCHEMA_VERSION = "kaetram-hf-snapshot-lock-v1"
SNAPSHOTS = {
    "base_2b": {
        "repo_id": "Qwen/Qwen3.5-2B",
        "revision": "15852e8c16360a2fea060d615a32b45270f8a8fc",
    },
    "opd_r1_2b": {
        "repo_id": "patnir41/kaetram-qwen3.5-2b-opd-r1",
        "revision": "d24e0c216153ce4ce4949d66ba13e05211cc3a69",
    },
    "opd_r2_2b": {
        "repo_id": "patnir41/kaetram-qwen3.5-2b-opd-r2",
        "revision": "2b51ca6d31869368fad562c03d786e21de60899c",
    },
    "opd_r3_2b": {
        "repo_id": "patnir41/kaetram-qwen3.5-2b-opd-r3",
        "revision": "c26ae0691bd16788102d4231e598c7d16c90f3e0",
    },
    "teacher_4b": {
        "repo_id": "Qwen/Qwen3.5-4B",
        "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
    },
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}")


class LockBuildError(RuntimeError):
    """Raised when the Hub response cannot produce an immutable lock."""


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def build_snapshot_record(name: str, expected: dict, payload: dict) -> dict:
    """Convert a Hub ``?blobs=true`` response into a pinned file record."""
    if payload.get("sha") != expected["revision"]:
        raise LockBuildError(
            f"{name}: Hub returned revision {payload.get('sha')!r}, "
            f"expected {expected['revision']}"
        )
    if payload.get("private") is True or payload.get("gated") not in (False, None):
        raise LockBuildError(f"{name}: snapshot is private or gated")

    files: list[dict] = []
    seen: set[str] = set()
    for sibling in sorted(payload.get("siblings") or [], key=lambda item: item["rfilename"]):
        path = sibling.get("rfilename")
        size = sibling.get("size")
        if not isinstance(path, str) or not _safe_relative_path(path):
            raise LockBuildError(f"{name}: unsafe file path {path!r}")
        if path in seen:
            raise LockBuildError(f"{name}: duplicate file path {path}")
        seen.add(path)
        if not isinstance(size, int) or size < 0:
            raise LockBuildError(f"{name}/{path}: missing byte size")

        record = {"path": path, "size_bytes": size}
        lfs = sibling.get("lfs")
        if isinstance(lfs, dict):
            digest = lfs.get("sha256")
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise LockBuildError(f"{name}/{path}: invalid LFS SHA-256")
            if lfs.get("size") != size:
                raise LockBuildError(f"{name}/{path}: LFS and file sizes disagree")
            record["sha256"] = digest
        else:
            blob_id = sibling.get("blobId")
            if not isinstance(blob_id, str) or not _GIT_SHA1_RE.fullmatch(blob_id):
                raise LockBuildError(f"{name}/{path}: invalid Git blob identity")
            record["git_blob_sha1"] = blob_id
        files.append(record)

    if not files:
        raise LockBuildError(f"{name}: snapshot contains no files")
    return {
        "repo_type": "model",
        "repo_id": expected["repo_id"],
        "revision": expected["revision"],
        "file_count": len(files),
        "size_bytes": sum(item["size_bytes"] for item in files),
        "files": files,
    }


def fetch_snapshot_metadata(repo_id: str, revision: str) -> dict:
    encoded_repo = quote(repo_id, safe="/")
    url = (
        f"https://huggingface.co/api/models/{encoded_repo}/revision/"
        f"{revision}?blobs=true"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "kaetram-artifact-lock/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LockBuildError(f"failed to fetch {repo_id}@{revision}: {exc}") from exc


def build_lock() -> dict:
    snapshots = {}
    for name, expected in SNAPSHOTS.items():
        payload = fetch_snapshot_metadata(expected["repo_id"], expected["revision"])
        snapshots[name] = build_snapshot_record(name, expected, payload)
    lock = {
        "schema_version": SCHEMA_VERSION,
        "source": "https://huggingface.co",
        "snapshots": snapshots,
    }
    lock["lock_sha256"] = sha256_json(lock)
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(
            "research/experiments/provenance/public-hf-snapshots.lock.json"
        ),
    )
    args = parser.parse_args(argv)
    try:
        lock = build_lock()
    except LockBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
