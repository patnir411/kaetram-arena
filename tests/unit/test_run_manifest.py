from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from run_manifest import (
    ManifestError,
    atomic_write_json,
    build_input_provenance,
    build_run_manifest,
    capture_git_state,
    hash_path,
    remote_content,
    sha256_bytes,
    tool_schema_record,
    validate_manifest_files,
    validate_git_constraints,
    validate_manifest_shape,
)
from scripts.bootstrap_reproduction import clone_checkout, reproduction_command
from scripts.reproduce_run import expand_reproduction_argv


GIT = {
    "repository": "git@example.test:owner/repo.git",
    "commit": "a" * 40,
    "branch": "main",
    "dirty": False,
    "dirty_paths": [],
}
STAMP = "2026-07-18T20:00:00+00:00"


def _sidecar(tmp_path: Path, kind: str, content: dict) -> tuple[dict, dict]:
    record = build_input_provenance(
        kind=kind,
        name=f"test-{kind}",
        reference=f"test/{kind}",
        content=content,
        source="unit test",
        created_at_utc=STAMP,
        producer_git=GIT,
    )
    path = tmp_path / f"{kind}.provenance.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record, hash_path(path, root=tmp_path)


def _manifest_fixture(tmp_path: Path) -> dict:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("system prompt\n")
    config = tmp_path / "config.json"
    config.write_text('{"temperature":0}\n')
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"messages":[]}\n')
    output = tmp_path / "results.json"
    output.write_text('{"score":15}\n')
    dataset_record, dataset_file = _sidecar(
        tmp_path, "dataset", hash_path(dataset, root=tmp_path)
    )
    checkpoint_record, checkpoint_file = _sidecar(
        tmp_path, "checkpoint", remote_content("modal:/checkpoints/r2", "b" * 64)
    )
    artifact = hash_path(output, root=tmp_path)
    artifact["name"] = "results"
    return build_run_manifest(
        run={
            "run_id": "run_test",
            "model": "2b-opd-r2",
            "harness": "qwen",
            "scenario": "core3-6h",
            "recovery_enabled": False,
        },
        source_git=GIT,
        prompt=hash_path(prompt, root=tmp_path),
        config=hash_path(config, root=tmp_path),
        dataset_record=dataset_record,
        dataset_provenance_file=dataset_file,
        checkpoint_record=checkpoint_record,
        checkpoint_provenance_file=checkpoint_file,
        artifacts=[artifact],
        reproduction_argv=["python3", "scripts/analyze.py", "--run", "run_test"],
        created_at_utc=STAMP,
    )


def test_directory_hash_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "b.txt").write_text("b")
    (data / "a.txt").write_text("a")
    first = hash_path(data, root=tmp_path)
    assert hash_path(data, root=tmp_path) == first
    assert first["file_count"] == 2
    (data / "a.txt").write_text("changed")
    assert hash_path(data, root=tmp_path)["sha256"] != first["sha256"]


def test_hash_path_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ManifestError, match="symlinks"):
        hash_path(link)


def test_manifest_builder_is_deterministic_and_self_identifying(tmp_path: Path) -> None:
    first = _manifest_fixture(tmp_path)
    second = _manifest_fixture(tmp_path)
    assert first == second
    assert validate_manifest_shape(first) == []
    assert first["provenance"]["tool_schema"] == tool_schema_record()


def test_manifest_detects_tampering(tmp_path: Path) -> None:
    manifest = _manifest_fixture(tmp_path)
    manifest["run"]["model"] = "different"
    assert "manifest_sha256 does not match manifest contents" in validate_manifest_shape(manifest)


def test_manifest_requires_sanitized_clone_repository(tmp_path: Path) -> None:
    manifest = _manifest_fixture(tmp_path)
    manifest.pop("manifest_sha256")
    manifest["source_git"]["repository"] = (
        "https://user:token@example.test/org/repo.git?signature=secret"
    )
    errors = validate_manifest_shape(manifest, check_identity=False)
    assert any("must not contain credentials" in error for error in errors)


