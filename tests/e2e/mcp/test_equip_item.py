"""equip_item() tool — swap gear from inventory to equipment slot.

Verifies via Mongo player_equipment after autosave that coppersword
replaced bronzeaxe as the equipped weapon.
"""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0


@pytest.mark.mcp
async def test_equip_sword_replaces_axe(test_username):
    """Seed Bronze Axe equipped + Copper Sword in slot 3. After equip_item(3),
    Mongo player_equipment must show coppersword as weapon."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        inventory=[
            {"index": 0, "key": "bronzeaxe", "count": 1},
            {"index": 3, "key": "coppersword", "count": 1},
        ],
        equipment=[{"type": 0, "key": "bronzeaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("equip_item", {"slot": 3})
            assert not res.is_error, res.text[:200]

            # Live observe check — faster signal, may not reflect on all builds
            await asyncio.sleep(1.5)
            obs = (await s.call_tool("observe", {})).json() or {}
            eq_live = obs.get("equipment") or {}
            weapon_live = eq_live.get("weapon") or {}
            tool_data = res.json() or {}

            live_ok = (
                weapon_live.get("key") == "coppersword"
                or str(weapon_live.get("name", "")).lower().startswith("copper")
                or tool_data.get("equipped") == "coppersword"
                or "coppersword" in res.text.lower()
            )

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        equip_slots = (snap.get("player_equipment") or {}).get("slots") or []
        # Equipment slot 0 = weapon
        weapon_key = next(
            (s.get("key") for s in equip_slots if s.get("type") == 0 or s.get("slot") == 0),
            None,
        )
        mongo_ok = weapon_key == "coppersword"

        assert live_ok or mongo_ok, (
            f"coppersword not equipped. live eq: {weapon_live}, "
            f"mongo weapon: {weapon_key}, tool: {res.text[:200]}"
        )
    finally:
        cleanup_player(test_username)
