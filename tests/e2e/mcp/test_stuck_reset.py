"""stuck_reset() tool — clears the stuck detector's accumulated state."""

from __future__ import annotations

import pytest

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_stuck_reset_clears_counter(seeded_player):
    """stuck_reset is idempotent; invoking it should never fail, and a
    subsequent observe should report stuck=False."""
    async with mcp_session(username=seeded_player["username"]) as s:
        await s.call_tool("observe", {})
        res = await s.call_tool("stuck_reset", {})
        assert not res.is_error, res.text[:200]
        obs = (await s.call_tool("observe", {})).json() or {}
        status = obs.get("status") or {}
        assert status.get("stuck") in (False, None), f"still stuck: {status}"
