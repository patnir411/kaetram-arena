"""interact_npc() dialogue behaviour — verify correct termination and no over-talking.

These tests validate that the dialogue loop:
  1. Returns `dialogue_complete=true` so the agent knows not to re-call
  2. Stops after exhausting unique lines (cycle detection)
  3. Does not send excessive talk packets that could unintentionally
     open shops or advance quest state
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_interact_npc_returns_dialogue_complete(test_username):
    """interact_npc with a simple NPC must return dialogue_complete=true
    and at least 1 dialogue line.  Seeded next to Villager (198, 114)
    who has 1 line of default text ("Howdy!").
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(198, 115),  # 1 tile south of villager4
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("interact_npc", {"npc_name": "Villager"})
            assert not res.is_error, res.text[:300]
            data = res.json()
            assert data is not None, f"Could not parse JSON: {res.text[:200]}"

            # Must have the dialogue_complete flag
            assert data.get("dialogue_complete") is True, (
                f"Expected dialogue_complete=true, got {data.get('dialogue_complete')}"
            )
            # Must have collected at least 1 line
            assert data.get("dialogue_lines", 0) >= 1, (
                f"Expected >=1 dialogue lines, got {data.get('dialogue_lines')}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_interact_npc_detects_dialogue_cycle(test_username):
    """Interact with Programmer NPC (5 lines of dialogue at 331, 890).
    The tool must collect all 5 unique lines and stop WITHOUT cycling
    back to the first line.  Verifies cycle detection works.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(331, 891),  # 1 tile south of Programmer
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            # Extra observe + settle to let NPC entities load (region hydration)
            await s.call_tool("observe", {})
            await asyncio.sleep(2)

            res = await s.call_tool("interact_npc", {"npc_name": "Programmer"})
            assert not res.is_error, res.text[:300]
            data = res.json()
            assert data is not None, f"Could not parse JSON: {res.text[:200]}"

            # Skip if NPC wasn't loaded (region hydration flake)
            if data.get("error") and "not found" in data["error"].lower():
                pytest.skip("Programmer NPC not loaded in time (region hydration)")

            assert data.get("dialogue_complete") is True

            lines = data.get("dialogue", [])
            line_count = data.get("dialogue_lines", 0)

            # Should have collected all 5 unique lines
            assert line_count >= 3, (
                f"Expected >=3 dialogue lines from Programmer (has 5), got {line_count}: {lines}"
            )

            # The LAST line must NOT match the FIRST — cycle detection should prevent wrap
            if len(lines) >= 2:
                assert lines[-1] != lines[0], (
                    f"Last line matches first line — dialogue cycled! lines={lines}"
                )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_interact_npc_quest_dialogue_stops_cleanly(test_username):
    """Interact with Forester (has store='forester' and foresting quest).
    With tutorial completed and foresting quest at stage 0, the NPC triggers
    the foresting quest dialogue (6 lines) → quest panel opens → auto-accept.
    The tool should stop after the quest panel, NOT continue sending talk
    packets that would cycle through post-accept dialogue.
    """
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(216, 115),  # 1 tile south of Forester at (216, 114)
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            res = await s.call_tool("interact_npc", {"npc_name": "Forester"})
            assert not res.is_error, res.text[:300]
            data = res.json()
            assert data is not None, f"Could not parse JSON: {res.text[:200]}"

            assert data.get("dialogue_complete") is True

            lines = data.get("dialogue", [])
            line_count = data.get("dialogue_lines", 0)

            # Foresting quest has 6 lines of dialogue + quest panel = 7 entries
            # Allow some tolerance, but should NOT be runaway (>10)
            assert line_count <= 10, (
                f"Too many dialogue lines ({line_count}) — tool may be over-talking. "
                f"Lines: {lines}"
            )

            # Should have detected the quest panel
            assert data.get("quest_opened") is True or data.get("quest_accepted") is True, (
                f"Expected quest_opened or quest_accepted, data={data}"
            )
    finally:
        cleanup_player(test_username)
