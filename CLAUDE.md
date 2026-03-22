# CLAUDE.md — Kaetram AI Agent (Developer Reference)

> **This file is for the human developer using Claude Code interactively.**
> The agent subprocess launched by `play.sh` does NOT read this file — its instructions live exclusively in `prompts/system.md`. Do not add agent behavioral instructions here.

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using Claude Code + Playwright browser automation. It collects gameplay data for finetuning a text model (Qwen3.5 9B).

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

**Playwright subprocess deadlock** — `play.sh` MUST be launched from a separate terminal. If you spawn `claude -p` as a subprocess of the current Claude Code session, both processes share the same Playwright MCP browser and deadlock. Symptoms: agent session freezes at ~0 CPU, screenshot stops updating, log file stays 0 bytes. Fix: `ps aux | grep "claude -p" | grep -v grep` then `kill <PID>`.

**Node.js version** — Kaetram requires Node 16/18/20. Node 24/25 crashes on startup (uWS.js incompatibility). Always `nvm use 20` before starting the server.

**Port conflicts** — If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill all node processes first.

**yarn build required** — After cloning, `yarn start` alone fails. Run `yarn build` first.

**`require()` is not available in Playwright MCP `browser_run_code`** — The execution context is an ESM-like sandbox, not CommonJS Node.js. `require('fs')`, `require('path')`, etc. all fail with "require is not defined". Errors are silently swallowed by try/catch. Do NOT attempt to write files from `browser_run_code` — use a separate Bash tool call instead, or read data from the session log.

---

## MANAGING TRAINING RUNS

### Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/restart-agent.sh [N] [H]` | **Primary command.** Kills everything, resets DB (fresh Level 1 characters), clears state, relaunches N agents for H hours. Default: 4 agents, 24h. Use `0` for no time limit. Supports `--aggressive N --methodical N --curious N --efficient N`. |
| `./scripts/stop-agent.sh` | Stop orchestrator + all agents gracefully. Preserves logs. |
| `./scripts/resume-agent.sh` | Resume agents without DB reset. Preserves character progress. Supports `--aggressive N --methodical N --curious N --efficient N --hours H`. |
| `./scripts/reset-state.sh [N] [--force]` | Reset MongoDB player data only (no restart). Use `--force` to skip safety check. |
| `./scripts/start-kaetram.sh` | Start Kaetram game server (single-agent mode, Node 20 required). |

### Quick start (multi-agent)

```bash
# Restart fresh: 4 agents, no time limit (round-robin personalities)
./scripts/restart-agent.sh 4 0

# One of each playstyle
./scripts/restart-agent.sh --aggressive 1 --methodical 1 --curious 1 --efficient 1 --hours 0

# Custom mix: 2 aggressive + 2 efficient
./scripts/restart-agent.sh --aggressive 2 --efficient 2 --hours 0

# Monitor
tail -f /tmp/orchestrate.log        # orchestrator status
tmux attach -t datacol               # orchestrator tmux session
# Dashboard at http://localhost:8080 (WebSocket screenshots on :8081)
```

### What restart-agent.sh does

1. Kills orchestrator + all claude agent processes
2. Kills game server instances (preserves client on :9000)
3. **Resets MongoDB player data** — agents start fresh Level 1 with Bronze Axe
4. Clears sandbox state (screenshots, progress.json, game_state.json)
5. Ensures dashboard is running on :8080
6. Launches orchestrator in `datacol` tmux session

### Single-agent mode (development/testing)

Run each in its own terminal, in order:

1. **Terminal 1 — Kaetram server** (Node 20 required)
   ```bash
   ./scripts/start-kaetram.sh
   ```

2. **Terminal 2 — Dashboard** (optional)
   ```bash
   python3 dashboard.py
   ```

3. **Terminal 3 — Agent loop** — MUST be a separate terminal, never a subprocess
   ```bash
   ./play.sh
   ```

### Multi-agent mode (scaled data collection)

```bash
# 4 agents, no time limit (round-robin personalities)
./scripts/restart-agent.sh 4 0

# 2 agents, 8 hours
./scripts/restart-agent.sh 2 8

# One of each personality
./scripts/restart-agent.sh --aggressive 1 --methodical 1 --curious 1 --efficient 1 --hours 0
```

Port allocation: agent N gets server WS port `9001 + N*10` (9001, 9011, 9021, 9031). All agents share the static client on port 9000. Each agent logs in as `ClaudeBotN`.

