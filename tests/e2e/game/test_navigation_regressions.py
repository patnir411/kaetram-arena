"""Focused navigation regressions for browser-side pathing behavior.

These are intentionally narrower than quest reachability tests. They validate
the browser nav implementation directly so we can distinguish:

1. obstacle-avoidance regressions in `state_extractor.js`
2. long-route BFS boundary behavior
3. local stall / reroute failures near collision clutter
"""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session
from ..helpers.kaetram_world import NPCS

START_CLUSTER = (319, 158)
CLUSTER_ESCAPE_TARGET = (379, 205)
RICK_POS = NPCS["rick"]


async def _observe_nav(session) -> tuple[dict, dict]:
    obs = (await session.call_tool("observe", {})).json() or {}
    return obs, (obs.get("navigation") or {})


@pytest.mark.mcp
async def test_nav_obstacle_cluster_reroutes_and_escapes(test_username):
    """Regression: a local reroute near Mudwich should not park behind trees.

    This seeds the player in the exact obstacle cluster that repeatedly trapped
    the Arts and Crafts reachability walk. The browser nav should use BFS and
    move the player materially off the cluster instead of idling in place.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=START_CLUSTER,
        hit_points=3039,
        skills=[{"type": 3, "experience": 15_000_000}],
        inventory=[
            {"index": 0, "key": "bronzeaxe", "count": 1},
            {"index": 1, "key": "coppersword", "count": 1},
        ],
    )
    try:
        async with mcp_session(username=test_username) as s:
            result = await s.call_tool("navigate", {"x": CLUSTER_ESCAPE_TARGET[0], "y": CLUSTER_ESCAPE_TARGET[1]})
            assert not result.is_error, result.text[:300]
            data = result.json() or {}
            assert data.get("pathfinding") == "bfs", f"expected bfs route, got {data}"

            moved_far = False
            last_obs: dict = {}
            last_nav: dict = {}
            for _ in range(15):
                await asyncio.sleep(2.0)
                last_obs, last_nav = await _observe_nav(s)
                pos = last_obs.get("pos") or {}
                dist_from_start = abs(int(pos.get("x", START_CLUSTER[0])) - START_CLUSTER[0]) + abs(
                    int(pos.get("y", START_CLUSTER[1])) - START_CLUSTER[1]
                )
                if dist_from_start >= 12:
                    moved_far = True
                    break
                assert last_nav.get("status") != "stuck", (
                    f"navigation got stuck before escaping obstacle cluster: nav={last_nav} obs={last_obs}"
                )

            assert moved_far, f"player did not escape obstacle cluster: nav={last_nav} obs={last_obs}"

            arrived = False
            for _ in range(15):
                await asyncio.sleep(2.0)
                last_obs, last_nav = await _observe_nav(s)
                pos = last_obs.get("pos") or {}
                dist_to_target = abs(int(pos.get("x", -999)) - CLUSTER_ESCAPE_TARGET[0]) + abs(
                    int(pos.get("y", -999)) - CLUSTER_ESCAPE_TARGET[1]
                )
                if dist_to_target <= 6 or last_nav.get("status") == "arrived":
                    arrived = True
                    break
                assert last_nav.get("status") != "stuck", (
                    f"navigation got stuck after reroute: nav={last_nav} obs={last_obs}"
                )

            assert arrived, f"player did not reach reroute target: nav={last_nav} obs={last_obs}"
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_nav_long_direct_route_fails_bounded_without_linear_fallback(test_username):
    """A direct long route beyond BFS bounds should fail explicitly.

    The old behavior degraded into straight-line waypointing (`linear_fallback`)
    which walked into walls and clutter. Long direct routes should now return a
    bounded stuck/no-route result instead.
    """
    cleanup_player(test_username)
    seed_player(test_username, position=(188, 157), hit_points=69)
    try:
        async with mcp_session(username=test_username) as s:
            result = await s.call_tool("navigate", {"x": RICK_POS[0], "y": RICK_POS[1]})
            assert not result.is_error, result.text[:300]
            data = result.json() or {}
            assert data.get("pathfinding") != "linear_fallback", f"unexpected linear fallback: {data}"
            assert data.get("status") == "stuck", f"expected bounded failure, got {data}"
            assert data.get("pathfinding") == "bfs_failed", f"expected bfs_failed, got {data}"
            assert "No BFS path found" in str(data.get("error") or ""), f"missing no-path error: {data}"

            obs, nav = await _observe_nav(s)
            assert nav.get("status") == "stuck", f"observe did not preserve stuck nav state: nav={nav} obs={obs}"
            assert nav.get("pathfinding_method") == "bfs_failed", (
                f"observe pathfinding method mismatch: nav={nav} obs={obs}"
            )
    finally:
        cleanup_player(test_username)
