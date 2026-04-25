"""tutorial_autowarp — seed inside tutorial zone, verify auto-warp to Mudwich fires."""

from __future__ import annotations

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session


@pytest.mark.game
async def test_autowarp_fires_from_tutorial_spawn(test_username):
    """Seed inside Programmer's house tutorial zone (328, 892). First observe
    must show player outside the zone — warp fired during login."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(328, 892),
        hit_points=69,
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            res = await s.call_tool("observe", {})
            data = res.json() or {}
            pos = data.get("pos") or {}
            x, y = pos.get("x", 0), pos.get("y", 0)
            in_tutorial = (300 <= x <= 360) and (860 <= y <= 920)
            assert not in_tutorial, f"auto-warp did not fire: player at ({x},{y})"
            assert 150 <= x <= 230, f"not near Mudwich x: {x}"
            assert 140 <= y <= 200, f"not near Mudwich y: {y}"
    finally:
        cleanup_player(test_username)