**Agent playstyles:** Each agent gets a playstyle that defines its DECIDE priorities in `system.md`. Playstyle files in `prompts/personalities/` are injected via the `__PERSONALITY_BLOCK__` placeholder. All agents get `game_knowledge.md` appended. Dashboard shows playstyle badges (red=AGGRESSIVE, amber=METHODICAL, blue=CURIOUS, purple=EFFICIENT). Default (no flags): round-robin assignment. Each agent's sandbox gets a `metadata.json` with its playstyle.

| Flag | Playstyle | Color | Approach |
|------|-----------|-------|----------|
| `--aggressive` | Aggressive | Red | Takes risks, pushes combat zones, attempts bosses early |
| `--methodical` | Methodical | Amber | Over-prepares, builds skills, crafts before advancing |
| `--curious` | Curious | Blue | Talks to every NPC, enters every building, discovers paths |
| `--efficient` | Efficient | Purple | Shortest path through quest chain, no wasted turns |

**Resource budget (4 agents on this VM):** ~3.3 GB RAM, ~35% CPU, ~6 GB disk/24h — comfortable on 16 GB / 4 vCPU.

**Database**: MongoDB (`kaetram-mongo` Docker container, port 27017) persists player state. `SKIP_DATABASE=false` in `.env`. Characters survive disconnects/server restarts.

