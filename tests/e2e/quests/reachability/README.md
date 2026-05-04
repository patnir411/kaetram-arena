# Reachability tests — Core 3

These tests answer **"can the agent complete this discrete quest step from
the cumulative playthrough state it realistically has at that point?"**
Each step is a separate test seeded via `playthrough_seed_kwargs(step_id)`
in `conftest.py`, which layers prior-quest rewards, accumulated skill XP,
achievements, and gear/gold on top of a vanilla post-tutorial baseline
(Mudwich spawn + tutorial starter kit + 3039 HP / 15M Health-XP buffer
so nav-only tests don't fail on stray aggro).

A separate static layer (`test_static_world_connectivity.py`) verifies
the prompt-knowledge claims about quest coords against the offline
`world.json` collision data — runs in <1s without a game server.

## Coverage

### Herbalist's Desperation (7 tests)
| ID | What | Marker |
|---|---|---|
| H1 | Overland walk Mudwich → Herbalist (~270 tiles) | `slow` |
| H2 | Accept quest via `interact_npc` | |
| H3 | Foraging 1→5 from Mudwich blueberry bushes | `slow` |
| H4 | Gather tomato at Foraging Lv5 | |
| H5 | Gather paprika at Foraging Lv5 | |
| H6 | Full turn-in chain with seeded items | |
| H7 | Gather bluelily at Foraging Lv5 (all three Herbalist nodes share a single Lv5 gate) | |

### Rick's Roll (7 tests)
| ID | What | Marker |
|---|---|---|
| R1 | Overland walk Mudwich → Rick (~1500 tiles) | `slow` |
| R2 | Accept quest | |
| R3 | Fish shrimp at nearest spot | |
| R4 | Cook shrimp via `craft_item` | |
| R5 | 5× cookedshrimp turn-in → seaweedroll | |
| R6 | Stage-2 quest door teleport + deliver to Lena → 1987 gold | |
| R7 | Negative: Lena rejects rawshrimp (only seaweedroll completes stage 2) | |

### Static world connectivity (6 tests, no game server)
| What |
|---|
| Forester (216, 114) vanilla-reachable from Mudwich |
| Herbalist (333, 281) vanilla-reachable |
| Rick door 1025 entry (379, 388) vanilla-reachable |
| Full Rick's Roll pin-chain coords vanilla-reachable |
| Recommended Paprika coords vanilla-reachable; (298, 300) disjoint-pocket regression guard |
| Paprika cluster has ≥100 walkable cells from Mudwich |

## Seed model

Every step seeds the cumulative state an agent realistically has when
arriving at that step under `prompts/game_knowledge.md`'s suggested play
order (Foresting → Herbalist → Rick's Roll). Prior-quest rewards,
accumulated skill XP, achievements, and gear/gold are all layered on top
of the vanilla post-tutorial baseline.

Seeds are centralized in `playthrough_seed_kwargs(step_id)` in
`conftest.py`.

Every test carries the `@reachability` marker.

## Running

```bash
# Fast subset (excludes slow overland walks + combat grinds):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v

# Full reachability audit (includes 15-30 min walk + combat tests):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m reachability -v

# Static-only (no game server, ~1s):
.venv/bin/pytest tests/e2e/quests/reachability/test_static_world_connectivity.py -v
```

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
Some "obvious" overland walks are physically impossible. Always verify
connectivity via offline BFS over `world.json` before writing a long-walk
test. The static layer (`test_static_world_connectivity.py`) does this
proactively for every Core 3 quest's agent-visible coords.

### 5. Probabilistic gather/combat needs tolerant loops
Single-attempt `gather` calls return zero items frequently — the rock or
fishing spot has a chance miss. `gather_until_count` in
`tests/e2e/quests/conftest.py` keeps trying instead of asserting on the
first miss. Per-attack damage is also small; goblins (90 HP) take 5–15
swings to kill.

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
