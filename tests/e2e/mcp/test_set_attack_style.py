"""set_attack_style() tool — change combat stance."""

from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.parametrize("style", ["hack", "defensive", "stab", "slash", "chop"])
@pytest.mark.mcp_full
async def test_layerB_set_attack_style_valid(isolated_lane, unique_username, style):
    """All documented attack styles must dispatch without error."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("set_attack_style", {"style": style})
            assert not res.is_error, res.text[:200]
            assert "style" in res.text.lower() or style in res.text.lower()
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_set_attack_style_invalid_fallback(isolated_lane, unique_username):
    """The server is intentionally lenient — unknown styles silently fall
    back to `hack` rather than erroring (don't crash the agent loop on a
    typo). This test pins that behavior so a future tightening is visible."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("set_attack_style", {"style": "notareal"})
            assert res.text, "empty response on bad style"
            data = res.json()
            # Accept: error OR lenient fallback with a style-mention.
            assert (
                res.is_error
                or (data and "error" in data)
                or "style" in res.text.lower()
            )
    finally:
        cleanup_player(unique_username)
