from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_historical_artifacts import AGENTS, CLAIM_RUNS, build_inventory
from scripts.log_analysis.artifact_requirements import (
    INVALID,
    TERMINAL_ONLY_INTERRUPTED,
    MissingEvidenceError,
    audit_agent_run_logs,
    classify_session_evidence,
    has_semantic_session_evidence,
    missing_agent_run_logs,
    require_agent_run_logs,
    require_files,
)
from scripts.log_analysis.parse import list_agent_dirs


REPO = Path(__file__).resolve().parents[2]


def _write_run(root: Path, agent: str, run_id: str) -> None:
    run = root / agent / "runs" / run_id
    run.mkdir(parents=True)
    records = [
        {"type": "user", "message": {"content": "play"}},
        {"type": "assistant", "message": {"content": "act"}},
    ]
    (run / "session_1_test.log").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )


def _write_terminal_only(run: Path, name: str = "session_2_test.log") -> Path:
    path = run / name
    records = [
        {"type": "system", "subtype": "init", "session_id": "qwen-s2"},
        {
            "type": "result",
            "subtype": "session_end",
            "result": "interrupted",
            "terminal_reason": "interrupted",
            "is_error": False,
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def test_agent_run_check_requires_nonempty_session_bundle(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_run(raw, "agent_0", "run_a")
    assert missing_agent_run_logs(raw, agents=["agent_0"], run_ids=["run_a"]) == []
    missing = missing_agent_run_logs(
        raw, agents=["agent_0", "agent_1"], run_ids=["run_a"]
    )
    assert missing == [str(raw / "agent_1" / "runs" / "run_a")]

    empty_run = raw / "agent_0" / "runs" / "run_empty"
    empty_run.mkdir(parents=True)
    (empty_run / "session_1_test.log").touch()
    assert missing_agent_run_logs(
        raw, agents=["agent_0"], run_ids=["run_empty"]
    ) == [str(empty_run / "session_*.log")]


def test_log_discovery_accepts_external_raw_root(tmp_path: Path) -> None:
    raw = tmp_path / "immutable-evidence" / "raw"
    (raw / "agent_2").mkdir(parents=True)
    (raw / "agent_0").mkdir()
    (raw / "not_an_agent").mkdir()

    assert list_agent_dirs(raw) == [raw / "agent_0", raw / "agent_2"]


@pytest.mark.parametrize(
    "content",
    ("x", "{}\n", '{"type":"assistant","message":{"content":"act"}}\n', "{broken\n{}\n"),
)
def test_session_evidence_rejects_trivial_or_corrupt_logs(tmp_path: Path, content: str) -> None:
    path = tmp_path / "session_1_test.log"
    path.write_text(content)

    assert not has_semantic_session_evidence(path)


def test_terminal_only_interruption_is_classified_but_not_semantic(tmp_path: Path) -> None:
    path = _write_terminal_only(tmp_path)
    assert classify_session_evidence(path) == TERMINAL_ONLY_INTERRUPTED
    assert not has_semantic_session_evidence(path)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda records: records.append({"type": "assistant", "message": {"content": "act"}}),
        lambda records: records[1].update({"is_error": True}),
        lambda records: records[1].update({"result": "context_overflow"}),
        lambda records: records[0].update({"subtype": "other"}),
    ),
)
def test_terminal_only_classifier_rejects_near_misses(tmp_path: Path, mutation) -> None:
    path = _write_terminal_only(tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    mutation(records)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert classify_session_evidence(path) == INVALID


def test_run_allows_disclosed_terminal_only_tail_after_semantic_session(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_run(raw, "agent_0", "run_a")
    run = raw / "agent_0" / "runs" / "run_a"
    excluded = _write_terminal_only(run)

    audit = audit_agent_run_logs(raw, agents=["agent_0"], run_ids=["run_a"])
    assert audit["missing"] == []
    assert audit["invalid_sessions"] == []
    assert audit["excluded_terminal_only"] == [str(excluded)]


def test_run_with_only_terminal_interruption_remains_incomplete(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    run = raw / "agent_0" / "runs" / "run_a"
    run.mkdir(parents=True)
    excluded = _write_terminal_only(run)

    audit = audit_agent_run_logs(raw, agents=["agent_0"], run_ids=["run_a"])
    assert audit["missing"] == [str(run / "session_*.log")]
    assert audit["excluded_terminal_only"] == [str(excluded)]


def test_model_only_session_keeps_otherwise_semantic_bundle_incomplete(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_run(raw, "agent_0", "run_a")
    run = raw / "agent_0" / "runs" / "run_a"
    invalid = run / "session_2_test.log"
    record = {"type": "assistant", "message": {"content": "I observed the world"}}
    invalid.write_text(json.dumps(record) + "\n")

    audit = audit_agent_run_logs(raw, agents=["agent_0"], run_ids=["run_a"])
    assert audit["missing"] == [str(run / "session_*.log")]
    assert audit["invalid_sessions"] == [str(invalid)]


def test_requirements_fail_closed_with_actionable_paths(tmp_path: Path) -> None:
    with pytest.raises(MissingEvidenceError, match="Restore the immutable artifacts"):
        require_agent_run_logs(
            tmp_path, agents=["agent_0"], run_ids=["run_missing"], analysis="test",
        )
    with pytest.raises(MissingEvidenceError, match="supporting artifact"):
        require_files([tmp_path / "train.json"], analysis="test")


def test_inventory_is_complete_only_when_every_claim_bundle_exists(tmp_path: Path) -> None:
    first = build_inventory(tmp_path)
    assert not first["complete"]
    for run_ids in CLAIM_RUNS.values():
        for agent in AGENTS:
            for run_id in run_ids:
                _write_run(tmp_path, agent, run_id)
    complete = build_inventory(tmp_path)
    assert complete["complete"]
    assert all(group["complete"] for group in complete["groups"].values())
    assert complete["schema_version"] == "kaetram-historical-artifact-inventory-v2"


def test_checked_in_r10_scripts_refuse_to_publish_zero_evidence() -> None:
    for script in ("scripts/r10_stats.py", "scripts/r10_credit_diag.py"):
        result = subprocess.run(
            [sys.executable, script], cwd=REPO, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "required raw-log bundle(s) are missing" in result.stderr
        assert "runs parsed:           0" not in result.stdout


def test_inventory_cli_reports_missing_and_exits_nonzero(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_historical_artifacts.py", "--raw-root", str(tmp_path)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["complete"] is False
