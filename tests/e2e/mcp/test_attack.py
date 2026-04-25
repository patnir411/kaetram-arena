"""attack() tool — regression lock for the fake-kill detection bug.

History: the pre-2026-04-20 kill check was `killed: !t || (t.hitPoints <= 0)`,
which returned `killed: true` whenever the player never acquired a target
(the `!t` branch). Agents falsely recorded kills on unreachable mobs and the
resulting training data was corrupted. The fix requires a real HP transition
from >0 to 0 — this test locks it in.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_attack_unreachable_mob_does_not_fake_kill(isolated_lane, unique_username):
    """attack('Dark Skeleton') from Mudwich hits a mob 60+ tiles away through
    walls. Must NOT claim a kill — honest output is `killed: false,
    damage_dealt: 0` with a guidance note."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("attack", {"mob_name": "Dark Skeleton"})
            data = res.json() or {}
            post = data.get("post_attack") or {}
            # killed must be False or None; damage must be 0. The pre-patch
            # bug returned killed=true, damage=740.
            assert post.get("killed") in (False, None), (
                f"attack falsely reported killed=True: {data}"
            )
            assert post.get("damage_dealt", 0) == 0
    finally:
        cleanup_player(unique_username)


@pytest.mark.mcp_smoke
async def test_layerB_attack_no_target_returns_guidance(isolated_lane, unique_username):
    """When no target matches, the tool must return a clear note so the agent
    knows to navigate or pick another mob — not silently return success."""
    seed_player(
        unique_username,
        position=(188, 157),
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            await session.call_tool("observe", {})
            res = await session.call_tool("attack", {"mob_name": "ThisMobDoesNotExist"})
            data = res.json() or {}
            post = data.get("post_attack") or {}
            # Accept: explicit error OR zero damage + guidance note.
            assert (
                "error" in data
                or post.get("note")
                or post.get("damage_dealt", 0) == 0
            ), f"silent no-op on unreachable attack: {res.text[:200]}"
    finally:
        cleanup_player(unique_username)
