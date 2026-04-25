"""cancel_nav() — start navigation toward a distant tile, cancel mid-way."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session

# Start position and a destination 30 tiles east — far enough that we can
# cancel before arrival but close enough for the nav to register immediately.
START_POS = (188, 157)
DEST_X, DEST_Y = 220, 157


@pytest.mark.mcp
async def test_cancel_nav_stops_movement(test_username):
    """Seed at (188,157), navigate east to (220,157), cancel after 1 second.
    Player must be between start and destination — not at the destination."""
    cleanup_player(test_username)
    seed_player(test_username, position=START_POS)
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})

            nav_res = await s.call_tool("navigate", {"x": DEST_X, "y": DEST_Y})
            assert not nav_res.is_error, f"navigate errored: {nav_res.text[:200]}"

            await asyncio.sleep(1.0)

            cancel_res = await s.call_tool("cancel_nav", {})
            assert not cancel_res.is_error, f"cancel_nav errored: {cancel_res.text[:300]}"
            data = cancel_res.json() or {}
            assert "error" not in data, f"cancel_nav returned error: {data}"

            obs = (await s.call_tool("observe", {})).json() or {}
            pos = obs.get("pos") or {}
            x = int(pos.get("x", START_POS[0]))
            assert x < DEST_X, (
                f"player reached destination after cancel: x={x}, dest={DEST_X}"
            )
    finally:
        cleanup_player(test_username)