@pytest.mark.parametrize(
    "unsafe_arg",
    [
        "https://inference.example.test/v1",
        "--api-key=secret-value",
        "--access-token",
        "authorization:Bearer-value",
    ],
)
def test_manifest_rejects_endpoints_and_secret_bearing_argv(
    tmp_path: Path, unsafe_arg: str
) -> None:
    manifest = _manifest_fixture(tmp_path)
    manifest.pop("manifest_sha256")
    manifest["reproduction"]["argv"].append(unsafe_arg)
    errors = validate_manifest_shape(manifest, check_identity=False)
    assert any("reproduction.argv" in error for error in errors)


def test_file_validation_detects_dataset_drift(tmp_path: Path) -> None:
    manifest = _manifest_fixture(tmp_path)
    assert validate_manifest_files(manifest, tmp_path) == []
    (tmp_path / "dataset.jsonl").write_text("changed\n")
    errors = validate_manifest_files(manifest, tmp_path)
    assert any("dataset.record.content.sha256 mismatch" in error for error in errors)


def test_atomic_write_is_create_only(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    atomic_write_json(target, {"one": 1})
    with pytest.raises(ManifestError, match="refusing to overwrite"):
        atomic_write_json(target, {"two": 2})
    assert json.loads(target.read_text()) == {"one": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_remote_content_requires_real_sha256() -> None:
    with pytest.raises(ManifestError, match="SHA-256"):
        remote_content("modal:/checkpoint", "not-a-digest")
    assert remote_content("modal:/checkpoint", sha256_bytes(b"weights"))["kind"] == "remote"


def test_dataset_provenance_rejects_remote_only_content() -> None:
    with pytest.raises(ManifestError, match="locally hashed"):
        build_input_provenance(
            kind="dataset",
            name="remote-dataset",
            reference="mutable-name",
            content=remote_content("remote-dataset", "b" * 64),
            source="unit test",
            created_at_utc=STAMP,
            producer_git=GIT,
        )


def test_capture_git_state_records_dirty_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    assert capture_git_state(tmp_path)["dirty"] is False
    tracked.write_text("dirty\n")
    dirty = capture_git_state(tmp_path)
    assert dirty["dirty"] is True
    assert "tracked.txt" in dirty["dirty_paths"]


def test_capture_git_state_strips_remote_credentials_and_query(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "https://user:token@example.test/org/repo.git?signature=secret"],
        check=True,
    )
    assert capture_git_state(tmp_path)["repository"] == "https://example.test/org/repo.git"


def test_clone_checkout_creates_exact_detached_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "one"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    destination = tmp_path / "clone"
    clone_checkout(str(source), commit, destination)
    cloned_commit = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert cloned_commit == commit
    assert subprocess.run(
        ["git", "-C", str(destination), "symbolic-ref", "-q", "HEAD"],
        check=False,
    ).returncode != 0
    with pytest.raises(ManifestError, match="refusing to overwrite"):
        clone_checkout(str(source), commit, destination)


def test_bootstrap_runs_reproducer_from_exact_clone(tmp_path: Path) -> None:
    manifest = tmp_path / "input" / "manifest.json"
    bundle = tmp_path / "bundle"
    clone = tmp_path / "exact-source-clone"

    command = reproduction_command(manifest, bundle, clone, execute=True)

    assert command[1] == str(clone.resolve() / "scripts" / "reproduce_run.py")
    assert command[2:] == [
        str(manifest.resolve()),
        "--root",
        str(clone.resolve()),
        "--artifact-root",
        str(bundle.resolve()),
        "--execute",
    ]


def test_reproduction_placeholders_expand_to_external_bundle(tmp_path: Path) -> None:
    repo_root = tmp_path / "clean-clone"
    artifact_root = tmp_path / "external-bundle"
    command = expand_reproduction_argv(
        [
            "python3",
            "{repo_root}/scripts/analyze.py",
            "--input={artifact_root}/artifacts/raw.jsonl",
        ],
        repo_root=repo_root,
        artifact_root=artifact_root,
    )
    assert command == [
        "python3",
        str(repo_root.resolve() / "scripts" / "analyze.py"),
        f"--input={artifact_root.resolve()}/artifacts/raw.jsonl",
    ]


def test_strict_git_validation_rejects_manifest_recorded_as_dirty(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    current = capture_git_state(tmp_path)
    recorded = {**current, "dirty": True, "dirty_paths": ["old-change.txt"]}
    errors = validate_git_constraints(
        {"source_git": recorded}, tmp_path, require_commit_match=True, require_clean=True
    )
    assert "manifest was created from a dirty git worktree" in errors
