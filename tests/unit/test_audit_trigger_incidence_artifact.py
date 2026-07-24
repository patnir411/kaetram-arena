import json
from pathlib import Path

import pytest

from scripts.opd.audit_trigger_incidence_artifact import AuditError, audit_artifact
from scripts.opd.export_trigger_incidence_artifact import sha256_file, sha256_json
from tests.unit.test_export_trigger_incidence_artifact import _export, _fixture


def _reseal_outer(root: Path) -> None:
    index_path = root / "artifact-index.json"
    index = json.loads(index_path.read_text())
    records = []
    for record in index["files"]:
        path = root / record["path"]
        records.append(
            {
                "path": record["path"],
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    index["files"] = records
    index["tree_sha256"] = sha256_json(records)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")


def test_independently_recomputes_exported_result(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)

    result = audit_artifact(output)

    assert result["scheduled_requests"] == 12
    assert result["successful_requests"] == 12
    assert result["failed_requests"] == 0
    assert result["cell_count"] == 12
    assert result["contrast_count"] == 9


def test_rejects_resealed_raw_outcome_forgery(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)
    results = output / "runs" / "base" / "results.jsonl"
    rows = [json.loads(line) for line in results.read_text().splitlines()]
    rows[0]["recovery_opportunity"] = True
    results.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    _reseal_outer(output)

    with pytest.raises(AuditError, match="stored outcome mismatch"):
        audit_artifact(output)


def test_rejects_resealed_analysis_forgery(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    _export(fixture, output)
    summary_path = output / "analysis" / "analysis-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["recovery_opportunities"] = 99
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _reseal_outer(output)

    with pytest.raises(AuditError, match="independent analysis mismatch"):
        audit_artifact(output)
