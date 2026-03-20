# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using Claude Code (Sonnet) and Playwright browser automation. The agent plays the game, collects structured training data, and builds a dataset for finetuning a smaller vision-language model (Qwen3 VL 4B).

## What it does

- Logs in, navigates the world, fights monsters, loots drops, talks to NPCs, completes quests
- Extracts real-time game state (nearby entities, combat events, XP) directly from the browser via `page.evaluate()`
- Records every action as a `(screenshot, game_state, reasoning, action)` tuple
- Runs indefinitely in sessions — each session picks up where the last left off
- Supports multi-agent mode: run N agents in parallel for scaled data collection

## Architecture

```
play.sh ──────────► Claude Code (Sonnet) ──────► Playwright MCP ──► browser @ localhost:9000
                          │                           │                        │
                    reads/writes                page.evaluate()         window.game
                    state/, prompts/            extracts game state    (Kaetram client)
                          │                           │
                          │                    writes state/game_state.json
                          │                           │
                          └───────────────────► logger.py ◄── watches screenshot mtime
                                                writes dataset/session_N/steps.jsonl
```

**`play.sh`** — infinite loop, launches Claude Code sessions (10,000 turns max, 10s pause between)

**`state_extractor.js`** — injected into the browser during login; exposes `window.__extractGameState()` which the agent calls each turn to read player position, nearby entities, combat target, HP, XP

**`logger.py`** — watches `state/screenshot.png` for changes, records one step per screenshot into `dataset/session_N/steps.jsonl`

**`dashboard.py`** — live web UI at port 8080, shows screenshots, entity list, session log

**`prompts/system.md`** — the system prompt Claude reads every session: login, OODA loop, targeting by coordinate, healing, quests

## Quick start

### Single-agent mode

Run each in its own terminal:

```bash
# Terminal 1 — Kaetram game server (Node 20 required)
./scripts/start-kaetram.sh

# Terminal 2 — Dashboard (optional, live monitoring)
python3 dashboard.py

# Terminal 3 — Agent loop (must be a separate terminal — see gotchas)
./play.sh
```

> **`play.sh` must always be in its own terminal.** Running it as a subprocess of Claude Code deadlocks both processes on the shared Playwright MCP browser.

### Multi-agent mode (scaled data collection)

Run N agents in parallel, each with its own Kaetram server instance:

```bash
# 4 agents for 24 hours
python3 orchestrate.py --agents 4 --hours 24

# 2 agents, run until ctrl-c
python3 orchestrate.py --agents 2
```

Each agent gets its own server port (9001, 9011, 9021, 9031), username (`ClaudeBot0`–`ClaudeBot3`), and log directory. Resource budget for 4 agents: ~3.3 GB RAM, ~35% CPU.

