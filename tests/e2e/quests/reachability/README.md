# Reachability tests — Core 2-5

These tests answer **"can a vanilla post-tutorial player physically complete
this quest without exploits or skipped prerequisites?"** Each discrete step
of a Core quest is a separate test with a minimal seed — typically just
Mudwich spawn + the tutorial starter kit.

They complement but do not replace:

| Tier | Location | Seeds | Proves |
|---|---|---|---|
| Stage | `../test_0{1..5}_*.py` | Everything pre-satisfied | Quest runtime transitions work |
| Integration | `../integration/` (planned) | Moderate, re-seeds per phase | End-to-end playthrough completes |
| **Reachability** | **this dir** | **Minimal — Mudwich + starter kit** | **Vanilla player CAN physically play the quest** |

## Coverage — 30 tests across 4 files

### Herbalist's Desperation (6 tests)
| ID | What | Marker |
|---|---|---|
| H1 | Overland walk Mudwich → Herbalist (~270 tiles) | `slow` |
| H2 | Accept quest via `interact_npc` | |
| H3 | Foraging 1→5 from Mudwich blueberry bushes | `slow` |
| H4 | Gather tomato at Foraging Lv15 | |
| H5 | Gather paprika at Foraging Lv25 | |
| H6 | Full turn-in chain with seeded items | |

### Rick's Roll (7 tests)
| ID | What | Marker |
|---|---|---|
| R1 | Overland walk Mudwich → Rick (~1500 tiles) | `slow` |
| R2 | Accept quest | |
| R3 | Fish shrimp at nearest spot | |
| R4 | Cook shrimp via `craft_item` | |
| R5 | 5× cookedshrimp turn-in → seaweedroll | |
| R6 | Stage-2 quest door teleport | |
| R7 | Deliver to Lena → 1987 gold | |

### Arts and Crafts (9 tests)
| ID | What | Marker |
|---|---|---|
| A1 | Overland walk Mudwich → Babushka door | `slow` |
| A2 | Door teleport (483,275) → (702,613) | |
| A3 | Accept quest | |
| A4a | Mine beryl with **bronzeaxe** (starter kit) | |
| A4b | Mine beryl with **bronzepickaxe** (control) | |
| A5 | Craft string from bluelily | |
| A6 | Fletch logs → sticks → bowlmedium | |
| A7 | Farm mushroom1 from goblins during quest | `slow` |
| A8 | Cook stew + final turn-in | |

### Sea Activities (8 tests)
| ID | What | Marker |
|---|---|---|
| S1 | Overland walk Mudwich → Water Guardian (~680 tiles) | `slow` |
| S3 | Kill Water Guardian at lvl-35 combat | |
| S4 | Warp undersea after `waterguardian` achievement | |
| S5 | Dialogue chain Sponge↔Pickle stages 0→4 | |
| S6 | Arena door teleport | |
| **S7** | Picklemob fight with **realistic mid-route gear** | `slow` |
| S7' | Picklemob fight with end-game gear (control) | `slow` |
| S8 | Final turn-in chain → 10000 gold | |

> **S7 is the critical diagnostic.** If it passes, Sea Activities is
> genuinely playable by a fresh route agent. If it fails while S7' passes,
> Core 5 must formally acknowledge Stage 4 requires a seeded checkpoint.

## Running

```bash
# Fast reachability suite (excludes slow overland walks + combat grinds):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v

# Full reachability audit (includes 15-30 min walk + combat tests):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m reachability -v
```

## Debugging a failed test

These tests exercise a lot of async game state, and failures can come from
the MCP tool layer, the game world, OR the test itself. Flip `KAETRAM_DEBUG=1`
to enable full trace logging — designed to be **temporary** and easy to
strip out once the question is answered.

