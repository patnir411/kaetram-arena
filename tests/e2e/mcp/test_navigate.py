"""navigate() — navigate 12 tiles east, verify player position changed."""

from __future__ import annotations

import asyncio
import math

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session

MOVE_TIMEOUT = 4.0


@pytest.mark.mcp
async def test_navigate_moves_player(test_username):
    """Seed at (188, 157), navigate to (200, 157). After MOVE_TIMEOUT seconds,
    observe must show the player moved ≥1 tile from seed position."""
    cleanup_player(test_username)
    seed_player(test_username, position=(188, 157))
    try:
        async with mcp_session(username=test_username) as s:
            obs0 = (await s.call_tool("observe", {})).json() or {}
            pos0 = obs0.get("pos") or {}
            x0, y0 = pos0.get("x", 188), pos0.get("y", 157)

            res = await s.call_tool("navigate", {"x": 200, "y": 157})
            assert not res.is_error, res.text[:200]
            data = res.json() or {}
            assert data.get("status") in (
                "navigating", "arrived", "short_path", "stuck",
            ), f"unexpected nav status: {data}"

            await asyncio.sleep(MOVE_TIMEOUT)

            obs1 = (await s.call_tool("observe", {})).json() or {}
            pos1 = obs1.get("pos") or {}
            x1, y1 = pos1.get("x", x0), pos1.get("y", y0)
            dist = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
            assert dist >= 1, (
                f"player didn't move: ({x0},{y0}) → ({x1},{y1}); status={data.get('status')}"
            )
    finally:
        cleanup_player(test_username)
