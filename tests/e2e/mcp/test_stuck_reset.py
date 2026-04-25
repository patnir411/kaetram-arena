"""stuck_reset() tool — clears accumulated stuck-detector state.

Idempotent: calling on a fresh session should succeed and a following
observe should show `digest.stuck = false`.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_stuck_reset_clears_state(isolated_lane, unique_username):
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
            res = await session.call_tool("stuck_reset", {})
            assert not res.is_error, res.text[:200]
            obs = (await session.call_tool("observe", {})).json() or {}
            digest = obs.get("digest") or {}
            assert digest.get("stuck") in (False, None), (
                f"still stuck after reset: {digest}"
            )
    finally:
        cleanup_player(unique_username)
