"""drop_item() — drop logs from slot 0, verify absent from Mongo after autosave."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0


@pytest.mark.mcp
async def test_drop_item_removes_from_inventory(test_username):
    """Seed logs in slot 0, drop slot 0. Tool must not return an error and
    Mongo inventory must not contain logs after autosave."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        inventory=[
            {"index": 0, "key": "logs", "count": 1},
            {"index": 1, "key": "apple", "count": 3},
        ],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("drop_item", {"slot": 0})
            assert not res.is_error, f"drop_item errored: {res.text[:300]}"
            data = res.json() or {}
            assert "error" not in data, f"drop_item returned error: {data}"
            await asyncio.sleep(1.0)

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_keys = [
            sl.get("key") for sl in
            (snap.get("player_inventory") or {}).get("slots") or []
            if sl.get("key")
        ]
        assert "logs" not in inv_keys, f"logs still in Mongo after drop+autosave: {inv_keys}"
    finally:
        cleanup_player(test_username)
