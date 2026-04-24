"""Herbalist's Desperation stage-by-stage quest coverage."""

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    assert_quest_finished,
    gather_until_count,
    wait_for_quest_state,
)

FORAGING = 15
BLUE_LILY_POS = (327, 288)
TOMATO_POS = (275, 248)
PAPRIKA_POS = (298, 300)


@pytest.mark.quest_chain
async def test_herbalistdesperation_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("herbalist"),
        inventory=[],
        quests=[{"key": "herbalistdesperation", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Herby Mc. Herb"})
            assert not result.is_error, result.text[:300]
        await wait_for_quest_state(
            test_username,
            "herbalistdesperation",
            stage=1,
            sub_stage=0,
            completed_sub_stages=[],
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_herbalistdesperation_action_bluelily_gather_works(test_username):
    seed_player(
        test_username,
        position=(BLUE_LILY_POS[0], BLUE_LILY_POS[1] + 1),
        inventory=[],
        skills=[{"type": FORAGING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            await gather_until_count(
                session,
                resource_name="Blue Lily",
                item_key="bluelily",
                target_count=1,
                attempts=3,
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_herbalistdesperation_stage_1_to_2_bluelily_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("herbalist"),
        inventory=[{"key": "bluelily", "count": 3}],
        quests=[{"key": "herbalistdesperation", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Herby Mc. Herb"})
            assert not result.is_error, result.text[:300]
        await wait_for_quest_state(
            test_username,
            "herbalistdesperation",
            stage=2,
            sub_stage=0,
            completed_sub_stages=[],
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_herbalistdesperation_action_tomato_and_paprika_gather_work(test_username):
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
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
        cleanup_player(test_username)
        seed_player(
            test_username,
            position=(PAPRIKA_POS[0], PAPRIKA_POS[1] + 1),
            inventory=[],
            skills=[{"type": FORAGING, "experience": 100_000}],
        )
        async with mcp_session(username=test_username) as session:
            await gather_until_count(
                session,
                resource_name="Paprika",
                item_key="paprika",
                target_count=1,
                attempts=3,
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_herbalistdesperation_stage_2_to_3_final_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("herbalist"),
        inventory=[
            {"key": "tomato", "count": 2},
            {"key": "paprika", "count": 2},
        ],
        quests=[{"key": "herbalistdesperation", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Herby Mc. Herb"})
            assert not result.is_error, result.text[:300]
        await wait_for_quest_state(
            test_username,
            "herbalistdesperation",
            stage=3,
        )
        assert_quest_finished(test_username, "herbalistdesperation", stage_count=3)
    finally:
        cleanup_player(test_username)
