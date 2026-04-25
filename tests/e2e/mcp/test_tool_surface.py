"""tool_surface — lock the 17-tool model-visible contract.

If any tool is renamed, deleted, or hidden from the model, this fires red
immediately — before the training data pipeline silently collects broken
sessions that still parse but never call the missing action.

Gives a single-file early-warning signal for any edit to
mcp_game_server.py's @mcp.tool() registrations.
"""

from __future__ import annotations

import pytest

from tool_surface import MODEL_VISIBLE_TOOL_NAMES

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_mcp_exposes_all_model_visible_tools(isolated_lane, unique_username):
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
            tools = set(await session.list_tools())
            missing = [t for t in MODEL_VISIBLE_TOOL_NAMES if t not in tools]
            assert not missing, (
                f"MCP does not expose model-visible tools: {missing}. "
                f"Check @mcp.tool() registrations in mcp_game_server.py."
            )
    finally:
        cleanup_player(unique_username)
