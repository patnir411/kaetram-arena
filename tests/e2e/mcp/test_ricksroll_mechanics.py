"""Empirically verify RICK'S ROLL mechanics against the live (compiled) game.

The agent runs stalled Rick's Roll at stage 1/4. Before changing any prompt /
harness logic we must ground the game-mechanic questions in observed truth
(source files can differ from the running game):

  T1. Does fishing+cooking 5 shrimp BEFORE accepting count toward stage 1?
      (i.e. can the agent prep on the safe Mudwich coast, then cross the lethal
      seaside ONCE to accept+turn in — vs. multiple lethal crossings.)
  T2. Are there Shrimp Fishing Spots at Rick's seaside (x>1000), or only
      Mudwich-side (~x 324-348)?
  T3. What survives the door-landing (1138,800) → Rick (1088,833) zone, and
      what mob levels are actually there?
  T4. Does query_quest's current_step surface cookedshrimp needed/have/remaining
      at stage 1? (harness-side: the stage_summary `cookedshrimp x5` token.)
  T5. Can the agent FISH rawshrimp standing at the Mudwich shrimp cluster?
      (Seed a verified-walkable fishing tile from real Claude completion runs —
      (326,358); gather('Shrimp Fishing Spot') yields rawshrimp.)
  T6. Can the agent COOK at the (323,892) station? (Seeding a fishing-spot water
      tile bounces the player to the spawn dungeon (328,892), 5 tiles from the
      station; craft_item('cooking') cooks seeded raw shrimp there.)
  T5 + T6 are the two legs of the path Claude actually used to FINISH Rick's Roll
  (verified in run_20260504_221206 et al.): fish at the coast, travel to the
  (323,892) cooking station, cook, then cross to Rick. They are separate sites
  (~560 tiles apart) — the agent travels between them; it does not cook on the coast.

Run against the test lane (scripts/start-test-kaetram.sh, :9191, db kaetram_e2e).
These are measurement tests: they print observed truth and assert only the
load-bearing facts, so a flaky leg still yields data.
"""
from __future__ import annotations

import asyncio

import pytest

from bench.seed import cleanup_player, seed_player

from ..helpers.mcp_client import mcp_session

# --- coords from ricksroll.json walkthrough + observed agent logs ---
RICK = (1088, 833)
RICK_ADJ = (1088, 832)        # seed 1 tile from Rick for T1
DOOR_LANDING = (1138, 800)    # where door 1025 (379,388) teleports you
MUDWICH_SHRIMP = (336, 328)   # a Shrimp Fishing Spot agents saw on the safe side

# Tank loadout: survive the L76-L118 seaside long enough to measure.
TANK_SKILLS = [
    {"name": "health", "experience": 5_000_000},
    {"name": "strength", "experience": 2_000_000},
    {"name": "defense", "experience": 2_000_000},
    {"name": "accuracy", "experience": 2_000_000},
    {"name": "fishing", "experience": 5_000},
    {"name": "cooking", "experience": 5_000},
]
FOOD = [{"index": 10, "key": "apple", "count": 50}]


def _quest_stage(obs: dict, name_sub: str = "rick"):
    """Return ('active', stage) / ('finished', None) / (None, None) for the
    first quest whose name contains name_sub (case-insensitive)."""
    for q in (obs.get("active_quests") or []):
        if name_sub in str(q.get("name", "")).lower():
            return "active", q.get("stage")
    for q in (obs.get("finished_quests") or []):
        if name_sub in str(q.get("name", "")).lower():
            return "finished", None
    return None, None


def _has_item(obs: dict, key: str) -> int:
    return sum(int(i.get("count", 0) or 0)
               for i in (obs.get("inventory") or [])
               if key in str(i.get("key", "")).lower())


def _shrimp_spots(obs: dict) -> list[dict]:
    return [r for r in ((obs.get("nearby") or {}).get("resources") or [])
            if "shrimp" in str(r.get("name", "")).lower()]


def _mobs(obs: dict) -> list[dict]:
    return (obs.get("nearby") or {}).get("mobs") or []


async def _staged_walk(s, tx: int, ty: int, max_hops: int = 14):
    """Walk toward (tx,ty) in observe->navigate hops (loads regions as you go).
    Return (arrived_within_3, end_pos, last_nav_json)."""
    last = {}
    for _ in range(max_hops):
        o = (await s.call_tool("observe", {})).json() or {}
        p = o.get("pos") or {}
        px, py = p.get("x"), p.get("y")
        if px is not None and py is not None and abs(px - tx) + abs(py - ty) <= 3:
            return True, p, last
        nav = await s.call_tool("navigate", {"x": tx, "y": ty})
        await asyncio.sleep(3.0)
        last = nav.json() if not nav.is_error else {"error": nav.text[:160]}
    o = (await s.call_tool("observe", {})).json() or {}
    return False, o.get("pos"), last


