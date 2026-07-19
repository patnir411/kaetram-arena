"""Seed the three Qwen agents at the Herbalist's Desperation stage-1 wall.

Bucket-B state seeding for OPD round 2: the 4B teacher demonstrably passes this
wall (2/3 agents finished Herbalist 3/3 in run_20260607_190204) while every 2B
policy stalls at stage 1 — but rollout data only queries the teacher at states
the student visits. Seeding the student AT the wall changes the visitation
distribution d^πS, not the loss: the teacher is still only scored on states the
student actually plays through.

The seeded state mirrors what a mid-run agent naturally has at this point
(verified against run_20260610_140358 logs): Foresting finished, Herbalist
accepted (stage 1, needs 3 bluelily), Foraging level 5 (the bluelilybush
levelRequirement — foraging.json), starter kit + ironaxe (Foresting reward),
standing one tile south of the lily cluster (e2e-verified walkable).

XP values use the RuneScape curve from Kaetram's loader.ts:
level 5 = 387..510 cumulative XP -> 450; health ~level 9 -> 1000.

Usage:
  python3 scripts/opd/seed_herbalist_wall.py          # seed all three Qwen names
  KAETRAM_SEED_WALL=herbalist1 ./scripts/restart-agent.sh --qwen-sft 3 ...  # via launcher
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.e2e.helpers.seed import STARTER_KIT, seed_player  # noqa: E402
from heldout_guard import assert_quests_not_reserved  # noqa: E402

QWEN_NAMES = ("qwengrinder", "qwencompletionist", "qwenexplorer")

# One tile south of the bush at (278, 250) — the bush tile itself is collision
# and the server falls back to global spawn for non-walkable seed positions.
# (278, 251) is the e2e-verified walkable spot (reachability conftest H-steps).
LILY_CLUSTER = (278, 251)

WALL_SEED = dict(
    position=LILY_CLUSTER,
    hit_points=100,
    inventory=list(STARTER_KIT) + [{"index": 5, "key": "ironaxe", "count": 1}],
    quests=[
        {"key": "foresting", "stage": 3, "subStage": 0, "completedSubStages": []},
        {"key": "herbalistdesperation", "stage": 1, "subStage": 0, "completedSubStages": []},
    ],
    skills=[
        {"name": "foraging", "experience": 450},        # level 5 — bluelily gate
        {"name": "health", "experience": 1000},         # ~level 9
        {"name": "lumberjacking", "experience": 150},   # residue of Foresting
    ],
)


def main() -> None:
    assert_quests_not_reserved(
        (q["key"] for q in WALL_SEED["quests"]),
        use="training_seed",
    )
    for name in QWEN_NAMES:
        seeded = seed_player(name, **WALL_SEED)
        quests = {q["key"]: q["stage"] for q in seeded["quests"]}
        print(f"  seeded {name}: pos={LILY_CLUSTER} quests={quests} "
              f"foraging_xp=450 (lvl 5)")


if __name__ == "__main__":
    main()
