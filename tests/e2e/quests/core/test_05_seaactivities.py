"""Sea Activities stage-by-stage quest coverage."""
import asyncio

import pytest

from bench.seed import cleanup_player, seed_player
from tests.e2e.helpers.kaetram_world import NPCS, adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    count_saved_inventory,
    wait_for_position,
)

WATERGUARDIAN_ACH = {"key": "waterguardian", "stage": 1, "stageCount": 1}
ARENA_ENTRY_DOOR = (693, 836)
ARENA_EXIT_DOOR = (858, 808)


@pytest.mark.quest_chain
async def test_seaactivities_stage_0_to_1_sponge_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("sponge"),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 0, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_1_to_2_first_sea_cucumber_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("picklenpc"),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 1, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=2, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_2_to_3_second_sponge_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("sponge"),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 2, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=3, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_3_to_4_second_sea_cucumber_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("picklenpc"),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 3, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=4, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_4_arena_door_traversal_works(test_username):
    seed_player(
        test_username,
        position=(ARENA_ENTRY_DOOR[0], ARENA_ENTRY_DOOR[1] + 1),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 4, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("navigate", {"x": ARENA_ENTRY_DOOR[0], "y": ARENA_ENTRY_DOOR[1]})
            assert not r.is_error, r.text[:300]
            await wait_for_position(
                session,
                x=ARENA_EXIT_DOOR[0],
                y=ARENA_EXIT_DOOR[1],
                max_distance=4,
                polls=15,
                delay_s=1.0,
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_4_to_5_kill_picklemob(test_username):
    """Kill the picklemob boss (lvl 88, 1250 HP).

    We seed the player with high combat skills, strong equipment, and plenty of
    HP so the fight completes within the test timeout window.
    """
    picklemob_x, picklemob_y = NPCS["picklemob"]

    # --- Combat skills (type enum from Modules.Skills) ---
    # 0=Lumberjacking, 1=Accuracy, 2=Archery, 3=Health, 4=Magic,
    # 5=Mining, 6=Strength, 7=Defense
    # 15_000_000 XP ≈ level 100 in Kaetram's RS-style exp curve.
    COMBAT_XP = 15_000_000
    combat_skills = [
        {"type": 1, "experience": COMBAT_XP},   # Accuracy
        {"type": 3, "experience": COMBAT_XP},   # Health
        {"type": 6, "experience": COMBAT_XP},   # Strength
        {"type": 7, "experience": COMBAT_XP},   # Defense
    ]

    # --- Equipment (type enum from Modules.Equipment) ---
    # 0=Helmet, 3=Chestplate, 4=Weapon, 5=Shield, 9=Legplates, 11=Boots
    boss_equipment = [
        {"type": 0, "key": "conquerorhelmet",     "count": 1, "enchantments": {}},
        {"type": 3, "key": "conquerorchestplate",  "count": 1, "enchantments": {}},
        {"type": 4, "key": "moongreataxe",         "count": 1, "enchantments": {}},
        {"type": 5, "key": "shieldofliberty",      "count": 1, "enchantments": {}},
        {"type": 9, "key": "hellkeeperlegplates",   "count": 1, "enchantments": {}},
        {"type": 11, "key": "hellkeeperboots",      "count": 1, "enchantments": {}},
    ]

    # HP at health level ~100: 39 + 100*30 = 3039
    seed_player(
        test_username,
        position=(picklemob_x - 1, picklemob_y),
        hit_points=3039,
        mana=200,
        inventory=[{"key": "apple", "count": 10}],
        equipment=boss_equipment,
        skills=combat_skills,
        quests=[{"key": "seaactivities", "stage": 4, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("attack", {"mob_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            # Boss has 1250 HP — give the fight time to resolve
            await asyncio.sleep(15.0)
        await asyncio.sleep(AUTOSAVE_WAIT + 3.0)
        assert_quest_state(test_username, "seaactivities", stage=5, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_5_to_6_picklenpc_talk(test_username):
    seed_player(
        test_username,
        position=adjacent_to("picklenpc"),
        inventory=[],
        quests=[{"key": "seaactivities", "stage": 5, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=6, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "gold") >= 1, (
            f"seaactivities stage 5 talk should award 1 gold, got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_seaactivities_stage_6_to_7_final_sponge_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("sponge"),
        inventory=[{"key": "gold", "count": 1}],
        quests=[{"key": "seaactivities", "stage": 6, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "seaactivities", stage_count=7)
        assert count_saved_inventory(test_username, "gold") >= 10_000, (
            f"seaactivities completion should award 10000 gold, got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
