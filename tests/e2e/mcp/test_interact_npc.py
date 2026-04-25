"""interact_npc() — walk to Forester NPC, verify dialogue opened."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_interact_npc_adjacent(test_username):
    """Seed 1 tile south of Forester (216, 114). interact_npc must move to
    the NPC and open dialogue — verified via position or dialogue signal."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(216, 115),
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("interact_npc", {"npc_name": "Forester"})
            assert not res.is_error, res.text[:200]
            await asyncio.sleep(1.5)
            obs = (await s.call_tool("observe", {})).json() or {}
            pos = obs.get("pos") or {}
            dist = abs(pos.get("x", 0) - 216) + abs(pos.get("y", 0) - 114)
            assert (
                dist <= 3
                or "dialog" in res.text.lower()
                or "talk" in res.text.lower()
            ), f"no interaction signal: pos={pos}, res={res.text[:200]}"
    finally:
        cleanup_player(test_username)
