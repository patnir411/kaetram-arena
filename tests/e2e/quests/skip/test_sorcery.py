"""Sorcery stage-by-stage quest coverage."""
import asyncio
import json

import pytest

from bench.seed import cleanup_player, seed_player, snapshot_player
from tests.e2e.helpers.kaetram_world import adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.quests.conftest import (
    AUTOSAVE_WAIT,
    assert_quest_finished,
    assert_quest_state,
    count_live_inventory,
    live_observe,
    wait_for_inventory_count,
    wait_for_position,
)

ACCURACY = 1
HEALTH = 3
MAGIC = 4
STRENGTH = 6
DEFENSE = 7
COMBAT_XP = 10_000_000
EQUIPMENT_WEAPON = 4
WARRIOR_CRAB_POS = (320, 455)
WARRIOR_CRAB_STAND_CANDIDATES = [
    (322, 455),
    (323, 455),
    (324, 455),
    (320, 453),
    (321, 453),
]
WARRIOR_CRAB_LAND_KILL_POS = (327, 455)


def _debug_sorcery_seed(username: str, label: str, expected_pos: tuple[int, int]) -> None:
    snap = snapshot_player(username)
    info = snap.get("player_info") or {}
    quests = (snap.get("player_quests") or {}).get("quests") or []
    equipment = (snap.get("player_equipment") or {}).get("equipments") or []
    skills = (snap.get("player_skills") or {}).get("skills") or []
    print(
        "[debug_sorcery] "
        + json.dumps(
            {
                "label": label,
                "player_info": {
                    "x": info.get("x"),
                    "y": info.get("y"),
                    "hp": info.get("hitPoints"),
                    "lastWarp": info.get("lastWarp"),
                    "mapVersion": info.get("mapVersion"),
                    "regionsLoaded": info.get("regionsLoaded"),
                },
                "quests": quests,
                "equipment": equipment,
                "skills": skills,
                "expected_stand": {"x": expected_pos[0], "y": expected_pos[1]},
                "warrior_crab": {"x": WARRIOR_CRAB_POS[0], "y": WARRIOR_CRAB_POS[1]},
            },
            sort_keys=True,
        ),
        flush=True,
    )


