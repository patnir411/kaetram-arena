# Quest e2e tests — tier layout

The 5-quest benchmark is the scored objective. The directory layout reflects
that directly: `core/` comes first and maps 1-to-1 to the Core 5 in
`prompts/game_knowledge.md`.

## Layout

```
tests/e2e/quests/
├── core/             ← Scored benchmark. This is the agent's whole job.
│   ├── test_01_foresting.py              Gather 10+10 oak logs → Iron Axe
│   ├── test_02_herbalists_desperation.py Forage blue lily + paprika + tomato (Foraging 25 gate)
│   ├── test_03_ricksroll.py              Fish/cook 5 shrimp → deliver seaweedroll through quest door
│   ├── test_04_artsandcrafts.py          Beryl pendant → bowlsmall → stew (3 stations)
│   └── test_05_seaactivities.py          7-stage courier chain + picklemob kill
│
├── extra/            ← Bonus progression. Not scored; run after Core 5 is green.
│   ├── test_01_desertquest.py            Unlocks crullfield + lakesworld warps
│   ├── test_02_royaldrama.py             10k gold via sewer door
│   ├── test_03_royalpet.py               Deliver 3 books (prereq: Royal Drama)
│   ├── test_04_scientistspotion.py       1-stage Alchemy unlock
│   └── test_05_ancientlands.py           Capstone — needs icesword from Ice Knight (L62)
│
├── bonus/            ← Completable filler. Only meaningful if all 10 above are green.
│   ├── test_anvilsechoes.py              Bronze boots (flavor text lies)
│   ├── test_scavenger.py                 7500 gold (fake shopping list in dialogue)
│   └── test_clamchowder.py               7500 gold (Fishing 10 + Cooking 15 + Fletching 3)
│
├── skip/             ← Upstream-broken. Kept for regression detection only — expected to fail.
│   ├── test_sorcery.py                   Reward item `staff` doesn't exist
│   ├── test_minersquest.py               Circular nisocrock gate
│   └── test_minersquest2.py              Depends on minersquest (impossible)
│
├── reachability/     ← Per-step "can a vanilla player reach this?" audit. See reachability/README.md.
│   ├── test_herbalists_steps.py    H1–H6 (6 tests)
│   ├── test_ricksroll_steps.py     R1–R7 (7 tests)
│   ├── test_artsandcrafts_steps.py A1–A8 (9 tests, 1 xfail)
│   ├── test_seaactivities_steps.py S1–S8 (8 tests)
│   ├── conftest.py                  navigate_long, vanilla seed, debug fixtures
│   └── debug.py                     Per-test JSONL trace logger (autouse)
│
└── conftest.py       ← Shared quest assertion helpers (traverse_door, gather_until_count, ...)
```

## Running

```bash
# Core 5 only — the scored benchmark:
./scripts/bench-core.sh
# or: pytest tests/e2e/quests/core/ -v

# Core + Extra (10 quests):
./scripts/bench-full.sh
# or: pytest tests/e2e/quests/core/ tests/e2e/quests/extra/ -v

# Every completable quest (13 = core + extra + bonus):
pytest tests/e2e/quests/core/ tests/e2e/quests/extra/ tests/e2e/quests/bonus/ -v

# Regression check on the upstream-broken quests:
pytest tests/e2e/quests/skip/ -v

# Reachability audit (30 per-step tests across H/R/A/S):
pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v   # fast subset
pytest tests/e2e/quests/reachability/ -m reachability -v                   # full (~22 min)
```

## Why this layout

The agent prompt (`prompts/system.md` + `prompts/game_knowledge.md`) is
organized around Core / Extra / SKIP tiers. The test tree now mirrors that
exactly, so the scored objective is the first thing a reader sees, and the
broken quests are quarantined instead of interleaved with the real benchmark.

See `prompts/game_knowledge.md` for the canonical tier definitions and
`../../../Kaetram-Open/QUEST_CITATIONS.md` for the source-of-truth runtime
status of every quest on the current upstream tree.
