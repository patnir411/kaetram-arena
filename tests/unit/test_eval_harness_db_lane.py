"""Database-lane contract for evaluation resets."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import eval_harness


def test_run_eval_reset_reuses_harness_collection_contract() -> None:
    source = (REPO_ROOT / "scripts" / "run-eval.sh").read_text()
    assert "from eval_harness import MONGO_COLLECTIONS" in source
    assert "for col in MONGO_COLLECTIONS" in source
    assert "for col in ['player_info'" not in source


def test_reset_player_db_targets_configured_database(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="reset_ok\n", stderr="", returncode=0)

    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness.subprocess, "run", fake_run)

    assert eval_harness.reset_player_db("EvalBot") is True
    assert calls[0][4] == "kaetram_eval"
    assert '"evalbot"' in calls[0][-1]


def test_reset_player_db_reports_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        eval_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="", returncode=0),
    )

    assert eval_harness.reset_player_db("EvalBot") is False


def test_required_reset_aborts_on_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness, "reset_player_db", lambda username: False)
    sleep_calls = []
    monkeypatch.setattr(eval_harness.time, "sleep", sleep_calls.append)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        eval_harness.require_player_db_reset("EvalBot")
    assert sleep_calls == [1.0, 1.0]


def test_required_reset_retries_transient_failure(monkeypatch) -> None:
    outcomes = iter((False, True))
    monkeypatch.setattr(eval_harness, "reset_player_db", lambda username: next(outcomes))
    monkeypatch.setattr(eval_harness.time, "sleep", lambda seconds: None)

    eval_harness.require_player_db_reset("EvalBot")


def test_reset_rejects_success_marker_from_failed_command(monkeypatch) -> None:
    monkeypatch.setattr(
        eval_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="reset_ok\n", stderr="connection failed", returncode=2,
        ),
    )

    assert eval_harness.reset_player_db("EvalBot") is False
