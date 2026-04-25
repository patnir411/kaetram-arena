"""attack() — seed 1 tile south of a known Rat spawn, verify damage dealt."""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player

from ..helpers.mcp_client import mcp_session

AUTOSAVE_WAIT = 5.0
# Rat entity confirmed at (202, 142) in world.json entities table (Mudwich).
RAT_SEED_POS = (202, 143)


@pytest.mark.mcp
async def test_attack_rat_deals_damage(test_username):
    """Seed with Copper Sword 1 tile south of Rat at (202,142). One attack call
    must either deal damage or confirm a kill. If a kill is confirmed, Mongo
    player_statistics must show ≥1 mob killed after autosave."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=RAT_SEED_POS,
        hit_points=69,
        inventory=[
            {"index": 0, "key": "coppersword", "count": 1},
            {"index": 1, "key": "apple", "count": 5},
        ],
        equipment=[{"type": 0, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("attack", {"mob_name": "Rat"})
            assert not res.is_error, f"attack errored: {res.text[:300]}"
            data = res.json() or {}
            post = data.get("post_attack") or {}
            damage = int(post.get("damage_dealt", 0))
            killed = bool(post.get("killed"))

            if damage == 0 and not killed:
                # Retry once if no damage — mob might have been wandering and we were walking
                res = await s.call_tool("attack", {"mob_name": "Rat"})
                assert not res.is_error, f"attack retry errored: {res.text[:300]}"
                data = res.json() or {}
                post = data.get("post_attack") or {}
                damage = int(post.get("damage_dealt", 0))
                killed = bool(post.get("killed"))

            assert damage > 0 or killed, (
                f"0 damage and no kill near {RAT_SEED_POS}; response: {data}"
            )
            if not killed:
                return

        await asyncio.sleep(AUTOSAVE_WAIT)
        snap = snapshot_player(test_username)
        stats = snap.get("player_statistics") or {}
        mob_kills_dict = stats.get("mobKills") or {}
        mob_kills = sum(int(v) for v in mob_kills_dict.values() if isinstance(v, (int, float)))
        assert mob_kills >= 1, f"kill reported but Mongo shows 0: {stats}"
    finally:
        cleanup_player(test_username)
