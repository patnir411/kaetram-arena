# Quest e2e tests — reachability

The quest test surface has a single tier: `reachability/`. Each test is a
per-step quest test, seeded with the cumulative playthrough state an agent
has at that step per `prompts/game_knowledge.md`. Covers the Core 3 quests
the agent is scored on; Foresting is exercised via `tests/e2e/game/` rather
than here.

## Layout

```
tests/e2e/quests/
├── reachability/                            ← Per-step quest reachability audit. See reachability/README.md.
│   ├── test_herbalists_steps.py             H1–H7 (Herbalist's Desperation)
│   ├── test_ricksroll_steps.py              R1–R7 (Rick's Roll)
│   ├── test_static_world_connectivity.py    Offline `world.json` BFS guards on quest-relevant coords
│   ├── conftest.py                          playthrough_seed_kwargs, navigate_long, debug
│   └── debug.py                             Per-test JSONL trace logger (autouse)
│
└── conftest.py       ← Shared quest assertion helpers (traverse_door, gather_until_count, ...)
```

Behavioral / fact-style game tests (item catalog, world NPC coords, navigation
regressions, stackability, etc.) live under `tests/e2e/game/`.

## Running

```bash
# Fast subset (excludes overland walks + combat grinds):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v

# Full audit:
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m reachability -v

# Static-only (no game server, ~1s — runs the world.json connectivity guards):
.venv/bin/pytest tests/e2e/quests/reachability/test_static_world_connectivity.py -v
```

See `reachability/README.md` for per-step coverage tables and the seed
accumulation map.
