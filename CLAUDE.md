# CLAUDE.md — Kaetram AI Agent (Developer Reference)

> **This file is for the human developer using Claude Code interactively.**
> The agent subprocess launched by `play.sh` does NOT read this file — its instructions live exclusively in `prompts/system.md`. Do not add agent behavioral instructions here.

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using a **custom MCP server** (`mcp_game_server.py`) that exposes typed game tools (observe, attack, navigate, etc.). The agent calls structured tools — never writes JavaScript. Gameplay sessions are collected as SFT training data for Qwen3.5 9B.

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

**Node.js version** — Kaetram requires Node 16/18/20. Node 24/25 crashes on startup (uWS.js incompatibility). Always `nvm use 20` before starting the server.

**Port conflicts** — If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill all node processes first.

**yarn build required** — After cloning, `yarn start` alone fails. Run `yarn build` first.

**MCP server uses Python venv** — `mcp_game_server.py` requires `.venv` with `mcp[cli]` and `playwright` installed. The `.mcp.json` template references `__VENV_PYTHON__` which resolves to `.venv/bin/python3`.

**`.mcp.json` is a template** — Contains placeholders (`__VENV_PYTHON__`, `__PROJECT_DIR__`, `__SERVER_PORT__`, `__USERNAME__`, `__SCREENSHOT_DIR__`). Resolved by `cli_adapter.py` or `play.sh` at launch time. Claude Code uses `--mcp-config` + `--strict-mcp-config` to read the resolved copy from the sandbox, NOT the project-level template.

---

## MANAGING TRAINING RUNS

### Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/restart-agent.sh [N] [H]` | **Primary command.** Kills everything, resets DB (fresh Level 1), clears state, relaunches N agents for H hours. Default: 4 agents, 24h. Use `0` for no time limit. Supports personality and harness flags. |
| `./scripts/resume-agent.sh` | Resume agents without DB reset. Preserves character progress. Supports personality and harness flags. |
| `./scripts/restart-single-agent.sh <ID>` | Restart one running agent (agent 0-3) without affecting others. Clears session counter for fresh start. Supports `--reset`, personality, and harness switches. |
| `./scripts/stop-agent.sh` | Stop orchestrator + all agents gracefully. Preserves logs. |
| `./scripts/reset-state.sh [N] [--force]` | Reset MongoDB player data only (no restart). Use `--force` to skip safety check. |
| `./scripts/start-kaetram.sh` | Start Kaetram game server (single-agent mode, Node 20 required). |

### Harness Flags

All scripts support harness selection via `--claude [N]`, `--codex [N]`, `--kimi [N]`, `--qwen-code [N]` (bare flag = all agents).

**Default models:**
- `--claude` → Sonnet (Claude Code)
- `--codex` → GPT-5.4 (OpenAI Codex)
- `--kimi` → Kimi K2 with `--thinking` enabled
- `--qwen-code` → Qwen3-Coder with stream-json output

### Quick start (multi-agent)

```bash
# Default: 4 Claude agents, 24h
./scripts/restart-agent.sh 4 0

# Mixed harnesses
./scripts/restart-agent.sh --claude 1 --codex 1 --kimi 1 --qwen-code 1 --hours 0

# With personalities
./scripts/restart-agent.sh --aggressive 2 --curious 2 --kimi 4 --hours 24

# Resume without reset
./scripts/resume-agent.sh --qwen-code 2 --hours 8

# Restart single agent (preserves others)
./scripts/restart-single-agent.sh 2 --kimi --reset

# Monitor
tail -f /tmp/orchestrate.log
tmux attach -t datacol
# Dashboard: http://localhost:8080
```

### What restart-agent.sh does

1. Kills orchestrator + all agent processes
2. Kills game server instances (preserves client on :9000)
3. **Resets MongoDB player data** — agents start fresh Level 1 with Bronze Axe
4. Clears sandbox state (screenshots, progress.json, game_state.json)
5. Ensures dashboard is running on :8080
6. Launches orchestrator in `datacol` tmux session

### What restart-single-agent.sh does

Restart a single running agent (0-3) without affecting others. Useful for:
- Switching one agent's harness (Claude → Kimi, etc.)
- Changing personality
- Resetting a stuck agent while others continue

Flags:
- `--reset` — Reset Level 1 + clear state (default: preserve progress)
- `--claude`, `--codex`, `--kimi`, `--qwen-code` — Change harness
- `--personality {aggressive,methodical,curious,efficient}` — Change playstyle

**Important:** Always clears `.session_counter` to ensure fresh session starts (not resumption).

