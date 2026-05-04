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
    wait_for_inventory_count,
    wait_for_position,
    wait_for_quest_state,
)
from tests.e2e.quests.reachability.conftest import (
    REACHABILITY_NO_PROGRESS_TIMEOUT_S,
    assert_pos_within,
    navigate_long,
    playthrough_seed_kwargs,
    reachability,
    slow,
)
from tests.e2e.quests.reachability.debug import get_current_test_debug

FISHING = 8
COOKING = 9
RICK_POS = NPCS["rick"]        # (1088, 833)
LENA_POS = NPCS["rickgf"]       # (455, 924)
RICK_DOOR = (260, 229)
# (260, 229) teleports to (425, 909) — but that's the doorway into a
# decoy puzzle room with 4 ladders. Three of them
# ((421, 903), (429, 903), (425, 905)) loop you back to (425, 909).
# Only (425, 901) advances → teleports to (453, 904) in Lena's house.
# Critically, the straight-north path from (425, 909) to (425, 901)
# passes through the decoy at (425, 905), so we must detour off the
# center column. (424, 902) is the safe waypoint adjacent to the real
# exit ladder.
PUZZLE_ENTRY      = (425, 909)
PUZZLE_SAFE_WP    = (424, 902)   # off-column waypoint that bypasses (425, 905)
PUZZLE_REAL_EXIT  = (425, 901)
# (425, 901) → (453, 904) lands you in Lena's *upper* room which has
# its own decoy puzzle: (449, 904), (453, 901), (457, 904) all loop
# back to (425, 909). The only real exit from this upper room is
# (453, 907), which teleports to (426, 927) in the lower room where
# Lena lives. From (426, 927) it's an unobstructed walk to (455, 924).
LENA_UPPER_ENTRY  = (453, 904)
LENA_UPPER_EXIT   = (453, 907)
# (453, 907) → (426, 927) lands you in the *left* half of Lena's lower
# floor, which is also walled off from the right half where Lena lives.
# (422, 920) loops back to (425, 909). The forward door is (431, 920),
# which → (455, 930) — a narrow corridor adjacent to Lena's room. From
# (455, 930) it's one tile north to (455, 929) inside her room.
LENA_LOWER_LANDING = (426, 927)
LENA_LOWER_FWD     = (431, 920)
LENA_CORRIDOR      = (455, 930)
# Kept for backwards reference / older callers.
LENA_SIDE_DOOR = PUZZLE_ENTRY

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
    seed_player(test_username, **playthrough_seed_kwargs("R1"))
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
    seed_player(
        test_username,
        **playthrough_seed_kwargs("R2", position=adjacent_to("rick")),
    )
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
        **playthrough_seed_kwargs(
            "R3",
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
    the cooking station infrastructure is accessible.

    Seeds the player at (702, 609), within `craft_item`'s 6-tile reach
    of a cooking station at (706, 605). The seed uses an explicit
    coordinate rather than an NPC reference because the test isolates
    the cooking-station infrastructure check from any quest-route
    state.
    """
    seed_player(
        test_username,
        **playthrough_seed_kwargs(
            "R4",
            position=(702, 609),
            inventory=[
                {"key": "rawshrimp", "count": 5},
            ],
            skills=[{"type": COOKING, "experience": 1_000}],
        ),
    )
    try:
        dbg = get_current_test_debug()
        async with mcp_session(username=test_username) as session:
            # Pre-craft observe — confirm seed placed the player at
            # (702, 609); distance to cooking station (706, 605) must
            # be ≤ 6 for craft_item to reach.
            obs0 = await session.call_tool("observe", {})
            if dbg is not None:
                dbg.action(
                    "observe", args={"_probe": "r4_pre_craft"},
                    ok=not obs0.is_error,
                    result_preview=(obs0.text or "")[:600] if obs0.text else None,
                    error=(obs0.text[:300] if obs0.is_error and obs0.text else None),
                )
                dbg.raw_observe("r4_pre_craft", obs0.text or "")
            r = await session.call_tool(
                "craft_item",
                {"skill": "cooking", "recipe_key": "cookedshrimp", "count": 5},
            )
            if dbg is not None:
                dbg.action(
                    "craft_item",
                    args={"skill": "cooking", "recipe_key": "cookedshrimp", "count": 5},
                    ok=not r.is_error,
                    result_preview=(r.text or "")[:600] if r.text else None,
                    error=(r.text[:600] if r.is_error and r.text else None),
                )
                dbg.event("r4_craft_response_full", text=(r.text or "")[:2000])
            assert not r.is_error, r.text[:300]
            data = r.json() or {}
            assert "error" not in data, data
            # Verify the cook actually persisted by polling the LIVE
            # inventory (server state). In live-suite mode the warm pool
            # keeps the session open, so Kaetram's `SAVE_INTERVAL=60s`
            # tick hasn't fired yet — Mongo `count_saved_inventory` would
            # return 0 even though the craft succeeded server-side. The
            # live observe is authoritative; mirror what r5/a5/a8 do.
            await wait_for_inventory_count(
                session, "cookedshrimp", expected_at_least=1,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_r5_shrimp_turnin_receives_seaweedroll(test_username):
    """R5: Turn in 5× cookedshrimp to Rick; receive seaweedroll; stage 1→2."""
    seed_player(
        test_username,
        **playthrough_seed_kwargs(
            "R5",
            position=adjacent_to("rick"),
            inventory=[{"key": "cookedshrimp", "count": 5}],
        ),
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
        **playthrough_seed_kwargs(
            "R6",
            position=(RICK_DOOR[0], RICK_DOOR[1] + 1),
            inventory=[{"key": "seaweedroll", "count": 1}],
        ),
    )
    try:
        dbg = get_current_test_debug()
        async with mcp_session(username=test_username) as session:
            # Pre-teleport observe — confirm seed put us at the door.
            obs_pre = await session.call_tool("observe", {})
            if dbg is not None:
                dbg.raw_observe("r6_pre_door", obs_pre.text or "")
            # First door: (260, 229) → puzzle room entry at (425, 909).
            await traverse_door(
                session,
                door_x=RICK_DOOR[0], door_y=RICK_DOOR[1],
                exit_x=PUZZLE_ENTRY[0], exit_y=PUZZLE_ENTRY[1],
                max_distance=5, polls=20, delay_s=1.0,
            )
            obs_after_door = await session.call_tool("observe", {})
            if dbg is not None:
                dbg.raw_observe("r6_post_door1", obs_after_door.text or "")
            # Detour off the center column to bypass the (425, 905)
            # decoy ladder, then approach the real exit at (425, 901).
            wp_step = await session.call_tool(
                "navigate",
                {"x": PUZZLE_SAFE_WP[0], "y": PUZZLE_SAFE_WP[1]},
            )
            if dbg is not None:
                dbg.action(
                    "navigate",
                    args={"x": PUZZLE_SAFE_WP[0], "y": PUZZLE_SAFE_WP[1], "_puzzle_wp": True},
                    ok=not wp_step.is_error,
                    result_preview=(wp_step.text or "")[:240] if wp_step.text else None,
                    error=wp_step.text[:240] if wp_step.is_error else None,
                )
            # Wait for the player to actually arrive at the waypoint
            # (the navigate call returns immediately on short_path).
            await wait_for_position(
                session,
                x=PUZZLE_SAFE_WP[0], y=PUZZLE_SAFE_WP[1],
                max_distance=1, polls=20, delay_s=0.5,
            )
            # Step onto the exit ladder → teleport to (453, 904) in
            # Lena's upper room.
            await traverse_door(
                session,
                door_x=PUZZLE_REAL_EXIT[0], door_y=PUZZLE_REAL_EXIT[1],
                exit_x=LENA_UPPER_ENTRY[0], exit_y=LENA_UPPER_ENTRY[1],
                max_distance=5, polls=20, delay_s=1.0,
            )
            if dbg is not None:
                obs_after_door2 = await session.call_tool("observe", {})
                dbg.raw_observe("r6_post_door2_upper", obs_after_door2.text or "")
            # Step onto the upper-room exit ladder → teleport to
            # (426, 927) in Lena's lower room. The decoys (449, 904),
            # (453, 901), (457, 904) all loop back to (425, 909); only
            # (453, 907) advances. From (453, 904) the path south on
            # column 453 doesn't cross any other door tile.
            await traverse_door(
                session,
                door_x=LENA_UPPER_EXIT[0], door_y=LENA_UPPER_EXIT[1],
                exit_x=LENA_LOWER_LANDING[0], exit_y=LENA_LOWER_LANDING[1],
                max_distance=5, polls=20, delay_s=1.0,
            )
            if dbg is not None:
                obs_after_door3 = await session.call_tool("observe", {})
                dbg.raw_observe("r6_post_door3_lower_left", obs_after_door3.text or "")
            # Step onto the forward ladder (431, 920) → (455, 930) in
            # the corridor adjacent to Lena's room. (422, 920) is the
            # decoy that loops back to (425, 909).
            await traverse_door(
                session,
                door_x=LENA_LOWER_FWD[0], door_y=LENA_LOWER_FWD[1],
                exit_x=LENA_CORRIDOR[0], exit_y=LENA_CORRIDOR[1],
                max_distance=5, polls=20, delay_s=1.0,
            )
            if dbg is not None:
                obs_after_door4 = await session.call_tool("observe", {})
                dbg.raw_observe("r6_post_door4_corridor", obs_after_door4.text or "")
            # Walk one tile north to enter Lena's room, then over to her.
            lx, ly = NPCS["rickgf"]
            await navigate_long(
                session, target_x=lx, target_y=ly,
                max_step=30, max_hops=4, arrive_tolerance=3,
            )
            # Pre-interact observe — record what's actually nearby right
            # before the Lena turn-in.
            obs_pre_lena = await session.call_tool("observe", {})
            if dbg is not None:
                dbg.raw_observe("r6_pre_lena_interact", obs_pre_lena.text or "")
            r = await session.call_tool("interact_npc", {"npc_name": "Lena"})
            if dbg is not None:
                dbg.action(
                    "interact_npc", args={"npc_name": "Lena"},
                    ok=not r.is_error,
                    result_preview=(r.text or "")[:600] if r.text else None,
                    error=(r.text[:600] if r.is_error and r.text else None),
                )
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
            # Live-inventory check — quest progression triggers an
            # explicit player.save() (quests.ts:95), so Mongo will
            # catch up, but live is authoritative and faster.
            await wait_for_inventory_count(
                session, "gold", expected_at_least=1987,
            )
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "ricksroll", stage_count=4)
    finally:
        cleanup_player(test_username)


@reachability
async def test_r7_lena_requires_seaweedroll_not_rawshrimp(test_username):
    """R7 (gap-fill, negative): Verify Lena's stage-2 turn-in requires
    `seaweedroll` specifically — handing her raw shrimp must NOT finish
    the quest. Catches a bench-fairness bug where an alternative
    inventory item silently satisfied the turn-in.
    """
    seed_player(
        test_username,
        **playthrough_seed_kwargs(
            "R7",
            position=adjacent_to("rickgf"),
            inventory=[{"key": "rawshrimp", "count": 5}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            for _ in range(2):
                r = await session.call_tool("interact_npc", {"npc_name": "Lena"})
                assert not r.is_error, r.text[:300]
                await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        # Quest must still be at stage 2 (not finished). The reward
        # `ricksroll` (1987 gold) should NOT have been granted.
        assert_quest_state(test_username, "ricksroll", stage=2, sub_stage=0, completed_sub_stages=[])
        assert count_saved_inventory(test_username, "gold") < 1987, (
            "Lena should not award 1987 gold without a seaweedroll; "
            f"got {count_saved_inventory(test_username, 'gold')}"
        )
    finally:
        cleanup_player(test_username)
