"""Seed the three Qwen agents at Core-3 milestone states for OPD round-3 bucket-B collection.

Generalizes seed_herbalist_wall.py to the full unsolved ladder. Two lane sets
(one per collection run), each assigning a different e2e-verified milestone to
each personality username. Seed kwargs replicate
tests/e2e/quests/reachability/conftest.py playthrough_seed_kwargs (H4/H6/R3-R6)
— positions and item shapes are e2e-verified playable — except the Health
buffer, which uses realistic mid-run XP instead of the test suite's huge pad so
survival dynamics in the collected rollouts stay representative.

Usage:
  python3 scripts/opd/seed_milestones.py A    # Herbalist stage-2 x2 + Rick's fishing
  python3 scripts/opd/seed_milestones.py B    # Rick's cook-decision / turn-in / door
  KAETRAM_SEED_MILESTONES=A ./scripts/restart-agent.sh --qwen-sft 3 ...   # via launcher
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.e2e.helpers.seed import STARTER_KIT, seed_player  # noqa: E402

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

MILESTONES = {
    # ── Run A ──
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
    "r3_fishing": dict(  # Rick's stage 1 at the shrimp fishing spot, pole equipped (conftest R3)
        position=(324, 360), hit_points=100,
        equipment=[{"type": 4, "key": "fishingpole", "count": 1, "ability": -1, "abilityLevel": 0}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_MID, {"type": 15, "experience": 11_500}],
    ),
    # ── Run B ──
    "r4_cook": dict(     # Rick's stage 1, rawshrimp x5 in hand — the cook-decision state (conftest R4)
        # Run-A lesson: a Mudwich seed leaves the cooking station (323,892)
        # ~700 tiles away — craft_item's auto-walk fails with "Could not reach
        # cooking station" (observed twice from agent_2 with PERFECT call args).
        # Seed at the station's own adjacent stand tile (from the tool's error
        # payload) so the lane exercises the cook decision, not the long haul.
        position=(323, 893), hit_points=100,
        inventory=[{"index": 0, "key": "knife", "count": 1},
                   {"key": "rawshrimp", "count": 5}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_LATE, {"type": 8, "experience": 1_000},   # Fishing
                {"type": 9, "experience": 200}],              # Cooking
    ),
    "r5_turnin": dict(   # Rick's stage 1, cookedshrimp x5 at Rick (conftest R5)
        position=(1088, 832), hit_points=100,
        inventory=[{"key": "cookedshrimp", "count": 5}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S1],
        skills=[HP_LATE],
    ),
    "r6_door": dict(     # Rick's stage 2, seaweedroll at the door entry (conftest R6)
        position=(260, 230), hit_points=100,
        inventory=[{"key": "seaweedroll", "count": 1}],
        quests=[FORESTING_DONE, HERBALIST_DONE, RICKS_S2],
        skills=[HP_LATE],
    ),
}

LANES = {
    "A": {"qwengrinder": "h2_tomato", "qwencompletionist": "h2_paprika", "qwenexplorer": "r3_fishing"},
    "B": {"qwengrinder": "r4_cook", "qwencompletionist": "r5_turnin", "qwenexplorer": "r6_door"},
}


def main() -> None:
    lane_set = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    if lane_set not in LANES:
        sys.exit(f"unknown lane set {lane_set!r} (expected A or B)")
    for username, mid in LANES[lane_set].items():
        kw = dict(MILESTONES[mid])
        kw.setdefault("inventory", list(STARTER_KIT))
        seeded = seed_player(username, **kw)
        quests = {q["key"]: q["stage"] for q in seeded["quests"]}
        print(f"  seeded {username}: milestone={mid} pos={kw['position']} quests={quests}")


if __name__ == "__main__":
    main()
