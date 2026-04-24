"""Ancient Lands stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import AUTOSAVE_WAIT, assert_quest_finished, assert_quest_state


@pytest.mark.quest_chain
async def test_ancientlands_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("ancientmanumentnpc", dy=-1),
        inventory=[],
        quests=[{"key": "ancientlands", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Ancient Monument"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "ancientlands", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_ancientlands_stage_1_to_2_icesword_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("ancientmanumentnpc", dy=-1),
        inventory=[{"key": "icesword", "count": 1}],
        quests=[{"key": "ancientlands", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Ancient Monument"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "ancientlands", stage_count=2)
    finally:
        cleanup_player(test_username)
