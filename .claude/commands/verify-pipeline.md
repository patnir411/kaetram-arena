---
description: Trigger when the user asks to verify the pipeline, check health, confirm data is flowing, asks "is everything working", "check the pipeline", "is game_state updating", "are steps being recorded", or wants to inspect a training record.
---

Run a full pipeline health check and print a pass/fail report.

**Step 1 — detect project root:**
```bash
PROJECT_DIR=$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || pwd)
```

**Step 2 — run all checks:**

**Check A — game_state.json freshness (multi-agent sandboxes):**
```python
import json, time, os, glob
# Check multi-agent sandboxes first, fall back to single-agent path
paths = sorted(glob.glob("/tmp/kaetram_agent_*/state/game_state.json"))
if not paths:
    single = f"{PROJECT_DIR}/state/game_state.json"
    if os.path.exists(single):
        paths = [single]
if not paths:
    print("FAIL: no game_state.json found in any agent sandbox")
else:
    for path in paths:
        age = time.time() - os.path.getmtime(path)
        d = json.load(open(path))
        entity_count = len(d.get("nearby_entities", []))
        agent = os.path.basename(os.path.dirname(os.path.dirname(path)))
        print(f"{'PASS' if age < 300 else 'STALE'}: {agent} game_state.json age={age:.0f}s, entities={entity_count}")
```


**Check C — dataset sessions exist:**
```bash
ls "$PROJECT_DIR/dataset/" 2>/dev/null
```

**Check D — read the latest training record and pretty-print it:**
```python
import json, os, glob

dataset_dir = f"{PROJECT_DIR}/dataset"
sessions = sorted(glob.glob(f"{dataset_dir}/*/steps.jsonl"))
if not sessions:
    print("FAIL: no steps.jsonl found in dataset/")
else:
    latest = sessions[-1]
    lines = open(latest).readlines()
    if not lines:
        print(f"FAIL: {latest} is empty")
    else:
        last_step = json.loads(lines[-1])
        print(f"PASS: {latest} — {len(lines)} steps")
        print(f"\nLatest training record:")
        print(f"  session:    {last_step.get('session')}")
        print(f"  step:       {last_step.get('step')}")
        print(f"  timestamp:  {last_step.get('timestamp')}")
        print(f"  screenshot: {last_step.get('screenshot')}")
        print(f"  reward:     {last_step.get('reward')}")
        print(f"  action:     {last_step.get('action')}")
        state = last_step.get('state', {})
        nearby = state.get('nearby_entities', [])
        print(f"  state.nearby_entities: {len(nearby)} entities")
        if nearby:
            print(f"    first: {nearby[0]}")
        print(f"  state.last_xp_event: {state.get('last_xp_event')}")
        print(f"  state.last_combat:   {state.get('last_combat')}")
```

**Step 3 — print a final pass/fail table:**

| Check | Result |
|-------|--------|
| game_state.json exists | PASS/FAIL |
| game_state.json fresh | PASS/STALE |
| dataset/ has sessions | PASS/FAIL |
| latest steps.jsonl non-empty | PASS/FAIL |
| latest record has entity data | PASS/FAIL |

**Step 4 — for each FAIL, print the exact fix command:**
- Missing game_state.json → Agent needs to run and complete an observe step
