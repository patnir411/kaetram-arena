"""Rick's Roll stage-by-stage coverage for the currently testable transitions."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    count_saved_inventory,
    wait_for_position,
)

RICK_DOOR = (260, 229)
LENA_SIDE_DOOR = (425, 909)


@pytest.mark.quest_chain
async def test_ricksroll_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("rick"),
        inventory=[],
        quests=[{"key": "ricksroll", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Rick"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "ricksroll", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_ricksroll_stage_1_to_2_shrimp_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("rick"),
        inventory=[{"key": "cookedshrimp", "count": 5}],
        quests=[{"key": "ricksroll", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Rick"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "ricksroll", stage=2, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "seaweedroll") >= 1, (
            f"ricksroll shrimp turn-in should award a seaweedroll, got {count_saved_inventory(test_username, 'seaweedroll')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_ricksroll_stage_2_to_3_door_traversal(test_username):
    seed_player(
        test_username,
        position=(RICK_DOOR[0], RICK_DOOR[1] + 1),
        inventory=[{"key": "seaweedroll", "count": 1}],
        quests=[{"key": "ricksroll", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("navigate", {"x": RICK_DOOR[0], "y": RICK_DOOR[1]})
            assert not r.is_error, r.text[:300]
            await wait_for_position(
                session,
                x=LENA_SIDE_DOOR[0],
                y=LENA_SIDE_DOOR[1],
                max_distance=4,
                polls=15,
                delay_s=1.0,
            )
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "ricksroll", stage=3, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_ricksroll_stage_3_to_4_lena_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("rickgf"),
        inventory=[{"key": "seaweedroll", "count": 1}],
        quests=[{"key": "ricksroll", "stage": 3, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Lena"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "ricksroll", stage_count=4)
        assert count_saved_inventory(test_username, "gold") >= 1_987, (
            f"ricksroll completion should award 1987 gold, got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
