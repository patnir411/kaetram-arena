"""Canonical OODA smoke — the single most useful signal when triaging.

Runs 6 steps end-to-end: observe → start-quest → navigate → stuck_reset →
warp → observe-again. If ANY step fails, something foundational is broken
(seed, login, MCP transport, state_extractor, observe schema, or a core
tool). Fix here before running the full matrix.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_full_ooda_smoke(isolated_lane, unique_username):
    seed_player(
        unique_username,
        position=adjacent_to("forestnpc"),
        hit_points=69,
        inventory=[
            {"key": "coppersword", "count": 1},
            {"key": "apple", "count": 3},
        ],
        equipment=[{"type": 0, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            # 1. observe — expected schema
            obs = (await session.call_tool("observe", {})).json() or {}
            # Accept either compact (pos) or full (player_position) shape.
            pos_key = "pos" if "pos" in obs else "player_position"
            assert pos_key in obs, f"observe missing position field: {list(obs)[:5]}"

            # 2. interact_npc — start Foresting quest
            await session.call_tool("interact_npc", {"npc_name": "Forester"})
            await asyncio.sleep(2.5)

            # 3. navigate — a few tiles
            pos0 = obs.get(pos_key) or {}
            x0, y0 = pos0.get("x", 0), pos0.get("y", 0)
            await session.call_tool("navigate", {"x": x0 + 5, "y": y0 + 2})
            await asyncio.sleep(4.0)

            # 4. stuck_reset — idempotent no-op
            r = await session.call_tool("stuck_reset", {})
            assert not r.is_error, f"stuck_reset crashed: {r.text[:200]}"

            # 5. warp home
            await session.call_tool("warp", {"location": "mudwich"})
            await asyncio.sleep(2.5)

            # 6. observe again — Mudwich area
            final = (await session.call_tool("observe", {})).json() or {}
            fpos = final.get(pos_key) or {}
            assert 180 <= fpos.get("x", 0) <= 200, (
                f"warp to mudwich landed off-target: {fpos}"
            )
    finally:
        cleanup_player(unique_username)
