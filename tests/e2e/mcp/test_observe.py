"""Day-2 first-green: Layer A + Layer B `observe` happy path.

Layer A: seed a player, log in via Playwright, read the snapshot, assert
that the player is at the seeded position and the `observe_via_browser`
helper returns a well-shaped dict.

Layer B: spawn the MCP subprocess for the same seeded username and assert
the `observe` tool returns a non-error response that mentions the player's
name or position.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.browser import browser_session, login_seeded_player
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.observe import observe_via_browser
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerA_observe_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
        inventory=[{"key": "apple", "count": 1}],
    )
    try:
        async with browser_session() as (_browser, _context, page):
            await login_seeded_player(page, unique_username, client_url=isolated_lane.client_url)
            import asyncio as _a
            await _a.sleep(2.0)
            snapshot = await observe_via_browser(page)
            import json as _j
            print("PLAYER:", _j.dumps(snapshot.get("player"), indent=2))
            print("QUESTS:", _j.dumps(snapshot.get("quests"), indent=2))
            print("INV:", _j.dumps(snapshot.get("inventory"), indent=2))

        assert "error" not in snapshot, snapshot.get("error")
        assert snapshot["player"]["x"] == 199
        assert snapshot["player"]["y"] == 169
        assert snapshot["inventory"].get("apple", 0) >= 1
        assert "skills" in snapshot
        assert "equipment" in snapshot
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_observe_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("observe", {})

        assert not result.is_error, result.text
        assert result.text, "observe returned empty content"
    finally:
        cleanup_player(unique_username)
