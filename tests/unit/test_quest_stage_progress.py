"""Tests for quest_stage_item_progress — the state-aware quest resume helper.

Exercises the pure logic that lets a memoryless agent continue a quest from its
live stage + inventory instead of re-planning from stage 0. Uses the real
Herbalist's Desperation walkthrough data plus synthetic edge cases.
"""

from mcp_server.utils import load_quest_walkthroughs, quest_stage_item_progress


def _herbalists() -> dict:
    for key, quest in load_quest_walkthroughs().items():
        if isinstance(quest, dict) and "herbalist" in (quest.get("name") or key).lower():
            return quest
    raise AssertionError("Herbalist's Desperation entry not found in walkthroughs")


def _ricks() -> dict:
    for key, quest in load_quest_walkthroughs().items():
        if isinstance(quest, dict) and "rick" in (quest.get("name") or key).lower():
            return quest
    raise AssertionError("Rick's Roll entry not found in walkthroughs")


def test_ricks_stage1_cookedshrimp_progress():
    # Rick's stage-1 summary must carry a `cookedshrimp x5` token so current_step
    # surfaces needed/have/remaining — a prose summary with no `key xN` token parses
    # to nothing, leaving the agent without an item-progress anchor for the stage.
    prog = quest_stage_item_progress(_ricks(), 1, [{"key": "cookedshrimp", "count": 2}])
    assert prog is not None, "Rick's stage 1 must parse an item requirement"
    assert prog["needed"] == {"cookedshrimp": 5}
    assert prog["have"] == {"cookedshrimp": 2}
    assert prog["remaining"] == {"cookedshrimp": 3}
    assert prog["all_satisfied"] is False
    full = quest_stage_item_progress(_ricks(), 1, [{"key": "cookedshrimp", "count": 5}])
    assert full["all_satisfied"] is True


def test_stage1_partial_progress():
    prog = quest_stage_item_progress(_herbalists(), 1, [{"key": "bluelily", "count": 1}])
    assert prog["needed"] == {"bluelily": 3}
    assert prog["have"] == {"bluelily": 1}
    assert prog["remaining"] == {"bluelily": 2}
    assert prog["all_satisfied"] is False


def test_stage1_satisfied():
    prog = quest_stage_item_progress(_herbalists(), 1, [{"key": "bluelily", "count": 3}])
    assert prog["remaining"] == {"bluelily": 0}
    assert prog["all_satisfied"] is True


def test_stage2_multi_item():
    prog = quest_stage_item_progress(
        _herbalists(), 2, [{"key": "paprika", "count": 2}, {"key": "tomato", "count": 1}]
    )
    assert prog["needed"] == {"paprika": 2, "tomato": 2}
    assert prog["remaining"] == {"paprika": 0, "tomato": 1}
    assert prog["all_satisfied"] is False


def test_stage0_talk_only_returns_none():
    # stage_summary[0] = "Talk to Herbalist." names no items → omit the block.
    assert quest_stage_item_progress(_herbalists(), 0, []) is None


def test_finished_stage():
    prog = quest_stage_item_progress(_herbalists(), 3, [])
    assert prog["finished"] is True
    assert prog["all_satisfied"] is True


def test_dict_inventory_shape():
    # Accepts a flat {key: count} inventory as well as the observe list shape.
    prog = quest_stage_item_progress(_herbalists(), 1, {"bluelily": 2})
    assert prog["remaining"] == {"bluelily": 1}


def test_bad_data_is_graceful():
    assert quest_stage_item_progress({}, 1, []) is None
    assert quest_stage_item_progress({"stage_summary": []}, 1, []) is None
    assert quest_stage_item_progress({"stage_summary": ["Turn in `x x1`."]}, "bad", []) is None
