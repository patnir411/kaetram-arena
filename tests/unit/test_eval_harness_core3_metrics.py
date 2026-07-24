"""Regression tests for DB-authoritative Core-3 scoring."""

from eval_harness import _diff_quest_achievement_metrics


def _snapshot(quests: dict[str, int]) -> dict:
    return {
        "quests": {
            key: {
                "stage": stage,
                "started": stage > 0,
                "finished": False,
            }
            for key, stage in quests.items()
        },
        "achievements": {},
    }


def test_core3_metric_uses_internal_quest_keys_from_db_snapshot() -> None:
    before = _snapshot({
        "foresting": 0,
        "herbalistdesperation": 0,
        "ricksroll": 0,
    })
    after = _snapshot({
        "foresting": 3,
        "herbalistdesperation": 3,
        "ricksroll": 4,
    })

    metrics = _diff_quest_achievement_metrics(before, after)

    assert metrics["core3_stages_advanced"] == 10


def test_core3_metric_does_not_count_unrelated_quest_keys() -> None:
    metrics = _diff_quest_achievement_metrics(
        _snapshot({}),
        _snapshot({"tutorial": 16, "desertquest": 7}),
    )

    assert metrics["core3_stages_advanced"] == 0


def test_core3_metric_treats_missing_snapshot_sections_as_empty() -> None:
    metrics = _diff_quest_achievement_metrics(
        {},
        {"quests": {"foresting": {"stage": 3, "started": True, "finished": True}}},
    )

    assert metrics["core3_stages_advanced"] == 3
    assert metrics["quests_completed_delta"] == 1
    assert metrics["achievement_stages_advanced"] == 0
