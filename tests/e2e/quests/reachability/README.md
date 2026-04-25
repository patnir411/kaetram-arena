# Reachability tests — Core 2-5

These tests answer **"can a vanilla post-tutorial player physically complete
this quest without exploits or skipped prerequisites?"** Each discrete step
of a Core quest is a separate test with a minimal seed — typically just
Mudwich spawn + the tutorial starter kit (plus a 3039 HP / 15M Health-XP
buffer so nav-only tests don't fail on stray aggro).

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
| A1 | Mudwich → Babushka door via **warp Aynor + door 463** | `slow` |
| A2 | Door teleport (483,275) → (702,613) | |
| A3 | Accept quest | |
| A4a | Confirm bronzeaxe **CANNOT** mine beryl (control) | |
| A4b | Mine beryl with bronzepickaxe | |
| A5 | Craft string from bluelily | |
| A6 | Fletch 4 sticks → 1 bowlmedium | |
| A7 | Farm mushroom1 from goblins (xfail — drop-rate math) | `slow` `xfail` |
| A8 | Cook stew + final turn-in | |

> **A1 is not pure overland.** Mudwich (188,157) and the Babushka exterior
> (483,276) are in disjoint walkable regions per `world.json` static
> collision data. The only in-game route uses the **Aynor warp** (gated
> behind the `ancientlands` quest, which the seed pre-finishes) and an
> unmarked door at (406,292) → (433,270) inside the Babushka exterior.
> A1 verifies that route works end-to-end.

> **A4a is inverted.** Bronze axes do not mine beryl in Kaetram — A4a
> asserts the player gets **zero** beryl after 5 attempts. Together with
> A4b (bronzepickaxe → beryl) this documents the tool gating.

> **A7 is xfail.** Goblin loot math from Kaetram-Open data: mushrooms
> droptable rolls at 6%, then 1-of-8 mushrooms = ~0.75% chance of
> mushroom1 per kill. The audit's "~5 kills" estimate was wrong. Combat
> path verified (kills land), so A7 stays as xfail to flag if Kaetram
> rebalances drops.

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

## Suite score (post-2026-04-25 fixes)

```
Baseline (2026-04-24):     15 PASS / 15 FAIL / 0 XFAIL
Current   (2026-04-25):    17 PASS / 12 FAIL / 1 XFAIL  (A7 documented xfail)
With FLETCHING enum fix:  ~20 PASS /  9 FAIL / 1 XFAIL  (expected)
```

Confirmed wins this iteration: A1, A2, A4a, A4b, A6.

## Common pitfalls (read before debugging)

### 1. Skill enum constants must match `Modules.Skills`
`Kaetram-Open/packages/common/network/modules.ts` defines the enum, and
seeded `skills=[{type: N, experience: ...}]` uses the integer index.
**Wrong indices silently grant XP to the wrong skill** and recipes/forages
gated above level 1 fail server-side with empty `inventory_delta`.

| Skill | Index | Skill | Index |
|---|---|---|---|
| Lumberjacking | 0 | Cooking | 9 |
| Accuracy | 1 | Smithing | 10 |
| Archery | 2 | Crafting | 11 |
| Health | 3 | Chiseling | 12 |
| Magic | 4 | **Fletching** | **13** |
| Mining | 5 | Smelting | 14 |
| Strength | 6 | **Foraging** | **15** |
| Defense | 7 | Eating | 16 |
| Fishing | 8 | Loitering | 17 |
| | | Alchemy | 18 |

The bolded ones bit us — the test files originally had Fletching=10 and
Foraging=12.

### 2. Seed positions must be on truly walkable tiles
The server's `verifyCollision` rejects login at colliding tiles and
respawns the player at `SPAWN_POINT = (328,892)`. If a test reports
`pos=(328,892)` despite a different seed coordinate, the seed tile is
colliding. Check it offline against `Kaetram-Open/packages/server/data/map/world.json`:

```python
import json
with open('packages/server/data/map/world.json') as f:
    world = json.load(f)
W = world['width']
data = world['data']
collisions = set(world['collisions'])
def colliding(x, y):
    d = data[W*y + x]
    if not d: return True
    tiles = [d] if isinstance(d, int) else d
    FLIP = (0x80000000 | 0x40000000 | 0x20000000)
    return any(((t & ~FLIP if t & FLIP else t) in collisions) for t in tiles)
```

The server also treats Resource entities (rocks, trees) as colliding via
the entity grid, so seeding *exactly on* a beryl/copper rock fails — pick
the adjacent walkable tile.

### 3. Doors are flagged collision in the map grid
The game's A* refuses to plan onto doors. The agent's `move`/`navigate`
pathing patches `map.grid[y][x]` and `map.data[idx]` for door targets
before calling `p.go()`, then restores them. This is in
`state_extractor.js` (`__moveTo`, `__navigateTo` short-path branch).

If a `traverse_door` call lands `move(doorX, doorY)` with `No path found,
distance: 1`, that patch regressed.

### 4. Map regions can be disjoint
Some "obvious" overland walks are physically impossible. Mudwich →
Babushka exterior is the canonical example. Always verify connectivity
via offline BFS over `world.json` before writing a long-walk test, and
fall back to `warp` + door chains where needed (see A1 for the pattern).

### 5. Probabilistic gather/combat needs tolerant loops
Single-attempt `gather` calls return zero items frequently — the rock or
fishing spot has a chance miss. `gather_until_count` in
`tests/e2e/quests/conftest.py` keeps trying instead of asserting on the
first miss. Per-attack damage is also small; goblins (90 HP) take 5–15
swings to kill. A7 demonstrates the swing-loop pattern.

## Debugging a failed test

These tests exercise a lot of async game state, and failures can come from
the MCP tool layer, the game world, OR the test itself. Flip `KAETRAM_DEBUG=1`
to enable full trace logging — designed to be **temporary** and easy to
strip out once the question is answered. The Tests tab on the dashboard
sets this automatically when the Debug toggle is on (default).

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
| `KAETRAM_DEBUG=1` | MCP server logs every tool call with args + result payload preview. `navigate` also logs `pathfinding: bfs/bfs_failed`, `waypoints_count`, `total_distance`, `error`. Per-test `TestDebugLog` fixture writes JSONL trace to `sandbox/<slot>/reachability_logs/<test_name>.jsonl` (autouse — every reachability test gets one). Compact stderr summary at test end. |
| `KAETRAM_NAV_DEBUG=1` | `navigate_long` prints per-hop decisions to stderr. |

### JSONL trace anatomy

Each line is one event. Useful for post-hoc analysis with `jq`:

| Event | When emitted | Key fields |
|---|---|---|
| `test_start` | test begins | `test` |
| `navigate_long_start` | start of a navigate_long call | `target`, `max_step`, `max_hops` |
| `snapshot` | observe payload captured | `pos`, `nav_status`, `nav_stuck_reason`, `hp`, `entities_nearby` |
| `action` | MCP tool called | `tool`, `args`, `ok`, `preview`, `error` |
| `hop_end` | a navigate_long hop finished | `hop`, `start`, `target`, `end`, `moved`, `reason`, `elapsed_s` |
| `same_cluster_detected` | navigate_long detected position cluster | `hop`, `cluster`, `recent_starts` |
| `oscillation_detected` | distance regressed across hops | `hop`, `recent_distances`, `cluster_span`, `progress_gain` |
| `escape_attempt` | escape-nav attempt away from a stuck cluster | `hop`, `target` |
| `position_reached` | wait_for_position succeeded | `target`, `actual`, `attempt` |
| `inventory_reached` | wait_for_inventory_count succeeded | `item_key`, `actual`, `attempt` |
| `gather_progress` | gather_until_count round | `current`, `target`, `attempts_remaining` |
| `craft_succeeded` | craft_recipe got `crafted: true` | `skill`, `recipe_key`, `count` |
| `door_attempt_failed` | traverse_door retry on a different approach tile | `attempt`, `door`, `exit` |
| `test_end` | teardown | `status`, `elapsed_s`, `tool_calls`, `tool_errors`, `first_pos`, `last_pos` |

Reason codes for `hop_end`: `at_hop` (arrived), `nav_arrived` (tool reported arrived), `nav_stuck` (tool reported stuck), `per_hop_timeout` (90s budget exhausted), `no_progress` (no movement for the configured timeout, default 10s).

### Propagating debug to your own test

The fixture is autouse — you don't need to request it. Grab the active
log via `get_current_test_debug()` if you want to add custom events:

```python
from tests.e2e.quests.reachability.debug import get_current_test_debug

async def test_my_thing(test_username):
    debug = get_current_test_debug()
    ...
    debug.action("attack", args={"mob_name": "Goblin"}, ok=True)
    debug.snapshot("pre_fight", obs_payload)
    debug.event("custom", info="whatever")
```

## Why `navigate_long` exists

`navigate` (the MCP tool) wraps `__navigateTo` in `state_extractor.js`
which uses BFS on the client's loaded map regions. BFS retries with
widening radii (80 → 150 → 250 → 400) before giving up; cross-region
walks still benefit from chunking the trip into shorter hops so the
client streams new regions in between.

`navigate_long` (in `conftest.py`) does that chunking and adds:

- Per-hop target picked along the longer axis remainder.
- Polling on `navigation.status in {arrived, stuck}` OR position-near-hop.
- Death fail-fast: aborts if the player is dead at any point.
- Same-cluster + oscillation detection: notices when consecutive hops
  stall at the same position or the distance-to-target regresses, and
  tries an escape direction perpendicular to the main heading.
- Re-plans on per-hop timeout (90s default) or no-progress timeout
  (10s default).
- Failure probes that dump observe + STUCK_CHECK to the debug log when
  the loop gives up.

Tuning knobs: `max_step`, `max_hops`, `arrive_tolerance`,
`per_hop_timeout_s`, `poll_interval_s`, `no_progress_timeout_s`,
`navigate_call_timeout_s`. Set `KAETRAM_NAV_DEBUG=1` to log per-hop
decisions to stderr.
