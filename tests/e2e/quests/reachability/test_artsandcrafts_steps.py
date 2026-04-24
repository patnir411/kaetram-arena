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
    wait_for_inventory_count,
    wait_for_position,
    wait_for_quest_state,
)
from tests.e2e.quests.reachability.conftest import (
    assert_pos_within,
    navigate_long,
    reachability,
    slow,
    vanilla_seed_kwargs,
)

MINING = 5
FORAGING = 15
CRAFTING = 11
FLETCHING = 13
COOKING = 9

BABUSHKA_POS = NPCS["iamverycoldnpc"]   # (702, 608)
BABUSHKA_DOOR = (483, 275)
BABUSHKA_DOOR_EXIT = (702, 613)
BERYL_CLUSTER = (645, 643)              # per audit
BLUELILY_SPOT = (278, 250)
GOBLIN_SPAWN = (106, 118)               # nearest to Mudwich per audit


@reachability
@slow
async def test_a1_navigate_mudwich_to_babushka_door(test_username):
    """A1: Vanilla overland walk Mudwich (188,157) → Babushka's entry
    door at (483, 275). ~413 tiles."""
    seed_player(test_username, **vanilla_seed_kwargs())
    try:
        async with mcp_session(username=test_username) as session:
            await navigate_long(
                session,
                target_x=BABUSHKA_DOOR[0],
                target_y=BABUSHKA_DOOR[1],
                max_step=50,
                max_hops=20,
                arrive_tolerance=4,
                per_hop_timeout_s=90.0,
                poll_interval_s=2.0,
                no_progress_timeout_s=45.0,
            )
            await assert_pos_within(
                session,
                target_x=BABUSHKA_DOOR[0],
                target_y=BABUSHKA_DOOR[1],
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
            r = await session.call_tool("navigate", {"x": BABUSHKA_DOOR[0], "y": BABUSHKA_DOOR[1]})
            assert not r.is_error, r.text[:300]
            await wait_for_position(
                session,
                x=BABUSHKA_DOOR_EXIT[0],
                y=BABUSHKA_DOOR_EXIT[1],
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
    """A4a: CAN bronzeaxe (starter kit) mine beryl, or does Kaetram require
    a pickaxe specifically? If this fails, the vanilla player needs to
    smith/buy a pickaxe before A&C is completable."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(BERYL_CLUSTER[0], BERYL_CLUSTER[1] + 1),
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
async def test_a4b_mine_beryl_with_bronzepickaxe(test_username):
    """A4b: Control — confirm bronzepickaxe DOES mine beryl. Taken together
    with A4a this answers whether any axe works or only pickaxes."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=(BERYL_CLUSTER[0], BERYL_CLUSTER[1] + 1),
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
    """A6: Full fletching chain — 1 log → 4 sticks → 1 bowlmedium (stick×4).
    Requires knife in inventory for Fletching unlock."""
    seed_player(
        test_username,
        **vanilla_seed_kwargs(
            position=adjacent_to("iamverycoldnpc"),
            inventory=[
                {"index": 0, "key": "knife", "count": 1},
                {"key": "logs", "count": 1},
            ],
            skills=[{"type": FLETCHING, "experience": 1_000}],
        ),
    )
    try:
        async with mcp_session(username=test_username) as session:
            # 1 log → 4 sticks
            await craft_recipe(session, skill="fletching", recipe_key="stick", count=1)
            await wait_for_inventory_count(session, "stick", expected_at_least=4)
            # 4 sticks → 1 bowlmedium
            await craft_recipe(session, skill="fletching", recipe_key="bowlmedium", count=1)
            await wait_for_inventory_count(session, "bowlmedium", expected_at_least=1)
    finally:
        cleanup_player(test_username)


@reachability
@slow
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
            # Attack goblins until we drop a mushroom1. Budget ~10 kills.
            for _ in range(12):
                r = await session.call_tool("attack", {"mob_name": "Goblin"})
                # attack returns kill data; loot may need separate call
                await asyncio.sleep(2.5)
                loot = await session.call_tool("loot", {})
                await asyncio.sleep(0.5)
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
