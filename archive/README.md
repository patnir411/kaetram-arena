# Archive

Frozen artifacts kept outside the active codebase. Nothing in here is run by
the test suite, imported by the agent stack, or referenced by docs.

## Why a file ends up here

Some artefacts (test files, scripts, prose) describe quest content that is
not part of the **Core 3** benchmark (`Foresting`, `Herbalist's Desperation`,
`Rick's Roll`). Removing them entirely would erase the work that produced
them; preserving them in `archive/` keeps the record without polluting the
live surface.

## Contents

```
archive/
└── tests/
    ├── quests/
    │   └── reachability/
    │       ├── test_artsandcrafts_steps.py   (per-step playthrough harness)
    │       └── test_seaactivities_steps.py   (per-step playthrough harness)
    └── mcp/
        └── test_craft_item.py                (Crafting-recipe smoke test)
```

## Caveats

- These files **will not run** against current `tests/e2e/helpers/` or
  `tests/e2e/quests/reachability/conftest.py` — the seed map, NPC dict, and
  item list have been trimmed to the Core 3 surface, so imports / fixtures
  used by these tests no longer resolve.
- Pytest does not collect this directory: it lives outside `pytest.ini`'s
  `testpaths = tests` setting.
- Use `git log -- archive/<path>` for the full history of each file.