Examples:
```bash
./scripts/restart-single-agent.sh 2 --kimi --reset           # Agent 2: switch to Kimi, reset Level 1
./scripts/restart-single-agent.sh 0 --qwen-code              # Agent 0: switch to Qwen Code, preserve progress
./scripts/restart-single-agent.sh 3 --personality curious    # Agent 3: change to curious playstyle
```

### Single-agent mode (development/testing)

Run each in its own terminal:

1. **Terminal 1 — Kaetram server** (Node 20 required)
   ```bash
   ./scripts/start-kaetram.sh
   ```

2. **Terminal 2 — Dashboard** (optional)
   ```bash
   python3 dashboard.py
   ```

3. **Terminal 3 — Agent loop** — MUST be separate terminal (never subprocess)
   ```bash
   ./play.sh                    # Claude (default)
   ./play.sh --kimi --curious   # Kimi with thinking
   ./play.sh --qwen-code        # Qwen Code
   ./play.sh --codex            # Codex
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

**Agent playstyles:** Each agent gets a playstyle that defines its DECIDE priorities in `system.md`. Playstyle files in `prompts/personalities/` are injected via the `__PERSONALITY_BLOCK__` placeholder. All agents get `game_knowledge.md` appended. Dashboard shows playstyle badges (red=AGGRESSIVE, amber=METHODICAL, blue=CURIOUS). Active collection uses 3 agents. Each agent's sandbox gets a `metadata.json` with its playstyle.

| Flag | Playstyle | Color | Approach |
|------|-----------|-------|----------|
| `--aggressive` | Aggressive | Red | HP threshold 30%, attacks above-level mobs, pushes new zones early |
| `--methodical` | Methodical | Amber | HP threshold 60%, needs 2+ food before quest mobs, infrastructure quest order |
| `--curious` | Curious | Blue | NPC-first, enters every building, zone rotation every 30 turns |

**Note:** EFFICIENT personality deprecated (April 3). Active: agent_0=AGGRESSIVE, agent_1=METHODICAL, agent_2=CURIOUS.

**Resource budget (3 agents on this VM):** ~2.5 GB RAM, ~27% CPU, ~4.5 GB disk/24h — comfortable on 16 GB / 4 vCPU.

**Database**: MongoDB (`kaetram-mongo` Docker container, port 27017, db `kaetram_devlopment`) persists player state across 9 collections (`player_info`, `player_skills`, `player_equipment`, `player_inventory`, `player_bank`, `player_quests`, `player_achievements`, `player_statistics`, `player_abilities`). The dashboard reads directly from MongoDB via `pymongo` for authoritative game state (level, HP, mana, skills, quests, equipment, inventory). Requires `pymongo` in the venv.

### End-to-end data collection pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents, 24 hours
```

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

**Stage 2: Convert to Qwen format** — Transforms extracted turns into Qwen3.5 9B conversation records with system/user/assistant messages, `<think>` reasoning, and structured `<action>` tags. 90/10 train/val split stratified by session. Supports 3 modes (`--mode single|multi|mixed`) and 2 formats (`--format sft|grpo`).

```bash
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode multi --format grpo
```

**Action vocabulary** (used in `<action>` tags):
- `attack(mob_name)` — target and attack a mob via helper
- `interact_npc(npc_name)` — walk to and interact with NPC
- `navigate(x, y)` — multi-step pathfinding to grid coordinates
- `move(x, y)` — single-step movement to nearby tile
- `click(x, y)` — click canvas at pixel coordinates (generic fallback)
- `click_entity(label)` — click a specific entity by label
- `click_tile(x, y)` — click a specific grid tile
- `talk_npc(instance_id)` — open dialogue with NPC
- `warp(location)` — fast travel (Mudwich, Crossroads, Lakesworld)
- `equip(slot=N)` — equip item from inventory
- `heal(slot=N)` — consume edible item
- `quest_accept()` — click quest button
- `set_style(style)` — change attack style (Hack=6, Chop=7, Defensive=3)
- `stuck_reset()` — reset navigation when stuck
- `respawn()` — respawn after death
- `wait(Ns)` — wait for combat/regen

**Verified on existing data:** 5,162 turns extracted from 259 session logs (4 agents) → 3,844 train / 1,318 val Qwen3.5 SFT records.

---

## CURRENT STATUS

**Data collection ACTIVE.** 3 agents running (AGGRESSIVE, METHODICAL, CURIOUS) on GCP VM. ~190 sessions, 289MB. Training job running on Modal in parallel.

**Personalities finalized (April 3).** Dropped EFFICIENT after audit. 3 orthogonal axes confirmed working in logs: combat approach / HP-gated preparation / exploration-first. Next: let agents run, rebuild qwen_sft, evaluate distilled model quality.

