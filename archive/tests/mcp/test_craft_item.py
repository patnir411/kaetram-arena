"""craft_item() — craft a route-relevant item with the proper unlock state."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session
from ..helpers.kaetram_world import adjacent_to

AUTOSAVE_WAIT = 5.0
CRAFTING = 11
BABUSHKA_POS = adjacent_to("iamverycoldnpc")


@pytest.mark.mcp
async def test_craft_berylpendant_with_crafting_unlock(test_username):
    """Seed an active Arts and Crafts state, then craft `berylpendant`.

    This follows the actual current-tree route: Crafting is unlocked by
    starting Arts and Crafts, and `berylpendant` is the first real recipe the
    agent is expected to make for that quest line.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=BABUSHKA_POS,
        inventory=[
            {"index": 0, "key": "beryl", "count": 1},
            {"index": 1, "key": "string", "count": 1},
        ],
        quests=[{"key": "artsandcrafts", "stage": 1, "subStage": 0, "completedSubStages": []}],
        skills=[{"type": CRAFTING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("craft_item", {
                "skill": "crafting", "recipe_key": "berylpendant", "count": 1
            })
            assert not res.is_error, f"craft_item errored: {res.text[:300]}"
            data = res.json() or {}
            assert "error" not in data, f"craft_item returned error: {data}"
            delta = data.get("inventory_delta") or {}
            assert int(delta.get("berylpendant", 0)) >= 1, f"expected pendant craft delta, got: {data}"

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_slots = (snap.get("player_inventory") or {}).get("slots") or []
        inv_keys = [sl.get("key") for sl in inv_slots if sl.get("key")]
        assert "berylpendant" in inv_keys, (
            f"berylpendant missing from Mongo inventory after crafting: {inv_keys}"
        )
        assert "beryl" not in inv_keys and "string" not in inv_keys, (
            f"ingredients still present after crafting pendant: {inv_keys}"
        )
    finally:
        cleanup_player(test_username)
