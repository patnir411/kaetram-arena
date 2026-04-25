"""gather() — chop oak with a valid woodcutting setup."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0
# Oak tree entity at (206, 118) confirmed near the Forester grove.
# Seed 1 tile east so the player starts adjacent.
OAK_SEED_POS = (207, 118)
# Skill type 0 = Lumberjacking (from packages/common/network/modules.ts SkillsOrder).
LUMBERJACKING = 0


@pytest.mark.mcp
async def test_gather_tree_adds_logs(test_username):
    """Seed a real axe plus enough lumberjacking skill to remove ambiguity.

    The old version conflated tree proximity with a valid gathering loadout.
    This one explicitly seeds the tool and skill level needed for a clean oak
    chop.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=OAK_SEED_POS,
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
        equipment=[{"type": 0, "key": "bronzeaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[{"type": LUMBERJACKING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            equip_res = await s.call_tool("equip_item", {"slot": 0})
            assert not equip_res.is_error, f"equip_item errored before gather: {equip_res.text[:300]}"
            await asyncio.sleep(1.0)

            res = await s.call_tool("gather", {"resource_name": "oak"})
            assert not res.is_error, f"gather errored: {res.text[:300]}"

            # Tree cutting can complete slightly after the tool returns if the
            # client/server animation finishes on a later tick. Give the chop a
            # little extra time, then poll observe until logs appear.
            for _ in range(12):
                await asyncio.sleep(2.0)
                obs = (await s.call_tool("observe", {})).json() or {}
                if any("log" in str(i.get("name", "")).lower() for i in (obs.get("inventory") or [])):
                    break
            else:
                pytest.fail(f"no logs in live inventory after gather(oak); tool response: {res.json()}")

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_keys = [
            sl.get("key") for sl in
            (snap.get("player_inventory") or {}).get("slots") or []
            if sl.get("key")
        ]
        assert "logs" in inv_keys, f"logs missing from Mongo after autosave: {inv_keys}"

        skills = (snap.get("player_skills") or {}).get("skills") or []
        lumber_xp = next(
            (int(sk.get("experience", 0)) for sk in skills if sk.get("type") == LUMBERJACKING),
            0,
        )
        assert lumber_xp > 0, f"lumberjacking XP still 0 in Mongo; skills={skills}"
    finally:
        cleanup_player(test_username)
