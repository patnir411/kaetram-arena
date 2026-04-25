"""Layer B tests for the `equip_item` MCP tool.

Apr 18-19 production data shows equip_item at 0% success across 283 calls
with top error "stat/level req not met" x69. The agent reliably thinks
level unlocks gear, then retries the same identical equip_item call after
a failure. These tests pin both the happy path and the documented guard.

Layer B covers:

    - happy path: seed a tin sword (non-starter, no stat req) into
  slot 0, call equip_item(0), assert the response JSON says equipped=true
  and some equipment slot actually changed.
- stat/level guard: seed a high-tier weapon (ironaxe) into slot 0 while
  the player has no combat stats, call equip_item(0), assert equipped=false
  and the response surfaces the documented "Stat/level requirement not met"
  hint so the agent can stop retrying.
- empty-slot guard: call equip_item on an empty slot; the tool should
  surface a clean error rather than pretend it did something.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_equip_item_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
        inventory=[{"key": "tinsword", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("equip_item", {"slot": 0})

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        assert payload.get("equipped") is True, payload
        assert payload.get("item", "").lower() in {"tinsword", "tin sword"}, payload
        changes = payload.get("changes") or {}
        assert changes, f"expected at least one equipment slot to change, got {payload}"
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_equip_item_stat_req_not_met(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
        inventory=[{"key": "ironaxe", "count": 1}],
        skills=[
            {"type": 0, "experience": 0},
            {"type": 1, "experience": 0},
            {"type": 2, "experience": 0},
        ],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("equip_item", {"slot": 0})

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        assert payload.get("equipped") is False, payload
        err = (payload.get("error") or "").lower()
        assert "stat" in err or "level" in err, (
            f"expected stat/level hint in error so agent stops retrying, got {payload}"
        )
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_equip_item_empty_slot(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("equip_item", {"slot": 10})

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        assert payload.get("equipped") is not True, payload
        assert "error" in payload or payload.get("equipped") is False, payload
    finally:
        cleanup_player(unique_username)
