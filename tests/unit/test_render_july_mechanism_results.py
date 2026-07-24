from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from run_manifest import hash_path, sha256_json
from scripts import render_july_mechanism_results as render
from scripts.capture_analysis_provenance import AnalysisProvenanceError


def _raw_fixture(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    for run_id in render.CLAIM_RUNS[render.CLAIM_GROUP]:
        for agent in render.AGENTS:
            run = raw / agent / "runs" / run_id
            run.mkdir(parents=True)
            (run / "run.meta.json").write_text(json.dumps({
                "started_at": "2026-07-11T06:00:00-04:00",
                "hours_budget": 6.0,
            }))
            (run / "session_1_20260711_100001.log").write_text(json.dumps({
                "type": "assistant",
                "timestamp": "2026-07-11T10:00:01",
                "message": {"content": [{
                    "type": "text",
                    "text": "clock fixture",
                }]},
            }) + "\n")
    return raw


def _evidence_manifest(tmp_path: Path, raw: Path) -> Path:
    run_ids = render.CLAIM_RUNS[render.CLAIM_GROUP]
    bundles = []
    for run_id in run_ids:
        for agent in render.AGENTS:
            bundles.append({
                "claim_group": render.CLAIM_GROUP,
                "run_id": run_id,
                "agent": agent,
                "content": hash_path(
                    raw / agent / "runs" / run_id,
                    root=raw,
                ),
            })
    report = {
        "schema_version": render.EVIDENCE_SCHEMA,
        "raw_root": "dataset/raw",
        "source_manifest": {"name": "SOURCE_SHA256SUMS", "sha256": "a" * 64},
        "claim_groups": {render.CLAIM_GROUP: list(run_ids)},
        "bundle_count": len(bundles),
        "source_manifest_verified_file_count": sum(
            record["content"]["file_count"] for record in bundles
        ),
        "bundles": bundles,
        "missing": [],
        "complete": True,
    }
    report["manifest_sha256"] = sha256_json(report)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _analysis() -> dict:
    return {
        "manifest_sha256": "c" * 64,
        "source_git_commit": "d" * 40,
        "python_version": "3.12.12",
        "implementation_sha256": "e" * 64,
    }


def _rows(arm: str) -> list[dict]:
    return [
        {
            "run": render.ARMS[arm]["runs"][0],
            "agent": agent,
            "stages": {
                render.CORE3[0]: 3,
                render.CORE3[1]: total - 3,
                render.CORE3[2]: 0,
            },
            "total": total,
        }
        for agent, total in zip(
            render.AGENTS,
            render.DOCUMENTED_AGENT_TOTALS[arm],
            strict=True,
        )
    ]


def _patch_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        render,
        "load_and_verify_analysis_provenance",
        lambda *args, **kwargs: _analysis(),
    )


def test_result_report_is_complete_self_identifying_and_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)
    monkeypatch.setattr(render, "collect_arm", lambda arm, raw_root: _rows(arm))

    report = render.build_result_report(raw, evidence, tmp_path / "analysis.json")

    assert report["analysis_status"] == "complete"
    assert [arm["arm"] for arm in report["arms"]] == list(render.JULY_ARM_ORDER)
    assert [arm["core3_total"] for arm in report["arms"]] == [
        sum(render.DOCUMENTED_AGENT_TOTALS[arm])
        for arm in render.JULY_ARM_ORDER
    ]
    assert report["evidence_manifest"]["verified_scored_bundle_count"] == 27
    assert report["timestamp_contract"]["lane_count"] == 27
    assert report["timestamp_contract"]["semantic_record_count"] == 27
    unsigned = copy.deepcopy(report)
    identity = unsigned.pop("manifest_sha256")
    assert identity == sha256_json(unsigned)
    assert str(tmp_path) not in str(report)


def test_result_report_rejects_tampered_evidence_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)
    report = json.loads(evidence.read_text())
    report["complete"] = False
    evidence.write_text(json.dumps(report))

    with pytest.raises(render.ResultError, match="invalid self-identity"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


def test_result_report_rejects_raw_substitution_after_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)
    first_run = render.CLAIM_RUNS[render.CLAIM_GROUP][0]
    substituted = raw / render.AGENTS[0] / "runs" / first_run / "session_1_20260711_100001.log"
    substituted.write_text(substituted.read_text() + "\n")

    with pytest.raises(render.ResultError, match="bytes disagree"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


@pytest.mark.parametrize("mutation", ["duplicate", "malformed"])
def test_result_report_rejects_invalid_bundle_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)
    report = json.loads(evidence.read_text())
    if mutation == "duplicate":
        report["bundles"].append(copy.deepcopy(report["bundles"][0]))
    else:
        report["bundles"][0]["content"].pop("size_bytes")
    report["manifest_sha256"] = sha256_json({
        key: value for key, value in report.items() if key != "manifest_sha256"
    })
    evidence.write_text(json.dumps(report))

    with pytest.raises(render.ResultError, match="bundle"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


def test_result_report_rejects_unaligned_naive_record_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    first_run = render.CLAIM_RUNS[render.CLAIM_GROUP][0]
    log = raw / render.AGENTS[0] / "runs" / first_run / "session_1_20260711_100001.log"
    record = json.loads(log.read_text())
    record["timestamp"] = "2026-07-11T14:00:01"
    log.write_text(json.dumps(record) + "\n")
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)

    with pytest.raises(render.ResultError, match="does not align"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


def test_result_report_rejects_analysis_provenance_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)

    def reject(*args, **kwargs):
        raise AnalysisProvenanceError("analysis implementation bytes do not match")

    monkeypatch.setattr(render, "load_and_verify_analysis_provenance", reject)
    with pytest.raises(render.ResultError, match="implementation bytes"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


def test_result_report_rejects_score_disagreement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_fixture(tmp_path)
    evidence = _evidence_manifest(tmp_path, raw)
    _patch_analysis(monkeypatch)

    def wrong_rows(arm: str, raw_root: Path) -> list[dict]:
        rows = _rows(arm)
        if arm == "base-2B+rec":
            rows[0]["total"] += 1
        return rows

    monkeypatch.setattr(render, "collect_arm", wrong_rows)
    with pytest.raises(render.ResultError, match="disagree"):
        render.build_result_report(raw, evidence, tmp_path / "analysis.json")


def test_result_output_must_not_overlap_inputs_or_use_symlink(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    analysis = tmp_path / "analysis.json"
    analysis.write_text("{}")
    with pytest.raises(render.ResultError, match="scored raw root"):
        render._validate_output_path(
            raw / "result.json",
            raw_root=raw,
            input_files=(evidence, analysis),
        )
    with pytest.raises(render.ResultError, match="input receipt"):
        render._validate_output_path(
            evidence,
            raw_root=raw,
            input_files=(evidence, analysis),
        )
    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "result-link.json"
    link.symlink_to(target)
    with pytest.raises(render.ResultError, match="symlink"):
        render._validate_output_path(
            link,
            raw_root=raw,
            input_files=(evidence, analysis),
        )
