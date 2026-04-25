# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using a custom MCP server with typed game tools. The agent calls structured tools (observe, attack, navigate, interact_npc, etc.) — never writes JavaScript. Gameplay sessions are collected as SFT/KTO training data for finetuning Qwen3.5 9B.

> **For developers:** see [`CLAUDE.md`](CLAUDE.md) for the full developer reference, [`research/INDEX.md`](research/INDEX.md) for the compiled research knowledge base, and [`session_log.md`](session_log.md) for the most recent decisions.

## What it does

- Logs in, navigates the world, fights monsters, loots drops, talks to NPCs, completes quests
- Extracts real-time game state (nearby entities, combat events, XP) directly from the browser via `page.evaluate()`
- Records every action as a `(game_state, reasoning, action)` tuple
- Runs indefinitely in sessions — each session picks up where the last left off
- Supports multi-agent mode: run N agents in parallel for scaled data collection
- 3 capability archetypes (grinder, completionist, explorer_tinkerer) as a data factory for diverse training trajectories

## Current status

For the latest run state, training results, and what's in flight, see
[`session_log.md`](session_log.md) and
[`research/INDEX.md`](research/INDEX.md). The status summary that used to
live here drifted in days; those two files are the source of truth.

- **Harnesses.** `--claude` is the primary data-collection harness and the
  only one whose turns flow into Qwen SFT training. `--codex`, `--gemini`,
  and `--opencode` are experimental smoke-test harnesses that share the
  orchestrator/dashboard/log paths but are excluded from training.
- **Training.** Most recent SFT run + dataset stats: see
  [`research/experiments/training-runs.md`](research/experiments/training-runs.md)
  and [`dataset/DATA.md`](dataset/DATA.md).
- **Eval harness.** `eval_harness.py` runs side-by-side episodes on
  dedicated ports (9061 r9-sft, 9071 base). Live dashboard tab.
- **World model.** WIP concept in [`world/`](world/). Not prioritized.

## Architecture

```
play.sh ──────► Claude/Codex/Gemini/OpenCode CLI ──► mcp_server/ (FastMCP) ──► Playwright ──► browser
                       │                                  │                        │
                 reads system.md +                17 typed tools             page.evaluate()
                 game_knowledge.md                (observe, attack,           calls state_extractor.js
                       │                          navigate, warp...)          helpers internally
                       │                                  │
                       └──► logs/session_N_*.log (auto-logged JSONL)

                  dashboard (8080) ◄─── HLS (/tmp/hls/agent_N) + Mongo (kaetram_devlopment, 27017)
```

**`mcp_server/`** — custom FastMCP package exposing 17 typed game tools (entry point `mcp_game_server.py`). Manages Playwright internally. Agents call structured tools — never write JavaScript. See [`mcp_server/README.md`](mcp_server/README.md).

**`state_extractor.js`** — injected into browser via `context.add_init_script()`. Exposes `window.__extractGameState()`, `window.__attackMob()`, `window.__navigateTo()`, etc. Called by MCP server internally, never by the agent.

**`prompts/system.md`** — agent system prompt: OODA loop, decision tree, tool descriptions. Uses XML tags for structure. ~90 lines.

**`prompts/game_knowledge.md`** — game-specific knowledge (quest walkthroughs, NPC coords) appended to all agents

## Quick start

### Single-agent mode

Run each in its own terminal:

```bash
# Terminal 1 — Kaetram game server (Node 20 required)
./scripts/start-kaetram.sh

# Terminal 2 — Dashboard (optional, live monitoring)
./scripts/start-dashboard.sh

# Terminal 3 — Agent loop (must be a separate terminal — see gotchas)
./play.sh
```

> **`play.sh` must always be in its own terminal.** Running it as a subprocess of Claude Code deadlocks both processes on the shared Playwright MCP browser.

### Multi-agent mode (scaled data collection)

Run N agents in parallel, each with its own Kaetram server instance. The preferred entry point is `restart-agent.sh`, which kills stale processes, resets MongoDB player state, clears sandbox state, and launches the orchestrator under tmux (`datacol` session):

