"""warp() — warp to mudwich, verify player lands near Mudwich centre."""

from __future__ import annotations

import asyncio

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_warp_to_mudwich(seeded_player):
    """warp('mudwich') must not error and observe must place player near
    Mudwich centre (180–200, 150–170) after settling."""
    async with mcp_session(username=seeded_player["username"]) as s:
        await s.call_tool("observe", {})
        res = await s.call_tool("warp", {"location": "mudwich"})
        assert not res.is_error, res.text[:200]
        await asyncio.sleep(1.0)
        obs = (await s.call_tool("observe", {})).json() or {}
        pos = obs.get("pos") or {}
        assert 180 <= pos.get("x", 0) <= 200, f"not near Mudwich x: {pos}"
        assert 150 <= pos.get("y", 0) <= 170, f"not near Mudwich y: {pos}"
