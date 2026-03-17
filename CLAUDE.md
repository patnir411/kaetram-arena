# CLAUDE.md — Kaetram AI Agent

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using Claude Code + Playwright browser automation. Read this before touching anything.

---

## SESSION STARTUP (read this every session)

At the start of every new session, before doing anything else:
1. Read this file (`CLAUDE.md`)
2. Read `session_log.md` (recent decisions and context)
3. Read `.claude/commands/training-summary/history.json` if it exists (reward trends)
4. Only then ask what the user wants to do — never start cold

At the end of every session, update `session_log.md` (under 30 lines).

---

## GOTCHAS

**⚠️  Playwright subprocess deadlock** — `play.sh` MUST be launched from a separate terminal. If you spawn `claude -p` as a subprocess of the current Claude Code session, both processes share the same Playwright MCP browser and deadlock. Symptoms: agent session freezes at ~0 CPU, screenshot stops updating, log file stays 0 bytes. Fix: `ps aux | grep "claude -p" | grep -v grep` then `kill <PID>`.

**⚠️  ws_observer spawn mismatch** — `ws_observer.py` connects as its own guest at the default spawn (~tile 328, 892, Programmer's house). Its `nearby_entities` reflects mobs near *that* position, not near ClaudeBot's actual location. When reasoning about entity coordinates from `game_state.json`, account for this offset. ClaudeBot teleports to Mudwich (188, 157); the ws_observer stays near 328, 892.

**Node.js version** — Kaetram requires Node 16/18/20. Node 24/25 crashes on startup (uWS.js incompatibility). Always `nvm use 20` before starting the server.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial that can't be bypassed even with `TUTORIAL_ENABLED=false`. Immediately after login, send `/teleport 188 157` in chat to jump to Mudwich village center.

**Port conflicts** — If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill all node processes first.

**yarn build required** — After cloning, `yarn start` alone fails. Run `yarn build` first.

---

## SESSION STARTUP ORDER

Run each in its own terminal, in order:

1. **Terminal 1 — Kaetram server** (Node 20 required)
   ```bash
   ./scripts/start-kaetram.sh
   ```

2. **Terminal 2 — WebSocket observer**
   ```bash
   python3 ws_observer.py
   ```

3. **Terminal 3 — Dashboard** (optional)
   ```bash
   python3 dashboard.py
   ```

4. **Terminal 4 — Dataset logger** (optional)
   ```bash
   python3 logger.py
   ```

5. **Terminal 5 — Agent loop** ⚠️ MUST be a separate terminal, never a subprocess
   ```bash
   ./play.sh
   ```

---

## CURRENT STATUS

**Last updated:** 2026-03-17

| PR | Title | Status |
|----|-------|--------|
| PR 1 | ws_observer.py + 21 unit tests | ✅ merged |
| PR 2 | logger.py dataset recording | ✅ merged |
| PR 3 | Inject game_state.json into session prompt | ✅ verified live, ready to merge |
| PR 4 | Skills system + CLAUDE.md overhaul | 🔄 in progress (this session) |

**Verified live (2026-03-17):** PR3 prompt injection confirmed — `nearby_entities` present in prompt, ClaudeBot in active combat with Rat (HP 12/20), +5 XP event captured in game_state.json.

**Next:** Merge PR3, merge PR4, then consider making ws_observer follow ClaudeBot's position instead of staying at default spawn.

**Blocked:** Nothing currently blocked.

---

## What the system does

```
play.sh → Claude Code (this process) → Playwright MCP → browser @ localhost:9000
                                                              ↓
                                               ws_observer.py → state/game_state.json
                                               logger.py      → dataset/session_N/steps.jsonl
```

- `play.sh` — infinite loop that runs Claude Code sessions (25 turns each, then 10s pause)
- `ws_observer.py` — separate Python process; connects to Kaetram WS on port 9001, writes `state/game_state.json` with nearby entities, combat events, XP
- `logger.py` — separate Python process; watches screenshot mtime, records (frame, state, action, reward) per step into `dataset/`
- `dashboard.py` — live web dashboard at port 8080

## Starting the stack

```bash
# Terminal 1 — Kaetram game server (Node 20 required — see gotchas)
# Use scripts/start-kaetram.sh — handles both Homebrew and manual nvm installs
./scripts/start-kaetram.sh
# or manually:
export NVM_DIR="$HOME/.nvm" && source "$(brew --prefix nvm)/nvm.sh" && nvm use 20
cd ~/projects/Kaetram-Open
ACCEPT_LICENSE=true SKIP_DATABASE=true yarn start

# Terminal 2 — WebSocket observer
python3 ws_observer.py

# Terminal 3 — Dataset logger (optional)
python3 logger.py

# Terminal 4 — Agent loop
./play.sh
```

## Ports

| Port | What |
|------|------|
| 9000 | Kaetram game client (HTTP + WebSocket for client assets) |
| 9001 | Kaetram game server WebSocket (ws_observer connects here) |
| 8080 | Dashboard |

## Key files

| File | Purpose |
|------|---------|
| `prompts/system.md` | System prompt Claude reads every session |
| `state/progress.json` | Written by Claude each session — carries state across sessions |
| `state/screenshot.png` | Written by Claude via Playwright — current game view |
| `state/game_state.json` | Written by ws_observer — nearby entities, combat, XP (gitignored) |
| `logs/session_N_*.log` | Claude Code JSONL session logs |
| `dataset/session_N/steps.jsonl` | Training records (screenshot path, state, action, reward) |

## Skills (slash commands)

Three custom skills live in `.claude/commands/`. Use them via `/game-session`, `/verify-pipeline`, `/training-summary`.

| Skill | When to trigger |
|-------|----------------|
| `/game-session` | User wants to start/check the stack, asks what's running, says "launch the game" |
| `/verify-pipeline` | User wants to confirm data is flowing, check health, inspect a training record |
| `/training-summary` | User asks about dataset stats, reward trends, "how is training going" |

---

## Kaetram gotchas (hard-won)

**Node.js version**: Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup. Always `nvm use 20`.

**Tutorial-gated spawn**: New guest players spawn in the Programmer's house. The exit requires completing a 16-stage tutorial quest. Even with `TUTORIAL_ENABLED=false`, the check still blocks. Workaround: send `/teleport 188 157` in chat immediately after login to jump to Mudwich village center.

**Key coordinates**:
- Mudwich village center: `188, 157` (outdoor starting area, use this)
- Default spawn: `328, 892` (Programmer's house — stuck behind tutorial)

**Port conflicts**: If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill everything first.

**yarn build required**: After cloning, `yarn start` alone fails ("Cannot find module dist/main.js"). Must run `yarn build` first.

## Playwright gotchas

**Screenshot paths must be absolute.** Relative paths cause Playwright MCP to navigate the browser to the path as a URL, losing the game page.

**WASD is hold-to-move.** Use `keyboard.down('w')` + wait + `keyboard.up('w')`. Tap = no movement.

**Keep all actions in `browser_run_code` blocks** to avoid browser page garbage collection between tool calls.

## WebSocket protocol (ws_observer.py)

Packets are JSON. Server sends `[[packetId, data], ...]` (outer array batches multiple packets). Packets with sub-opcodes: `[packetId, subOpcode, data]` (3 elements).

Handshake flow:
1. Server → `[[0, null]]` (Connected)
2. Client → `[1, {"gVer": "0.5.5-beta"}]` (Handshake) — gVer must match or server closes with 1010
3. Server → `[[1, {...}]]` (Handshake response)
4. Client → `[2, {"opcode": 2}]` (Login as Guest)
5. Server → `[[3, {player_data}]]` (Welcome)
6. Client → `[9, {"regionsLoaded": 0, "userAgent": "ws_observer"}]` (Ready)
7. Server → `[[6, 0, {"entities": [...ids]}]]` (List of nearby entity IDs)
8. Client → `[7, [...ids]]` (Who — request spawn data)
9. Server → Spawn packets with entity data

Key packet IDs: Connected=0, Handshake=1, Login=2, Welcome=3, Spawn=5, List=6, Who=7, Ready=9, Movement=11, Despawn=13, Combat=15, Points=17, Experience=28, Death=29

Death packet sends instance as a plain string: `[29, "instance_string"]` (not a dict).

## Tests

```bash
python3 tests/test_ws_observer.py   # 21 unit tests for ws_observer
python3 tests/test_logger.py        # simulated 5-turn logger test
```

## Storage / teardown

Kaetram-Open is ~1.3–2 GB installed. See `TEARDOWN.md` for full uninstall steps and a "keep but trim" option (~1 GB reclaimed by deleting node_modules/dist while keeping source).

## Portability note

`prompts/system.md` uses `__PROJECT_DIR__` as a placeholder for absolute paths. `play.sh` substitutes the real path at runtime via `sed` — so it works on any machine regardless of where the repo is cloned. Never hardcode absolute paths in `system.md`.

## E2E verified (live Kaetram server)

Full pipeline tested live: `play.sh` + `ws_observer.py` + `logger.py` all running together.
- Claude logs in, teleports to Mudwich via `/teleport 188 157`, finds and attacks rats, reaches level 2, writes `progress.json`
- `ws_observer` maintains 40+ nearby entities in `game_state.json` throughout
- `logger` records steps with `nearby_entities` count in every `steps.jsonl` entry
- Screenshot frames saved correctly alongside each step
