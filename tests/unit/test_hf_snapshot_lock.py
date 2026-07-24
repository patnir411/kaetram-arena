from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from run_manifest import sha256_json
from scripts.build_hf_snapshot_lock import (
    LockBuildError,
    SCHEMA_VERSION,
    build_snapshot_record,
)
from scripts.fetch_hf_snapshot import (
    SnapshotError,
    fetch_snapshot,
    file_identity,
    load_lock,
    locked_snapshot_tree_sha256,
    snapshot_url,
)


def _signed_lock(file_record: dict) -> dict:
    lock = {
        "schema_version": SCHEMA_VERSION,
        "source": "https://huggingface.co",
        "snapshots": {
            "model": {
                "repo_type": "model",
                "repo_id": "owner/model",
                "revision": "a" * 40,
                "file_count": 1,
                "size_bytes": file_record["size_bytes"],
                "files": [file_record],
            }
        },
    }
    lock["lock_sha256"] = sha256_json(lock)
    return lock


def test_snapshot_record_preserves_lfs_and_git_identities() -> None:
    expected = {"repo_id": "owner/model", "revision": "a" * 40}
    payload = {
        "sha": "a" * 40,
        "private": False,
        "gated": False,
        "siblings": [
            {
                "rfilename": "config.json",
                "size": 2,
                "blobId": "b" * 40,
                "lfs": None,
            },
            {
                "rfilename": "model.safetensors",
                "size": 3,
                "blobId": "c" * 40,
                "lfs": {"sha256": "d" * 64, "size": 3},
            },
        ],
    }
    record = build_snapshot_record("model", expected, payload)
    assert record["files"] == [
        {"path": "config.json", "size_bytes": 2, "git_blob_sha1": "b" * 40},
        {"path": "model.safetensors", "size_bytes": 3, "sha256": "d" * 64},
    ]


def test_snapshot_record_rejects_revision_drift_and_traversal() -> None:
    expected = {"repo_id": "owner/model", "revision": "a" * 40}
    with pytest.raises(LockBuildError, match="Hub returned revision"):
        build_snapshot_record(
            "model",
            expected,
            {"sha": "b" * 40, "private": False, "gated": False, "siblings": []},
        )
    with pytest.raises(LockBuildError, match="unsafe file path"):
        build_snapshot_record(
            "model",
            expected,
            {
                "sha": "a" * 40,
                "private": False,
                "gated": False,
                "siblings": [
                    {"rfilename": "../escape", "size": 1, "blobId": "b" * 40}
                ],
            },
        )


def test_lock_identity_and_path_validation(tmp_path: Path) -> None:
    lock = _signed_lock(
        {"path": "config.json", "size_bytes": 2, "git_blob_sha1": "b" * 40}
    )
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    assert load_lock(path) == lock

    lock["snapshots"]["model"]["files"][0]["path"] = "../escape"
    path.write_text(json.dumps(lock))
    with pytest.raises(SnapshotError, match="identity does not match"):
        load_lock(path)


def test_file_identity_supports_lfs_sha256_and_git_blob_sha1(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    path.write_bytes(b"abc")
    sha256_record = {
        "path": "artifact",
        "size_bytes": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(),
    }
    ok, _ = file_identity(path, sha256_record)
    assert ok

    git_digest = hashlib.sha1(b"blob 3\0abc").hexdigest()
    git_record = {
        "path": "artifact",
        "size_bytes": 3,
        "git_blob_sha1": git_digest,
    }
    ok, _ = file_identity(path, git_record)
    assert ok
    path.write_bytes(b"abd")
    ok, _ = file_identity(path, git_record)
    assert not ok


def test_snapshot_url_pins_revision_and_encodes_path() -> None:
    snapshot = {"repo_id": "owner/model", "revision": "a" * 40}
    assert snapshot_url(snapshot, "dir/file name.json") == (
        "https://huggingface.co/owner/model/resolve/"
        + "a" * 40
        + "/dir/file%20name.json?download=true"
    )


def test_verify_only_requires_exact_locked_snapshot_closure(tmp_path: Path) -> None:
    snapshot = _signed_lock({
        "path": "nested/config.json",
        "size_bytes": 2,
        "git_blob_sha1": hashlib.sha1(b"blob 2\0{}").hexdigest(),
    })["snapshots"]["model"]
    destination = tmp_path / "model"
    (destination / "nested").mkdir(parents=True)
    (destination / "nested" / "config.json").write_text("{}")

    receipt = fetch_snapshot(snapshot, destination, verify_only=True)
    assert receipt["snapshot_tree_sha256"] == locked_snapshot_tree_sha256(snapshot)

    (destination / "unexpected.json").write_text("{}")
    with pytest.raises(SnapshotError, match="unlocked runtime paths"):
        fetch_snapshot(snapshot, destination, verify_only=True)


def test_snapshot_closure_rejects_unlocked_symlinks_and_directories(
    tmp_path: Path,
) -> None:
    snapshot = _signed_lock({
        "path": "config.json",
        "size_bytes": 2,
        "git_blob_sha1": hashlib.sha1(b"blob 2\0{}").hexdigest(),
    })["snapshots"]["model"]
    destination = tmp_path / "model"
    destination.mkdir()
    (destination / "config.json").write_text("{}")
    (destination / "empty").mkdir()
    with pytest.raises(SnapshotError, match="unlocked runtime paths"):
        fetch_snapshot(snapshot, destination, verify_only=True)

    (destination / "empty").rmdir()
    (destination / "alias").symlink_to(destination / "config.json")
    with pytest.raises(SnapshotError, match="symlinked snapshot path"):
        fetch_snapshot(snapshot, destination, verify_only=True)
