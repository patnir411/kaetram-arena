from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import McpSession, mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player

MUDWICH_CENTER = (188, 157)
FORESTER_ADJACENT = (216, 115)
BLACKSMITH_ADJACENT = (199, 168)
BIKE_LYSON_ADJACENT = (166, 113)


async def _observe_state(session: McpSession) -> dict:
    return (await session.call_tool("observe", {})).observe_state()


def _inventory_count(state: dict, key: str) -> int:
    total = 0
    for item in state.get("inventory", []):
        if item.get("key") == key:
            total += int(item.get("count") or 0)
    return total


def _distance_to(state: dict, target: tuple[int, int]) -> int:
    pos = state.get("player_position") or {}
    return abs(pos.get("x", 9999) - target[0]) + abs(pos.get("y", 9999) - target[1])


async def _observe_until_moved(
    session: McpSession,
    *,
    away_from: tuple[int, int],
    attempts: int = 4,
) -> dict:
    latest = {}
    for _ in range(attempts):
        latest = await _observe_state(session)
        if _distance_to(latest, away_from) >= 1:
            return latest
    return latest


@pytest.mark.mcp_smoke
async def test_layerB_interact_npc_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=BLACKSMITH_ADJACENT,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Blacksmith"})

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("npc") == "Blacksmith", payload
        assert payload.get("arrived") is True, payload
        assert payload.get("dialogue_lines", 0) >= 1 or payload.get("quest_opened") is True, payload
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_eat_food_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
        hit_points=20,
        inventory=[{"key": "burger", "count": 1}],
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            before = await _observe_state(session)
            result = await session.call_tool("eat_food", {"slot": 0})
            after = await _observe_state(session)

        assert before.get("player_stats", {}).get("hp") == 20, before
        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("eating") is True, payload
        assert after.get("player_stats", {}).get("hp", 0) > 20, after
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_loot_round_trip_after_drop(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
        inventory=[{"key": "mushroom1", "count": 1}],
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            dropped = await session.call_tool("drop_item", {"slot": 0})
            await session.call_tool("navigate", {"x": 190, "y": 157})
            moved = await _observe_until_moved(session, away_from=MUDWICH_CENTER)
            states = []
            for _ in range(3):
                states.append((await session.call_tool("loot", {})).json())
                observed = await _observe_state(session)
                if _inventory_count(observed, "mushroom1") >= 1:
                    break

        assert not dropped.is_error, dropped.text
        assert dropped.json().get("dropped") is True, dropped.text
        assert _distance_to(moved, MUDWICH_CENTER) >= 1, moved
        assert any(state.get("state") != "nothing_nearby" for state in states), states
        assert _inventory_count(observed, "mushroom1") >= 1, (states, observed)
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_respawn_warps_to_mudwich(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=FORESTER_ADJACENT,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("respawn", {})
            state = await _observe_state(session)

        assert not result.is_error, result.text
        assert "respawned and combat cleared" in result.text.lower(), result.text
        assert _distance_to(state, MUDWICH_CENTER) <= 6, state
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_query_quest_exact_match(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=BLACKSMITH_ADJACENT,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("query_quest", {"quest_name": "Anvil's Echoes"})

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("name") == "Anvil's Echoes", payload
        assert payload.get("matched_name") == "Anvil's Echoes", payload
        assert payload.get("status"), payload
        assert payload.get("walkthrough"), payload
    finally:
        cleanup_player(unique_username)