### End-to-end data pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents for 24 hours
```

## SFT data pipeline

Three-stage pipeline transforms raw Claude session logs into Qwen3 VL training data:

```
logs/session_*.log  ──►  extract_turns.py  ──►  dataset/extracted/*/turns.jsonl
                                                         │
                                                convert_to_qwen.py  ──►  dataset/qwen_sft/train.json
                                                                          dataset/qwen_sft/val.json
```

**Stage 1: Extract turns** — Parses JSONL session logs, identifies OODA cycles (observe + reason + act), extracts game state, reasoning, and structured actions.

```bash
python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/
```

**Stage 2: Convert to Qwen format** — Transforms turns into Qwen3 VL conversation records with `<think>` reasoning and structured `<action>` tags. 90/10 train/val split.

```bash
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
```

### Output format (Qwen3 VL SFT)

```json
{
  "messages": [
    {"role": "system", "content": [{"type": "text", "text": "<condensed game rules>"}]},
    {"role": "user", "content": [
      {"type": "image", "image": "file:///path/to/screenshot.png"},
      {"type": "text", "text": "<game_state>\n{...}\n</game_state>\n\nWhat should you do?"}
    ]},
    {"role": "assistant", "content": [{"type": "text", "text": "<think>\nI see a Rat at distance 2...\n</think>\n<action>\nclick(408, 312)\n</action>"}]}
  ]
}
```

### Action vocabulary

| Action | Description |
|--------|-------------|
| `click(x, y)` | Click canvas at pixel coordinates (attack, walk, interact) |
| `equip(slot=N)` | Equip item from inventory slot |
| `heal(slot=N)` | Consume edible item |
| `warp(location)` | Fast travel (Mudwich, Crossroads, Lakesworld) |
| `quest_accept()` | Accept/progress a quest |
| `set_style(style)` | Change attack style (Stab, Hack, Chop) |
| `wait(Ns)` | Wait for combat/regen |

## Project structure

```
kaetram-agent/
├── play.sh                  # Single-agent loop — launches Claude Code sessions
├── orchestrate.py           # Multi-agent launcher + health monitor
├── extract_turns.py         # JSONL log → clean OODA turn extraction
├── convert_to_qwen.py       # Turns → Qwen3 VL SFT format
├── state_extractor.js       # Injected into browser — exposes window.__extractGameState()
├── logger.py                # Real-time dataset logger (watches screenshot mtime)
├── dashboard.py             # Live web dashboard (port 8080)
├── ws_observer.py           # [Deprecated] WebSocket observer
├── prompts/
│   └── system.md            # System prompt: login, OODA loop, targeting, quests
├── scripts/
│   ├── start-kaetram.sh     # Starts Kaetram server (handles nvm use 20)
│   ├── restart-agent.sh     # Kill + restart agent fresh
│   ├── collect_sft_data.sh  # End-to-end: orchestrate → extract → convert
│   ├── play_session.mjs     # Standalone Playwright script for manual testing
│   ├── cut-highlight.sh     # Extract highlight clips from recordings
│   └── format-vertical.sh   # Convert clips to 9:16 vertical format
├── tests/
│   ├── test_ws_observer.py  # 21 unit tests for ws_observer
│   └── test_logger.py       # Simulated 5-turn logger test
├── .claude/
│   └── commands/            # Claude Code slash commands
│       ├── game-session.md  # /game-session — check stack status
│       ├── verify-pipeline.md # /verify-pipeline — health check
│       └── training-summary.md # /training-summary — dataset stats
├── state/                   # Runtime state (gitignored)
├── dataset/                 # Training data
│   ├── session_N/           # Real-time logger output (steps.jsonl + frames/)
│   ├── extracted/           # Extracted OODA turns (gitignored)
│   ├── qwen_sft/            # Final Qwen SFT dataset (gitignored)
│   └── raw/                 # Multi-agent raw logs (gitignored)
├── logs/                    # Claude Code JSONL session logs
├── session_log.md           # Running decision log across sessions
└── CLAUDE.md                # Developer reference for Claude Code
```

## Ports

| Port | What |
|------|------|
| 9000 | Kaetram game client (HTTP, shared across agents) |
| 9001 | Kaetram game server WS (single-agent default) |
| 9001, 9011, 9021, 9031 | Game server WS (multi-agent, one per agent) |
| 8080 | Dashboard |

## Slash commands

| Command | When to use |
|---------|-------------|
| `/game-session` | Check what's running, get startup commands, see port status |
| `/verify-pipeline` | Confirm data is flowing, inspect latest training record |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |

## Gotchas

**Playwright subprocess deadlock** — `play.sh` must run in a separate terminal. Spawning it as a subprocess of Claude Code deadlocks both on the shared Playwright MCP browser.

**Node 20 required** — Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial. The agent uses warp to skip this.

**Absolute screenshot paths** — Playwright MCP requires absolute paths. Relative paths cause it to navigate the browser to the path as a URL.

**Multi-agent port conflicts** — If running `orchestrate.py`, kill any existing Kaetram servers first. The orchestrator manages its own server instances.

## Tests

```bash
python3 tests/test_ws_observer.py   # 21 unit tests — no live server needed
python3 tests/test_logger.py        # Simulated 5-turn logger test
```

## License

Tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0).
