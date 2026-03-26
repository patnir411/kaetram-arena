# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using Claude Code (Sonnet) and Playwright browser automation. The agent plays the game, collects structured training data, and builds a dataset for finetuning a text model (Qwen3.5 9B).

## What it does

- Logs in, navigates the world, fights monsters, loots drops, talks to NPCs, completes quests
- Extracts real-time game state (nearby entities, combat events, XP) directly from the browser via `page.evaluate()`
- Records every action as a `(game_state, reasoning, action)` tuple
- Runs indefinitely in sessions — each session picks up where the last left off
- Supports multi-agent mode: run N agents in parallel for scaled data collection
- 4 agent playstyles (aggressive, methodical, curious, efficient) for diverse training data

## Architecture

```
play.sh ──────────► Claude Code (Sonnet) ──────► Playwright MCP ──► browser @ localhost:9000
                          │                           │                        │
                    reads/writes                page.evaluate()         window.game
                    state/, prompts/            extracts game state    (Kaetram client)
                          │                           │
                          │                   returns state as tool result
                          │                           │
                          └──► logs/session_N_*.log (auto-logged JSONL)

                     dashboard (port 8080) ◄─── MongoDB (kaetram_devlopment, port 27017)
                              │                    authoritative player state:
                              │                    level, HP, skills, quests,
                              └── fallback ──►     equipment, inventory, achievements
                                   session log parsing (if DB unavailable)
```

**`play.sh`** — infinite loop, launches Claude Code sessions (150 turns max, 10s pause between)

**`state_extractor.js`** — injected into the browser during login; exposes `window.__extractGameState()` + `window.__generateAsciiMap()` which the agent calls each turn to read player position, nearby entities, combat target, HP, XP, and a text map of the viewport

**`dashboard.py`** — live web UI at port 8080. Reads player state directly from MongoDB (level, HP, mana, skills, quests, equipment, inventory, achievements) with session log parsing as fallback. Shows live screenshots, entity list, activity feed, and per-agent game state

**`prompts/system.md`** — base system prompt: login, OODA loop, targeting, healing, combat (generic, no game-specific knowledge)

**`prompts/game_knowledge.md`** — game-specific knowledge (mob stats, quest walkthroughs, NPC coords) appended to all agents

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
# 4 agents for 24 hours (round-robin personalities)
python3 orchestrate.py --agents 4 --hours 24

# 2 agents, run until ctrl-c
python3 orchestrate.py --agents 2

# One of each playstyle
python3 orchestrate.py --aggressive 1 --methodical 1 --curious 1 --efficient 1
```

Each agent gets its own server port (9001, 9011, 9021, 9031), username (`ClaudeBot0`–`ClaudeBot3`), log directory, and personality. All agents get `prompts/game_knowledge.md` (quest guides, NPC coords, mob stats). Resource budget for 4 agents: ~3.3 GB RAM, ~35% CPU.

### End-to-end data pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents for 24 hours
```

## SFT data pipeline

Three-stage pipeline transforms raw Claude session logs into Qwen3.5 9B training data:

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

**Stage 2: Convert to Qwen format** — Transforms turns into Qwen3.5 9B conversation records with `<think>` reasoning and structured `<action>` tags. 90/10 train/val split stratified by session.

```bash
# Default: mixed mode (70% multi-turn + 30% single-turn), SFT format
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/

# Single-turn only (one state → one action per record)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode single

# Multi-turn with windowed context (state deltas across turns)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode multi

# GRPO format (prompt-only with reward context for reinforcement learning)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --format grpo
```

### Output format (Qwen3.5 9B SFT)

```json
{
  "messages": [
    {"role": "system", "content": [{"type": "text", "text": "<condensed game rules>"}]},
    {"role": "user", "content": [
      {"type": "text", "text": "<game_state>\n{...}\n</game_state>\n\nWhat should you do?"}
    ]},
    {"role": "assistant", "content": [{"type": "text", "text": "<think>\nI see a Rat at distance 2...\n</think>\n<action>\nclick(408, 312)\n</action>"}]}
  ]
}
```

### Action vocabulary

| Action | Description |
|--------|-------------|
| `attack(mob_name)` | Target and attack a mob via helper |
| `interact_npc(npc_name)` | Walk to and interact with NPC |
| `navigate(x, y)` | Multi-step pathfinding to grid coordinates |
| `move(x, y)` | Single-step movement to nearby tile |
| `click(x, y)` | Click canvas at pixel coordinates (generic fallback) |
| `click_entity(label)` | Click a specific entity by label |
| `click_tile(x, y)` | Click a specific grid tile |
| `talk_npc(instance_id)` | Open dialogue with NPC |
| `warp(location)` | Fast travel (Mudwich, Crossroads, Lakesworld) |
| `equip(slot=N)` | Equip item from inventory slot |
| `heal(slot=N)` | Consume edible item |
| `quest_accept()` | Accept/progress a quest |
| `set_style(style)` | Change attack style (Hack=6, Chop=7, Defensive=3) |
| `stuck_reset()` | Reset navigation when stuck |
| `respawn()` | Respawn after death |
| `wait(Ns)` | Wait for combat/regen |

## Project structure