# ────────────────────────────────────────────────────────────────────────
# T1 — does pre-accept-acquired cookedshrimp count toward stage 1?
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t1_preheld_cookedshrimp_count_at_turnin(test_username):
    """Seed a tank ADJACENT to Rick holding 5 cookedshrimp, quest NOT started.
    accept (0->1), then turn in. If stage reaches 2 and seaweedroll appears, the
    shrimp acquired BEFORE accepting count -> agent can prep then cross once."""
    cleanup_player(test_username)
    inv = list(seed_player_starter_with(
        {"index": 8, "key": "cookedshrimp", "count": 5},
        *FOOD,
    ))
    # Seed at the door-landing and WALK in (like T3) so Rick's region/NPC streams
    # before we interact — seeding adjacent + interacting immediately races NPC load.
    seed_player(test_username, position=DOOR_LANDING, hit_points=5000,
                skills=TANK_SKILLS, inventory=inv)
    progression = []
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            arrived, pos, _ = await _staged_walk(s, *RICK, max_hops=12)
            o0 = (await s.call_tool("observe", {})).json() or {}
            npcs = [(n.get("name"), n.get("dist")) for n in ((o0.get("nearby") or {}).get("npcs") or [])]
            print(f"\n[T1] walked to Rick: arrived={arrived} pos={pos} nearby_npcs={npcs}")
            progression.append(("start", _quest_stage(o0), _has_item(o0, "cookedshrimp")))
            for label in ("accept", "turnin1", "turnin2"):
                args = {"npc_name": "Rick"}
                if label == "accept":
                    args["accept_quest_offer"] = True
                r = await s.call_tool("interact_npc", args)
                await asyncio.sleep(2.0)
                o = (await s.call_tool("observe", {})).json() or {}
                progression.append((
                    label,
                    _quest_stage(o),
                    f"cooked={_has_item(o,'cookedshrimp')} roll={_has_item(o,'seaweedroll')}",
                    (r.json() if not r.is_error else {"error": r.text[:160]}),
                ))
            final = (await s.call_tool("observe", {})).json() or {}
            kind, stage = _quest_stage(final)
            roll = _has_item(final, "seaweedroll")
            print("\n[T1] progression:")
            for row in progression:
                print("   ", row)
            print(f"[T1] FINAL: quest={kind} stage={stage} seaweedroll={roll} "
                  f"cookedshrimp={_has_item(final,'cookedshrimp')}")
            counts = (roll >= 1) or (stage is not None and stage >= 2)
            print(f"[T1] VERDICT: pre-accept cookedshrimp COUNT = {counts}")
    finally:
        cleanup_player(test_username)


def seed_player_starter_with(*extra):
    """Starter kit + extra items (cooked shrimp / food) at distinct slots."""
    from bench.seed import STARTER_KIT
    return list(STARTER_KIT) + list(extra)


# ────────────────────────────────────────────────────────────────────────
# T2 — are there Shrimp Fishing Spots at the seaside (x>1000)?
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t2_seaside_shrimp_spots(test_username):
    """Seed a tank at the door-landing and scan nearby.resources walking to Rick.
    Record every fishing spot + coords; flag any Shrimp Fishing Spot at x>1000."""
    cleanup_player(test_username)
    seed_player(test_username, position=DOOR_LANDING, hit_points=5000,
                skills=TANK_SKILLS, inventory=seed_player_starter_with(*FOOD))
    seaside_shrimp = []
    all_spots = []
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            for tx, ty in (DOOR_LANDING, (1110, 820), RICK, (1060, 845)):
                await _staged_walk(s, tx, ty, max_hops=8)
                o = (await s.call_tool("observe", {})).json() or {}
                px = (o.get("pos") or {}).get("x")
                for r in ((o.get("nearby") or {}).get("resources") or []):
                    if "fish" in str(r.get("name", "")).lower() or r.get("kind") == "fishspot":
                        all_spots.append((px, r.get("name"), r.get("x"), r.get("y")))
                for sp in _shrimp_spots(o):
                    if isinstance(sp.get("x"), (int, float)) and sp["x"] > 1000:
                        seaside_shrimp.append(sp)
            print("\n[T2] all fishing spots seen near Rick (player_x, name, sx, sy):")
            for row in all_spots:
                print("   ", row)
            print(f"[T2] VERDICT: seaside (x>1000) Shrimp Fishing Spots found = "
                  f"{len(seaside_shrimp)}: {seaside_shrimp}")
    finally:
        cleanup_player(test_username)