```bash
# Enable all debug streams on a single test:
DISPLAY=:99 KAETRAM_DEBUG=1 KAETRAM_NAV_DEBUG=1 \
    pytest tests/e2e/quests/reachability/test_ricksroll_steps.py::test_r1_navigate_mudwich_to_rick \
    -v -s

# Read the per-test JSONL trace afterwards:
jq . sandbox/niral/reachability_logs/test_r1_navigate_mudwich_to_rick.jsonl
```

### What each flag gives you

| Flag | What changes |
|---|---|
| `KAETRAM_DEBUG=1` | MCP server logs every tool call with args + result payload preview. `navigate` also logs `pathfinding: bfs/linear_fallback`, `waypoints_count`, `total_distance`, `error`. Also: per-test `TestDebugLog` fixture writes JSONL trace to `sandbox/<slot>/reachability_logs/<test_name>.jsonl` (only for tests that request the `test_debug` fixture — currently H1, R1, S1, S7). Compact stderr summary at test end. |
| `KAETRAM_NAV_DEBUG=1` | `navigate_long` prints per-hop decisions to stderr: "hop 0: (188,157) -> (238,157) [remaining: dx=145, dy=124]" and "hop 0: ended at (232,149) reason=at_hop moved=52 elapsed=26.3s". |

### JSONL trace anatomy

Each line is one event. Useful for post-hoc analysis with `jq`:

| Event | When emitted | Key fields |
|---|---|---|
| `test_start` | test begins | `test` |
| `navigate_long_start` | start of a navigate_long call | `target`, `max_step`, `max_hops` |
| `snapshot` | observe payload captured | `pos`, `nav_status`, `nav_stuck_reason`, `hp`, `entities_nearby` |
| `action` | MCP tool called | `tool`, `args`, `ok`, `preview`, `error` |
| `hop_end` | a navigate_long hop finished | `hop`, `start`, `target`, `end`, `moved`, `reason`, `elapsed_s` |
| `stuck_check` | STUCK_CHECK trailer on a stall | `hop`, `stuck` (full payload) |
| `test_end` | teardown | `status`, `elapsed_s`, `tool_calls`, `tool_errors`, `first_pos`, `last_pos` |

Reason codes for `hop_end`: `at_hop` (arrived), `nav_arrived` (tool reported arrived), `nav_stuck` (tool reported stuck), `per_hop_timeout` (90s budget exhausted), `no_progress` (no movement for 45s).

### Propagating debug to your own test

Add the `test_debug` fixture and pass it to `navigate_long`:

```python
async def test_my_thing(test_username, test_debug):
    ...
    await navigate_long(session, target_x=X, target_y=Y, debug=test_debug)
    # or manually:
    test_debug.action("attack", args={"mob_name": "Goblin"}, ok=True)
    test_debug.snapshot("pre_fight", obs_payload)
    test_debug.event("custom", info="whatever")
```

## Empirically verified on 2026-04-24

- ✅ H1 (Mudwich→Herbalist 270 tiles): 110s, 5 hops
- ✅ H2 (accept Herbalist quest): 13s
- ✅ R2 (accept Rick's Roll): 13s
- ✅ A3 (accept A&C): 14s
- ✅ S4 (warp undersea): 17s

Remaining tests are written and collected but not yet run. The big ones
(R1, A1, S1, S7) will surface most of the hidden gotchas.

## Why `navigate_long` exists

Kaetram's MCP `navigate` tool internally uses BFS capped at 150-tile radius
(`state_extractor.js:1265`). Beyond that it falls back to naive linear
interpolation which walks straight into walls. Cross-region tests must
therefore chain `navigate` calls at ≤90-tile hops. `navigate_long` (in
`conftest.py`) implements this with:

- Per-hop target picked along the longer axis remainder.
- Polling on `navigation.status in {arrived, stuck}` OR position-near-hop.
- Re-plans on per-hop timeout (90s), no-progress timeout (45s), or stuck.

Tuning knobs: `max_step`, `max_hops`, `per_hop_timeout_s`,
`no_progress_timeout_s`. Set `KAETRAM_NAV_DEBUG=1` to log per-hop decisions
to stderr.
