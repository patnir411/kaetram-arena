"""set_attack_style() — set to 'hack', verify in response and subsequent observe."""

from __future__ import annotations

import asyncio

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_set_attack_style_hack(seeded_player):
    """Set style to 'hack', verify the tool confirms it, then read back from
    observe — if observe exposes attack_style, it must reflect the change."""
    async with mcp_session(username=seeded_player["username"]) as s:
        await s.call_tool("observe", {})
        res = await s.call_tool("set_attack_style", {"style": "hack"})
        assert not res.is_error, res.text[:200]
        assert "hack" in res.text.lower() or "style" in res.text.lower(), res.text[:200]

        await asyncio.sleep(0.5)
        obs = (await s.call_tool("observe", {})).json() or {}
        style = (
            obs.get("attack_style")
            or (obs.get("stats") or {}).get("attack_style")
            or (obs.get("player") or {}).get("attackStyle")
        )
        if style is not None:
            assert "hack" in str(style).lower(), (
                f"observe returned style={style!r} after set_attack_style(hack)"
            )
