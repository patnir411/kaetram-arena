"""Miner's Quest — accept, mine one nisocore, then turn in the final batch."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_stage,
    gather_until_count,
)

MINER_ADJACENT = adjacent_to("miner")
MINING_SKILL = 5
NISOC_ROCK_ADJACENT = (656, 644)

# Seed tutorial complete so the runtime does not snap the player back into the
# house/tutorial flow during login.
FINISHED_TUTORIAL = {"key": "tutorial", "stage": 16, "subStage": 0, "completedSubStages": []}


@pytest.mark.quest_chain
async def test_minersquest_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=MINER_ADJACENT,
        inventory=[],
        quests=[
            FINISHED_TUTORIAL,
            {"key": "minersquest", "stage": 0, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Miner"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_stage(test_username, "minersquest", 1)
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_minersquest_action_nisoc_gather_works(test_username):
    seed_player(
        test_username,
        position=NISOC_ROCK_ADJACENT,
        inventory=[{"key": "bronzepickaxe", "count": 1}],
        equipment=[{"type": 0, "key": "bronzepickaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[{"type": MINING_SKILL, "experience": 1_000}],
        quests=[
            FINISHED_TUTORIAL,
            {"key": "minersquest", "stage": 1, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            equip_result = await session.call_tool("equip_item", {"slot": 0})
            assert not equip_result.is_error, equip_result.text[:300]
            await asyncio.sleep(1.0)
            await gather_until_count(
                session,
                resource_name="Nisoc Rock",
                item_key="nisocore",
                target_count=1,
                attempts=2,
                polls_after_gather=15,
                delay_after_gather_s=2.0,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_minersquest_stage_1_to_2_final_turnin(test_username):
    seed_player(
        test_username,
        position=MINER_ADJACENT,
        inventory=[{"key": "nisocore", "count": 15}],
        quests=[
            FINISHED_TUTORIAL,
            {"key": "minersquest", "stage": 1, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Miner"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "minersquest", stage_count=2)
    finally:
        cleanup_player(test_username)
