"""Desert Quest stage-by-stage quest coverage."""
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
)


@pytest.mark.quest_chain
async def test_desertquest_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("lavanpc"),
        inventory=[],
        quests=[{"key": "desertquest", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Dying Soldier"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "desertquest", stage=1, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "cd") >= 1, (
            f"desertquest accept should award 1 cd, inventory={count_saved_inventory(test_username, 'cd')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_desertquest_stage_1_to_2_deliver_cd_to_wife(test_username):
    seed_player(
        test_username,
        position=adjacent_to("villagegirl"),
        inventory=[{"key": "cd", "count": 1}],
        quests=[{"key": "desertquest", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Wife"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
            r = await session.call_tool("interact_npc", {"npc_name": "Wife"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "desertquest", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_desertquest_stage_2_to_3_report_back(test_username):
    seed_player(
        test_username,
        position=adjacent_to("lavanpc"),
        inventory=[],
        quests=[{"key": "desertquest", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Dying Soldier"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "desertquest", stage_count=3)
    finally:
        cleanup_player(test_username)
