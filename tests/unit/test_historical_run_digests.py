from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from run_manifest import ManifestError, sha256_json
from scripts.manifest_historical_runs import (
    _validate_output_path,
    _write_identical_or_new,
    build_historical_run_digests,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "dataset" / "raw"
    run = raw / "agent_0" / "runs" / "run_a"
    run.mkdir(parents=True)
    (run / "run.meta.json").write_text('{"run_id":"run_a"}\n')
    (run / "session_1.log").write_text('{"type":"system"}\n')
    source = tmp_path / "SHA256SUMS"
    lines = []
    for path in sorted(run.iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(tmp_path).as_posix()
        lines.append(f"{digest}  {relative}\n")
    source.write_text("".join(lines))
    return raw, source


def _build(raw: Path, source: Path) -> dict:
    return build_historical_run_digests(
        raw,
        source_manifest=source,
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
    )


def test_digest_manifest_is_deterministic_and_self_identifying(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    first = _build(raw, source)
    second = _build(raw, source)

    assert first == second
    identity = first.pop("manifest_sha256")
    assert identity == sha256_json(first)
    assert first["complete"]
    assert first["bundle_count"] == 1
    assert first["bundles"][0]["content"]["file_count"] == 2
    assert first["bundles"][0]["content"]["path"] == "agent_0/runs/run_a"
    assert first["source_manifest_verified_file_count"] == 2


def test_digest_can_record_a_portable_raw_root_label(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    report = build_historical_run_digests(
        raw,
        source_manifest=source,
        raw_root_label="dataset/raw",
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
    )

    assert report["raw_root"] == "dataset/raw"
    assert str(tmp_path) not in str(report)


def test_digest_changes_when_run_content_changes(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    first = _build(raw, source)
    (raw / "agent_0" / "runs" / "run_a" / "session_1.log").write_text(
        '{"type":"assistant"}\n'
    )
    with pytest.raises(ManifestError, match="digest mismatch"):
        _build(raw, source)


def test_missing_run_is_reported_and_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "SHA256SUMS"
    source.write_text(f"{'a' * 64}  unrelated.txt\n")
    report = build_historical_run_digests(
        tmp_path / "raw",
        source_manifest=source,
        groups=["test"],
        claim_runs={"test": ["run_missing"]},
        agents=["agent_0"],
    )

    assert not report["complete"]
    assert report["bundle_count"] == 0
    assert report["missing"] == [
        str(tmp_path / "raw" / "agent_0" / "runs" / "run_missing")
    ]


def test_source_manifest_is_required(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="source manifest does not exist"):
        build_historical_run_digests(
            tmp_path / "raw",
            source_manifest=tmp_path / "missing",
            groups=["test"],
            claim_runs={"test": ["run_a"]},
            agents=["agent_0"],
        )


def test_source_manifest_must_bind_every_selected_bundle_file(
    tmp_path: Path,
) -> None:
    raw, source = _fixture(tmp_path)
    source.write_text(
        "\n".join(
            line for line in source.read_text().splitlines()
            if not line.endswith("run.meta.json")
        ) + "\n"
    )

    with pytest.raises(ManifestError, match="does not bind"):
        _build(raw, source)


def test_source_manifest_rejects_selected_file_missing_from_bundle(
    tmp_path: Path,
) -> None:
    raw, source = _fixture(tmp_path)
    missing_name = "dataset/raw/agent_0/runs/run_a/missing.log"
    source.write_text(
        source.read_text() + f"{'a' * 64}  {missing_name}\n"
    )

    with pytest.raises(ManifestError, match="absent from the recovered bundle"):
        _build(raw, source)


def test_source_manifest_rejects_duplicate_logical_path_aliases(
    tmp_path: Path,
) -> None:
    raw, source = _fixture(tmp_path)
    first_line = source.read_text().splitlines()[0]
    digest, name = first_line.split("  ", 1)
    source.write_text(
        source.read_text() + f"{digest}  {name.removeprefix('dataset/raw/')}\n"
    )

    with pytest.raises(ManifestError, match="unsafe or duplicate"):
        _build(raw, source)


def test_source_manifest_rejects_unsafe_or_duplicate_paths(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    source.write_text(f"{'a' * 64}  ../escape\n")
    with pytest.raises(ManifestError, match="unsafe or duplicate"):
        _build(raw, source)


def test_output_must_not_overlap_inputs_or_use_a_symlink(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    with pytest.raises(ManifestError, match="source manifest"):
        _validate_output_path(source, raw_root=raw, source_manifest=source)
    with pytest.raises(ManifestError, match="hashed raw root"):
        _validate_output_path(
            raw / "report.json",
            raw_root=raw,
            source_manifest=source,
        )
    target = tmp_path / "target.json"
    target.write_text("{}\n")
    link = tmp_path / "report-link.json"
    link.symlink_to(target)
    with pytest.raises(ManifestError, match="symlink"):
        _validate_output_path(link, raw_root=raw, source_manifest=source)


def test_output_writer_is_create_or_identical_only(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    _write_identical_or_new(output, b"same\n")
    _write_identical_or_new(output, b"same\n")
    with pytest.raises(ManifestError, match="overwrite different"):
        _write_identical_or_new(output, b"different\n")
