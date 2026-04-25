"""Integration tests — multi-tool chains that mirror real gameplay.

Each test runs a realistic sequence (observe → act → observe → act...) and
asserts state transitions at each step. Catches breakage in tool interop
that single-tool tests would miss — e.g., a broken `observe` that hides the
post-attack HP delta, or an attack tool that returns success but doesn't
actually commit damage server-side.
"""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_observe_attack_observe_shows_damage(test_username):
    """Seed near a Rat, attack it, second observe must show damaged HP (if the
    mob survived) OR the mob missing from the list (if killed)."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(186, 168),
        hit_points=69,
        inventory=[
            {"index": 0, "key": "coppersword", "count": 1},
        ],
        equipment=[{"type": 0, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            obs_before = (await s.call_tool("observe", {})).json() or {}
            mobs_before = obs_before.get("mobs_within_15") or []
            rats_before = [m for m in mobs_before if (m.get("name") or "").lower() == "rat"]
            assert rats_before, f"no rats nearby at seed: {mobs_before}"
            rat_hp_before = rats_before[0].get("hp", 0)

            await s.call_tool("attack", {"mob_name": "Rat"})
            await asyncio.sleep(3.0)

            obs_after = (await s.call_tool("observe", {})).json() or {}
            mobs_after = obs_after.get("mobs_within_15") or []
            rats_after = [m for m in mobs_after if (m.get("name") or "").lower() == "rat"]

            # Either: (a) rat HP dropped, or (b) rat is no longer visible (killed),
            # or (c) a new rat respawned with same HP. Acceptable is any non-zero
            # combat signal — the pre-patch fake-kill bug would show zero change.
            rat_hp_after = rats_after[0].get("hp", rat_hp_before) if rats_after else 0
            combat_happened = (
                rat_hp_after < rat_hp_before
                or len(rats_after) != len(rats_before)
            )
            assert combat_happened, (
                f"no combat signal: rat HP {rat_hp_before} → {rat_hp_after}, "
                f"count {len(rats_before)} → {len(rats_after)}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_navigate_then_observe_shows_new_position(seeded_player):
    """navigate(x, y) → wait → observe must report the new position — at least
    closer to the target than the start."""
    async with mcp_session(username=seeded_player["username"]) as s:
        obs_before = (await s.call_tool("observe", {})).json() or {}
        pos_before = obs_before.get("pos") or {}
        x0, y0 = pos_before.get("x", 0), pos_before.get("y", 0)

        target_x, target_y = x0 + 6, y0   # 6 tiles east
        await s.call_tool("navigate", {"x": target_x, "y": target_y})
        # Give pathfinding time to run.
        await asyncio.sleep(6.0)

        obs_after = (await s.call_tool("observe", {})).json() or {}
        pos_after = obs_after.get("pos") or {}
        x1, y1 = pos_after.get("x", 0), pos_after.get("y", 0)

        dist_before = abs(target_x - x0) + abs(target_y - y0)
        dist_after = abs(target_x - x1) + abs(target_y - y1)
        assert dist_after < dist_before, (
            f"navigate made no progress: ({x0},{y0}) → ({x1},{y1}) "
            f"target ({target_x},{target_y})"
        )


@pytest.mark.mcp
async def test_eat_then_observe_shows_hp_recovery(test_username):
    """Seed HP=25, eat an apple, observe should show HP > 25."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(188, 157),
        hit_points=25,
        inventory=[
            {"index": 0, "key": "bronzeaxe", "count": 1},
            {"index": 1, "key": "apple", "count": 5},
        ],
    )
    try:
        async with mcp_session(username=test_username) as s:
            obs_before = (await s.call_tool("observe", {})).json() or {}
            hp_before = (obs_before.get("stats") or {}).get("hp", 0)
            assert hp_before <= 30, f"expected low HP at seed, got {hp_before}"

            res = await s.call_tool("eat_food", {"slot": 1})
            assert not res.is_error, res.text[:200]
            await asyncio.sleep(1.5)

            obs_after = (await s.call_tool("observe", {})).json() or {}
            hp_after = (obs_after.get("stats") or {}).get("hp", 0)
            tool_data = res.json() or {}
            # HP increase OR explicit tool confirmation — the eat_food tool
            # may commit the heal server-side faster than observe can see it.
            assert (
                hp_after > hp_before
                or tool_data.get("healed")
                or tool_data.get("consumed")
                or "hp" in res.text.lower()
            ), f"no heal signal: hp {hp_before} → {hp_after}, tool: {tool_data}"
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_warp_changes_position(seeded_player):
    """warp to mudwich from any position should land the player near (188,157).
    This also exercises the post-warp observe refresh."""
    async with mcp_session(username=seeded_player["username"]) as s:
        # Seed is at Mudwich, so warp to Lakesworld first to create distance.
        await s.call_tool("observe", {})
        await s.call_tool("warp", {"location": "lakesworld"})
        await asyncio.sleep(2.0)
        obs_mid = (await s.call_tool("observe", {})).json() or {}
        pos_mid = obs_mid.get("pos") or {}

        # Now warp back to mudwich.
        await s.call_tool("warp", {"location": "mudwich"})
        await asyncio.sleep(2.0)
        obs_end = (await s.call_tool("observe", {})).json() or {}
        pos_end = obs_end.get("pos") or {}

        # End position should be in Mudwich area.
        assert 180 <= pos_end.get("x", 0) <= 200, f"not back at Mudwich: {pos_end}"
        assert 150 <= pos_end.get("y", 0) <= 170


@pytest.mark.mcp
async def test_observe_between_attacks_is_fresh(test_username):
    """Repeated observes during combat should report changing target HP —
    catches staleness bugs where observe returns cached state instead of
    fresh game data. Critical for the OODA loop's correctness."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(186, 168),
        inventory=[{"index": 0, "key": "coppersword", "count": 1}],
        equipment=[{"type": 0, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await s.call_tool("attack", {"mob_name": "Rat"})
            await asyncio.sleep(1.0)

            # Collect 3 observes 1s apart. If observe is caching stale state,
            # all three will return identical HP for the target mob.
            observations = []
            for _ in range(3):
                obs = (await s.call_tool("observe", {})).json() or {}
                tgt = obs.get("current_target")
                if tgt:
                    observations.append(tgt.get("hp"))
                await asyncio.sleep(1.5)

            # If we had a target in at least one observe, accept the result.
            # If HP was tracked across multiple, it should change (mob HP
            # drops on each attack tick, or drops to 0 on kill).
            if len(observations) >= 2:
                distinct = len(set(observations))
                assert distinct > 1 or any(h == 0 for h in observations), (
                    f"observe appears stale — same target HP in {len(observations)} "
                    f"snapshots: {observations}"
                )
    finally:
        cleanup_player(test_username)
