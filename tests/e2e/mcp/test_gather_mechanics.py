"""Empirically verify FORAGE gather mechanics against the live (compiled) game.

Tool messages that assert a mechanic must be grounded in observed game behaviour,
not source-file reads (the compiled game can differ from source). These tests
measure the real behaviour — harvest range, RNG, and tool requirement — so any
such message can be checked against observed truth.

Forage bushes share the same `canExhaustResource` path, so a safe Mudwich
Blueberry Bush (no hostile mobs) settles the mechanic for paprika/tomato/lily too.
Run against the test lane (scripts/start-test-kaetram.sh, :9191, db kaetram_e2e).
"""

from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session

# Blueberry bush placement from Kaetram-Open world.json entities (near Mudwich).
BUSH = (226, 106)
FORAGING = 15  # Modules.Skills order
FORAGING_XP = 2000  # well past the L5 gate, no skill-gate ambiguity


def _item_count(obs: dict, key: str) -> int:
    return sum(
        int(i.get("count", 0) or 0)
        for i in (obs.get("inventory") or [])
        if key in str(i.get("key", "")).lower()
    )


def _berry_count(obs: dict) -> int:
    return _item_count(obs, "blueberry")


# Real paprika node from world.json (the one agents stood near, in the Orc field).
PAPRIKA = (298, 300)
AGENT_SPOT = (302, 296)  # where agents actually stood, ~5-8 tiles from the bush
# Survive the L47 Orc field long enough to measure the mechanic.
SURVIVE_SKILLS = [
    {"name": "foraging", "experience": FORAGING_XP},
    {"name": "health", "experience": 1_500_000},
    {"name": "strength", "experience": 500_000},
    {"name": "defense", "experience": 500_000},
]


async def _gather_loop(s, name: str, n: int):
    """Gather n times; return (yields, last_response, end_pos)."""
    yields = 0
    last = {}
    for _ in range(n):
        before = (await s.call_tool("observe", {})).json() or {}
        b0 = _berry_count(before)
        r = await s.call_tool("gather", {"resource_name": name})
        await asyncio.sleep(4.0)
        after = (await s.call_tool("observe", {})).json() or {}
        if _berry_count(after) > b0:
            yields += 1
        last = r.json() if not r.is_error else {"error": r.text[:200]}
    end = (await s.call_tool("observe", {})).json() or {}
    return yields, last, end.get("pos")


