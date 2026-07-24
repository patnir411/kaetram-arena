from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from run_manifest import canonical_json_bytes, sha256_json
from scripts import export_july_score_artifact as exporter
from scripts import score_july_public_artifact as scorer


def _row(
    *,
    lane_index: int,
    sequence: int,
    timestamp: str,
    active: list[dict] | None = None,
    finished: list[str] | None = None,
) -> dict:
    return {
        "lane_index": lane_index,
        "sequence": sequence,
        "timestamp": timestamp,
        "source_log_index": 0,
        "source_line_number": sequence + 1,
        "source_record_sha256": hashlib.sha256(
            f"{lane_index}:{sequence}".encode()
        ).hexdigest(),
        "active_quests": active or [],
        "finished_quests": finished or [],
    }


def _registry(rows_by_lane: list[list[dict]]) -> dict:
    lanes = []
    for lane_index, rows in enumerate(rows_by_lane):
        lanes.append({
            "lane_index": lane_index,
            "run_id": f"run_{lane_index:02d}",
            "agent": f"agent_{lane_index % 3}",
            "started_at": "2026-07-11T06:00:00-04:00",
            "hours_budget": 6.0,
            "source_bundle_sha256": "a" * 64,
            "source_logs": [{
                "source_log_index": 0,
                "path": f"agent_{lane_index % 3}/runs/run_{lane_index:02d}/session_1.log",
                "sha256": "b" * 64,
            }],
            "observation_count": len(rows),
            "observations_sha256": sha256_json(rows),
        })
    registry = {
        "schema_version": scorer.REGISTRY_SCHEMA,
        "projection_scope": "test",
        "claim_boundary": "test",
        "evidence_manifest_sha256": "c" * 64,
        "analysis_provenance_sha256": "d" * 64,
        "raw_result_manifest_sha256": "e" * 64,
        "core3_stage_counts": scorer.CORE3_STAGE_COUNTS,
        "lane_count": len(lanes),
        "lanes": lanes,
    }
    registry["manifest_sha256"] = sha256_json(registry)
    return registry


def test_public_projection_scores_inclusive_cutoff_and_ignores_late_state() -> None:
    rows = [
        _row(lane_index=0, sequence=0, timestamp="2026-07-11T10:00:01"),
        _row(
            lane_index=0,
            sequence=1,
            timestamp="2026-07-11T16:00:00",
            active=[{"name": "Foresting", "stage": 2, "sub_stage": 0}],
        ),
        _row(
            lane_index=0,
            sequence=2,
            timestamp="2026-07-11T16:00:00.000001",
            finished=["Foresting"],
        ),
    ]
    registry = _registry([rows])

    report = scorer.score_projection(registry, rows)

    assert report["lane_scores"][0]["stages"]["Foresting"] == 2
    assert report["lane_scores"][0]["total"] == 2
    assert report["lane_scores"][0]["included_observation_count"] == 2


def test_public_projection_rejects_nonzero_first_core3_state() -> None:
    rows = [
        _row(
            lane_index=0,
            sequence=0,
            timestamp="2026-07-11T10:00:01",
            active=[{"name": "Foresting", "stage": 1, "sub_stage": 0}],
        ),
    ]
    with pytest.raises(scorer.PublicScoreError, match="first observation"):
        scorer.score_projection(_registry([rows]), rows)


def test_extract_lane_projection_binds_source_record_and_log(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    run = raw / "agent_0" / "runs" / "run_a"
    run.mkdir(parents=True)
    assistant = {
        "type": "assistant",
        "timestamp": "2026-07-11T10:00:00",
        "message": {"content": [{
            "type": "tool_use",
            "id": "observe-1",
            "name": "mcp__kaetram__observe",
            "input": {},
        }]},
    }
    user = {
        "type": "user",
        "timestamp": "2026-07-11T10:00:01",
        "message": {"content": [{
            "type": "tool_result",
            "tool_use_id": "observe-1",
            "content": json.dumps({
                "active_quests": [{
                    "name": "Foresting",
                    "stage": 1,
                    "subStage": 2,
                    "description": "omitted",
                }],
                "finished_quests": [{"name": "Scavenger"}],
                "inventory": {"secret": 3},
            }),
        }]},
    }
    log = run / "session_1_20260711_100000.log"
    log.write_text(json.dumps(assistant) + "\n" + json.dumps(user) + "\n")

    rows, source_logs = exporter._extract_lane_observations(
        run,
        raw_root=raw,
        lane_index=0,
    )

    assert rows == [{
        "lane_index": 0,
        "sequence": 0,
        "timestamp": "2026-07-11T10:00:01",
        "source_log_index": 0,
        "source_line_number": 2,
        "source_record_sha256": hashlib.sha256(
            canonical_json_bytes(user)
        ).hexdigest(),
        "active_quests": [{
            "name": "Foresting",
            "stage": 1,
            "sub_stage": 2,
        }],
        "finished_quests": ["Scavenger"],
    }]
    assert source_logs[0]["path"].endswith("session_1_20260711_100000.log")
    assert source_logs[0]["sha256"] == exporter._sha256_file(log)
    assert "inventory" not in json.dumps(rows)


def _write_artifact(root: Path) -> None:
    root.mkdir()
    rows_by_lane = []
    all_rows = []
    for lane_index in range(27):
        rows = [
            _row(
                lane_index=lane_index,
                sequence=0,
                timestamp="2026-07-11T10:00:01",
            ),
            _row(
                lane_index=lane_index,
                sequence=1,
                timestamp="2026-07-11T10:01:00",
                active=[{"name": "Foresting", "stage": 1, "sub_stage": 0}],
            ),
        ]
        rows_by_lane.append(rows)
        all_rows.extend(rows)
    registry = _registry(rows_by_lane)
    scores = scorer.score_projection(registry, all_rows)
    (root / "README.md").write_text("fixture\n")
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n"
    )
    with (root / "observations.jsonl").open("w") as handle:
        for row in all_rows:
            handle.write(canonical_json_bytes(row).decode() + "\n")
    (root / "scores.json").write_text(
        json.dumps(scores, indent=2, sort_keys=True) + "\n"
    )
    paths = [
        root / "README.md",
        root / "observations.jsonl",
        root / "registry.json",
        root / "scores.json",
    ]
    files = [{
        "path": path.name,
        "sha256": scorer._sha256_file(path),
        "size_bytes": path.stat().st_size,
    } for path in paths]
    index = {
        "schema_version": scorer.INDEX_SCHEMA,
        "study_id": "fixture",
        "export_source_git_commit": "f" * 40,
        "export_script_sha256": scorer._sha256_file(
            Path(exporter.__file__).resolve()
        ),
        "scorer_script_sha256": scorer._sha256_file(
            Path(scorer.__file__).resolve()
        ),
        "evidence_manifest_sha256": registry["evidence_manifest_sha256"],
        "raw_result_manifest_sha256": registry["raw_result_manifest_sha256"],
        "registry_manifest_sha256": registry["manifest_sha256"],
        "scores_manifest_sha256": scores["manifest_sha256"],
        "files": files,
        "tree_sha256": sha256_json(files),
    }
    index["manifest_sha256"] = sha256_json(index)
    (root / "artifact-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )


def test_public_artifact_verifier_replays_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact"
    _write_artifact(root)
    verified = scorer.verify_artifact(root)
    assert verified["observation_count"] == 54
    assert len(verified["scores"]["lane_scores"]) == 27

    with (root / "observations.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(scorer.PublicScoreError, match="bytes disagree"):
        scorer.verify_artifact(root)
