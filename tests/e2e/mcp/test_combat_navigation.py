from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import McpSession, mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player

MUDWICH_CENTER = (188, 157)
BLACKSMITH_TILE = (199, 169)
FORESTER_TILE = (216, 114)


async def _observe_state(session: McpSession) -> dict:
    return (await session.call_tool("observe", {})).observe_state()


def _distance_to(state: dict, target: tuple[int, int]) -> int:
    pos = state.get("player_position") or {}
    return abs(pos.get("x", 9999) - target[0]) + abs(pos.get("y", 9999) - target[1])


async def _observe_until_position(
    session: McpSession,
    target: tuple[int, int],
    *,
    attempts: int = 4,
) -> dict:
    latest = {}
    for _ in range(attempts):
        latest = await _observe_state(session)
        if latest.get("player_position") == {"x": target[0], "y": target[1]}:
            return latest
    return latest


@pytest.mark.mcp_smoke
async def test_layerB_attack_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("attack", {"mob_name": "Rat"})
            state = await _observe_state(session)

        assert not result.is_error, result.text
        payload = result.json()
        assert "error" not in payload, payload
        post = payload.get("post_attack") or {}
        target = state.get("current_target") or {}
        assert payload.get("attacking", "").lower() == "rat", payload
        assert (
            post.get("damage_dealt", 0) > 0
            or post.get("killed") is True
            or target.get("name", "").lower() == "rat"
        ), (payload, state)
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_navigate_short_range_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("navigate", {"x": FORESTER_TILE[0], "y": FORESTER_TILE[1]})
            state = await _observe_state(session)

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("status") == "navigating", payload
        assert state.get("navigation", {}).get("active") is True, state
        assert state.get("navigation", {}).get("target") == {
            "x": FORESTER_TILE[0],
            "y": FORESTER_TILE[1],
        }, state
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_warp_happy_path(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=FORESTER_TILE,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("warp", {"location": "mudwich"})
            state = await _observe_state(session)

        assert not result.is_error, result.text
        payload = result.json()
        assert payload.get("error") is None, payload
        assert _distance_to(state, MUDWICH_CENTER) <= 6, state
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_set_attack_style_returns_requested_mapping(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            result = await session.call_tool("set_attack_style", {"style": "defensive"})

        assert not result.is_error, result.text
        assert "defensive" in result.text.lower(), result.text
        assert "id=3" in result.text.lower(), result.text
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_cancel_nav_stops_active_route(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            await session.call_tool("navigate", {"x": FORESTER_TILE[0], "y": FORESTER_TILE[1]})
            before = await _observe_state(session)
            result = await session.call_tool("cancel_nav", {})
            after = await _observe_state(session)

        assert before.get("navigation", {}).get("active") is True, before
        assert not result.is_error, result.text
        assert result.text.strip() == "Navigation cancelled", result.text
        assert after.get("navigation", {}).get("active") is False, after
        assert after.get("navigation", {}).get("status") == "idle", after
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_stuck_reset_clears_stuck_flag(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=MUDWICH_CENTER,
    )
    try:
        async with mcp_session(username=unique_username, client_url=isolated_lane.client_url) as session:
            stuck_observes = [await session.call_tool("observe", {}) for _ in range(6)]
            reset = await session.call_tool("stuck_reset", {})
            observe_5 = await session.call_tool("observe", {})

        assert any(obs.observe_stuck_check().get("stuck") is True for obs in stuck_observes), [
            obs.observe_stuck_check() for obs in stuck_observes
        ]
        assert not reset.is_error, reset.text
        assert reset.text.strip() == "Stuck state reset", reset.text
        assert observe_5.observe_stuck_check().get("stuck") is False
    finally:
        cleanup_player(unique_username)