```
kaetram-agent/
├── play.sh                  # Claude Code agent loop (150 turns/session)
├── play_qwen.py             # Qwen agent loop — lightweight 2-tool harness
├── play_qwen.sh             # Qwen agent session launcher (system prompt substitution)
├── play_opencode.sh         # OpenCode + Playwright MCP agent launcher
├── orchestrate.py           # Multi-agent launcher + health monitor
├── extract_turns.py         # JSONL log → clean OODA turn extraction
├── convert_to_qwen.py       # Turns → Qwen3.5 9B SFT/GRPO format (single/multi/mixed modes)
├── state_extractor.js       # Injected into browser — exposes window.__extractGameState()
├── dashboard.py             # Live web dashboard launcher (port 8080)
├── qwen_dashboard.py        # Lightweight MJPEG dashboard for Qwen agent (port 8082)
├── opencode.json            # OpenCode provider config (Modal/Ollama endpoints)
├── dashboard/               # Dashboard package (modular)
│   ├── api.py               # API endpoints (DB-first, log-fallback game state)
│   ├── constants.py         # Config (ports, paths, MongoDB connection)
│   ├── db.py                # MongoDB reader — authoritative player state
│   ├── game_state.py        # Game state extraction (DB-based + log-based fallback)
│   ├── handler.py           # HTTP request handler
│   ├── parsers.py           # Session log parsing utilities
│   ├── server.py            # HTTP + WebSocket server
│   └── templates/index.html # Dashboard frontend
├── finetune/                # ML training pipeline
│   ├── SETUP_3060.md        # RTX 3060 local deployment guide
│   ├── train_modal.py       # SFT training on Modal (Unsloth + T4/L40S)
│   ├── train_grpo_modal.py  # GRPO reinforcement learning on Modal
│   ├── serve_modal.py       # vLLM serving endpoint (OpenAI-compatible)
│   ├── convert_gguf.py      # Model → GGUF Q4_K_M conversion
│   └── merge_and_quantize.py # LoRA merge + GGUF export (local)
├── world/                   # Forward dynamics model (2.2M param Transformer)
│   ├── README.md            # Architecture overview + quickstart
│   ├── schema.py            # State/action encoding (16-dim vectors, 26 actions)
│   ├── model.py             # Transformer forward dynamics model
│   ├── extract_transitions.py # Extract (state, action, next_state) from logs
│   ├── train.py             # Local PyTorch training
│   ├── train_modal.py       # Modal cloud training (T4 GPU)
│   ├── evaluate.py          # Per-field accuracy + rollout drift metrics
│   ├── mcts.py              # MCTS planner for multi-step lookahead
│   └── demo.py              # Interactive terminal demo
├── prompts/
│   ├── system.md            # Base system prompt: login, OODA loop, targeting
│   ├── game_knowledge.md    # Game knowledge: quests, NPCs, mobs (appended to all agents)
│   └── personalities/       # Playstyle DECIDE overrides (aggressive, methodical, curious, efficient)
├── scripts/
│   ├── start-kaetram.sh     # Starts Kaetram server (handles nvm use 20)
│   ├── restart-agent.sh     # Kill + restart agents fresh (resets DB)
│   ├── resume-agent.sh      # Resume agents without DB reset
│   ├── stop-agent.sh        # Graceful shutdown of orchestrator + agents
│   ├── reset-state.sh       # Reset MongoDB player data only
│   ├── collect_sft_data.sh  # End-to-end: orchestrate → extract → convert
│   ├── play_session.mjs     # Standalone Playwright script for manual testing
│   ├── cut-highlight.sh     # Extract highlight clips from recordings
│   └── format-vertical.sh   # Convert clips to 9:16 vertical format
├── .claude/commands/        # Claude Code slash commands
├── dataset/                 # Training data (gitignored)
├── state/                   # Runtime state (gitignored)
├── logs/                    # Claude Code JSONL session logs (gitignored)
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
| 8081 | Dashboard WebSocket relay (realtime screenshot push) |
| 8082 | Qwen dashboard (MJPEG stream) |
| 27017 | MongoDB (`kaetram-mongo` Docker container, db `kaetram_devlopment`) |

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

## Finetuned agent (Qwen3.5 9B)

The finetuned Qwen3.5-9B model can play autonomously using a lightweight 2-tool harness instead of Claude Code:

```bash
# Direct mode — play_qwen.py drives browser via Playwright
./play_qwen.sh

# OpenCode mode — uses OpenCode + Playwright MCP with Ollama/Modal endpoint
./play_opencode.sh

# Monitor Qwen agent (MJPEG dashboard on port 8082)
python3 qwen_dashboard.py
```

**Dual-VM architecture:**
- **GCP VM**: Hosts Kaetram game server (:9001 WS) + client (:9000 HTTP)
- **GPU VM** (RTX 3060): Runs finetuned model in Ollama + agent harness via Playwright

See `finetune/SETUP_3060.md` for local deployment instructions.

## World model

A small Transformer forward dynamics model (2.2M params) predicts combat outcomes for MCTS planning and reward shaping:

```bash
# Extract transitions from session logs
python3 -m world.extract_transitions --log-dir logs/

# Train locally
python3 -m world.train --data dataset/world_model/transitions.pt

# Interactive demo
python3 -m world.demo
```

See `world/README.md` for architecture details.

## License

Tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0).
