"""Layer B tests for the `drop_item` MCP tool.

Apr 18-19 production data shows drop_item at 0% success across 82 calls,
typically firing from an inventory-full panic. Agents often correctly
guess that a lootbag popup is blocking the drop but the tool reports only
"inventory count unchanged" without surfacing that modal state.

Layer B covers:

- happy path: seed one junk item in slot 0 on an otherwise empty inventory,
  call drop_item(0), assert the response JSON reports dropped=true and the
  inventory_after < inventory_before.
- empty-slot guard: call drop_item on an empty slot; assert the tool
  surfaces a clean "No item in slot N" error rather than silently
  claiming success.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_drop_item_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
        inventory=[{"key": "mushroom1", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("drop_item", {"slot": 0})

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        assert payload.get("dropped") is True, payload
        before = payload.get("inventory_before")
        after = payload.get("inventory_after")
        assert isinstance(before, int) and isinstance(after, int), payload
        assert after < before, f"expected inventory to shrink, got {payload}"
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_drop_item_empty_slot(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=(199, 169),
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            result = await session.call_tool("drop_item", {"slot": 5})

        assert not result.is_error, result.text
        payload = json.loads(result.text)
        err = (payload.get("error") or "").lower()
        assert "no item" in err or "slot" in err, (
            f"expected an empty-slot error so agent doesn't retry blindly, got {payload}"
        )
    finally:
        cleanup_player(unique_username)
