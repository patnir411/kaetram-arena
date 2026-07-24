from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_historical_initial_state import (
    CANONICAL_INITIAL_STATE,
    build_initial_state_audit,
    initial_state_projection,
    state_mismatches,
)


def _write_session(run_dir: Path, state: dict, *, first_tool: str = "observe") -> None:
    run_dir.mkdir(parents=True)
    records = [
        {"type": "system", "subtype": "init", "model": "test"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": first_tool,
                        "input": {},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": json.dumps(state),
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "session_end",
            "num_turns": 1,
            "is_error": False,
        },
    ]
    (run_dir / "session_1_test.log").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )


def _payload() -> dict:
    state = dict(CANONICAL_INITIAL_STATE)
    state["inventory"] = [
        {**item, "name": item["key"].title(), "slots": [item["slot"]]}
        for item in CANONICAL_INITIAL_STATE["inventory"]
    ]
    state["finished_quests"] = [{"name": name} for name in state["finished_quests"]]
    return state


def test_projection_matches_canonical_state() -> None:
    projected = initial_state_projection(_payload())
    assert projected == CANONICAL_INITIAL_STATE
    assert state_mismatches(projected) == []


def test_state_mismatches_identifies_persistent_carryover() -> None:
    state = _payload()
    state["stats"] = {**state["stats"], "level": 7, "xp": 3200}
    state["active_quests"] = [{"name": "Foresting"}]

    mismatches = state_mismatches(initial_state_projection(state))
    assert [item["field"] for item in mismatches] == ["stats", "active_quests"]


def test_bundle_audit_accepts_canonical_first_observe(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_session(raw / "agent_0" / "runs" / "run_a", _payload())

    report = build_initial_state_audit(
        raw,
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
    )
    assert report["complete"]
    assert report["groups"]["test"]["clean_agent_run_bundles"] == 1
    assert report["groups"]["test"]["run_ids"] == ["run_a"]
    assert report["groups"]["test"]["anomalies"] == []


def test_bundle_audit_rejects_action_before_initial_observe(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_session(
        raw / "agent_0" / "runs" / "run_a",
        _payload(),
        first_tool="navigate",
    )

    report = build_initial_state_audit(
        raw,
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
    )
    assert not report["complete"]
    anomaly = report["groups"]["test"]["anomalies"][0]
    assert anomaly["error"] == "first_tool_is_not_observe"


def test_bundle_audit_reports_missing_runs(tmp_path: Path) -> None:
    report = build_initial_state_audit(
        tmp_path,
        groups=["test"],
        claim_runs={"test": ["run_missing"]},
        agents=["agent_0"],
    )
    assert not report["complete"]
    anomaly = report["groups"]["test"]["anomalies"][0]
    assert anomaly["error"] == "missing_run_directory"


def test_bundle_audit_binds_source_manifest_digest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_session(raw / "agent_0" / "runs" / "run_a", _payload())
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text("abc  artifact\n")

    report = build_initial_state_audit(
        raw,
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
        source_manifest=manifest,
    )
    assert report["source_manifest"] == {
        "name": "SHA256SUMS",
        "sha256": "667b9106e544cf8f5a5c8b2e97e322e2845a4e6139dcf3bf2cf3a1b64f275210",
    }
