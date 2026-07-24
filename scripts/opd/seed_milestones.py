"""Seed the three Qwen agents at Core-3 milestone states for OPD bucket-B collection.

Generalizes seed_herbalist_wall.py to the full unsolved ladder — every stage
the 2B student stalls on across Herbalist's Desperation stage 2 and the entire
Rick's Roll chain (accept -> fish -> cook -> turn-in -> door -> Lena). Three
lane sets (one per collection run), each assigning a milestone to each
personality username, banded by progression phase.

Positions are walkability-verified against Kaetram-Open map/world.json (NPC,
quest-door, fishing-spot and cooking-station tiles extracted from the live
data). The Herbalist-stage and Rick's-stage-1 milestones additionally mirror
tests/e2e/quests/reachability/conftest.py playthrough_seed_kwargs. The Health
buffer uses realistic mid-run XP instead of the test suite's huge pad so
survival dynamics in the collected rollouts stay representative.

Each Rick's milestone isolates ONE decision at the relevant NPC/station so the
student can complete it in-session (the geography makes the full chain a
~4000-tile relay no single session collects): Rick lives at (1088,833), 700+
tiles from any of the map's 6 cooking stations, and Lena (455,924) sits inside
a free-teleport door maze.

DOOR-GATED NPCs (2026-07-17 live probe + DeepSeek/Claude completion trace): Rick
(1088,833) and Lena (455,924) are in door-gated regions a raw position write
CANNOT reach — the server's login collision check (intro -> setPosition ->
verifyCollision -> sendToSpawn, player.ts) resets the agent to SPAWN_POINT
(328,892). Overworld tiles DO seed (verified). The successful DeepSeek/Claude
Rick's completions reach both NPCs by crossing doors, so these seeds are staged
on the overworld door-ENTRY tiles and rely on the agent stepping onto the door:
  - Rick (r2_accept / r5_turnin): seed (378,388), cross the non-gated approach
    door (379,388) -> lands (1138,800) -> navigate to Rick.
  - Lena (r6_door): seed (260,230), cross the stage-2 quest door (260,229) ->
    lands (425,909) -> navigate the maze to Lena.
Doors are crossed by plain navigate() ONTO the door tile (no special action);
the binding constraint is the model choosing to target the door, which base-2B/4B
did not in bounded runs (0 door-targeted navigates) but DeepSeek/Claude did.

Usage:
  python3 scripts/opd/seed_milestones.py A    # Herbalist stage-2 x2 + Rick's accept
  python3 scripts/opd/seed_milestones.py B    # Rick's stage-1 chain: fish / cook / turn-in
  python3 scripts/opd/seed_milestones.py C    # Rick's stage 2->3 finish: door / Lena
  KAETRAM_SEED_MILESTONES=A ./scripts/restart-agent.sh --qwen-sft 3 ...   # via launcher
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.e2e.helpers.seed import STARTER_KIT, seed_player  # noqa: E402
from heldout_guard import assert_quests_not_reserved  # noqa: E402

FORESTING_DONE = {"key": "foresting", "stage": 3, "subStage": 0, "completedSubStages": []}
HERBALIST_DONE = {"key": "herbalistdesperation", "stage": 3, "subStage": 0, "completedSubStages": []}
HERBALIST_S2 = {"key": "herbalistdesperation", "stage": 2, "subStage": 0, "completedSubStages": []}
RICKS_S1 = {"key": "ricksroll", "stage": 1, "subStage": 0, "completedSubStages": []}
RICKS_S2 = {"key": "ricksroll", "stage": 2, "subStage": 0, "completedSubStages": []}

# Realistic mid-run Health XP (r2 eval agents ranged ~L9-23), not the e2e pad.
# Run-A lesson: the Herby-zone lane at ~L13 death-looped (L45-54 aggro mobs);
# late-game lanes get ~L22 (6,500 XP — the level r2 agents reached naturally).
HP_MID = {"type": 3, "experience": 2_500}
HP_LATE = {"type": 3, "experience": 6_500}
# Rick (1088,833) sits in an L76-118 aggressive seaside (darkwolf/darkskeleton/
# darkscorpion/blackwizard/minidragon, aggro range up to 6). A realistic-HP seed
# there dies on spawn and respawns at SPAWN_POINT (328,892), so the turn-in seeds
# use a survival buffer (matches reachability conftest R5's 3039 HP / 15M XP pad).
HP_SEASIDE = {"type": 3, "experience": 15_000_000}
SEASIDE_HP = 3039

MILESTONES = {
    # ── Lane A: finish Herbalist, enter Rick's ──
    "h2_tomato": dict(   # Herbalist stage 2, tomato side (conftest H4 position)
        position=(220, 108), hit_points=100,
        quests=[FORESTING_DONE, HERBALIST_S2],
        skills=[HP_MID, {"type": 15, "experience": 1_500},   # Foraging L10 (tomato-safe)
                {"type": 0, "experience": 150}],
    ),
    "h2_paprika": dict(  # Herbalist stage 2, paprika side (conftest H6 position, Herby zone)
        position=(333, 282), hit_points=100,
        quests=[FORESTING_DONE, HERBALIST_S2],
        skills=[HP_MID, {"type": 15, "experience": 1_500},
                {"type": 0, "experience": 150}],
    ),
    "r2_accept": dict(  # Herbalist done, Rick's NOT yet accepted — the handoff (conftest R2)
        # Seed on the overworld APPROACH-DOOR tile (378,388), NOT at Rick: Rick
        # (1088,833) is in a door-gated region a position write can't reach (resets
        # to SPAWN_POINT). The successful DeepSeek/Claude Rick's completions cross
        # the non-gated door (379,388) -> lands (1138,800) -> navigate to Rick.
        # Cross, then interact_npc + accept. Seaside HP buffer for the post-crossing
        # walk through the L76-118 aggro. No ricksroll row = offer still open.
        position=(378, 388), hit_points=SEASIDE_HP,
        quests=[FORESTING_DONE, HERBALIST_DONE],
        skills=[HP_SEASIDE],
    ),
    # ── Lane B: Rick's stage-1 chain (fish -> cook -> turn-in) ──
    "r3_fishing": dict(  # Rick's stage 1 at the shrimp fishing spot, pole equipped (conftest R3)
        # Demonstrates the fish action, not stage completion: (324,360) is one
        # tile off shrimpspot (325,360); shrimp is Fishing L1-ungated so no
        # fishing XP is needed. Cooking + the ~700-tile haul to Rick are split
        # into r4_cook / r5_turnin — this lane is only the fishing decision.
        position=(324, 360), hit_points=100,
        equipment=[{"type": 4, "key": "fishingpole", "count": 1, "ability": -1, "abilityLevel": 0}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_MID],
    ),
    "r4_cook": dict(     # Rick's stage 1, rawshrimp x5 in hand — the cook-decision state (conftest R4)
        # Seed on the (411,866) cooking station's stand tile — an overworld
        # station near Lena's town. NOT the (323,892) station: that one sits
        # 5 tiles from the death-respawn point, so base agents read the seed
        # tile as "respawn dungeon" and warp to Mudwich before cooking (0 cooks
        # in the base-2B r4 probe, run_20260717_142441). craft_item auto-walks
        # from the stand tile, so the lane exercises the cook, not a long haul.
        position=(411, 866), hit_points=100,
        inventory=[{"index": 0, "key": "knife", "count": 1},
                   {"key": "rawshrimp", "count": 5}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_LATE, {"type": 8, "experience": 1_000},   # Fishing
                {"type": 9, "experience": 200}],              # Cooking
    ),
    "r5_turnin": dict(   # Rick's stage 1, cookedshrimp x5 -> Rick (conftest R5)
        # Seed on the overworld approach-door tile (378,388), NOT at Rick
        # (1088,833) — Rick's region is door-gated and a position write resets to
        # SPAWN_POINT (observed live, run_20260717_145155/_152528). DeepSeek/Claude
        # reach Rick by crossing the non-gated door (379,388)->(1138,800) then
        # navigating in; cross, walk to Rick (seaside HP buffer for the L76-118
        # aggro), interact turn-in -> receive seaweedroll.
        position=(378, 388), hit_points=SEASIDE_HP,
        inventory=[{"key": "cookedshrimp", "count": 5}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_SEASIDE],
    ),
    # ── Lane C: Rick's stage 2 -> 3 — cross the quest door to Lena ──
    "r6_door": dict(     # Rick's stage 2, seaweedroll at the quest door (conftest R6)
        # THE Lena seed. Validated against the successful DeepSeek/Claude Rick's
        # completions: navigate ONTO the stage-2 door (260,229) -> teleports to
        # (425,909) -> navigate the maze to Lena (455,924) -> interact turn-in ->
        # 1987 gold. (260,230) is the overworld entry tile those trajectories
        # staged at, and it seeds (sticks — verified live run_20260717_165842).
        # There is deliberately no "seed at Lena" milestone: Lena's region is
        # door-gated, a position write there resets to SPAWN_POINT, and crossing
        # (260,229) is the only way in.
        position=(260, 230), hit_points=100,
        inventory=[{"key": "seaweedroll", "count": 1}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S2],
        skills=[HP_LATE],
    ),
}

# Lane C is all r6_door: crossing the stage-2 quest door to Lena is the scarcest
# completion (the successful DeepSeek/Claude paths navigate ONTO (260,229)), so
# all three agents attempt it per run.
LANES = {
    "A": {"qwengrinder": "h2_tomato", "qwencompletionist": "h2_paprika", "qwenexplorer": "r2_accept"},
    "B": {"qwengrinder": "r3_fishing", "qwencompletionist": "r4_cook", "qwenexplorer": "r5_turnin"},
    "C": {"qwengrinder": "r6_door", "qwencompletionist": "r6_door", "qwenexplorer": "r6_door"},
}


def main() -> None:
    assert_quests_not_reserved(
        (q["key"] for milestone in MILESTONES.values() for q in milestone["quests"]),
        use="training_seed",
    )
    lane_set = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    if lane_set not in LANES:
        sys.exit(f"unknown lane set {lane_set!r} (expected {'/'.join(LANES)})")
    for username, mid in LANES[lane_set].items():
        kw = dict(MILESTONES[mid])
        kw.setdefault("inventory", list(STARTER_KIT))
        seeded = seed_player(username, **kw)
        quests = {q["key"]: q["stage"] for q in seeded["quests"]}
        print(f"  seeded {username}: milestone={mid} pos={kw['position']} quests={quests}")


if __name__ == "__main__":
    main()
