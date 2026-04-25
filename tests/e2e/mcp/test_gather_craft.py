from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import McpSession, mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player

FORESTER_ADJACENT = (216, 115)
MUDWICH_CENTER = (188, 157)


async def _observe_state(session: McpSession) -> dict:
    return (await session.call_tool("observe", {})).observe_state()


def _inventory_count(state: dict, key: str) -> int:
    total = 0
    for item in state.get("inventory", []):
        if item.get("key") == key:
            total += int(item.get("count") or 0)
    return total


@pytest.mark.mcp_smoke
async def test_layerB_gather_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=FORESTER_ADJACENT,
        skills=[{"type": 15, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("gather", {"resource_name": "Tomato"})
            state = await _observe_state(session)

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("resource", "").lower().startswith("tomato"), payload
        assert _inventory_count(state, "tomato") >= 1, (payload, state)
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_craft_item_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
        inventory=[
            {"key": "knife", "count": 1},
            {"key": "logs", "count": 1},
        ],
        skills=[{"type": 13, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool(
                "craft_item",
                {"skill": "fletching", "recipe_key": "stick", "count": 1},
            )
            state = await _observe_state(session)

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("crafted") is True, payload
        assert payload.get("skill") == "fletching", payload
        assert payload.get("recipe_key") == "stick", payload
        assert _inventory_count(state, "stick") >= 4, (payload, state)
        assert _inventory_count(state, "logs") == 0, (payload, state)
    finally:
        cleanup_player(unique_username)