### End-to-end data collection pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents, 24 hours
```

> **Note:** `ws_observer.py` is deprecated. Game state is extracted directly from the browser via `page.evaluate()` in the agent's observe step.

---

## SFT DATA PIPELINE

Three-stage pipeline transforms raw Claude session logs into Qwen3.5 9B training data:

```
logs/session_*.log  →  extract_turns.py  →  dataset/extracted/*/turns.jsonl
                                                    │
                                           convert_to_qwen.py  →  dataset/qwen_sft/train.json
                                                                   dataset/qwen_sft/val.json
```

**Stage 1: Extract turns** — Parses JSONL session logs, identifies OODA cycles (observe + reason + act), extracts game state, reasoning, and structured actions. Handles combined observe+action browser calls.

```bash
python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/ --no-frames
python3 extract_turns.py --log-file logs/session_2_20260319_060749.log   # single file
```

**Stage 2: Convert to Qwen format** — Transforms extracted turns into Qwen3.5 9B conversation records with system/user/assistant messages, `<think>` reasoning, and structured `<action>` tags. 90/10 train/val split stratified by session.

```bash
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
```

**Action vocabulary** (used in `<action>` tags):
- `click(x, y)` — click canvas at pixel coordinates (attack, walk, interact)
- `equip(slot=N)` — equip item from inventory
- `heal(slot=N)` — consume edible item
- `warp(location)` — fast travel (Mudwich, Crossroads, Lakesworld)
- `quest_accept()` — click quest button
- `set_style(style)` — change attack style (Hack=6, Chop=7, Defensive=3)
- `wait(Ns)` — wait for combat/regen

**Verified on existing data:** 558 turns extracted from 54 session logs → 542 train / 16 val Qwen3.5 SFT records.

---

## CURRENT STATUS

**Last updated:** 2026-03-20

| PR | Title | Status |
|----|-------|--------|
| PR 1 | ws_observer.py + 21 unit tests | merged |
| PR 2 | logger.py dataset recording | merged |
| PR 3 | game_state.json prompt injection | merged |
| PR 4 | Skills system + CLAUDE.md overhaul | merged |
| PR 5-10 | Dashboard, ws_observer fixes, cleanup | merged |
| — | Multi-agent SFT pipeline | implemented, ready to run |
| — | 4-playstyle agent system | implemented (aggressive, methodical, curious, efficient) |

**Next:** Collect data with personality-diverse agents, then finetune Qwen3.5 9B.

**Blocked:** Nothing currently blocked.

---

## Architecture

```
play.sh ─────► Claude Code (Sonnet) ─────► Playwright MCP ──► browser @ localhost:9000
                     │                          │                        │
               reads/writes               page.evaluate()         window.game
               state/, prompts/           extracts game state    (Kaetram client)
                     │                          │
                     │                   returns state as tool result
                     │                          │
                     └──────────────────► logger.py ◄── watches screenshot mtime
                                           writes dataset/session_N/steps.jsonl

Dashboard reads game state by parsing the latest session log (tool results).

Multi-agent mode:
orchestrate.py ──► N × (GameServer + AgentInstance)
                   each agent gets own server port, sandbox, and log directory
                        │
                   dataset/raw/agent_N/logs/session_*.log
                        │
                   extract_turns.py → convert_to_qwen.py → dataset/qwen_sft/
```

## Ports

| Port | What |
|------|------|
| 9000 | Kaetram game client (HTTP, shared across agents) |
| 9001 | Kaetram game server WS (single-agent default) |
| 9001, 9011, 9021, 9031 | Game server WS (multi-agent, one per agent) |
| 8080 | Dashboard |
| 8081 | Dashboard WebSocket relay (realtime screenshot push) |

## Key files

| File | Purpose |
|------|---------|
| `play.sh` | Single-agent loop — launches Claude Code sessions |
| `orchestrate.py` | Multi-agent launcher + health monitor |
| `extract_turns.py` | JSONL log → clean OODA turn extraction |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT format |
| `scripts/collect_sft_data.sh` | End-to-end pipeline wrapper |
| `prompts/system.md` | Base system prompt with `__PERSONALITY_BLOCK__` placeholder |
| `prompts/game_knowledge.md` | Game-specific knowledge (mob stats, quest guides, NPC coords) — appended for all agents |
| `prompts/personalities/*.md` | Playstyle DECIDE overrides (aggressive, methodical, curious, efficient) |
| `state_extractor.js` | Injected into browser — exposes `window.__extractGameState()` |
| `logger.py` | Real-time dataset logger (watches screenshot mtime) |
| `dashboard.py` | Live web dashboard (port 8080) |
| `state/progress.json` | Cross-session state (written by Claude) |
| `state/game_state.json` | Legacy — no longer written live. Dashboard reads state from session logs instead. |
| `logs/session_N_*.log` | Claude Code JSONL session logs |

## Placeholders in `prompts/system.md`

| Placeholder | Substituted by | Default (single-agent) |
|-------------|----------------|----------------------|
| `__PROJECT_DIR__` | `play.sh` via sed | repo root |
| `__USERNAME__` | `play.sh` or `orchestrate.py` | `ClaudeBot` |
| `__SERVER_PORT__` | `play.sh` or `orchestrate.py` | empty (no override) |
| `__PERSONALITY_BLOCK__` | `play.sh` or `orchestrate.py` | empty (generic DECIDE) |

## Skills (slash commands)

Three custom skills live in `.claude/commands/`:

| Skill | When to trigger |
|-------|----------------|
| `/game-session` | Check stack status, startup guide, port status |
| `/verify-pipeline` | Confirm data is flowing, inspect training records |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |

---

## Kaetram gotchas (hard-won)

**Node.js version**: Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup. Always `nvm use 20`.

**Key coordinates**:
- Mudwich village center: `188, 157` (outdoor starting area, use this)
- Default spawn: `328, 892` (Programmer's house — stuck behind tutorial)

**Port conflicts**: If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill everything first.

**yarn build required**: After cloning, `yarn start` alone fails ("Cannot find module dist/main.js"). Must run `yarn build` first.

## Playwright gotchas

**Screenshot paths must be absolute.** Relative paths cause Playwright MCP to navigate the browser to the path as a URL, losing the game page.

**WASD is hold-to-move.** Use `keyboard.down('w')` + wait + `keyboard.up('w')`. Tap = no movement.

**Keep all actions in `browser_run_code` blocks** to avoid browser page garbage collection between tool calls.

## Browser-side state extraction

**Game state is read via `page.evaluate()`** from `window.game` — the Kaetram client stores the full game object there (see `packages/client/src/main.ts` in Kaetram-Open). Key properties:
- `window.game.player` — player instance (gridX, gridY, hitPoints, level, experience, target, etc.)
- `window.game.entities.entities` — dict of all loaded entities {instance: Entity}
- `window.__kaetramState` — our injected hooks for combat/XP event tracking (installed during login)

## Tests

```bash
python3 tests/test_ws_observer.py   # 21 unit tests for ws_observer
python3 tests/test_logger.py        # simulated 5-turn logger test
```

## Storage / teardown

Kaetram-Open is ~1.3–2 GB installed. See `TEARDOWN.md` for full uninstall steps and a "keep but trim" option (~1 GB reclaimed by deleting node_modules/dist while keeping source).