def _seed_sorcery_warrior_crab_attempt(test_username: str, spawn_pos: tuple[int, int]) -> None:
    seed_player(
        test_username,
        position=spawn_pos,
        hit_points=500,
        mana=500,
        inventory=[],
        equipment=[{"type": EQUIPMENT_WEAPON, "key": "cursestaff", "count": 1, "ability": -1, "abilityLevel": 0}],
        skills=[
            {"type": ACCURACY, "experience": COMBAT_XP},
            {"type": HEALTH, "experience": COMBAT_XP},
            {"type": MAGIC, "experience": COMBAT_XP},
            {"type": STRENGTH, "experience": COMBAT_XP},
            {"type": DEFENSE, "experience": COMBAT_XP},
        ],
        quests=[{"key": "sorcery", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )


def _has_nearby_bead_drop(obs: dict) -> bool:
    for item in obs.get("items_nearby") or []:
        name = str(item.get("name") or "").lower()
        key = str(item.get("key") or "").lower()
        item_type = item.get("type")
        if "bead" in name or key == "bead" or item_type == 8:
            return True
    return False


async def _loot_bead_and_wait(session) -> bool:
    last_loot = None
    for _ in range(16):
        obs = await live_observe(session)
        if count_live_inventory(obs.get("inventory") or [], "bead") >= 1:
            return True
        if last_loot is None or _has_nearby_bead_drop(obs):
            last_loot = await session.call_tool("loot", {})
            assert not last_loot.is_error, last_loot.text[:300]
        await asyncio.sleep(0.5)

    return False


@pytest.mark.quest_chain
async def test_sorcery_stage_0_to_1_accept(test_username):
    seed_player(
        test_username,
        position=adjacent_to("sorcerer"),
        inventory=[],
        quests=[{"key": "sorcery", "stage": 0, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Sorcerer"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_state(test_username, "sorcery", stage=1, sub_stage=0, completed_sub_stages=[])
    finally:
        cleanup_player(test_username)


@pytest.mark.quest_chain
async def test_sorcery_action_bead_drop_from_warrior_crab(test_username):
    failures = []

    for candidate in WARRIOR_CRAB_STAND_CANDIDATES:
        cleanup_player(test_username)
        _seed_sorcery_warrior_crab_attempt(test_username, candidate)
        _debug_sorcery_seed(test_username, "after_seed_before_login", candidate)

        async with mcp_session(username=test_username) as session:
            obs = {}
            for attempt in range(8):
                obs = await live_observe(session)
                pos = obs.get("pos") or {}
                print(
                    "[debug_sorcery] "
                    + json.dumps(
                        {
                            "label": "wait_for_stand_pos",
                            "attempt": attempt + 1,
                            "observed_pos": pos,
                            "expected_pos": {"x": candidate[0], "y": candidate[1]},
                            "map": obs.get("map"),
                            "stats": obs.get("stats"),
                            "active_quests": obs.get("active_quests"),
                            "nearby_mobs": obs.get("mobs_within_15"),
                            "nearby_npcs": obs.get("npcs_within_15"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if pos == {"x": candidate[0], "y": candidate[1]}:
                    break
                await asyncio.sleep(0.5)
            else:
                _debug_sorcery_seed(test_username, "after_position_wait_failure", candidate)
                failures.append({"candidate": candidate, "last_observe": obs})
                continue

            pull = await session.call_tool("attack", {"mob_name": "Hermit Crab Warrior"})
            assert not pull.is_error, pull.text[:300]
            move = await session.call_tool(
                "navigate",
                {"x": WARRIOR_CRAB_LAND_KILL_POS[0], "y": WARRIOR_CRAB_LAND_KILL_POS[1]},
            )
            assert not move.is_error, move.text[:300]
            await wait_for_position(
                session,
                x=WARRIOR_CRAB_LAND_KILL_POS[0],
                y=WARRIOR_CRAB_LAND_KILL_POS[1],
                max_distance=0,
                polls=24,
                delay_s=0.5,
            )

            last_attack = None
            killed = False
            for _ in range(30):
                last_attack = await session.call_tool("attack", {"mob_name": "Hermit Crab Warrior"})
                assert not last_attack.is_error, last_attack.text[:300]
                try:
                    attack_data = json.loads(last_attack.text)
                    killed = bool((attack_data.get("post_attack") or {}).get("killed"))
                except Exception:
                    attack_data = {}
                obs = await live_observe(session)
                if count_live_inventory(obs.get("inventory") or [], "bead") >= 1:
                    break
                if _has_nearby_bead_drop(obs):
                    if await _loot_bead_and_wait(session):
                        break
                if killed and await _loot_bead_and_wait(session):
                    break
            else:
                raise AssertionError(
                    "Hermit Crab Warrior did not yield a bead after repeated attacks. "
                    f"last_attack={last_attack.text[:500] if last_attack else None}, observe={obs}"
                )
            await wait_for_inventory_count(session, "bead", expected_at_least=1, polls=12, delay_s=0.5)
            cleanup_player(test_username)
            return

    cleanup_player(test_username)
    raise AssertionError(f"No Warrior Crab spawn candidate survived login: {failures}")


@pytest.mark.quest_chain
async def test_sorcery_stage_1_to_2_bead_turnin(test_username):
    seed_player(
        test_username,
        position=adjacent_to("sorcerer"),
        inventory=[{"key": "bead", "count": 3}],
        quests=[{"key": "sorcery", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as session:
            result = await session.call_tool("interact_npc", {"npc_name": "Sorcerer"})
            assert not result.is_error, result.text[:300]
            await asyncio.sleep(1.0)
        await asyncio.sleep(AUTOSAVE_WAIT)
        assert_quest_finished(test_username, "sorcery", stage_count=2)
    finally:
        cleanup_player(test_username)
