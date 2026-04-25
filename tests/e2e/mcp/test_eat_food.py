"""eat_food() — consume apple at low HP, verify heal + Mongo shows fewer apples."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0


@pytest.mark.mcp
async def test_eat_food_heals_and_consumes(test_username):
    """Seed 30 HP + 5 apples. eat_food(1) must heal (HP delta or payload signal)
    and Mongo must show fewer apples after autosave."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        hit_points=30,
        inventory=[
            {"index": 0, "key": "bronzeaxe", "count": 1},
            {"index": 1, "key": "apple", "count": 5},
        ],
    )
    try:
        async with mcp_session(username=test_username) as s:
            before_obs = (await s.call_tool("observe", {})).json() or {}
            hp_before = (before_obs.get("stats") or {}).get("hp", 0)

            res = await s.call_tool("eat_food", {"slot": 1})
            assert not res.is_error, res.text[:200]

            await asyncio.sleep(2.0)
            after_obs = (await s.call_tool("observe", {})).json() or {}
            hp_after = (after_obs.get("stats") or {}).get("hp", 0)

            data = res.json() or {}
            assert (
                hp_after > hp_before or data.get("healed") or data.get("consumed")
            ), f"no heal: hp_before={hp_before}, hp_after={hp_after}, payload={data}"

        await asyncio.sleep(AUTOSAVE_WAIT)

        snap = snapshot_player(test_username)
        inv_slots = (snap.get("player_inventory") or {}).get("slots") or []
        apple_after = next(
            (int(s.get("count", 0)) for s in inv_slots if s.get("key") == "apple"),
            0,
        )
        assert apple_after < 5, (
            f"apple count unchanged in Mongo after eating (after={apple_after})"
        )
    finally:
        cleanup_player(test_username)
