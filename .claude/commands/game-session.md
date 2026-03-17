---
description: Trigger when the user asks to start the game, launch the stack, check what's running, set up the environment, or says anything like "start everything", "is the game running", "launch kaetram", "check ports". Checks ports 9000/9001/8080, reports a status table, and prints startup commands for any missing services.
---

Check the Kaetram game stack status and guide startup.

**Step 1 — detect project root:**
```bash
PROJECT_DIR=$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || pwd)
```

**Step 2 — run these checks in parallel:**
```bash
lsof -i :9000 -i :9001 -i :8080 2>/dev/null | grep LISTEN
ps aux | grep -E "ws_observer\.py|logger\.py|dashboard\.py" | grep -v grep
ls -la "$PROJECT_DIR/state/game_state.json" 2>/dev/null
```

**Step 3 — print a status table like this:**

| Service | Port | Status |
|---------|------|--------|
| Kaetram game client | 9000 | ✓ running / ✗ down |
| Kaetram game server WS | 9001 | ✓ running / ✗ down |
| ws_observer.py | — | ✓ running / ✗ down |
| logger.py | — | ✓ running / ✗ down |
| dashboard.py | 8080 | ✓ running / ✗ down |

Also check if `state/game_state.json` exists and how old it is (fresh = < 30s).

**Step 4 — if anything is down, print these commands (do NOT run them yourself — user must run each in its own terminal):**

```
# Terminal 1 — Kaetram server (Node 20 required)
./scripts/start-kaetram.sh

# Terminal 2 — WebSocket observer
cd <PROJECT_DIR> && python3 ws_observer.py

# Terminal 3 — Dashboard (optional)
cd <PROJECT_DIR> && python3 dashboard.py

# Terminal 4 — Dataset logger (optional)
cd <PROJECT_DIR> && python3 logger.py

# Terminal 5 — Agent loop (MUST be separate terminal — see gotcha below)
cd <PROJECT_DIR> && ./play.sh
```

**Step 5 — always print this gotcha block regardless of status:**

> ⚠️  **SUBPROCESS DEADLOCK GOTCHA**: `play.sh` MUST be run in a separate terminal.
> Never spawn it as a subprocess of Claude Code. Both processes share the same
> Playwright MCP browser instance and will deadlock — the agent session freezes
> with ~0 CPU, screenshot stops updating, log stays 0 bytes.
> Kill signal: `ps aux | grep "claude -p" | grep -v grep` then `kill <PID>`.

**Step 6 — save status to `$PROJECT_DIR/.claude/commands/game-session/last_run.json`:**
```python
import json, datetime, os
status = {
    "timestamp": datetime.datetime.now().isoformat(),
    "kaetram_9000": <bool>,
    "ws_9001": <bool>,
    "ws_observer": <bool>,
    "logger": <bool>,
    "dashboard_8080": <bool>,
    "game_state_fresh": <bool>
}
os.makedirs(os.path.dirname("$PROJECT_DIR/.claude/commands/game-session/last_run.json"), exist_ok=True)
json.dump(status, open("$PROJECT_DIR/.claude/commands/game-session/last_run.json", "w"), indent=2)
```
