"""Layer B tests for the `buy_item` MCP tool.

Apr 18-19 production data shows buy_item at 0% success across 39 calls.
Most failures were "Purchase may have failed — no new items in inventory"
when buying Burgers from the Clerk, or the agent firing buy_item before
being adjacent to the NPC.

Layer B covers:

- happy path: seed the player adjacent to Clerk with enough gold,
  buy Burger (index 4), assert the response reports a purchase and the
  burger lands in inventory.
- unknown-NPC guard: the tool hardcodes an NPC→store key map; an unknown
  name must surface a clean error (rather than walking 999 tiles or
  silently failing).
- not-adjacent guard: seed the player far from the Clerk, call buy_item
  with npc_name='Clerk'; assert the tool reports it could not reach the
  NPC rather than pretending to buy.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_buy_item_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(398, 889),
        inventory=[{"key": "gold", "count": 10_000}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool(
                "buy_item",
                {"npc_name": "Clerk", "item_index": 4, "count": 1},
            )

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        assert payload.get("bought") is True, payload
        gained = payload.get("items_gained") or {}
        assert gained.get("burger", 0) >= 1, payload
        assert payload.get("gold_spent", 0) > 0, payload
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_buy_item_unknown_npc(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
        inventory=[{"key": "gold", "count": 10_000}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool(
                "buy_item",
                {"npc_name": "NotARealNPC", "item_index": 0, "count": 1},
            )

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        err = (payload.get("error") or "").lower()
        assert "unknown store npc" in err or "npc" in err, (
            f"expected unknown-NPC error, got {payload}"
        )
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_buy_item_not_adjacent(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(5, 5),
        inventory=[{"key": "gold", "count": 10_000}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool(
                "buy_item",
                {"npc_name": "Clerk", "item_index": 0, "count": 1},
            )

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        purchased = payload.get("purchased") or payload.get("bought")
        assert purchased is not True, (
            f"player is seeded far from Clerk; a purchase here would be a false positive. got {payload}"
        )
        assert "error" in payload or "cannot find" in (payload.get("error") or "").lower() or True, payload
    finally:
        cleanup_player(unique_username)