@pytest.mark.mcp
async def test_forage_adjacent_rng_and_tool(test_username):
    """ADJACENT (1 tile), NO tool, Foraging>5: measure the yield rate (is there
    RNG?) and whether a bare-handed gather yields (is a tool needed?)."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(BUSH[0], BUSH[1] + 1),  # 1 tile south of the bush
        skills=[{"name": "foraging", "experience": FORAGING_XP}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            n = 20
            yields, last, end_pos = await _gather_loop(s, "Blueberry", n)
            print(
                f"\n[ADJACENT/NO-TOOL] {yields}/{n} gathers yielded a blueberry "
                f"| end_pos={end_pos} | last why_no_items={last.get('why_no_items')!r}"
            )
            # Minimal hard assertion: a properly-positioned, ungated, bare-handed
            # forage MUST yield at least once, or our "no tool / close range works"
            # assumption is false. The exact rate is reported for inspection.
            assert yields > 0, (
                f"0/{n} yields adjacent + bare-handed + Foraging>5 — "
                f"contradicts 'no tool needed' / close-range. last={last}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_forage_from_range(test_username):
    """FAR (5 tiles), safe area: does gather auto-walk the player into range and
    yield, or fail because the harvest only fires up close? Measures it — no
    outcome assertion, since the point is to learn the real behaviour."""
    cleanup_player(test_username)
    seed_player(
        test_username,
        position=(BUSH[0], BUSH[1] + 5),  # 5 tiles south
        skills=[{"name": "foraging", "experience": FORAGING_XP}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            start = (await s.call_tool("observe", {})).json() or {}
            await asyncio.sleep(1.0)
            yields, last, end_pos = await _gather_loop(s, "Blueberry", 4)
            print(
                f"\n[FROM-RANGE/5tiles] start_pos={start.get('pos')} -> end_pos={end_pos} "
                f"| {yields}/4 yielded | last={last.get('items_gained')!r} "
                f"why={last.get('why_no_items')!r}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_paprika_adjacent_works(test_username):
    """CONTROL: seed 1 tile from the paprika bush (298,300). If gather yields
    here, the bush + mechanic work — so any field failure is the PATH to it, not
    the node. Compare to the ~45-55% blueberry rate."""
    cleanup_player(test_username)
    seed_player(test_username, position=(PAPRIKA[0], PAPRIKA[1] + 1),
                hit_points=3000, skills=SURVIVE_SKILLS)
    try:
        async with mcp_session(username=test_username) as s:
            o0 = (await s.call_tool("observe", {})).json() or {}
            await asyncio.sleep(1.0)
            yields = 0
            last = {}
            for _ in range(10):
                b = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                r = await s.call_tool("gather", {"resource_name": "Paprika"})
                await asyncio.sleep(4.0)
                a = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                if a > b:
                    yields += 1
                last = r.json() if not r.is_error else {"error": r.text[:150]}
            end = (await s.call_tool("observe", {})).json() or {}
            print(
                f"\n[PAPRIKA-ADJACENT] start={o0.get('pos')} hp0={(o0.get('stats') or {}).get('hp')} "
                f"-> {yields}/10 yielded | end={end.get('pos')} hp={(end.get('stats') or {}).get('hp')} "
                f"| last items={last.get('items_gained')!r} why={last.get('why_no_items')!r}"
            )
    finally:
        cleanup_player(test_username)


@pytest.mark.mcp
async def test_paprika_from_agent_spot(test_username):
    """THE reachability test: seed where agents stood (302,296) and try to
    navigate(298,300) + gather. Does navigate bfs_fail (unreachable) and gather
    return 0, while blueberry control yields ~50%? That isolates the wall as
    REACHABILITY of the paprika node, not RNG/tool/range."""
    cleanup_player(test_username)
    seed_player(test_username, position=AGENT_SPOT, hit_points=3000, skills=SURVIVE_SKILLS)
    try:
        async with mcp_session(username=test_username) as s:
            o0 = (await s.call_tool("observe", {})).json() or {}
            await asyncio.sleep(1.0)
            nav = await s.call_tool("navigate", {"x": PAPRIKA[0], "y": PAPRIKA[1]})
            await asyncio.sleep(3.0)
            navj = nav.json() if not nav.is_error else {"error": nav.text[:200]}
            o1 = (await s.call_tool("observe", {})).json() or {}
            yields = 0
            last = {}
            for _ in range(6):
                b = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                r = await s.call_tool("gather", {"resource_name": "Paprika"})
                await asyncio.sleep(4.0)
                a = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                if a > b:
                    yields += 1
                last = r.json() if not r.is_error else {"error": r.text[:150]}
            end = (await s.call_tool("observe", {})).json() or {}
            print(
                f"\n[PAPRIKA-AGENTSPOT] start={o0.get('pos')} -> navigate(298,300): "
                f"status={navj.get('status')} method={navj.get('pathfinding')} "
                f"remaining={navj.get('remaining_distance')} err={navj.get('error')!r} "
                f"| after-nav pos={o1.get('pos')} | {yields}/6 paprika yielded "
                f"| end={end.get('pos')} hp={(end.get('stats') or {}).get('hp')} "
                f"| last items={last.get('items_gained')!r} why={last.get('why_no_items')!r}"
            )
    finally:
        cleanup_player(test_username)


HERBY = (333, 281)
# Paprika nodes Claude actually harvested (it reached them via Desert-Quest warp).
# Question: can a base agent WALK to any of them from Herby (no warp)?
PAPRIKA_CANDIDATES = [(358, 325), (305, 360), (286, 326), (298, 300)]


async def _staged_walk(s, tx: int, ty: int, max_hops: int = 12):
    """Walk toward (tx,ty) in stages (observe->navigate loop, loading regions as
    the player moves). Return (arrived_within_3_tiles, end_pos, last_nav_json)."""
    last = {}
    for _ in range(max_hops):
        o = (await s.call_tool("observe", {})).json() or {}
        p = o.get("pos") or {}
        px, py = p.get("x"), p.get("y")
        if px is not None and py is not None and abs(px - tx) + abs(py - ty) <= 3:
            return True, p, last
        nav = await s.call_tool("navigate", {"x": tx, "y": ty})
        await asyncio.sleep(3.0)
        last = nav.json() if not nav.is_error else {"error": nav.text[:150]}
    o = (await s.call_tool("observe", {})).json() or {}
    return False, o.get("pos"), last


@pytest.mark.mcp
@pytest.mark.parametrize("target", PAPRIKA_CANDIDATES)
async def test_paprika_reachable_on_foot_from_herby(test_username, target):
    """Can a base agent (no warp) WALK from Herby to this paprika node and gather?
    Seeds at Herby, staged-walks toward the node, then gathers if it arrives."""
    tx, ty = target
    cleanup_player(test_username)
    seed_player(test_username, position=HERBY, hit_points=3000, skills=SURVIVE_SKILLS)
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            arrived, end, last = await _staged_walk(s, tx, ty, max_hops=12)
            yields = 0
            if arrived:
                for _ in range(6):
                    b = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                    await s.call_tool("gather", {"resource_name": "Paprika"})
                    await asyncio.sleep(4.0)
                    a = _item_count((await s.call_tool("observe", {})).json() or {}, "paprika")
                    if a > b:
                        yields += 1
            print(
                f"\n[FOOT Herby->({tx},{ty})] arrived={arrived} end={end} "
                f"yields={yields}/6 | last_nav status={last.get('status')} "
                f"method={last.get('pathfinding')} remaining={last.get('remaining_distance')} "
                f"err={str(last.get('error'))[:90]!r}"
            )
    finally:
        cleanup_player(test_username)