# ────────────────────────────────────────────────────────────────────────
# T3 — mob levels at the landing, and can a high-level tank cross alive?
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t3_landing_mob_levels_and_survival(test_username):
    """Seed a tank at (1138,800); record nearby mob levels+aggression, then
    walk to Rick and back, reporting whether it survives."""
    cleanup_player(test_username)
    seed_player(test_username, position=DOOR_LANDING, hit_points=5000,
                skills=TANK_SKILLS, inventory=seed_player_starter_with(*FOOD))
    try:
        async with mcp_session(username=test_username) as s:
            o0 = (await s.call_tool("observe", {})).json() or {}
            mobs0 = [(m.get("name"), m.get("level"), m.get("aggressive"), m.get("dist"))
                     for m in _mobs(o0)]
            print(f"\n[T3] landing (1138,800) nearby mobs: {mobs0}")
            arrived, pos, _ = await _staged_walk(s, *RICK, max_hops=12)
            o1 = (await s.call_tool("observe", {})).json() or {}
            dead1 = (o1.get("status") or {}).get("dead")
            hp1 = (o1.get("stats") or {}).get("hp")
            print(f"[T3] after walk to Rick: arrived={arrived} pos={pos} hp={hp1} dead={dead1}")
            mobs1 = [(m.get("name"), m.get("level"), m.get("aggressive"))
                     for m in _mobs(o1)]
            print(f"[T3] mobs near Rick: {mobs1}")
            back, posb, _ = await _staged_walk(s, *DOOR_LANDING, max_hops=12)
            o2 = (await s.call_tool("observe", {})).json() or {}
            dead2 = (o2.get("status") or {}).get("dead")
            print(f"[T3] after walk back: pos={posb} hp={(o2.get('stats') or {}).get('hp')} dead={dead2}")
            levels = sorted({m.get("level") for m in _mobs(o0) + _mobs(o1)
                             if isinstance(m.get("level"), int)})
            print(f"[T3] VERDICT: landing/Rick mob levels seen = {levels}; "
                  f"tank reached Rick alive = {arrived and not dead1}")
    finally:
        cleanup_player(test_username)


# ────────────────────────────────────────────────────────────────────────
# T4 — current_step now surfaces cookedshrimp progress at stage 1
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t4_current_step_cookedshrimp_progress(test_username):
    """Seed Rick's Roll accepted at stage 1 holding 2 cookedshrimp at a safe spot;
    query_quest must now return current_step.needed/have/remaining for cookedshrimp
    (the stage_summary `cookedshrimp x5` token fix)."""
    cleanup_player(test_username)
    seed_player(
        test_username, position=(188, 157), hit_points=200,
        skills=TANK_SKILLS,
        inventory=seed_player_starter_with({"index": 8, "key": "cookedshrimp", "count": 2}),
        quests=[{"key": "ricksroll", "stage": 1, "subStage": 0, "completedSubStages": []}],
    )
    try:
        async with mcp_session(username=test_username) as s:
            await s.call_tool("observe", {})
            await asyncio.sleep(1.0)
            r = (await s.call_tool("query_quest", {"quest_name": "Rick's Roll"})).json() or {}
            cs = r.get("current_step") or {}
            print(f"\n[T4] current_step = {cs}")
            assert cs.get("accepted") is True
            assert cs.get("stage") == 1
            assert cs.get("needed") == {"cookedshrimp": 5}, cs
            assert cs.get("have") == {"cookedshrimp": 2}, cs
            assert cs.get("remaining") == {"cookedshrimp": 3}, cs
            print("[T4] VERDICT: current_step surfaces cookedshrimp progress = True")
    finally:
        cleanup_player(test_username)


