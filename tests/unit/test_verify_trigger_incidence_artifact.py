import json
from pathlib import Path

import pytest

from scripts.opd.export_trigger_incidence_artifact import ExportError
from scripts.opd.verify_trigger_incidence_artifact import verify_bundle
from tests.unit.test_export_trigger_incidence_artifact import _export, _fixture


def test_verifies_exported_bundle_without_modifying_it(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    manifest = _export(fixture, output)
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }

    result = verify_bundle(output)

    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert result["tree_sha256"] == manifest["tree_sha256"]
    assert result["scheduled_requests"] == 12
    assert result["successful_requests"] == 12


def test_rejects_tampered_indexed_file(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)
    path = output / "analysis" / "cells.csv"
    path.write_text(path.read_text() + "\n")

    with pytest.raises(ExportError, match="artifact file mismatch"):
        verify_bundle(output)


def test_rejects_unindexed_file(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)
    (output / "unindexed.txt").write_text("extra")

    with pytest.raises(ExportError, match="artifact tree differs"):
        verify_bundle(output)


def test_rejects_noncanonical_run_directories(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)
    run_dir = output / "runs" / "base"
    moved = output / "runs" / "renamed"
    run_dir.rename(moved)
    index = json.loads((output / "artifact-index.json").read_text())
    for record in index["files"]:
        if record["path"].startswith("runs/base/"):
            record["path"] = record["path"].replace("runs/base/", "runs/renamed/", 1)
    index["files"].sort(key=lambda record: record["path"])
    from scripts.opd.export_trigger_incidence_artifact import sha256_json

    index["tree_sha256"] = sha256_json(index["files"])
    (output / "artifact-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )

    with pytest.raises(ExportError, match="run directories are not canonical"):
        verify_bundle(output)
