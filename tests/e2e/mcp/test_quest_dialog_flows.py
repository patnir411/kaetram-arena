"""Deterministic quest-dialog coverage — calls interact_npc directly
(no LLM) to verify the MCP tool surface advances quest stages given the
right state.

Acts as a contract test complementing the LLM-driven tests in
`tests/e2e/quests/`:
  - LLM tests: "can qwen drive interact_npc to complete a quest?"
  - This file: "does interact_npc's quest-advance path work at all?"

If tests here go red, no LLM will pass. Fix here first.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.helpers.kaetram_world import QUESTS, adjacent_to
from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player, snapshot_player


# Quests whose accept flow is a simple talk (no item gate). Subset that
# should reliably advance stage 0 → > 0 via interact_npc.
SIMPLE_ACCEPT_QUESTS = [
    "foresting", "anvilsechoes", "scientistspotion", "minersquest",
    "herbalistdesperation", "ricksroll",
    "royaldrama", "royalpet", "clamchowder", "scavenger", "sorcery",
    "desertquest", "ancientlands",
]


def _quest_stage(username: str, quest_key: str) -> int:
    snap = snapshot_player(username)
    quests = (snap.get("player_quests") or {}).get("quests") or []
    q = next((q for q in quests if q.get("key") == quest_key), None)
    return int((q or {}).get("stage", 0) or 0)


@pytest.mark.parametrize("quest_key", SIMPLE_ACCEPT_QUESTS)
@pytest.mark.mcp_smoke
async def test_interact_npc_accepts_quest(isolated_lane, unique_username, quest_key):
    """Seed adjacent to the quest's NPC at stage 0, call interact_npc,
    verify Mongo stage advances."""
    info = QUESTS[quest_key]
    npc_key = info["npc_key"]
    display = info["display"]

    cleanup_player(unique_username)
    # Per-quest seed tweaks driven by game rules:
    #  - ancientlands: seed north of the Monument (dy=-1) because dy=+1
    #    lands on a plateau-boundary tile and the server relocates to
    #    SPAWN_POINT on login.
    #  - royalpet: King is hidden until royaldrama is FINISHED
    #    (royaldrama.json hideNPCs: {"king": "before"}).
    dy = -1 if quest_key == "ancientlands" else 1
    quests = [{"key": quest_key, "stage": 0, "subStage": 0,
               "completedSubStages": []}]
    if quest_key == "royalpet":
        quests.append({"key": "royaldrama", "stage": 3, "subStage": 0,
                       "completedSubStages": []})
    seed_player(
        unique_username,
        position=adjacent_to(npc_key, dy=dy),
        inventory=[{"key": "bronzeaxe", "count": 1}],
        quests=quests,
    )
    try:
        assert _quest_stage(unique_username, quest_key) == 0
        async with mcp_session(
            username=unique_username, client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            await session.call_tool("interact_npc", {"npc_name": display})
            await asyncio.sleep(3.0)
        # Allow Mongo autosave to flush.
        await asyncio.sleep(1.0)
        final = _quest_stage(unique_username, quest_key)
        assert final > 0, (
            f"{quest_key}: interact_npc({display}) did not advance stage (still 0). "
            f"Check NPC placement + quest JSON stage-0 task."
        )
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_full
async def test_foresting_stage1_turn_in_contract(isolated_lane, unique_username):
    """Key contract test: 10 non-stackable logs seeded across 10 slots
    (NON_STACKABLE_KEYS auto-expansion) must be consumed by interact_npc and
    the quest stage must advance."""
    cleanup_player(unique_username)
    seed_player(
        unique_username,
        position=adjacent_to("forestnpc"),
        inventory=[
            {"key": "ironaxe", "count": 1},
            {"key": "logs", "count": 10},   # NON_STACKABLE → 10 slots
        ],
        equipment=[{"type": 0, "key": "ironaxe", "count": 1,
                    "ability": -1, "abilityLevel": 0}],
        quests=[{"key": "foresting", "stage": 1, "subStage": 0,
                 "completedSubStages": []}],
    )
    try:
        async with mcp_session(
            username=unique_username, client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            await session.call_tool("interact_npc", {"npc_name": "Forester"})
            await asyncio.sleep(3.0)
        await asyncio.sleep(1.0)
        final = _quest_stage(unique_username, "foresting")
        assert final > 1, (
            f"Foresting stage stuck at 1 after 10-logs turn-in. "
            f"Likely seed.NON_STACKABLE_KEYS is missing `logs` or the "
            f"interact_npc → handleItemRequirement path is broken."
        )
    finally:
        cleanup_player(unique_username)
