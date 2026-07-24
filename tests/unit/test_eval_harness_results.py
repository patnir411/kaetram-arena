"""Regression tests for evaluation result metadata."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import eval_harness


@pytest.mark.parametrize("scenario", sorted(eval_harness.SCENARIOS))
def test_save_results_records_time_budget_without_max_turns(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    """Every current scenario must save after a successful episode."""
    monkeypatch.setattr(
        eval_harness.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )
    output_path = tmp_path / "results.json"

    results = eval_harness._save_results(
        output_path,
        model_name="base",
        endpoint="https://example.invalid/v1",
        scenario=scenario,
        episodes=[{"episode": 1, "status": "ok"}],
    )

    assert results["schema_version"] == "kaetram.eval-results.v1"
    assert results["meta"]["model"] == "base"
    assert results["meta"]["endpoint"] == "https://example.invalid/v1"
    assert results["meta"]["scenario"] == scenario
    assert results["meta"]["scenario_name"] == eval_harness.SCENARIOS[scenario]["name"]
    assert results["meta"]["duration_minutes"] == eval_harness.SCENARIOS[scenario]["duration_minutes"]
    assert "max_turns" not in results["meta"]
    assert results["meta"]["total_episodes"] == 1
    assert results["meta"]["ok_episodes"] == 1
    assert results["meta"]["git_sha"] == "abc123"
    assert json.loads(output_path.read_text()) == results


def test_save_results_records_failed_episode(
    tmp_path: Path, monkeypatch
) -> None:
    """A zero-turn failure must still leave resumable result metadata."""
    monkeypatch.setattr(
        eval_harness.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )
    output_path = tmp_path / "results.json"
    results = eval_harness._save_results(
        output_path,
        model_name="base",
        endpoint="https://example.invalid/v1",
        scenario="A",
        episodes=[{"episode": 1, "status": "no_log"}],
    )

    assert results["meta"]["total_episodes"] == 1
    assert results["meta"]["ok_episodes"] == 0
    assert results["metrics"] == {}
    assert json.loads(output_path.read_text()) == results


def test_save_results_preserves_previous_checkpoint_if_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    output_path = tmp_path / "results.json"
    previous = {"episodes": [{"episode": 1, "status": "ok"}]}
    output_path.write_text(json.dumps(previous))
    monkeypatch.setattr(eval_harness.subprocess, "check_output", lambda *a, **k: "abc123\n")

    def fail_replace(source, destination) -> None:
        raise OSError("simulated interrupted replacement")

    monkeypatch.setattr(eval_harness.os, "replace", fail_replace)

    with pytest.raises(OSError, match="interrupted replacement"):
        eval_harness._save_results(
            output_path,
            model_name="base",
            endpoint="https://example.invalid/v1",
            scenario="A",
            episodes=[{"episode": 2, "status": "ok"}],
        )

    assert json.loads(output_path.read_text()) == previous
    assert list(tmp_path.glob(".results.json.*.tmp")) == []


def test_scenarios_have_one_time_budget_contract() -> None:
    for scenario in eval_harness.SCENARIOS.values():
        assert isinstance(scenario["name"], str) and scenario["name"]
        assert isinstance(scenario["description"], str) and scenario["description"]
        assert isinstance(scenario["duration_minutes"], int)
        assert scenario["duration_minutes"] > 0
        assert "max_turns" not in scenario