**Finetune v1 DONE.** Qwen3.5-9B finetuned on 3,844 gameplay turns via Modal H100 (27min). Model loaded in Ollama on RTX 3060 GPU machine. New training run in progress with updated MCP-format logs.

**Qwen agent harness DONE.** Three modes available:
- `QwenCodeAdapter` in `cli_adapter.py` — wraps the `qwen` CLI (a Claude Code / Gemini CLI fork). Uses Playwright MCP, `stream-json` output, `--yolo` mode. Same architecture as Claude/Kimi/Codex adapters. Used by `orchestrate.py` and `play.sh --qwen-code`. **This is NOT the finetuned model** — it calls the Qwen Code CLI which hits the Qwen API.
- `play_qwen.py` / `play_qwen.sh` — lightweight custom 2-tool loop (browser_run_code + bash) driving Playwright directly via Python. Calls an OpenAI-compatible endpoint (Modal/Ollama). **This IS the finetuned model** harness.
- `play_opencode.sh` + `opencode.json` — OpenCode + Playwright MCP with Ollama/Modal endpoint

**World model DONE.** 2.2M param Transformer forward dynamics model trained on gameplay transitions. Used for MCTS planning and GRPO reward shaping. See `world/README.md`.

**Remote agent setup:**
- **GCP VM** (`35.224.227.251`): Hosts Kaetram game server (:9001 WS) + client (:9000 HTTP). This is the game world.
- **GPU VM** (`73.173.11.56:1738` via SSH): Runs finetuned `kaetram` model in Ollama (RTX 3060 12GB) + agent harness via Playwright. This is the agent brain.
- Agent on GPU VM connects browser to `http://35.224.227.251:9000` and plays via Playwright.

### Remote access
| Machine | IP | SSH | Purpose |
|---------|------|------|---------|
| GCP VM (this) | 35.224.227.251 | patnir41@35.224.227.251 | Game server + client, data collection, training pipeline |
| GPU VM (3060) | 73.173.11.56 | pnir41@73.173.11.56 -p 1738 | Finetuned model inference, agent harness (OpenCode) |

---

## Architecture

