"""Sanity tests for bench.seed — without spawning any MCP subprocess.

These are fast (<1s each) and validate the Mongo write path independently of
the game server.
"""

from __future__ import annotations

import pytest

from bench.seed import (
    ALL_COLLECTIONS,
    cleanup_player,
    seed_player,
    snapshot_player,
    summarize_snapshot,
)


@pytest.mark.mcp
def test_seed_writes_all_collections(test_username):
    """Every collection that receives a non-None field should have a doc."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(200, 150),
        hit_points=50,
        inventory=[{"index": 0, "key": "ironaxe", "count": 1}],
        equipment=[{"type": 0, "key": "ironaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
        quests=[{"key": "foresting", "stage": 1, "subStage": 0, "completedSubStages": []}],
        skills=[{"type": 3, "experience": 120}],
    )
    try:
        snap = snapshot_player(test_username)
        # player_info + inventory are always written. Others only when passed.
        assert snap["player_info"] is not None
        assert snap["player_inventory"] is not None
        assert snap["player_equipment"] is not None
        assert snap["player_quests"] is not None
        assert snap["player_skills"] is not None
        # Unsupplied collections stay empty.
        assert snap["player_bank"] is None
        assert snap["player_achievements"] is None
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
def test_seed_inserts_tutorial_bypass(test_username):
    """Without the finished-tutorial row, Kaetram's applyTutorialBypass would
    override our seeded spawn position. Verify the row is auto-inserted."""
    cleanup_player(test_username)
    seed_player(test_username, position=(200, 150))
    try:
        snap = snapshot_player(test_username)
        quests = (snap["player_quests"] or {}).get("quests") or []
        tutorial = next((q for q in quests if q.get("key") == "tutorial"), None)
        assert tutorial is not None, "tutorial row not inserted"
        assert tutorial.get("stage") == 16, f"tutorial not finished: {tutorial}"
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
def test_cleanup_removes_all_rows(test_username):
    """After cleanup_player, no collection should have a row for the username."""
    cleanup_player(test_username)
    seed_player(test_username, position=(200, 150), skills=[{"type": 3, "experience": 10}])
    deleted = cleanup_player(test_username)
    # At least player_info, player_inventory, player_quests, player_skills.
    assert deleted["player_info"] == 1
    assert deleted["player_inventory"] == 1
    assert deleted["player_quests"] == 1
    assert deleted["player_skills"] == 1

    # Nothing left.
    snap = snapshot_player(test_username)
    for coll in ALL_COLLECTIONS:
        assert snap[coll] is None, f"{coll} still has a row after cleanup"


@pytest.mark.mcp
def test_summarize_snapshot_roundtrip(test_username):
    """summarize_snapshot should reduce the snapshot to the scalars the runner
    uses for before/after diffing."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(210, 120),
        hit_points=42,
        inventory=[
            {"index": 0, "key": "logs", "count": 10},
            {"index": 1, "key": "apple", "count": 3},
        ],
        quests=[{"key": "foresting", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        summ = summarize_snapshot(snapshot_player(test_username))
        assert summ["position"] == {"x": 210, "y": 120}
        assert summ["hit_points"] == 42
        assert summ["quests"]["foresting"]["stage"] == 2
        inv_keys = [i["key"] for i in summ["inventory"]]
        assert "logs" in inv_keys and "apple" in inv_keys
    finally:
        cleanup_player(test_username)
