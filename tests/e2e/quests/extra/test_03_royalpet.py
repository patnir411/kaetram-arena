"""Royal Pet stage-by-stage quest coverage with explicit substage assertions."""
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

FINISHED_ROYALDRAMA = {"key": "royaldrama", "stage": 3, "subStage": 0, "completedSubStages": []}


@pytest.mark.quest_chain
async def test_royalpet_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("king"),
        inventory=[],
        quests=[
            FINISHED_ROYALDRAMA,
            {"key": "royalpet", "stage": 0, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "King"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "royalpet", stage=1, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "book") >= 3, (
            f"royalpet accept should award 3 books, got {count_saved_inventory(test_username, 'book')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royalpet_stage_1_substage_0_to_1_shepherd_boy(test_username):
    seed_player(
        test_username,
        position=adjacent_to("shepherdboy"),
        inventory=[{"key": "book", "count": 3}],
        quests=[
            FINISHED_ROYALDRAMA,
            {"key": "royalpet", "stage": 1, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Shepherd Boy"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(
            test_username,
            "royalpet",
            stage=1,
            sub_stage=1,
            completed_sub_stages=["shepherdboy"],
        )
        assert count_saved_inventory(test_username, "book") == 2, (
            f"shepherd delivery should consume 1 book, got {count_saved_inventory(test_username, 'book')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royalpet_stage_1_substage_1_to_2_flaris(test_username):
    seed_player(
        test_username,
        position=adjacent_to("redbikinigirlnpc"),
        inventory=[{"key": "book", "count": 2}],
        quests=[
            FINISHED_ROYALDRAMA,
            {
                "key": "royalpet",
                "stage": 1,
                "subStage": 1,
                "completedSubStages": ["shepherdboy"],
            },
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Flaris"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(
            test_username,
            "royalpet",
            stage=1,
            sub_stage=2,
            completed_sub_stages=["shepherdboy", "redbikinigirlnpc"],
        )
        assert count_saved_inventory(test_username, "book") == 1, (
            f"flaris delivery should consume 1 book, got {count_saved_inventory(test_username, 'book')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royalpet_stage_1_substage_2_to_stage_2_fisherman(test_username):
    seed_player(
        test_username,
        position=adjacent_to("fisherman"),
        inventory=[{"key": "book", "count": 1}],
        quests=[
            FINISHED_ROYALDRAMA,
            {
                "key": "royalpet",
                "stage": 1,
                "subStage": 2,
                "completedSubStages": ["shepherdboy", "redbikinigirlnpc"],
            },
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Fisherman"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "royalpet", stage=2, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "book") == 0, (
            f"fisherman delivery should consume the last book, got {count_saved_inventory(test_username, 'book')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_royalpet_stage_2_to_3_return_to_king(test_username):
    seed_player(
        test_username,
        position=adjacent_to("king"),
        inventory=[],
        quests=[
            FINISHED_ROYALDRAMA,
            {"key": "royalpet", "stage": 2, "subStage": 0, "completedSubStages": []},
        ],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "King"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "royalpet", stage_count=3)
    finally:
        cleanup_player(test_username)
