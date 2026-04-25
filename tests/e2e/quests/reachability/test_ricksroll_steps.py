"""Rick's Roll — per-step reachability checks.

Rick at (1088, 833) sits ~1500 Manhattan tiles from Mudwich (188, 157)
with no warp getting close and (per the audit) no reqQuest/reqAchievement
gates along the route. R1 is therefore the most expensive reachability
test in the suite — ~15-30 navigate_long hops at 50 tiles each.

Steps:
  R1: overland nav Mudwich → Rick (the walk-heavy test)
  R2: accept quest (seeded near Rick)
  R3: fish shrimp at nearest shrimp spot
  R4: cook shrimp via craft_item
  R5: shrimp → cookedshrimp turn-in receives seaweedroll
  R6: stage-2 quest door teleports (260,229) → (425,909)
  R7: deliver seaweedroll to Lena → 1987 gold
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
    gather_until_count,
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

FISHING = 8
COOKING = 9
RICK_POS = NPCS["rick"]        # (1088, 833)
LENA_POS = NPCS["rickgf"]       # (455, 924)
RICK_DOOR = (260, 229)
LENA_SIDE_DOOR = (425, 909)

# Nearest shrimp spot to Rick per audit (320-370, 320-370 range is closest
# documented; the audit reported (336, 328) as a candidate — still far from
# Rick but the closest in processed world).
# (336,328) is a shrimpspot but it sits in a 1-tile water pocket with no
# proper shore neighbor within distance 1 — only walkable approach is
# (336,326) which is itself a water tile (visible in headed mode: player
# spawns standing in water, not on a dock). Server's interact check at
# player.ts:892 (`entity.getDistance > 2`) likely fails or the click is
# eaten before it reaches the resource. Use (325,360) instead — it has a
# proper distance-1 shore tile at (324,360).
SHRIMP_SPOT_NEAR_RICK = (325, 360)
SHRIMP_SPOT_SHORE = (324, 360)  # walkable shore tile west of the spot


@reachability
@slow
async def test_r1_navigate_mudwich_to_rick(test_username, test_debug):
    """R1: Vanilla overland walk Mudwich (188,157) → Rick (1088,833).

    ~1,500 Manhattan tiles. Expect 20-30 navigate_long hops, ~8-15 min
    wall-clock. This test empirically confirms the whole Mudwich→Rick
    corridor is walkable — the audit found no reqQuest gates but did not
    analyze collision data.
    """
    seed_player(test_username, **vanilla_seed_kwargs())
    # Mudwich (188,157) and Rick (1088,833) are in disjoint walkable
    # regions per offline BFS over world.json. The route uses door 1025
    # at (379,388) which teleports to (1138,800), within the same region
    # as Rick. Pin chain along the corridor was extracted from a full-map
    # BFS path (444 tiles total to door, 100 tiles after).
    try:
        async with mcp_session(username=test_username) as session:
            for pin_x, pin_y in [
                (245, 170), (285, 190), (293, 242), (311, 254),
                (324, 301), (340, 345), (367, 348), (375, 370),
            ]:
                await navigate_long(
                    session, target_x=pin_x, target_y=pin_y,
                    max_step=25, max_hops=8, arrive_tolerance=4, debug=test_debug,
                )
            # Step on door 1025 (379,388) -> (1138,800).
            await traverse_door(
                session, door_x=379, door_y=388,
                exit_x=1138, exit_y=800, max_distance=5,
            )
            # Final approach to Rick (1088, 833).
            await navigate_long(
                session, target_x=RICK_POS[0], target_y=RICK_POS[1],
                max_step=25, max_hops=10, arrive_tolerance=6, debug=test_debug,
            )
            await assert_pos_within(
                session, target_x=RICK_POS[0], target_y=RICK_POS[1], tolerance=6
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r2_accept_ricksroll_quest(test_username):
    """R2: Accept Rick's Roll by talking to Rick (seeded adjacent)."""
    seed_player(test_username, **vanilla_seed_kwargs(position=adjacent_to("rick")))
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool(
                "interact_npc", {"npc_name": "Rick", "accept_quest_offer": True}
            )
            assert not r.is_error, r.text[:300]
        await wait_for_quest_state(
            test_username, "ricksroll", stage=1, sub_stage=0, completed_sub_stages=[]
        )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r3_fish_shrimp_at_nearest_spot(test_username):
    """R3: Fish one rawshrimp at the shrimp spot nearest Rick.

    Fishing Lv1 is the starter level — we grant a small seed just to
    stabilize the test (Kaetram's RS-style XP curve starts slow and the
    first few fishes can be low-yield).
    """
    # Seed on the shore tile west of the shrimp spot — distance 1, proper
    # dock topology (verified via offline BFS: 18/21 of the world's shrimp
    # spots have distance-1 walkable neighbors; the original (336,328)
    # was the only one without a real shore).
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=SHRIMP_SPOT_SHORE,
            skills=[{"type": FISHING, "experience": 1_000}],
            # Fishing requires an EQUIPPED fishing weapon (server-side
            # check at fishing.ts:50). Inventory-only doesn't count.
            equipment=[
                {"type": 4, "key": "fishingpole", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await gather_until_count(
                session,
                # Display name in fishing.json is "Shrimp Fishing Spot" — the
                # gather tool does case-insensitive substring match against
                # nearby_entities[].name, so "Shrimp Spot" doesn't match.
                resource_name="Shrimp Fishing Spot",
                item_key="rawshrimp",
                target_count=1,
                attempts=5,
                polls_after_gather=6,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r4_cook_shrimp_via_craft(test_username):
    """R4: Can the player cook 5× rawshrimp via `craft_item`? This proves
    the cooking station infrastructure is accessible from the quest region.

    Seeds adjacent to Babushka (iamverycoldnpc at 702,608) — the same
    pattern A8 uses successfully. The cooking station at (706,605) sits
    ~4 tiles away, well within `craft_item`'s 6-tile reach. Seeding
    adjacent to "doctor" landed the player at (698,551), 62 tiles from
    the station, and `craft_item` aborted with "Could not reach cooking
    station".
    """
    seed_player(
        test_username,
        position=adjacent_to("iamverycoldnpc"),
        inventory=[
            {"key": "rawshrimp", "count": 5},
        ],
        skills=[{"type": COOKING, "experience": 1_000}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool(
                "craft_item",
                {"skill": "cooking", "recipe_key": "cookedshrimp", "count": 5},
            )
            assert not r.is_error, r.text[:300]
            data = r.json() or {}
            assert "error" not in data, data
        # Confirm cookedshrimp arrived in inventory.
        assert count_saved_inventory(test_username, "cookedshrimp") >= 1, (
            "craft_item(cooking, cookedshrimp) should yield at least 1 "
            f"cookedshrimp; got {count_saved_inventory(test_username, 'cookedshrimp')}"
        )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r5_shrimp_turnin_receives_seaweedroll(test_username):
    """R5: Turn in 5× cookedshrimp to Rick; receive seaweedroll; stage 1→2."""
    seed_player(
        test_username,
        position=adjacent_to("rick"),
        inventory=[{"key": "cookedshrimp", "count": 5}],
        quests=[{"key": "ricksroll", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            for _ in range(2):
                r = await session.call_tool("interact_npc", {"npc_name": "Rick"})
                assert not r.is_error, r.text[:300]
                await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "ricksroll", stage=2, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "seaweedroll") >= 1, (
            "turn-in should award seaweedroll"
        )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r6_door_teleport_and_deliver_to_lena(test_username):
    """R6+R7 (merged): Step onto the stage-2 quest door at (260, 229),
    confirm teleport to (425, 909) near Lena, then deliver seaweedroll
    to Lena and verify quest finishes with 1987 gold.

    Single test answers two adjacent reachability questions: (a) the
    door teleport works at quest stage 2, (b) Lena interaction completes
    the quest. Splitting them only paid 2× the per-test setup overhead."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(RICK_DOOR[0], RICK_DOOR[1] + 1),
            inventory=[{"key": "seaweedroll", "count": 1}],
            quests=[{"key": "ricksroll", "stage": 2, "subStage": 0, "completedSubStages": []}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Door step → teleport to Lena's region.
            await traverse_door(
                session,
                door_x=RICK_DOOR[0], door_y=RICK_DOOR[1],
                exit_x=LENA_SIDE_DOOR[0], exit_y=LENA_SIDE_DOOR[1],
                max_distance=5, polls=20, delay_s=1.0,
            )
            # Walk to Lena and turn in.
            lx, ly = NPCS["rickgf"]
            await navigate_long(
                session, target_x=lx, target_y=ly,
                max_step=30, max_hops=8, arrive_tolerance=3,
            )
            r = await session.call_tool("interact_npc", {"npc_name": "Lena"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "ricksroll", stage_count=4)
        assert count_saved_inventory(test_username, "gold") >= 1987, (
            f"ricksroll completion awards 1987 gold; got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
