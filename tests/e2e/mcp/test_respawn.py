"""respawn() — current tool behavior is a forced recovery warp even while alive."""

from __future__ import annotations

import asyncio

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_respawn_while_alive_forces_recovery_warp(seeded_player):
    """Document the tool's real current-tree contract.

    `respawn()` is implemented as a recovery primitive: it clicks the respawn
    button, clears combat state, and dispatches a safe warp to Mudwich even if
    the player was still alive.
    """
    seeded_player["reseed"](position=(216, 115))
    async with mcp_session(username=seeded_player["username"]) as s:
        obs = (await s.call_tool("observe", {})).json() or {}
        assert not obs.get("is_dead"), "precondition: player should be alive"
        start = obs.get("pos") or {}
        assert (start.get("x"), start.get("y")) != (188, 157), f"bad seed for respawn test: {start}"

        res = await s.call_tool("respawn", {})
        assert not res.is_error, f"respawn errored unexpectedly: {res.text[:200]}"
        text = res.text
        assert "Respawned and combat cleared." in text, f"unexpected respawn text: {text}"
        compact = text.replace(" ", "").lower()
        assert '"warping":true' in compact, f"expected warp payload from respawn, got: {text}"
        assert '"warp_id":0' in compact, f"expected Mudwich warp id 0, got: {text}"

        await asyncio.sleep(4.0)
        post = (await s.call_tool("observe", {})).json() or {}
        pos = post.get("pos") or {}
        dist = abs(int(pos.get("x", 0)) - 188) + abs(int(pos.get("y", 0)) - 157)
        assert dist <= 4, f"expected to end up near Mudwich after respawn, got pos={pos}, response={text}"
