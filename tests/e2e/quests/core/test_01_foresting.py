"""Foresting stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    gather_until_count,
)

LUMBERJACKING = 0
OAK_SEED_POS = (207, 118)
FORESTER_ADJACENT = adjacent_to("forestnpc")


@pytest.mark.quest_chain
async def test_foresting_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("forestnpc"),
        inventory=[],
        quests=[{"key": "foresting", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Forester"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "foresting", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_foresting_action_oak_gather_works(test_username):
    seed_player(
        test_username,
        position=OAK_SEED_POS,
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
        equipment=[{"type": 0, "key": "bronzeaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[{"type": LUMBERJACKING, "experience": 100_000}],
        quests=[{"key": "foresting", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            equip = await session.call_tool("equip_item", {"slot": 0})
            assert not equip.is_error, equip.text[:300]
            await asyncio.sleep(1.0)

            await gather_until_count(
                session,
                resource_name="oak",
                item_key="logs",
                target_count=1,
                attempts=3,
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_foresting_stage_1_to_2_first_log_turnin(test_username):
    seed_player(
        test_username,
        position=FORESTER_ADJACENT,
        inventory=[{"key": "logs", "count": 10}],
        quests=[{"key": "foresting", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Forester"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "foresting", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_foresting_stage_2_to_3_second_log_gather_works(test_username):
    seed_player(
        test_username,
        position=OAK_SEED_POS,
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
        equipment=[{"type": 0, "key": "bronzeaxe", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[{"type": LUMBERJACKING, "experience": 100_000}],
        quests=[{"key": "foresting", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            equip = await session.call_tool("equip_item", {"slot": 0})
            assert not equip.is_error, equip.text[:300]
            await asyncio.sleep(1.0)

            await gather_until_count(
                session,
                resource_name="oak",
                item_key="logs",
                target_count=1,
                attempts=3,
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_foresting_stage_2_to_3_second_log_turnin(test_username):
    seed_player(
        test_username,
        position=FORESTER_ADJACENT,
        inventory=[{"key": "logs", "count": 10}],
        quests=[{"key": "foresting", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Forester"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "foresting", stage_count=3)
    finally:
        cleanup_player(test_username)
