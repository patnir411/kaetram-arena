from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_manifest import sha256_json
from scripts import capture_analysis_provenance as provenance


COMMIT = "a" * 40


def _clean_git() -> dict:
    return {
        "repository": None,
        "commit": COMMIT,
        "branch": "test",
        "dirty": False,
        "dirty_paths": [],
    }


def test_analysis_provenance_binds_clean_exact_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "analysis.py"
    script.write_text("print('bound')\n")
    monkeypatch.setattr(provenance, "capture_git_state", lambda _root: _clean_git())

    report = provenance.build_analysis_provenance(tmp_path, [script])
    identity = report["manifest_sha256"]
    unsigned = dict(report)
    unsigned.pop("manifest_sha256")
    assert identity == sha256_json(unsigned)
    assert report["source_git_commit"] == COMMIT
    assert report["generation_worktree_clean"] is True
    assert report["implementation_files"][0]["path"] == "analysis.py"

    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(report))
    verified = provenance.load_and_verify_analysis_provenance(
        receipt,
        repo_root=tmp_path,
        expected_files=[script],
    )
    assert verified == report


def test_analysis_provenance_rejects_dirty_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "analysis.py"
    script.write_text("print('dirty')\n")
    dirty = _clean_git()
    dirty.update({"dirty": True, "dirty_paths": ["analysis.py"]})
    monkeypatch.setattr(provenance, "capture_git_state", lambda _root: dirty)

    with pytest.raises(provenance.AnalysisProvenanceError, match="clean worktree"):
        provenance.build_analysis_provenance(tmp_path, [script])


def test_analysis_provenance_rejects_changed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "analysis.py"
    script.write_text("print('before')\n")
    monkeypatch.setattr(provenance, "capture_git_state", lambda _root: _clean_git())
    report = provenance.build_analysis_provenance(tmp_path, [script])
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(report))
    script.write_text("print('after')\n")

    with pytest.raises(
        provenance.AnalysisProvenanceError,
        match="implementation bytes",
    ):
        provenance.load_and_verify_analysis_provenance(
            receipt,
            repo_root=tmp_path,
            expected_files=[script],
        )


def test_analysis_provenance_output_is_create_or_identical_only(
    tmp_path: Path,
) -> None:
    implementation = tmp_path / "analysis.py"
    implementation.write_text("pass\n")
    output = tmp_path / "receipt.json"
    provenance._write_identical_or_new(
        output,
        b"same\n",
        forbidden_files=[implementation],
    )
    provenance._write_identical_or_new(
        output,
        b"same\n",
        forbidden_files=[implementation],
    )
    with pytest.raises(provenance.AnalysisProvenanceError, match="overwrite different"):
        provenance._write_identical_or_new(
            output,
            b"different\n",
            forbidden_files=[implementation],
        )
    with pytest.raises(provenance.AnalysisProvenanceError, match="implementation"):
        provenance._write_identical_or_new(
            implementation,
            b"replacement\n",
            forbidden_files=[implementation],
        )
