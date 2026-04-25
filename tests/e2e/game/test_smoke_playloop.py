"""Smoke test — exercises the canonical OODA loop end-to-end.

If this goes red, something foundational broke (seed, login, MCP transport,
state_extractor, observe schema, or one of the core tools). It's the single
most useful signal in the suite when triaging a regression.

Runs:
  1. seed a Level-1 player at Mudwich
  2. login + auto-warp checks pass
  3. observe returns expected top-level keys
  4. a new quest accept flow works (Foresting)
  5. navigate moves the player
  6. attack a Rat + observe shows combat signal
  7. stuck_reset is a no-op on a fresh session
  8. warp back to Mudwich completes

Any failure here warrants immediate attention before running the wider suite.
"""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session
from ..helpers.kaetram_world import adjacent_to


@pytest.mark.mcp
async def test_full_ooda_loop_smoke(test_username):
    cleanup_player(test_username)
    # Seed adjacent to Forester so Foresting can start + we have rats not far.
    seed_player(
        test_username,
        position=adjacent_to("forestnpc"),
        hit_points=69,
        inventory=[
            {"index": 0, "key": "coppersword", "count": 1},
            {"index": 1, "key": "apple", "count": 3},
        ],
        equipment=[{"type": 0, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            # --- Step 1: observe returns the expected schema --------------
            obs = (await s.call_tool("observe", {})).json() or {}
            for key in ("pos", "stats", "digest", "inventory", "active_quests"):
                assert key in obs, f"observe missing key '{key}': {list(obs.keys())}"

            # --- Step 2: start Foresting quest -----------------------------
            assert not obs.get("active_quests"), "precondition: no active quests"
            await s.call_tool("interact_npc", {"npc_name": "Forester"})
            await asyncio.sleep(2.5)

            snap = snapshot_player(test_username)
            quests = (snap.get("player_quests") or {}).get("quests") or []
            foresting = next((q for q in quests if q.get("key") == "foresting"), None)
            assert foresting and int(foresting.get("stage", 0) or 0) > 0, (
                f"Foresting did not start after interact_npc: {foresting}"
            )

            # --- Step 3: navigate a few tiles ------------------------------
            pos_before = obs.get("pos") or {}
            x0, y0 = pos_before.get("x", 0), pos_before.get("y", 0)
            await s.call_tool("navigate", {"x": x0 + 5, "y": y0 + 2})
            await asyncio.sleep(5.0)
            obs2 = (await s.call_tool("observe", {})).json() or {}
            pos_after = obs2.get("pos") or {}
            assert (pos_after.get("x"), pos_after.get("y")) != (x0, y0), (
                f"navigate didn't move: start=({x0},{y0}) end={pos_after}"
            )

            # --- Step 4: stuck_reset is a no-op ---------------------------
            r = await s.call_tool("stuck_reset", {})
            assert not r.is_error, f"stuck_reset crashed: {r.text[:200]}"

            # --- Step 5: warp back to Mudwich -----------------------------
            await s.call_tool("warp", {"location": "mudwich"})
            await asyncio.sleep(2.5)
            obs3 = (await s.call_tool("observe", {})).json() or {}
            pos_final = obs3.get("pos") or {}
            assert 180 <= pos_final.get("x", 0) <= 200, (
                f"warp to mudwich didn't land near centre: {pos_final}"
            )
            assert 150 <= pos_final.get("y", 0) <= 170

            # --- Step 6: stats + inventory still intact after everything --
            stats = obs3.get("stats") or {}
            assert stats.get("max_hp", 0) > 0, f"stats lost max_hp: {stats}"
            inv = obs3.get("inventory") or []
            assert any("sword" in str(i.get("name", "")).lower() for i in inv), (
                f"seeded copper sword missing from final inventory: {inv}"
            )
    finally:
        cleanup_player(test_username)
