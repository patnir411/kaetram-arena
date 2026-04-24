"""Royal Drama stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.kaetram_world import NPCS, adjacent_to
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    count_saved_inventory,
    wait_for_position,
)

SEWER_ENTRY_DOOR = (1082, 714)
SEWER_EXIT_DOOR = (1090, 707)


@pytest.mark.quest_chain
async def test_royaldrama_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("royalguard2"),
        inventory=[],
        quests=[{"key": "royaldrama", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Royal Guard"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "royaldrama", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royaldrama_stage_1_to_2_rat_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("ratnpc"),
        inventory=[],
        quests=[{"key": "royaldrama", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Rat"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "royaldrama", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royaldrama_stage_2_sewer_door_traversal_works(test_username):
    seed_player(
        test_username,
        position=(SEWER_ENTRY_DOOR[0], SEWER_ENTRY_DOOR[1] + 1),
        inventory=[],
        quests=[{"key": "royaldrama", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("navigate", {"x": SEWER_ENTRY_DOOR[0], "y": SEWER_ENTRY_DOOR[1]})
            assert not r.is_error, r.text[:300]
            await wait_for_position(
                session,
                x=SEWER_EXIT_DOOR[0],
                y=SEWER_EXIT_DOOR[1],
                max_distance=3,
                polls=15,
                delay_s=1.0,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royaldrama_stage_2_to_3_king_talk(test_username):
    king2_x, king2_y = NPCS["king2"]
    seed_player(
        test_username,
        position=(king2_x, king2_y + 1),
        inventory=[],
        quests=[{"key": "royaldrama", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "King"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "royaldrama", stage_count=3)
        assert count_saved_inventory(test_username, "gold") >= 10_000, (
            f"royaldrama completion should award 10000 gold, got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
