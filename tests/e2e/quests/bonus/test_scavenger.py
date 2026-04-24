"""Scavenger stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    craft_recipe,
    gather_until_count,
)

SCAVENGER_ITEMS = [
    {"key": "tomato", "count": 2},
    {"key": "strawberry", "count": 2},
    {"key": "string", "count": 1},
]
FORAGING = 15
CRAFTING = 11
TOMATO_POS = (220, 107)


@pytest.mark.quest_chain
async def test_scavenger_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("villagegirl2"),
        inventory=[],
        quests=[{"key": "scavenger", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Village Girl"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "scavenger", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_scavenger_stage_1_to_2_first_old_lady_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("oldlady"),
        inventory=[],
        quests=[{"key": "scavenger", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Old Lady"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "scavenger", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_scavenger_action_tomato_gather_works(test_username):
    seed_player(
        test_username,
        position=(TOMATO_POS[0], TOMATO_POS[1] + 1),
        inventory=[],
        skills=[{"type": FORAGING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            await gather_until_count(
                session,
                resource_name="Tomato",
                item_key="tomato",
                target_count=1,
                attempts=3,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_scavenger_action_string_craft_works(test_username):
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[{"key": "bluelily", "count": 1}],
        quests=[{"key": "artsandcrafts", "stage": 1, "subStage": 0, "completedSubStages": []}],
        skills=[{"type": CRAFTING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            data = await craft_recipe(session, skill="crafting", recipe_key="string", count=1)
            delta = data.get("inventory_delta") or {}
            assert int(delta.get("string", 0)) >= 1, f"expected string craft delta, got: {data}"
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_scavenger_stage_2_to_3_second_old_lady_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("oldlady"),
        inventory=SCAVENGER_ITEMS,
        quests=[{"key": "scavenger", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Old Lady"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "scavenger", stage_count=3)
    finally:
        cleanup_player(test_username)
