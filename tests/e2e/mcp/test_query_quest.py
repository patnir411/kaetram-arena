"""query_quest() — look up Foresting walkthrough, verify response is non-empty."""

from __future__ import annotations

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_query_quest_known(seeded_player):
    """Foresting is a known quest. query_quest must return data or a clean
    'not found' — never a crash."""
    async with mcp_session(username=seeded_player["username"]) as s:
        res = await s.call_tool("query_quest", {"quest_name": "Foresting"})
        assert not res.is_error
        text = res.text.lower()
        assert "foresting" in text or "walkthrough" in text or "not found" in text
