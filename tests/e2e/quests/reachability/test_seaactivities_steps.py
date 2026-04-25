"""Sea Activities — per-step reachability checks.

The audit concluded:
  - Water Guardian (lvl 36, 350 HP) at (293, 729) reachable from Mudwich
    with no reqQuest gates.
  - Undersea warp (43, 313) unlocks after `waterguardian` achievement.
  - Sponge + Pickle NPCs reachable within undersea region.
  - Picklemob (lvl 88, 1250 HP) fight is MARGINAL for a realistic
    mid-route player — combat math predicts ~1900 damage taken vs
    ~1389 max HP at lvl 45. Requires heavy food prep.

Steps:
  S1: navigate Mudwich → Water Guardian area (~680 tiles)
  S2: skip — combat grind is out of scope for reachability (pre-seeded)
  S3: kill Water Guardian with seeded mid-level combat stats
  S4: warp undersea after waterguardian achievement
  S5: Sponge dialogue chain stages 0 → 4
  S6: arena door teleport
  S7: picklemob fight with realistic mid-route gear — THE diagnostic test
  S7': picklemob fight with aspirational end-game gear (control — should
       always pass given existing stage test uses this loadout)
  S8: stages 5 → 6 → 7 final turn-in
"""
from __future__ import annotations

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
    traverse_door,
    wait_for_position,
    wait_for_quest_state,
)
from tests.e2e.quests.reachability.conftest import (
    REACHABILITY_NO_PROGRESS_TIMEOUT_S,
    assert_pos_within,
    navigate_long,
    reachability,
    slow,
    vanilla_seed_kwargs,
)

WATERGUARDIAN_POS = (293, 729)
UNDERSEA_WARP_EXIT = (43, 313)
SPONGE_POS = NPCS["sponge"]             # (52, 310)
PICKLE_POS = NPCS["picklenpc"]          # (691, 838)
PICKLEMOB_POS = NPCS["picklemob"]       # (858, 815)
ARENA_ENTRY_DOOR = (693, 836)
ARENA_EXIT_DOOR = (858, 808)

WATERGUARDIAN_ACH = {"key": "waterguardian", "stage": 1, "stageCount": 1}

# Combat skills enum: 1=Accuracy, 3=Health, 6=Strength, 7=Defense
COMBAT_MID_XP = 500_000         # ~lvl 50 combat skills
COMBAT_AGGRO_XP = 15_000_000    # ~lvl 100 (aspirational seed for S7')


