"""loot() — pick up ground items and real lootbags."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session, send_chat_command_via_browser

AUTOSAVE_WAIT = 2.5
ADMIN_RANK = 2
MOVE_AWAY_POS = (191, 157)


async def _wait_for_observe_position(
    session,
    *,
    x: int,
    y: int,
    timeout_s: float = 8.0,
) -> dict:
    """Poll observe() until the player reaches the requested tile and nav settles."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    last = {}
    while asyncio.get_running_loop().time() < deadline:
        res = await session.call_tool("observe", {})
        assert not res.is_error, f"observe errored while waiting for ({x},{y}): {res.text[:300]}"
        last = res.json() or {}
        pos = last.get("pos") or {}
        nav = str(last.get("nav") or "idle").lower()
        if int(pos.get("x", -999)) == x and int(pos.get("y", -999)) == y and nav in {
            "idle", "arrived", "short_path",
        }:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"player did not settle at ({x},{y}) within {timeout_s:.1f}s; last_observe={last}")


@pytest.mark.mcp
async def test_loot_walks_back_to_dropped_item(test_username):
    """Drop an item, move away from it, then use loot() to walk back and pick it up.

    This exercises the actual movement + pickup behavior instead of the old
    "drop at feet and immediately re-loot" shortcut.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        inventory=[
            {"index": 0, "key": "apple", "count": 1},
        ],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})

            drop_res = await s.call_tool("drop_item", {"slot": 0})
            assert not drop_res.is_error, f"drop_item errored: {drop_res.text[:200]}"
            await asyncio.sleep(1.5)

            nav_res = await s.call_tool("navigate", {"x": MOVE_AWAY_POS[0], "y": MOVE_AWAY_POS[1]})
            assert not nav_res.is_error, f"navigate away errored: {nav_res.text[:200]}"
            await _wait_for_observe_position(s, x=MOVE_AWAY_POS[0], y=MOVE_AWAY_POS[1])

            res = await s.call_tool("loot", {})
            assert not res.is_error, f"loot errored: {res.text[:300]}"
            data = res.json() or {}
            assert "error" not in data, f"loot returned error: {data}"
            assert data.get("target_type") == "ground_item", f"expected ground item loot, got: {data}"
            collected = data.get("items_collected") or {}
            assert collected != "none (item may have despawned or inventory full)", f"loot did not collect item: {data}"
            assert int(collected.get("apple", 0)) >= 1, f"expected apple loot delta, got: {data}"
            await asyncio.sleep(1.0)

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_keys = [
            sl.get("key") for sl in
            (snap.get("player_inventory") or {}).get("slots") or []
            if sl.get("key")
        ]
        assert "apple" in inv_keys, (
            f"apple missing from Mongo after loot+autosave: {inv_keys}; loot={data}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_loot_walks_back_to_real_lootbag(test_username):
    """Spawn a deterministic server-side lootbag, then loot it through MCP.

    This uses the existing admin `/lootbag` command through a harness-only
    browser setup step, then validates that MCP `loot()` performs the real
    lootbag pickup flow and that the items persist after autosave.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        player_info_overrides={"rank": ADMIN_RANK},
    )
    try:
        await send_chat_command_via_browser(
            username=test_username,
            message="/lootbag",
        )

        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})

            nav_res = await s.call_tool("navigate", {"x": MOVE_AWAY_POS[0], "y": MOVE_AWAY_POS[1]})
            assert not nav_res.is_error, f"navigate away errored: {nav_res.text[:200]}"
            await _wait_for_observe_position(s, x=MOVE_AWAY_POS[0], y=MOVE_AWAY_POS[1])

            res = await s.call_tool("loot", {})
            assert not res.is_error, f"loot errored: {res.text[:300]}"
            data = res.json() or {}
            assert "error" not in data, f"loot returned error: {data}"
            assert data.get("target_type") == "lootbag", f"expected lootbag loot, got: {data}"
            collected = data.get("items_collected") or {}
            assert collected != "none (item may have despawned or inventory full)", (
                f"lootbag did not collect items: {data}"
            )
            assert int(collected.get("oldonesblade", 0)) == 1, f"expected exactly 1 oldonesblade from lootbag, got: {data}"
            assert int(collected.get("froghelm", 0)) == 1, f"expected exactly 1 froghelm from lootbag, got: {data}"
            assert int(collected.get("gold", 0)) == 1500, f"expected exactly 1500 gold from lootbag, got: {data}"
            await asyncio.sleep(1.0)

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_slots = (snap.get("player_inventory") or {}).get("slots") or []
        inv_keys = [sl.get("key") for sl in inv_slots if sl.get("key")]
        gold_total = sum(int(sl.get("count", 0) or 0) for sl in inv_slots if sl.get("key") == "gold")
        assert "oldonesblade" in inv_keys, (
            f"oldonesblade missing from Mongo after lootbag+autosave: {inv_keys}"
        )
        assert "froghelm" in inv_keys, (
            f"froghelm missing from Mongo after lootbag+autosave: {inv_keys}"
        )
        assert gold_total == 1500, (
            f"expected exactly 1500 gold from lootbag after autosave, got {gold_total}; inv={inv_keys}"
        )
    finally:
        cleanup_player(test_username)