```bash
# Default: 4 agents for 24 hours (round-robin personalities)
./scripts/restart-agent.sh

# 4 agents, no time limit
./scripts/restart-agent.sh 4 0

# One of each archetype
./scripts/restart-agent.sh --grinder 1 --completionist 1 --explorer 1 --hours 0

# Resume without DB reset (preserves character progress)
./scripts/resume-agent.sh --hours 8

# Restart a single agent (0-3) without affecting the others
./scripts/restart-single-agent.sh 2 --reset
```

Each agent gets its own server port (9001, 9011, 9021, 9031), username (`ClaudeBot0`–`ClaudeBot3`), log directory, and personality. All agents get `prompts/game_knowledge.md` (quest guides, NPC coords, mob stats). Resource budget for 3 agents (active collection config): ~2.5 GB RAM, ~27% CPU, ~4.5 GB disk/24h.

> **Default agent count is 3** (was 8 prior to commit `3909f97`, dropped after the 2026-04-19 CPU-starvation reboot that prompted the e2-standard-4→8 VM upgrade). Pass an explicit count to `restart-agent.sh` if you want more or fewer.

> **Harness flags:** `--claude` (primary, training data source) is fully integrated. `--codex` (GPT-5.4, Stop hook), `--gemini` (Gemini 2.5 Flash, `maxSessionTurns`), and `--opencode` (NVIDIA Qwen free API via OpenCode CLI; uses `opencode.template.json` + `AGENTS.md`; reasoning capture requires the NIM proxy at `scripts/start-nim-proxy.sh`) are experimental — their logs are collected but excluded from Qwen SFT training until validated. See [`CLAUDE.md`](CLAUDE.md) for details.

