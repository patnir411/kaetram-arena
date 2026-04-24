"""Clam Chowder stage-by-stage quest coverage."""
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

FISHING = 8
COOKING = 9
CLAM_SPOT_POS = (322, 318)
PRETZEL_ADJACENT = adjacent_to("bluebikinigirlnpc")
DOCTOR_ADJACENT = adjacent_to("doctor")
OLD_LADY_ADJACENT = adjacent_to("oldlady2")


@pytest.mark.quest_chain
async def test_clamchowder_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("bluebikinigirlnpc"),
        inventory=[],
        quests=[{"key": "clamchowder", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Pretzel"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_action_clam_gather_works(test_username):
    seed_player(
        test_username,
        position=(CLAM_SPOT_POS[0], CLAM_SPOT_POS[1] + 1),
        inventory=[{"index": 0, "key": "fishingpole", "count": 1}],
        equipment=[{"type": 0, "key": "fishingpole", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[{"type": FISHING, "experience": 100_000}],
        quests=[{"key": "clamchowder", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            equip = await session.call_tool("equip_item", {"slot": 0})
            assert not equip.is_error, equip.text[:300]
            await asyncio.sleep(1.0)
            await gather_until_count(
                session,
                resource_name="clam",
                item_key="clamobject",
                target_count=1,
                attempts=3,
                polls_after_gather=4,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_1_to_2_clam_turnin(test_username):
    seed_player(
        test_username,
        position=PRETZEL_ADJACENT,
        inventory=[{"key": "clamobject", "count": 5}],
        quests=[{"key": "clamchowder", "stage": 1, "subStage": 0, "completedSubStages": []}],
        skills=[{"type": FISHING, "experience": 100_000}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Pretzel"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_2_to_3_doctor_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("doctor"),
        inventory=[],
        quests=[{"key": "clamchowder", "stage": 2, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Doctor"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=3, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_action_chowder_cooking_works(test_username):
    seed_player(
        test_username,
        position=DOCTOR_ADJACENT,
        inventory=[
            {"key": "clamobject", "count": 1},
            {"key": "potato", "count": 1},
            {"key": "bowlsmall", "count": 1},
        ],
        skills=[{"type": COOKING, "experience": 100_000}],
        quests=[],
    )
    try:
        async with mcp_session(username=test_username) as session:
            data = await craft_recipe(session, skill="cooking", recipe_key="clamchowder", count=1)
            assert int((data.get("inventory_delta") or {}).get("clamchowder", 0)) >= 1, data
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_3_to_4_first_chowder_turnin(test_username):
    seed_player(
        test_username,
        position=DOCTOR_ADJACENT,
        inventory=[{"key": "clamchowder", "count": 2}],
        skills=[{"type": COOKING, "experience": 100_000}],
        quests=[{"key": "clamchowder", "stage": 3, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Doctor"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=4, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_4_to_5_old_lady_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("oldlady2"),
        inventory=[],
        quests=[{"key": "clamchowder", "stage": 4, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Old Lady"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=5, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_action_second_chowder_cooking_works(test_username):
    seed_player(
        test_username,
        position=OLD_LADY_ADJACENT,
        inventory=[
            {"key": "clamobject", "count": 1},
            {"key": "potato", "count": 1},
            {"key": "bowlsmall", "count": 1},
        ],
        skills=[{"type": COOKING, "experience": 100_000}],
        quests=[],
    )
    try:
        async with mcp_session(username=test_username) as session:
            data = await craft_recipe(session, skill="cooking", recipe_key="clamchowder", count=1)
            assert int((data.get("inventory_delta") or {}).get("clamchowder", 0)) >= 1, data
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_5_to_6_second_chowder_turnin(test_username):
    seed_player(
        test_username,
        position=OLD_LADY_ADJACENT,
        inventory=[{"key": "clamchowder", "count": 2}],
        skills=[{"type": COOKING, "experience": 100_000}],
        quests=[{"key": "clamchowder", "stage": 5, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Old Lady"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "clamchowder", stage=6, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_clamchowder_stage_6_to_7_final_pretzel_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("bluebikinigirlnpc"),
        inventory=[],
        quests=[{"key": "clamchowder", "stage": 6, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Pretzel"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "clamchowder", stage_count=7)
    finally:
        cleanup_player(test_username)
