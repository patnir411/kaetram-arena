"""Tests for fail-closed evaluation artifact validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_eval_results import ValidationError, validate_results


def _write_results(
    path: Path,
    *,
    statuses: list[str],
    scenario: str = "D",
    model: str = "base",
    episode_ids: list[int] | None = None,
) -> None:
    ok_count = statuses.count("ok")
    path.write_text(json.dumps({
        "meta": {
            "model": model,
            "scenario": scenario,
            "total_episodes": len(statuses),
            "ok_episodes": ok_count,
        },
        "episodes": [
            {"episode": episode_id, "status": status}
            for episode_id, status in zip(
                episode_ids or list(range(1, len(statuses) + 1)), statuses, strict=True
            )
        ],
        "metrics": {},
    }))


def test_accepts_complete_matching_arm(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=["ok", "ok"])

    validate_results(path, expected_episodes=2, expected_scenario="D")


@pytest.mark.parametrize(
    ("statuses", "message"),
    [
        (["ok"], "expected=2"),
        (["ok", "no_log"], "ok=1"),
        (["ok", "ok", "ok"], "expected=2"),
    ],
)
def test_rejects_incomplete_or_extra_episodes(
    tmp_path: Path, statuses: list[str], message: str
) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=statuses)

    with pytest.raises(ValidationError, match=message):
        validate_results(path, expected_episodes=2, expected_scenario="D")


def test_rejects_wrong_scenario(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=["ok"], scenario="A")

    with pytest.raises(ValidationError, match="scenario mismatch"):
        validate_results(path, expected_episodes=1, expected_scenario="D")


def test_rejects_wrong_model_and_duplicate_episode_ids(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=["ok", "ok"], model="base", episode_ids=[1, 1])

    with pytest.raises(ValidationError, match="episode IDs mismatch"):
        validate_results(
            path, expected_episodes=2, expected_scenario="D", expected_model="base"
        )

    _write_results(path, statuses=["ok"], model="r10-sft")
    with pytest.raises(ValidationError, match="model mismatch"):
        validate_results(
            path, expected_episodes=1, expected_scenario="D", expected_model="base"
        )


@pytest.mark.parametrize("payload", [[], {"episodes": {}, "meta": {}}, {"episodes": [1], "meta": {}}])
def test_rejects_invalid_result_shapes(tmp_path: Path, payload) -> None:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        validate_results(path, expected_episodes=1, expected_scenario="D")


def test_rejects_missing_and_malformed_files(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    with pytest.raises(ValidationError, match="missing results"):
        validate_results(path, expected_episodes=1, expected_scenario="D")

    path.write_text("not-json")
    with pytest.raises(ValidationError, match="invalid results"):
        validate_results(path, expected_episodes=1, expected_scenario="D")