### End-to-end data pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents for 24 hours
```

## Training pipeline

Four-stage pipeline transforms raw Claude session logs into SFT + KTO training data for Qwen3.5 9B:

```
logs/session_*.log  ──►  extract_turns.py  ──►  dataset/extracted/*/turns.jsonl
                                                         │
                                                convert_to_qwen.py  ──►  dataset/qwen_sft/train.json
                                                         │                dataset/qwen_sft/val.json
                                                         │                      │
                                                         │          finetune/train_modal.py (SFT, H100)
                                                         │
                               score_sessions.py + build_kto_dataset.py
                                                         │
                                                dataset/kto/*.json
                                                         │
                                            finetune/train_kto_modal.py (KTO, H100)
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

### Model-Visible Tool Vocabulary (17 tools)

| Tool | Description |
|------|-------------|
| `observe` | Game state JSON + ASCII map + stuck check |
| `attack(mob_name)` | Attack nearest mob by name |
| `set_attack_style(style)` | hack, chop, or defensive |
| `navigate(x, y)` | BFS pathfinding to grid coords |
| `warp(location)` | Fast travel (mudwich, aynor, lakesworld, crullfield, patsow, undersea). Auto-waits combat cooldown. |
| `cancel_nav` | Cancel navigation |
| `interact_npc(npc_name, accept_quest_offer=False)` | Walk to NPC, advance through dialogue, turn in eligible quests. Quest offers are read-only by default; pass `accept_quest_offer=True` to actually accept. |
| `buy_item(npc_name, item_index, count)` | Buy an item from an NPC shop |
| `eat_food(slot)` | Eat food to heal (fails at full HP) |
| `drop_item(slot)` | Drop item to free inventory space |
| `equip_item(slot)` | Equip item (returns success/failure with reason) |
| `stuck_reset` | Reset stuck detection |
| `respawn` | Respawn after death |
| `gather(resource_name)` | Gather from a tree, rock, bush, or fish spot |
| `loot` | Pick up nearby ground items and lootbag contents |
| `query_quest(quest_name)` | Look up walkthrough for a specific quest |
| `craft_item(skill, recipe_key, count)` | Open the right production interface and craft/cook/smelt the requested recipe |

> **Note:** The live MCP export now matches this 17-tool model-visible surface exactly. Deprecated wrappers (`login`, `move`, `talk_npc`, `accept_quest`, `clear_combat`, `click_tile`) were removed from the exported action space so the missing production primitive could be added without reintroducing tool bloat.

## Project structure

```
kaetram-agent/
├── mcp_game_server.py       # 19-line stub — entry point that imports mcp_server.tools
├── mcp_server/              # Modular MCP package (17 typed tools). See README.
├── cli_adapter.py           # Harness abstraction (Claude = production; Codex, Gemini, OpenCode = experimental)
├── play.sh                  # Claude Code agent loop (resolves .mcp.template.json)
├── play_qwen.py             # Finetuned-model agent loop — same MCP server
├── play_qwen.sh             # Qwen agent session launcher
├── orchestrate.py           # Multi-agent launcher: game servers, Xvfb, ffmpeg, MCP, harness
├── extract_turns.py         # JSONL log → clean OODA turn extraction
├── convert_to_qwen.py       # Turns → Qwen3.5 9B SFT/GRPO format
├── state_extractor.js       # Injected into browser — game helpers (called by MCP server)
├── .mcp.template.json       # MCP config template (placeholders resolved per-sandbox at launch)
├── opencode.template.json   # OpenCode provider config template (NVIDIA Qwen)
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
│   ├── train_modal.py       # SFT training on Modal (Unsloth, H100)
│   ├── train_kto_modal.py   # KTO preference learning on Modal (H100)
│   ├── train_grpo_modal.py  # GRPO reinforcement learning on Modal
│   ├── serve_modal.py       # vLLM serving endpoint (OpenAI-compatible)
│   ├── convert_gguf.py      # Model → GGUF Q4_K_M conversion
│   └── merge_and_quantize.py # LoRA merge + GGUF export (local)
├── score_sessions.py        # Score sessions 0-1 for KTO labels (XP/quest/exploration)
├── build_kto_dataset.py     # Build sliding-window KTO prompt/completion/label records
├── inspect_kto_dataset.py   # KTO dataset dry-run and sample inspection
├── research/                # Compiled research knowledge base (see research/INDEX.md)
│   ├── experiments/         # Training run history, data quality metrics
│   ├── related-work/        # Paper surveys (KTO, DPO, GRPO, agent SFT landscape)
│   ├── decisions/           # WHY docs (why KTO over PPO, r7 hyperparameters)
│   └── paper/               # ICLR 2027 contribution framing
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
│   └── personalities/       # Archetype DECIDE overrides (grinder, completionist, explorer_tinkerer)
├── scripts/
│   ├── start-kaetram.sh     # Starts Kaetram server (handles nvm use 20)
│   ├── restart-agent.sh     # Primary command: kill + restart agents fresh (resets DB)
│   ├── restart-single-agent.sh # Restart one agent (0-3) without affecting the others
│   ├── resume-agent.sh      # Resume agents without DB reset
│   ├── nuke-agents.sh       # Stop all agents (SIGKILL everything)
│   ├── reset-state.sh       # Reset MongoDB player data only
│   ├── collect_sft_data.sh  # End-to-end: orchestrate → extract → convert
│   ├── check_research_staleness.py      # Detect stale research/ files
│   ├── run_research_staleness_check.sh  # VM cron wrapper: auto-compile + commit + push
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
| 8081 | Dashboard WebSocket relay (state, activity, heartbeat, screenshot) |
| 27017 | MongoDB (`kaetram-mongo` Docker container, db `kaetram_devlopment`) |
| 9061, 9071 | Eval game servers (r9-sft, base) |
| 9191 | E2E test-lane game server (db `kaetram_e2e`) |

## Slash commands

| Command | When to use |
|---------|-------------|
| `/game-session` | Check what's running, get startup commands, see port status |
| `/verify-pipeline` | Confirm data is flowing, inspect latest training record |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |
| `/compile-research` | LLM compile pass over `research/` — fix stale facts, add missing entries |

## Gotchas

**Playwright subprocess deadlock** — `play.sh` must run in a separate terminal. Spawning it as a subprocess of Claude Code deadlocks both on the shared Playwright MCP browser.

**Node 20 required** — Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial. The agent uses warp to skip this.

**Absolute screenshot paths** — Playwright MCP requires absolute paths. Relative paths cause it to navigate the browser to the path as a URL.

**Multi-agent port conflicts** — If running `orchestrate.py`, kill any existing Kaetram servers first. The orchestrator manages its own server instances.

## Finetuned agent (Qwen3.5 9B)

The finetuned Qwen3.5-9B model is served from a Modal SGLang endpoint
(`finetune/serve_modal.py`) and exercised by the eval harness:

```bash
# Direct mode — play_qwen.py drives the browser, calls the Modal endpoint, uses the same mcp_server
./play_qwen.sh

# Side-by-side eval (r9-sft vs base) — see scripts/run-eval.sh
./scripts/run-eval.sh
```

**Dual-VM architecture:**
- **GCP VM** (`34.28.111.6`): Kaetram game server + client, data collection, training pipeline.
- **GPU VM** (`73.173.11.56:1738`, RTX 3060 12GB): Local-inference experiments + agent harness via Playwright. Connects back to the GCP VM for the game world. See `finetune/SETUP_3060.md`.

## World model (WIP)

Experimental forward dynamics model (2.2M param Transformer) in `world/`. Concept for MCTS planning and reward shaping — not prioritized. See `world/README.md` for details.

## Research Contribution

This project is the basis for an arXiv paper on **structured game-agent distillation** — distilling frontier LLM gameplay reasoning into a small open model using a typed tool API as the teacher-student interface.

Unlike prior work where LLMs serve as decision advisors for human players ([Think in Games](https://arxiv.org/abs/2508.21365)), generate raw code or click pixels ([CRADLE](https://arxiv.org/abs/2403.03186), [Voyager](https://arxiv.org/abs/2305.16291)), or operate in episodic single-player environments ([Orak](https://arxiv.org/abs/2506.03610), [GamingAgent](https://arxiv.org/abs/2505.15146)), **our agent operates fully autonomously in a persistent open world using a shared typed tool API as the teacher-student interface.**

### What's novel

**1. Shared typed MCP tool vocabulary** — Teacher (Claude) and student (Qwen3.5-9B) call the same 17 typed tools (`attack("goblin")`, `navigate(188, 157)`, `interact_npc("Blacksmith")`). This eliminates action space mismatch between teacher and student at training time — a structural problem in prior game-agent distillation where teachers write raw code or click pixels the student can't reliably reproduce.

**2. Capability-diverse teacher data** — 3 Claude agents with orthogonal capability archetypes (GRINDER, COMPLETIONIST, EXPLORER/TINKERER) produce structurally different decision distributions at overlapping game states. The student learns a richer action distribution than any single teacher policy provides. Archetypes are a data-factory mechanism, not a scientific claim — if trajectories collapse, we fall back to two policies (progression and uncertainty/recovery/coverage).

**3. KTO preference learning with automated game outcome scoring** — After SFT, we apply KTO using a 6-dimension composite reward signal (XP gain, level delta, quest progression, exploration, turn quality, death penalty). No human labels. Fully automated. Scales with agent runtime. Fits the MMORPG setting where there is no binary win condition.

### Comparison

| | This project | Think in Games | Orak | CRADLE / Voyager |
|---|---|---|---|---|
| Agent autonomy | Fully autonomous | Decision advisor (human executes) | Autonomous | Autonomous |
| World type | Persistent MMORPG | Episodic MOBA (replays) | Episodic single-player | Open-ended |
| Action interface | Shared typed MCP tools | 40 categorical labels | Heterogeneous per-game MCP | Raw code / pixel clicks |
| Teacher diversity | 3 personality-distinct agents | Single teacher model | Single teacher model | Single teacher model |
| Post-SFT refinement | KTO (offline, composite reward) | GRPO (online RL, Tencent scale) | None | None |
| Open source | Full (game + data + pipeline) | No (proprietary Tencent data) | Partial (closed games) | Partial |

See [`research/paper/contribution.md`](research/paper/contribution.md) for full novelty framing, ablation plan, and paper outline.

---

## License

Tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0).