```
Custom MCP Server (current):
  Claude CLI ──► mcp_game_server.py (FastMCP) ──► Playwright Python ──► browser
                   │                                    │
              16 typed tools                     page.evaluate()
              (observe, attack,                  calls window.__helperFn()
               navigate, warp...)                from state_extractor.js
                   │
              Agent NEVER writes JS — calls structured tools only

Multi-agent orchestration:
  orchestrate.py ──► N × (GameServer + AgentInstance)
                     each agent gets own MCP server process + browser
                          │
                     dataset/raw/agent_N/logs/session_*.log
                          │
           extract_turns.py (parses MCP tool calls) → turns.jsonl
                          │
           convert_to_qwen.py → dataset/qwen_sft/{train,val}.json

Rate limit / budget:
  orchestrate.py detects auth mode via `claude auth status`
  Subscription: parses rate_limit_event from stream-json (overageStatus)
  API key: detects 429 errors + passes --max-budget-usd
  Both: tracks cost via total_cost_usd, kills agent if over budget
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

## Key files

| File | Purpose |
|------|---------|
| `mcp_game_server.py` | **Custom MCP server** — FastMCP Python, 18 typed game tools, manages Playwright browser. Agent calls these instead of writing JS. |
| `.mcp.json` | MCP config **template** — placeholders resolved at launch. Claude uses `--mcp-config` + `--strict-mcp-config`. |
| `play.sh` | Single-agent loop (resolves `.mcp.json` template via sed) |
| `cli_adapter.py` | **Harness abstraction** — ClaudeAdapter resolves MCP config, passes `--mcp-config`/`--strict-mcp-config` |
| `orchestrate.py` | Multi-agent launcher + health monitor + rate limit detection + budget enforcement |
| `state_extractor.js` | Injected into browser via `context.add_init_script()` — exposes `window.__extractGameState()`, `window.__attackMob()`, etc. Called by MCP server internally, never by agent. |
| `extract_turns.py` | JSONL log → OODA turn extraction (parses MCP tool calls) |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT/GRPO format |
| `prompts/system.md` | Agent system prompt (~100 lines, no JS — just tool names + decision tree) |
| `prompts/game_knowledge.md` | Game knowledge (mob stats, quest guides, NPC coords) |
| `prompts/personalities/*.md` | Playstyle overrides (aggressive, methodical, curious, efficient) |
| `dashboard.py` | Live web dashboard (port 8080) |
| `dashboard/parsers.py` | Session log parser — classifies MCP tool calls for activity feed |
| `dashboard/api.py` | API endpoints — `/api/game-state`, `/api/agents`, `/api/activity` |

### Session log format (stream-json)

Logs are JSONL at `dataset/raw/agent_N/logs/session_*.log`. Key event types:

| `type` | Structure | How to parse |
|--------|-----------|-------------|
| `"system"` (line 1) | `mcp_servers[].status`, `tools[]` | Check `mcp_servers[0].status == "connected"` for MCP health |
| `"assistant"` | `message.content[]` with `tool_use` blocks | Tool name: `c.name`, params: `c.input` |
| `"user"` (tool results) | `message.content[].content` | For observe: split on `\n\nASCII_MAP:`, JSON.parse first part |
| `"rate_limit_event"` | `rate_limit_info.overageStatus`, `.resetsAt` | Check `overageStatus == "rejected"` |
| `"result"` (session end) | `total_cost_usd`, `num_turns`, `duration_ms` | Final session summary |

### Parsing observe results from logs

The observe tool returns game state as: `{"result": "<escaped JSON>\n\nASCII_MAP:\n..."}`. To extract:
```python
wrapper = json.loads(content_string)
raw = wrapper["result"]
state_json = raw.split("\n\nASCII_MAP:")[0]
gs = json.loads(state_json)
# gs["player_position"], gs["player_stats"], gs["quests"], gs["inventory"], etc.
```

## Placeholders

**In `prompts/system.md`** (resolved by `play.sh` or `orchestrate.py`):
`__PROJECT_DIR__`, `__USERNAME__`, `__SERVER_PORT__`, `__GAME_KNOWLEDGE_BLOCK__`, `__PERSONALITY_BLOCK__`

**In `.mcp.json`** (resolved by `cli_adapter.py` or `play.sh` sed):
`__VENV_PYTHON__`, `__PROJECT_DIR__`, `__SERVER_PORT__`, `__USERNAME__`, `__SCREENSHOT_DIR__`

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

## Agent prompt design principles

When editing `prompts/system.md`, `prompts/game_knowledge.md`, or `prompts/personalities/*.md`, follow these research-backed guidelines:

- **Total prompt under 3K tokens** (system.md + game_knowledge + personality). Reasoning degrades above this threshold (MLOps Community meta-analysis, RAG-MCP arXiv 2505.03275).
- **XML tags over Markdown** for section structure. Claude is specifically trained on XML-tagged prompts (`<tools>`, `<rules>`, `<gameplay_loop>`). Anthropic official best practices.
- **Calm language, not aggressive**. Claude 4.6 over-triggers on "CRITICAL", "MUST", "No exceptions". Use normal directives. (Anthropic: "dial back aggressive language").
- **WHY, not just WHAT**. "Observe between attacks — game state changes, stale state causes deaths" beats "Never batch attacks". Explanations improve compliance.
- **Reference data at top, instructions at end**. "Lost in the middle" effect: middle 40-60% of context is systematically ignored (Stanford NLP). Put game_knowledge above decision tree.
- **Personality = priority modifiers only**. Don't add new rules — modify ordering/thresholds of existing decision tree. Keep under 10 lines each. (ACL 2025: personality via explicit behavioral instructions works; instruction dilution from rule proliferation doesn't.)
- **One tool per turn is correct** for game agents. Validated by ReAct (Yao et al.), GamingAgent (ICLR 2026), Claude Code architecture.
- **18 tools is near the limit**. Performance degrades beyond ~19 tools (RAG-MCP). Don't add tools without removing/combining others.

## MCP server internals

**`mcp_game_server.py`** uses Python Playwright (`playwright.async_api`) with FastMCP lifespan pattern. Browser is launched once on MCP server start and kept alive for the entire session. `state_extractor.js` is injected via `context.add_init_script()` (survives page reloads).

**Game state** is read via `page.evaluate()` from `window.game`. Key properties:
- `window.game.player` — player instance (gridX, gridY, hitPoints, level, experience, target)
- `window.game.entities.entities` — dict of all loaded entities {instance: Entity}
- `window.__kaetramState` — combat/XP event hooks (installed by state_extractor.js)
- `window.__latestGameState` — auto-cached every 500ms by state_extractor.js
- `window.__attackMob()`, `__navigateTo()`, `__interactNPC()`, etc. — helper functions called by MCP tools internally

**`context.add_init_script()` with args** — Python Playwright does NOT accept a second argument for script parameters (unlike Node.js). Embed values directly in the script string via f-string. This was a launch-blocking bug.

## Storage / teardown

Kaetram-Open is ~1.3–2 GB installed. See `TEARDOWN.md` for full uninstall steps and a "keep but trim" option (~1 GB reclaimed by deleting node_modules/dist while keeping source).