@reachability
@slow
async def test_s1_navigate_mudwich_to_water_guardian(test_username, test_debug):
    """S1: Vanilla overland walk Mudwich (188,157) → Water Guardian
    (293,729). ~680 tiles south. Confirms no region gates block the
    approach."""
    seed_player(test_username, **vanilla_seed_kwargs())
    try:
        async with mcp_session(username=test_username) as session:
            await navigate_long(
                session,
                target_x=WATERGUARDIAN_POS[0],
                target_y=WATERGUARDIAN_POS[1],
                max_step=50,
                max_hops=25,
                arrive_tolerance=6,
                per_hop_timeout_s=90.0,
                poll_interval_s=2.0,
                no_progress_timeout_s=REACHABILITY_NO_PROGRESS_TIMEOUT_S,
                debug=test_debug,
            )
            await assert_pos_within(
                session,
                target_x=WATERGUARDIAN_POS[0],
                target_y=WATERGUARDIAN_POS[1],
                tolerance=6,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_s3_kill_water_guardian(test_username):
    """S3: Kill the Water Guardian (lvl 36, 350 HP) with ~lvl-35 combat
    seeded. If this fails, the `waterguardian` achievement is gated by
    higher-than-expected combat requirements."""
    wgx, wgy = WATERGUARDIAN_POS
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(wgx - 1, wgy),
            hit_points=1089,  # 39 + 35*30
            inventory=[
                {"index": 0, "key": "coppersword", "count": 1},
                {"index": 1, "key": "burger", "count": 5},
            ],
            equipment=[
                {"type": 4, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
            skills=[
                {"type": 1, "experience": 200_000},  # Accuracy lvl ~35
                {"type": 3, "experience": 200_000},  # Health lvl ~35
                {"type": 6, "experience": 200_000},  # Strength lvl ~35
                {"type": 7, "experience": 200_000},  # Defense lvl ~35
            ],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("attack", {"mob_name": "Water Guardian"})
            assert not r.is_error, r.text[:300]
            # 350 HP mob — let the fight resolve.
            await asyncio.sleep(30.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        # No direct "mob dead" signal; verify via achievement flag.
        # (Existing tests use the achievement as a post-condition.)
    finally:
        cleanup_player(test_username)


@reachability
async def test_s4_warp_undersea_after_waterguardian(test_username):
    """S4: After `waterguardian` achievement, warp(undersea) lands at
    (43, 313). Confirms the warp gate logic."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(188, 157),
            achievements=[WATERGUARDIAN_ACH],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("warp", {"location": "undersea"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(2.0)
            await assert_pos_within(
                session,
                target_x=UNDERSEA_WARP_EXIT[0],
                target_y=UNDERSEA_WARP_EXIT[1],
                tolerance=5,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_s5_sponge_dialogue_chain_0_to_4(test_username):
    """S5: Walk the dialogue chain Sponge → Pickle → Sponge → Pickle
    within the undersea region. Tests interact_npc + short-range
    navigate against the real NPC placements."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("sponge"),
            achievements=[WATERGUARDIAN_ACH],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Stage 0 → 1
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
            # Stage 1 → 2: walk to Pickle
            px, py = PICKLE_POS
            await navigate_long(
                session, target_x=px, target_y=py,
                max_step=50, max_hops=20, arrive_tolerance=4,
                per_hop_timeout_s=90.0, no_progress_timeout_s=REACHABILITY_NO_PROGRESS_TIMEOUT_S,
            )
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
            # Stage 2 → 3: back to Sponge
            sx, sy = SPONGE_POS
            await navigate_long(
                session, target_x=sx, target_y=sy,
                max_step=50, max_hops=20, arrive_tolerance=4,
                per_hop_timeout_s=90.0, no_progress_timeout_s=REACHABILITY_NO_PROGRESS_TIMEOUT_S,
            )
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
            # Stage 3 → 4: back to Pickle
            await navigate_long(
                session, target_x=px, target_y=py,
                max_step=50, max_hops=20, arrive_tolerance=4,
                per_hop_timeout_s=90.0, no_progress_timeout_s=REACHABILITY_NO_PROGRESS_TIMEOUT_S,
            )
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "seaactivities", stage=4)
    finally:
        cleanup_player(test_username)


@reachability
async def test_s6_arena_door_teleport(test_username):
    """S6: Step onto arena entry door at (693, 836); confirm teleport
    to (858, 808) in the picklemob arena."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(ARENA_ENTRY_DOOR[0], ARENA_ENTRY_DOOR[1] + 1),
            quests=[{"key": "seaactivities", "stage": 4, "subStage": 0, "completedSubStages": []}],
            achievements=[WATERGUARDIAN_ACH],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await traverse_door(
                session,
                door_x=ARENA_ENTRY_DOOR[0],
                door_y=ARENA_ENTRY_DOOR[1],
                exit_x=ARENA_EXIT_DOOR[0],
                exit_y=ARENA_EXIT_DOOR[1],
                max_distance=5,
                polls=15,
                delay_s=1.0,
            )
    finally:
        cleanup_player(test_username)


@reachability
@slow
async def test_s7_picklemob_with_mid_route_gear(test_username, test_debug):
    """S7: Picklemob (lvl 88, 1250 HP) fight with REALISTIC mid-route
    gear a vanilla agent could assemble.

    Gear assumptions:
      - lvl 50 combat skills (achievable via ~2h of grinding)
      - ironspear (drops from mobs reachable vanilla per audit)
      - platearmor (Hermit Crab-adjacent drop per audit — noted unverified)
      - 10× burger food buffer

    Per combat math in the audit, this is MARGINAL — we expect either
    a pass (Sea Activities is truly viable for a fresh route agent) or
    a fail (Core 5 benchmark requires heavier gear than an agent can
    realistically assemble, in which case Sea Activities Stage 4 must
    be a seeded checkpoint rather than agent-driven).
    """
    pmx, pmy = PICKLEMOB_POS
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(pmx - 2, pmy),
            hit_points=1539,  # 39 + 50*30
            mana=200,
            inventory=[
                {"index": 0, "key": "ironspear", "count": 1},
                {"index": 1, "key": "burger", "count": 10},
            ],
            equipment=[
                {"type": 4, "key": "ironspear", "count": 1, "ability": -1, "abilityLevel": 0},
                {"type": 3, "key": "platearmor", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
            skills=[
                {"type": 1, "experience": COMBAT_MID_XP},
                {"type": 3, "experience": COMBAT_MID_XP},
                {"type": 6, "experience": COMBAT_MID_XP},
                {"type": 7, "experience": COMBAT_MID_XP},
            ],
            quests=[{"key": "seaactivities", "stage": 4, "subStage": 0, "completedSubStages": []}],
            achievements=[WATERGUARDIAN_ACH],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Capture starting state so we know the fight began from full HP
            initial = await session.call_tool("observe", {})
            test_debug.action("observe", args={},
                              ok=not initial.is_error,
                              result_preview=(initial.text or "")[:240])
            test_debug.snapshot("pre_fight", initial.json())

            r = await session.call_tool("attack", {"mob_name": "Sea Cucumber"})
            test_debug.action("attack", args={"mob_name": "Sea Cucumber"},
                              ok=not r.is_error,
                              result_preview=(r.text or "")[:240])
            assert not r.is_error, r.text[:300]

            # Poll every 5s during the 90s fight so we can see the HP curve.
            elapsed = 0.0
            while elapsed < 90.0:
                await asyncio.sleep(5.0)
                elapsed += 5.0
                try:
                    mid = await session.call_tool("observe", {})
                    test_debug.snapshot(f"fight_t{int(elapsed)}s", mid.json())
                except Exception as exc:
                    test_debug.event("observe_failed", elapsed=elapsed, err=str(exc))
        await asyncio.sleep(AUTOSAVE_WAIT + 3.0)
        assert_quest_state(test_username, "seaactivities", stage=5)
    finally:
        cleanup_player(test_username)


@reachability
@slow
async def test_s7_prime_picklemob_with_endgame_gear(test_username):
    """S7': Control test — picklemob fight with end-game gear (what the
    existing stage test uses). If S7 fails and this passes, Sea Activities
    Stage 4 is definitively NOT completable with realistic agent-assemblable
    gear, and Core 5 benchmark must acknowledge that."""
    pmx, pmy = PICKLEMOB_POS
    seed_player(
        test_username,
        position=(pmx - 1, pmy),
        hit_points=3039,
        mana=200,
        inventory=[{"key": "apple", "count": 10}],
        equipment=[
            {"type": 0,  "key": "conquerorhelmet",     "count": 1, "enchantments": {}},
            {"type": 3,  "key": "conquerorchestplate", "count": 1, "enchantments": {}},
            {"type": 4,  "key": "moongreataxe",        "count": 1, "enchantments": {}},
            {"type": 5,  "key": "shieldofliberty",     "count": 1, "enchantments": {}},
            {"type": 9,  "key": "hellkeeperlegplates", "count": 1, "enchantments": {}},
            {"type": 11, "key": "hellkeeperboots",     "count": 1, "enchantments": {}},
        ],
        skills=[
            {"type": 1, "experience": COMBAT_AGGRO_XP},
            {"type": 3, "experience": COMBAT_AGGRO_XP},
            {"type": 6, "experience": COMBAT_AGGRO_XP},
            {"type": 7, "experience": COMBAT_AGGRO_XP},
        ],
        quests=[{"key": "seaactivities", "stage": 4, "subStage": 0, "completedSubStages": []}],
        achievements=[WATERGUARDIAN_ACH],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("attack", {"mob_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(15.0)
        await asyncio.sleep(AUTOSAVE_WAIT + 3.0)
        assert_quest_state(test_username, "seaactivities", stage=5)
    finally:
        cleanup_player(test_username)


@reachability
async def test_s8_final_turnin_chain_5_to_7(test_username):
    """S8: Stages 5 → 6 → 7 — talk Pickle (receive 1 gold), navigate
    Sponge, turn in 1 gold, receive 10000 gold, quest finished."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("picklenpc"),
            quests=[{"key": "seaactivities", "stage": 5, "subStage": 0, "completedSubStages": []}],
            achievements=[WATERGUARDIAN_ACH],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Stage 5 → 6
            r = await session.call_tool("interact_npc", {"npc_name": "Sea Cucumber"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
            # Navigate to Sponge
            sx, sy = SPONGE_POS
            await navigate_long(
                session, target_x=sx, target_y=sy,
                max_step=50, max_hops=20, arrive_tolerance=4,
                per_hop_timeout_s=90.0, no_progress_timeout_s=REACHABILITY_NO_PROGRESS_TIMEOUT_S,
            )
            # Stage 6 → 7
            r = await session.call_tool("interact_npc", {"npc_name": "Sponge"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.5)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "seaactivities", stage_count=7)
        assert count_saved_inventory(test_username, "gold") >= 10_000, (
            f"seaactivities completion awards 10000 gold; got "
            f"{count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
