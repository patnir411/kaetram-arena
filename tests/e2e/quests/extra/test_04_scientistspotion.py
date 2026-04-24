"""Scientist's Potion quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.quests.conftest import AUTOSAVE_WAIT, assert_quest_finished


@pytest.mark.quest_chain
async def test_scientistspotion_stage_0_to_1_finish(test_username):
    seed_player(
        test_username,
        position=adjacent_to("scientist"),
        inventory=[],
        quests=[{"key": "scientistspotion", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Scientist"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "scientistspotion", stage_count=1)
    finally:
        cleanup_player(test_username)
