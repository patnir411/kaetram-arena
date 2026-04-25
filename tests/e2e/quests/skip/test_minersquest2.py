"""Miner's Quest II stage-by-stage quest coverage.

Marked skip_tier: upstream-broken in Kaetram-Open. Run with
``pytest -m skip_tier`` for regression sweeps; excluded from default runs.
"""
import asyncio

import pytest

pytestmark = pytest.mark.skip_tier

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    craft_recipe,
    live_observe,
    wait_for_position,
)

# XP required for mining level 30 (RuneScape formula)
MINING_LVL30_XP = 37224
MINER_ADJACENT = adjacent_to("miner")
SMITHING = 10
SMELTER_SPAWN = (609, 299)
SMELTER_APPROACH_CANDIDATES = [
    (609, 299),
    (608, 299),
    (610, 299),
    (607, 299),
    (611, 299),
    (609, 300),
    (608, 300),
    (610, 300),
    (609, 301),
]

FINISHED_MINERSQUEST = {"key": "minersquest", "stage": 2, "subStage": 0, "completedSubStages": []}


async def _navigate_to_smelter(session) -> dict:
    before = await live_observe(session)
    attempts: list[dict] = []
    latest = before
    for x, y in SMELTER_APPROACH_CANDIDATES:
        nav = await session.call_tool("navigate", {"x": x, "y": y})
        assert not nav.is_error, f"navigate to smelter candidate {(x, y)} errored: {nav.text[:300]}"
        nav_data = nav.json() or {}
        attempt = {"target": {"x": x, "y": y}, "navigate": nav_data}
        attempts.append(attempt)

        if "error" in nav_data:
            continue

        try:
            after = await wait_for_position(
                session,
                x=x,
                y=y,
                max_distance=6,
                polls=35,
                delay_s=1.0,
            )
            attempt["after"] = after
            return {
                "before": before,
                "attempts": attempts,
                "after": after,
            }
        except AssertionError as exc:
            latest = await live_observe(session)
            attempt["latest"] = latest
            attempt["wait_error"] = str(exc)

    raise AssertionError(
        f"failed to reach any smelter approach candidate. "
        f"before={before}. latest={latest}. attempts={attempts}"
    )


@pytest.mark.quest_chain
async def test_minersquest2_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("miner"),
        inventory=[],
        quests=[
            FINISHED_MINERSQUEST,
            {"key": "minersquest2", "stage": 0, "subStage": 0, "completedSubStages": []},
        ],
        skills=[
            {"type": 5, "experience": MINING_LVL30_XP},
            {"type": SMITHING, "experience": 100_000},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Miner"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "minersquest2", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_minersquest2_action_tin_and_copper_smelting_work(test_username):
    seed_player(
        test_username,
        position=SMELTER_SPAWN,
        inventory=[
            {"key": "tinore", "count": 1},
            {"key": "copperore", "count": 1},
            {"key": "coal", "count": 2},
        ],
        quests=[FINISHED_MINERSQUEST],
        skills=[
            {"type": 5, "experience": MINING_LVL30_XP},
            {"type": SMITHING, "experience": 100_000},
        ],
        player_info_overrides={"rank": 2},
    )
    try:
        async with mcp_session(username=test_username) as session:
            travel = await _navigate_to_smelter(session)
            tin_data = await craft_recipe(session, skill="smelting", recipe_key="tinbar", count=1)
            assert int((tin_data.get("inventory_delta") or {}).get("tinbar", 0)) >= 1, (
                f"tinbar smelting did not add output. craft={tin_data}. travel={travel}"
            )
            copper_data = await craft_recipe(session, skill="smelting", recipe_key="copperbar", count=1)
            assert int((copper_data.get("inventory_delta") or {}).get("copperbar", 0)) >= 1, (
                f"copperbar smelting did not add output. craft={copper_data}. travel={travel}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_minersquest2_action_bronze_smelting_works(test_username):
    seed_player(
        test_username,
        position=SMELTER_SPAWN,
        inventory=[
            {"key": "tinore", "count": 1},
            {"key": "copperore", "count": 1},
        ],
        quests=[FINISHED_MINERSQUEST],
        skills=[
            {"type": 5, "experience": MINING_LVL30_XP},
            {"type": SMITHING, "experience": 100_000},
        ],
        player_info_overrides={"rank": 2},
    )
    try:
        async with mcp_session(username=test_username) as session:
            travel = await _navigate_to_smelter(session)
            bronze_data = await craft_recipe(session, skill="smelting", recipe_key="bronzebar", count=1)
            assert int((bronze_data.get("inventory_delta") or {}).get("bronzebar", 0)) >= 1, (
                f"bronzebar smelting did not add output. craft={bronze_data}. travel={travel}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_minersquest2_stage_2_to_3_bronze_turnin(test_username):
    seed_player(
        test_username,
        position=MINER_ADJACENT,
        inventory=[{"key": "bronzebar", "count": 5}],
        quests=[
            FINISHED_MINERSQUEST,
            {"key": "minersquest2", "stage": 2, "subStage": 0, "completedSubStages": []},
        ],
        skills=[
            {"type": 5, "experience": MINING_LVL30_XP},
            {"type": SMITHING, "experience": 100_000},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Miner"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "minersquest2", stage_count=3)
    finally:
        cleanup_player(test_username)