# ────────────────────────────────────────────────────────────────────────
# T5 — the FISH leg: rawshrimp at the Mudwich coast (the leg T6 doesn't cover)
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t5_fish_rawshrimp_at_the_coast(test_username):
    """Fish leg of the verified Claude path. Seed a real, walkable fishing tile
    from Claude's completion runs — (326,358), where ClaudeBot stood fishing in
    run_20260504_221206 — equip the starter fishingpole, and gather rawshrimp.

    (Seeding ON a fishing-spot WATER tile like (336,328) is rejected by the
    engine and bounces the player to the spawn dungeon — that's T6's setup.)

    Cooking is the SEPARATE leg (T6): the agent travels ~560 tiles to the
    (323,892) station. Asserts the load-bearing fact: fishing yields rawshrimp
    here. Best-effort on count (fishing is RNG) — ≥1 proves the leg works."""
    cleanup_player(test_username)
    seed_player(
        test_username, position=(326, 358), hit_points=200,
        skills=[{"name": "fishing", "experience": 500}],
        inventory=seed_player_starter_with(),   # STARTER_KIT carries fishingpole @ slot 2
    )
    try:
        async with mcp_session(username=test_username) as s:
            o0 = (await s.call_tool("observe", {})).json() or {}
            await asyncio.sleep(1.0)
            print(f"\n[T5] login pos={o0.get('pos')} spots_nearby={len(_shrimp_spots(o0))} "
                  f"alert={o0.get('location_alert')!r}")
            await s.call_tool("equip_item", {"slot": 2})   # starter fishingpole @ slot 2

            raw_yields = []
            raw = 0
            for i in range(15):
                g = await s.call_tool("gather", {"resource_name": "Shrimp Fishing Spot"})
                await asyncio.sleep(1.0)
                o = (await s.call_tool("observe", {})).json() or {}
                raw = _has_item(o, "rawshrimp")
                raw_yields.append(raw)
                if i < 4 or raw >= 3:
                    gj = g.json() if not g.is_error else {"error": g.text[:140]}
                    print(f"[T5] gather#{i} rawshrimp={raw} -> "
                          f"{gj.get('items_gained') or gj.get('error')}")
                if raw >= 3:
                    break
            final = (await s.call_tool("observe", {})).json() or {}
            raw = _has_item(final, "rawshrimp")
            print(f"[T5] VERDICT: fished rawshrimp at the coast = {raw} at "
                  f"pos={final.get('pos')} (yield curve {raw_yields})")

            assert raw >= 1, (
                f"no rawshrimp fished at the coast (yields {raw_yields}, "
                f"pos {final.get('pos')}) — fish leg failed")
    finally:
        cleanup_player(test_username)


# ────────────────────────────────────────────────────────────────────────
# T6 — the nearest reachable cooking station is by the spawn dungeon (323,892)
# ────────────────────────────────────────────────────────────────────────
@pytest.mark.mcp
async def test_t6_cooking_station_is_by_the_spawn_dungeon(test_username):
    """Counterpart to T5. Seeding onto a fishing-spot WATER tile (336,328) is
    rejected by the engine and the player lands in the spawn/respawn dungeon
    (~328,892) — 5 tiles from the ONLY cooking station near Mudwich, (323,892).
    From there craft_item('cooking') reaches the station and cooks seeded raw
    shrimp. T5 (fish at the coast) + T6 (cook by spawn) are the two legs of the
    path Claude used to finish Rick's Roll — separate sites ~560 tiles apart."""
    cleanup_player(test_username)
    seed_player(
        test_username, position=(336, 328), hit_points=200,   # water tile → bounces to spawn
        skills=[{"name": "cooking", "experience": 500}],
        inventory=seed_player_starter_with({"index": 8, "key": "rawshrimp", "count": 5}),
    )
    try:
        async with mcp_session(username=test_username) as s:
            o0 = (await s.call_tool("observe", {})).json() or {}
            await asyncio.sleep(1.0)
            pos = o0.get("pos") or {}
            print(f"\n[T6] login pos={pos} in_spawn_dungeon={bool(o0.get('location_alert'))}")
            cook = await s.call_tool(
                "craft_item",
                {"skill": "cooking", "recipe_key": "cookedshrimp", "count": 5},
            )
            cj = cook.json() if not cook.is_error else {"error": cook.text[:400]}
            await asyncio.sleep(2.0)
            oc = (await s.call_tool("observe", {})).json() or {}
            cooked = _has_item(oc, "cookedshrimp")
            tgt = cj.get("target") or {}
            print(f"[T6] craft_item(cooking) by spawn -> {cj}")
            print(f"[T6] after cook: pos={oc.get('pos')} cookedshrimp={cooked}")
            print(f"[T6] VERDICT: cooking station reachable from spawn = "
                  f"{cooked >= 1 or tgt.get('cursor') == 'cooking'}; target={tgt}")

            assert cooked >= 1 or tgt.get("cursor") == "cooking", (
                f"expected to reach the (323,892) cooking station from spawn: {cj}")
    finally:
        cleanup_player(test_username)
