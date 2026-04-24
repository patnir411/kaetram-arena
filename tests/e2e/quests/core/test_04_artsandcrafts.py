"""Arts and Crafts stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    craft_recipe,
)

CRAFTING = 11
COOKING = 9
FLETCHING = 13


@pytest.mark.quest_chain
async def test_artsandcrafts_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[],
        quests=[{"key": "artsandcrafts", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "artsandcrafts", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_artsandcrafts_stage_1_to_2_berylpendant_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[
            {"key": "beryl", "count": 1},
            {"key": "string", "count": 1},
        ],
        skills=[{"type": CRAFTING, "experience": 100_000}],
        quests=[{"key": "artsandcrafts", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            await craft_recipe(session, skill="crafting", recipe_key="berylpendant", count=1)
            result = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "artsandcrafts", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_artsandcrafts_stage_2_to_3_bowlsmall_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[
            {"index": 0, "key": "knife", "count": 1},
            {"key": "stick", "count": 2},
        ],
        skills=[{"type": FLETCHING, "experience": 100_000}],
        quests=[{"key": "artsandcrafts", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            await craft_recipe(session, skill="fletching", recipe_key="bowlsmall", count=1)
            result = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "artsandcrafts", stage=3, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_artsandcrafts_stage_3_to_4_stew_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[
            {"key": "bowlmedium", "count": 1},
            {"key": "mushroom1", "count": 1},
            {"key": "tomato", "count": 1},
        ],
        skills=[{"type": COOKING, "experience": 100_000}],
        quests=[{"key": "artsandcrafts", "stage": 3, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            await craft_recipe(session, skill="cooking", recipe_key="stew", count=1)
            result = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "artsandcrafts", stage_count=4)
    finally:
        cleanup_player(test_username)
