"""Arts and Crafts — per-step reachability checks.

The audit confirmed:
  - Babushka at (702, 608) reachable via ungated door (483, 275) → (702, 613).
  - Beryl rocks at (645-665, 643-656) reachable via ungated door
    (395, 157) → (697, 647).
  - Bluelily bushes densest (278-436, 250-370).
  - Mushroom1 from goblin/ogre kills (~5 kills for one).
  - Knife in starter kit → Fletching unlocks.

Steps:
  A1: navigate Mudwich → Babushka door at (483, 275)
  A2: door teleport (483, 275) → (702, 613) near Babushka
  A3: accept quest
  A4a: mine beryl with bronzeaxe (starter kit) — tests whether axe is a
       valid mining tool
  A4b: mine beryl with bronzepickaxe (seeded) — control for A4a
  A5: craft string from bluelily
  A6: fletch stick + bowlmedium chain
  A7: farm mushroom1 from goblins (during quest, store unavailable)
  A8: cook stew + final turn-in → finished
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
    craft_recipe,
    gather_until_count,
    live_observe,
    traverse_door,
    wait_for_inventory_count,
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
from tests.e2e.quests.reachability.debug import get_current_test_debug

# Skill enum indices — must match Modules.Skills in
# Kaetram-Open/packages/common/network/modules.ts. Wrong indices silently
# grant XP to the wrong skill, and recipes whose level gate exceeds 1
# fail server-side with an empty inventory_delta.
MINING = 5
CRAFTING = 11
FLETCHING = 13
FORAGING = 15
COOKING = 9

BABUSHKA_POS = NPCS["iamverycoldnpc"]   # (702, 608)
BABUSHKA_DOOR = (483, 275)
BABUSHKA_DOOR_APPROACH = (483, 276)
BABUSHKA_DOOR_EXIT = (702, 613)
BERYL_CLUSTER = (645, 643)              # rock entity tile (per world.json)
BERYL_GATHER_TILE = (644, 643)          # walkable west-side adjacent (per world.json data+collisions)
BERYL_DOOR = (395, 157)
BERYL_DOOR_APPROACH = (395, 158)
BERYL_DOOR_EXIT = (697, 647)
BLUELILY_SPOT = (278, 250)
GOBLIN_SPAWN = (190, 204)               # open goblin tile near Mudwich
# Mudwich and the Babushka exterior are in disjoint walkable regions; the only
# in-game route uses the Aynor warp + an unmarked door at (406,292) -> (433,270).
# Verified via offline BFS over world.json: warp Aynor lands in region 455,
# door 463 at (406,292) is region 455, dest (433,270) is region 326 which
# contains the door approach (483,276). The Aynor warp is gated by the
# ancientlands quest, so the seed must mark that quest finished.
A1_AYNOR_DOOR_ENTRY = (406, 292)         # door 463 src (region 455 = Aynor)
A1_AYNOR_DOOR_APPROACH = (406, 293)      # walkable tile one step south of door
A1_AYNOR_DOOR_EXIT = (433, 270)          # door 463 dst (region 326 = Babushka exterior)
ANCIENTLANDS_FINISHED_QUEST = {
    "key": "ancientlands",
    "stage": 2,                          # past last stage idx (1) => isFinished
    "subStage": 0,
    "completedSubStages": [],
}


@reachability
@slow
async def test_a1_navigate_mudwich_to_babushka_door(test_username):
    """A1: Travel from Mudwich (188,157) to the Babushka door approach
    (483,276) using only in-game tools — warp + walking.

    Mudwich and the Babushka exterior are not overland-connected; the
    canonical route uses the Aynor warp landing (411,288) and the unnamed
    door at (406,292), which teleports to (433,270) inside the Babushka
    exterior region. From there it's a short overland walk to (483,276).

    The Aynor warp is gated behind the `ancientlands` quest, so the seed
    pre-finishes it — A1 is testing reachability post-quest, not the
    quest itself.
    """
    seed_player(
        test_username,
        **vanilla_seed_kwargs(quests=[ANCIENTLANDS_FINISHED_QUEST]),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Step 1: warp to Aynor (lands at 411,288).
            warp = await session.call_tool("warp", {"location": "aynor"})
            assert not warp.is_error, warp.text[:300]
            await wait_for_position(session, x=411, y=288, max_distance=4, polls=15, delay_s=1.0)

            # Step 2: short overland walk to the door approach tile (south
            # of the door so we don't trip the teleport during navigate).
            await navigate_long(
                session,
                target_x=A1_AYNOR_DOOR_APPROACH[0],
                target_y=A1_AYNOR_DOOR_APPROACH[1],
                max_step=20,
                max_hops=6,
                arrive_tolerance=3,
            )

            # Step 3: explicit door step + teleport verification to (433,270).
            await traverse_door(
                session,
                door_x=A1_AYNOR_DOOR_ENTRY[0],
                door_y=A1_AYNOR_DOOR_ENTRY[1],
                exit_x=A1_AYNOR_DOOR_EXIT[0],
                exit_y=A1_AYNOR_DOOR_EXIT[1],
                max_distance=5,
                polls=15,
                delay_s=1.0,
            )

            # Step 4: overland from door exit to the Babushka door approach.
            await navigate_long(
                session,
                target_x=BABUSHKA_DOOR_APPROACH[0],
                target_y=BABUSHKA_DOOR_APPROACH[1],
                max_step=30,
                max_hops=8,
                arrive_tolerance=3,
            )
            await assert_pos_within(
                session,
                target_x=BABUSHKA_DOOR_APPROACH[0],
                target_y=BABUSHKA_DOOR_APPROACH[1],
                tolerance=4,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a2_door_teleport_to_babushka_room(test_username):
    """A2: Step onto door at (483, 275); confirm teleport to (702, 613)
    near Babushka."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(position=(BABUSHKA_DOOR[0], BABUSHKA_DOOR[1] + 1)),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await traverse_door(
                session,
                door_x=BABUSHKA_DOOR[0],
                door_y=BABUSHKA_DOOR[1],
                exit_x=BABUSHKA_DOOR_EXIT[0],
                exit_y=BABUSHKA_DOOR_EXIT[1],
                max_distance=5,
                polls=15,
                delay_s=1.0,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a3_accept_artsandcrafts_quest(test_username):
    """A3: Accept Arts and Crafts by talking to Babushka."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(position=adjacent_to("iamverycoldnpc")),
    )
    try:
        async with mcp_session(username=test_username) as session:
            r = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not r.is_error, r.text[:300]
        await wait_for_quest_state(
            test_username, "artsandcrafts", stage=1, sub_stage=0, completed_sub_stages=[]
        )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a4a_mine_beryl_with_bronzeaxe(test_username):
    """A4a: Confirm bronzeaxe (starter kit) CANNOT mine beryl. The complement
    of A4b — together they document that the vanilla player must smith or
    buy a pickaxe before the Arts & Crafts quest is completable.

    Five gather attempts at the rock with the axe must yield zero beryl."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=BERYL_GATHER_TILE,
            inventory=[
                {"index": 0, "key": "bronzeaxe", "count": 1},
            ],
            equipment=[
                {"type": 4, "key": "bronzeaxe", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
            skills=[{"type": MINING, "experience": 1_000}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            for _ in range(5):
                await session.call_tool("gather", {"resource_name": "Beryl Rock"})
            obs = await live_observe(session)
            beryl_count = sum(
                int(item.get("count", 0))
                for item in (obs.get("inventory") or [])
                if item.get("key") == "beryl"
            )
            assert beryl_count == 0, (
                f"bronzeaxe unexpectedly mined beryl ({beryl_count} obtained) — "
                "Kaetram may have changed the tool gate; update A4a accordingly."
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a4b_mine_beryl_with_bronzepickaxe(test_username):
    """A4b: Control — confirm bronzepickaxe DOES mine beryl. Taken together
    with A4a this answers whether any axe works or only pickaxes."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=BERYL_GATHER_TILE,
            inventory=[
                {"index": 0, "key": "bronzepickaxe", "count": 1},
            ],
            equipment=[
                {"type": 4, "key": "bronzepickaxe", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
            skills=[{"type": MINING, "experience": 1_000}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await gather_until_count(
                session,
                resource_name="Beryl Rock",
                item_key="beryl",
                target_count=1,
                attempts=5,
                polls_after_gather=6,
                delay_after_gather_s=0.5,
            )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a5_craft_string_from_bluelily(test_username):
    """A5: With quest accepted (Crafting unlocked) and bluelily in inventory,
    can the player craft string?"""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("iamverycoldnpc"),
            inventory=[{"key": "bluelily", "count": 1}],
            skills=[{"type": CRAFTING, "experience": 1_000}],
            quests=[{"key": "artsandcrafts", "stage": 1, "subStage": 0, "completedSubStages": []}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await craft_recipe(session, skill="crafting", recipe_key="string", count=1)
            await wait_for_inventory_count(session, "string", expected_at_least=1)
    finally:
        cleanup_player(test_username)


@reachability
async def test_a6_fletch_chain_logs_to_bowlmedium(test_username):
    """A6: Fletch 4 sticks into 1 bowlmedium. Requires knife for Fletching
    unlock.

    Note: chained fletching crafts in the same MCP session (logs → sticks →
    bowlmedium) hit a Kaetram server-side issue where the second
    Crafting.Craft packet on the same already-open interface lands as a
    no-op (selected_name updates client-side but inventory does not). The
    test seeds the player with the intermediate (4 sticks) so this remains
    a reachability check on the bowlmedium recipe, not a regression on
    Kaetram's chained-craft behaviour.
    """
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("iamverycoldnpc"),
            inventory=[
                {"index": 0, "key": "knife", "count": 1},
                {"key": "stick", "count": 4},
            ],
            skills=[{"type": FLETCHING, "experience": 1_000}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # Sanity check the seeded inventory before crafting — the server
            # silently coerces some stackable seeds, so verify 4 sticks landed.
            obs = await live_observe(session)
            stick_count = sum(
                int(item.get("count", 0))
                for item in (obs.get("inventory") or [])
                if item.get("key") == "stick"
            )
            assert stick_count >= 4, (
                f"seed only produced {stick_count}x stick (need 4); "
                f"inventory={obs.get('inventory')}"
            )
            await craft_recipe(session, skill="fletching", recipe_key="bowlmedium", count=1)
            await wait_for_inventory_count(session, "bowlmedium", expected_at_least=1)
    finally:
        cleanup_player(test_username)


@reachability
@slow
@pytest.mark.xfail(
    reason=(
        "Goblin loot math (verified from Kaetram-Open data): per kill the "
        "'mushrooms' droptable is rolled with chance 6000/100000 = 6%, then "
        "1 of 8 mushrooms is selected (12.5%) — net ~0.75% chance of "
        "mushroom1 specifically. The audit's '~5 kills' estimate is wrong; "
        "reliable farming would need ~130+ kills, which is not a useful "
        "reachability check. Combat itself works (verified: 2 goblin kills "
        "in ~75s) — A7 stays as xfail to flag if Kaetram rebalances drops."
    ),
    strict=False,
)
async def test_a7_farm_mushroom1_from_goblins_during_quest(test_username):
    """A7: With Babushka's store unavailable (quest active), farm mushroom1
    from goblin kills. Audit says ~5 kills expected."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(GOBLIN_SPAWN[0], GOBLIN_SPAWN[1] + 1),
            hit_points=200,
            inventory=[
                {"index": 0, "key": "coppersword", "count": 1},
            ],
            equipment=[
                {"type": 4, "key": "coppersword", "count": 1, "ability": -1, "abilityLevel": 0},
            ],
            skills=[
                {"type": 1, "experience": 10_000},   # Accuracy lvl ~20
                {"type": 3, "experience": 10_000},   # Health lvl ~20
                {"type": 6, "experience": 10_000},   # Strength lvl ~20
                {"type": 7, "experience": 10_000},   # Defense lvl ~20
            ],
            quests=[{"key": "artsandcrafts", "stage": 3, "subStage": 0, "completedSubStages": []}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            debug = get_current_test_debug()
            # Each `attack` call only initiates one swing (~5-20 dmg). Goblins
            # have 90 HP, so a kill takes 5-15 swings. Re-issue `attack` in a
            # tight loop until the mob dies, then loot, then move on. Budget
            # ~10 kills total to give the drop RNG a fair shot.
            for kill in range(10):
                killed = False
                for swing in range(20):
                    r = await session.call_tool("attack", {"mob_name": "Goblin"})
                    if debug is not None:
                        debug.action(
                            tool="attack",
                            args={"mob_name": "Goblin", "_kill": kill + 1, "_swing": swing + 1},
                            ok=not r.is_error,
                            result_preview=(r.text or "")[:240] if r.text else None,
                            error=r.text[:240] if r.is_error else None,
                        )
                    text = r.text or ""
                    if '"killed":true' in text or '"killed": true' in text:
                        killed = True
                        break
                    if '"error"' in text and "No alive mob" in text:
                        # Either the mob just died or we're out of range.
                        killed = True
                        break
                    await asyncio.sleep(0.5)
                loot = await session.call_tool("loot", {})
                if debug is not None:
                    debug.action(
                        tool="loot",
                        args={"_kill": kill + 1, "_killed": killed},
                        ok=not loot.is_error,
                        result_preview=(loot.text or "")[:240] if loot.text else None,
                        error=loot.text[:240] if loot.is_error else None,
                    )
                await asyncio.sleep(0.5)
                obs = await live_observe(session)
                if debug is not None:
                    debug.event(
                        "a7_progress",
                        kill=kill + 1,
                        killed=killed,
                        pos=obs.get("pos"),
                        inventory=obs.get("inventory"),
                    )
                current = count_saved_inventory(test_username, "mushroom1")
                if current >= 1:
                    break
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert count_saved_inventory(test_username, "mushroom1") >= 1, (
            "expected ≥1 mushroom1 from ~10 goblin kills during quest"
        )
    finally:
        cleanup_player(test_username)


@reachability
async def test_a8_cook_stew_and_final_turnin(test_username):
    """A8: With ingredients in hand and stage=3, cook stew and deliver to
    Babushka; quest finishes."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("iamverycoldnpc"),
            inventory=[
                {"key": "bowlmedium", "count": 1},
                {"key": "mushroom1", "count": 1},
                {"key": "tomato", "count": 1},
            ],
            skills=[{"type": COOKING, "experience": 100_000}],
            quests=[{"key": "artsandcrafts", "stage": 3, "subStage": 0, "completedSubStages": []}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            await craft_recipe(session, skill="cooking", recipe_key="stew", count=1)
            await wait_for_inventory_count(session, "stew", expected_at_least=1)
            r = await session.call_tool("interact_npc", {"npc_name": "Babushka"})
            assert not r.is_error, r.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "artsandcrafts", stage_count=4)
    finally:
        cleanup_player(test_username)
