# Kaetram AI Agent

![Kaetram Observatory — live monitoring of three Claude agents (grinder, completionist, explorer) playing Kaetram in parallel](assets/kaetram.jpg)

**Research project** on **structured game-agent distillation** — distilling frontier LLM gameplay reasoning (Claude Sonnet) into a small open student model (Qwen3.5 9B) using a typed MCP tool API as the shared teacher–student interface in a persistent 2D pixel MMORPG ([Kaetram](https://github.com/Kaetram/Kaetram-Open)).

The agent calls 17 structured tools (observe, attack, navigate, interact_npc, gather, craft_item, …) — never writes JavaScript or clicks pixels. Sessions across **4 frontier-LLM harnesses** (Claude / Codex / Gemini / OpenCode) are collected as SFT training data, with OpenCode multiplexing across xAI Grok, NVIDIA Qwen3.5, and DeepSeek V4 via `--opencode-model`. Progress is measured against the **Core 3 quest benchmark** (see below).

> **For developers:** see [`CLAUDE.md`](CLAUDE.md) for the full developer reference and [`session_log.md`](session_log.md) for the most recent decisions.

## What it does

- Logs in, navigates the world, fights monsters, loots drops, talks to NPCs, completes quests
- Extracts real-time game state (nearby entities, combat events, XP) directly from the browser via `page.evaluate()`
- Records every action as a `(game_state, reasoning, action)` tuple
- Runs indefinitely in sessions — each session picks up where the last left off
- Supports multi-agent mode: run N agents in parallel for scaled data collection
- 3 capability archetypes (GRINDER, COMPLETIONIST, EXPLORER_TINKERER) as a data factory for diverse training trajectories

## The Core 3 benchmark

Capability progress is measured against three canonical quests that span combat, gathering, crafting, dialogue, and long-route navigation. Each is implemented as a headed pytest under `tests/e2e/quests/` and run from the dashboard's **Tests tab** (see below).

| # | Quest | What it exercises |
|---|-------|-------------------|
| Q1 | **Foresting** | Woodcutting + simple multi-step gather/turn-in |
| Q2 | **Herbalist's Desperation** | Long-tail gathering (blueberries, Blue Lily) + skill-gated foraging |
| Q3 | **Rick's Roll** | Fishing + cooking via `craft_item` + dialogue branching + cross-region door routing |

The student model's quest completion rate on the Core 3 — alongside the Sonnet teacher's — is the primary capability metric, replacing earlier ad-hoc XP/level deltas.

## Current status

For the latest run state, training results, and what's in flight, see
[`session_log.md`](session_log.md) — that's the source of truth for fast-moving status.

- **Harnesses.** `--claude` is the primary data-collection harness and the
  only one whose turns flow into Qwen SFT training. `--codex`, `--gemini`,
  and `--opencode` (multi-model: Grok / Qwen / DeepSeek via `--opencode-model
  <alias>`) are experimental peer harnesses that share the
  orchestrator/dashboard/log paths but are excluded from training.
- **Training.** Dataset stats: [`dataset/DATA.md`](dataset/DATA.md).
- **Eval harness.** `eval_harness.py` runs side-by-side episodes on
  dedicated ports (9061 r10-sft, 9071 base). Live dashboard tab.
- **World model.** Deprecated — [`world/`](world/) is not in use (targets an older log shape).
- **Iteration history.** r1-r9 were rapid exploratory cycles; r10 onward is the
  deliberate phase — see [`research/INDEX.md`](research/INDEX.md) for the
  methodological turn.

## Architecture

```
play.sh ──► Claude / Codex / Gemini / OpenCode CLI ──► mcp_server/ (FastMCP) ──► Playwright ──► browser
                                       └─ OpenCode → xAI Grok / NVIDIA Qwen / DeepSeek (via --opencode-model)
                       │                                                  │                        │
                 reads system.md +                                17 typed tools             page.evaluate()
                 game_knowledge.md                                (observe, attack,           calls state_extractor.js
                       │                                          navigate, warp...)          helpers internally
                       │                                                  │
                       └──► logs/session_N_*.log (auto-logged JSONL)

                  dashboard (8080) ◄─── HLS (/tmp/hls/agent_N) + Mongo (kaetram_devlopment, 27017)
                                  ◄─── Tests tab (Xvfb :198 + ffmpeg MJPEG, headed pytest runs)
```

**`mcp_server/`** — modular FastMCP package exposing 17 typed game tools. Was a single 2039-line `mcp_game_server.py` until PR #29 (2026-04-25); now split into `mcp_server/{core, helpers, login, mob_stats, resource_gates, state_heartbeat, utils}.py` + `tools/`, with `mcp_game_server.py` reduced to a 30-line stub entry point. Manages Playwright internally. Agents call structured tools — never write JavaScript. See [`mcp_server/README.md`](mcp_server/README.md).

**`state_extractor.js`** — injected into browser via `context.add_init_script()`. Exposes `window.__extractGameState()`, `window.__attackMob()`, `window.__navigateTo()`, etc. Called by MCP server internally, never by the agent.

**`prompts/system.md`** — agent system prompt: OODA loop, decision tree, tool descriptions. Uses XML tags for structure. ~75 lines.

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
# Default: 3 agents, 24 hours (one per archetype)
./scripts/restart-agent.sh

# 3 agents, no time limit
./scripts/restart-agent.sh 3 0

# One of each archetype
./scripts/restart-agent.sh --grinder 1 --completionist 1 --explorer 1 --hours 0

# Resume without DB reset (preserves character progress)
./scripts/resume-agent.sh --hours 8

# Restart a single agent (0-3) without affecting the others
./scripts/restart-single-agent.sh 2 --reset
```

Each agent gets its own server port (9001, 9011, 9021), log directory, capability archetype, and an in-game username determined by harness: `ClaudeBot{N}`, `CodexBot{N}`, `GeminiBot{N}`, the in-house Qwen harness uses personality-based names `QwenGrinder` / `QwenCompletionist` / `QwenExplorer`, and opencode splits by model family — `BigQwenBot{N}` / `GrokBot{N}` / `DeepSeekBot{N}` / `OpenCodeBot{N}` (see `cli_adapter.opencode_bot_prefix`). All agents get `prompts/game_knowledge.md` (quest guides, NPC coords, mob stats). Resource budget for 3 agents on the active VM (`e2-standard-8`): ~2.5 GB RAM, well under 50% CPU.

> **Default agent count is 3 — one per archetype** (grinder + completionist + explorer-tinkerer). Set in `scripts/restart-agent.sh` after the 2026-04-28 standardization. Pass an explicit count or per-archetype flags to deviate.

> **Harness flags.** `--claude` (Sonnet, primary, training data source) is fully integrated. The others are experimental peer harnesses — their logs are collected but excluded from Qwen SFT training until validated:
> - `--codex` — OpenAI Codex (GPT-5.4), Stop hook for turn continuation
> - `--gemini` — Google Gemini 3 Flash, `maxSessionTurns` for turn limit
> - `--opencode` — multi-model via `--opencode-model <alias>`. Aliases: `grok-4-1-fast`, `qwen3.5-35a3b`, `qwen3.5-397a17b`, `qwen3-80a3b`, `deepseek-v4-flash`, `deepseek-v4-pro` (or any fully-qualified `provider/model` ID). NIM-routed Qwen models need `scripts/start-nim-proxy.sh`; DeepSeek needs `DEEPSEEK_API_KEY`; xAI needs `XAI_API_KEY`.
> - `--qwen-sft` / `--qwen-base` — in-house Qwen3.5-9B served on Modal SGLang. SFT routes to the finetuned endpoint and labels `model='r10-sft'`; base routes to the unfinetuned endpoint and labels `model='kaetram-base'`. Both spawn `play_qwen.py` per session via `QwenAdapter`; sessions roll over when context approaches 16K. Mixable in one run for direct A/B.
>
> See [`CLAUDE.md`](CLAUDE.md) for full details on each harness.

### End-to-end data pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 3 24    # 3 agents for 24 hours
```

## Training pipeline

Three stages transform raw Claude session logs into SFT training data for Qwen3.5 9B:

1. **Extract turns** (`extract_turns.py`) — parse JSONL session logs, identify OODA cycles, emit `(game_state, reasoning, action)` tuples per agent.
2. **Convert to Qwen format** (`convert_to_qwen.py`) — Qwen3.5 9B conversation records with `<think>` + `<action>` tags. 90/10 train/val split stratified by session. Modes: `single` / `multi` / `mixed` (default 70/30). Format: `sft` or `grpo`.
3. **Train + serve** — `finetune/train_modal.py` (SFT) on Modal H100s; `finetune/serve_modal.py` exposes an OpenAI-compatible SGLang endpoint for the eval harness.

**Post-SFT refinement (r11):** on-policy distillation (OPD) — the student rolls out in-game and a scaffolded larger same-family teacher supervises on the student's own visited states, co-evolving with harness-affordance improvements rather than a frozen scaffold. Current instantiation: Qwen3.5-4B teacher → base-2B student (`scripts/opd/`, `finetune/train_opd_2b.py`; see `research/experiments/opd-2b.md`). A KTO preference-learning track (`score_sessions.py` + `build_kto_dataset.py` → `finetune/train_kto_modal.py`, automated 0–1 game-outcome labels) is scaffolded but deferred.

### Model-visible tool vocabulary (17 tools)

Teacher and student call the same surface. Categories:

- **Core loop:** `observe`
- **Combat:** `attack(mob_name)` (auto-loots on kill), `set_attack_style(style)`, `eat_food(slot)`, `respawn`, `loot` (free-standing drops only)
- **Movement:** `navigate(x, y)`, `warp(location)`, `cancel_nav`, `stuck_reset`
- **Dialogue / quests:** `interact_npc(npc_name, accept_quest_offer=False)` (returns `quest_opened` / `quest_accepted` / `quest_offered` / `quest_state_changed`), `query_quest(quest_name)`
- **Economy / inventory:** `buy_item(npc_name, item_index, count)` (auto-walks to NPC + opens shop), `equip_item(slot)`, `drop_item(slot)`
- **Production:** `gather(resource_name)`, `craft_item(skill, recipe_key, count)` (auto-walks to nearest station on the current map)

The live MCP export matches this surface exactly — deprecated wrappers were removed in PR #29 to avoid tool-bloat regression. Per-tool reference: [`mcp_server/README.md`](mcp_server/README.md).

## Project structure

```
kaetram-agent/
├── mcp_server/              # Modular FastMCP package — 17 typed game tools (see mcp_server/README.md)
├── mcp_game_server.py       # 30-line stub entry point
├── cli_adapter.py           # Harness abstraction (Claude / Codex / Gemini / OpenCode / Qwen); opencode model aliases + bot-prefix helper
├── bootstrap.py             # Single source of truth for the orchestrate user bootstrap
├── play.sh                  # Single-agent dev loop (Claude / Codex / Gemini / OpenCode)
├── play_qwen.py             # Per-session Qwen subprocess (spawned by orchestrate --qwen-sft / --qwen-base or solo dev)
├── orchestrate.py           # Multi-agent launcher: game servers, Xvfb, ffmpeg, MCP, harness
├── extract_turns.py, convert_to_qwen.py  # SFT data pipeline (logs → Qwen records)
├── score_sessions.py, build_kto_dataset.py, inspect_kto_dataset.py  # KTO data pipeline (scaffolded, deferred)
├── eval_harness.py          # Side-by-side episode runner (r10-sft vs base)
├── state_extractor.js       # Injected browser helpers (called by MCP server)
├── dashboard/               # Live web dashboard + Tests tab (DB-first game state, MJPEG video)
├── finetune/                # SFT training + serving on Modal (KTO / GRPO scaffolded, deferred)
├── world/                   # Deprecated forward dynamics model (2.2M param Transformer) — not in use
├── prompts/                 # system.md, game_knowledge.md, personalities/
├── tests/                   # e2e quest tests, including Core 3 reachability under tests/e2e/quests/reachability/
├── scripts/                 # restart/resume/nuke agents, eval, dashboards
├── dataset/, state/, logs/  # Runtime artefacts (gitignored)
├── session_log.md           # Running decision log across sessions
└── CLAUDE.md                # Developer reference
```

## Ports

| Port | What |
|------|------|
| 9000 | Kaetram game client (HTTP, shared across agents) |
| 9001 | Kaetram game server WS (single-agent default) |
| 9001, 9011, 9021, 9031 | Game server WS (multi-agent, one per agent) |
| 8080 | Dashboard |
| 8081 | Dashboard WebSocket relay (state, activity, heartbeat) |
| 27017 | MongoDB (`kaetram-mongo` Docker container, db `kaetram_devlopment`) |
| 9061, 9071 | Eval game servers (r10-sft, base) |
| 9191 | E2E test-lane game server (db `kaetram_e2e`) |

## Tests tab (dashboard)

The dashboard at `http://localhost:8080` includes a **Tests tab** for launching headed pytest runs from the UI with live MJPEG video of the browser. This is how the Core 3 reachability suite (and the broader quest suite under `tests/e2e/quests/`) is exercised end-to-end against a real Kaetram instance.

- Uses a dedicated test-lane game server on **port 9191** (db `kaetram_e2e`) — start via `scripts/start-test-kaetram.sh`.
- Renders the headed browser into Xvfb display `:198`, captured by ffmpeg as a single overwriting MJPEG stream (lockstep reliable on short test runs, unlike HLS).
- Run history is persisted; per-test status pills update live via the dashboard WebSocket.
- Terminal-launched pytest runs also surface here via the `/ingest/test_event` CLI shim.

Backend lives in `dashboard/test_runner.py`; full reference in `dashboard/DASHBOARD.md`.

## Slash commands

| Command | When to use |
|---------|-------------|
| `/log-analysis` | Per-agent run analysis, Core 3 quest progression, 5 paper metrics |
| `/compile-research` | Lint pass over `research/` knowledge base |

## Gotchas

**Playwright subprocess deadlock** — `play.sh` must run in a separate terminal. Spawning it as a subprocess of Claude Code deadlocks both on the shared Playwright MCP browser.

**Node 20 required** — Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial. The agent uses warp to skip this.

**Multi-agent port conflicts** — If running `orchestrate.py`, kill any existing Kaetram servers first. The orchestrator manages its own server instances.

## Finetuned agent (Qwen3.5 9B)

The finetuned Qwen3.5-9B model is served from a Modal SGLang endpoint
(`finetune/serve_modal.py`) and exercised by the eval harness:

```bash
# Multi-agent run (3 personalities in parallel via orchestrate, like Claude)
./scripts/restart-agent.sh --qwen-sft 3 --grinder 1 --completionist 1 --explorer 1 --hours 3

# Solo dev — direct invocation (warm-session loop: MCP + browser persist
# across context rollovers; --max-duration-seconds 0 = unbounded)
python3 play_qwen.py --endpoint <modal-url> --personality completionist \
  --system-prompt prompts/system.md --sandbox /tmp/qwen_dev

# Side-by-side eval (r10-sft vs base) — see scripts/run-eval.sh
./scripts/run-eval.sh
```

**Architecture:** GCP VM (`vm.example.com`) hosts the Kaetram game server + client, data collection, and training pipeline. Training and serving both run on Modal (H100); the r10 model is exposed via `finetune/serve_modal.py` and consumed by `eval_harness.py` / `play_qwen.py`.

## World model (deprecated)

Forward dynamics model (2.2M param Transformer) in `world/`. **Deprecated / not in use** — it targets an older `browser_run_code` log shape and is not maintained against the current MCP harness. Originally a concept for MCTS planning and reward shaping. See `world/README.md` for details.

## Research contribution

This project is the basis for a **planned research paper** on **structured game-agent distillation** — distilling frontier LLM gameplay reasoning into a small open model using a typed tool API as the teacher-student interface.

Unlike prior work where LLMs serve as decision advisors for human players ([Think in Games](https://arxiv.org/abs/2508.21365)), generate raw code or click pixels ([CRADLE](https://arxiv.org/abs/2403.03186), [Voyager](https://arxiv.org/abs/2305.16291)), or operate in episodic single-player environments ([Orak](https://arxiv.org/abs/2506.03610), [GamingAgent](https://arxiv.org/abs/2505.15146)), **our agent operates fully autonomously in a persistent open world using a shared typed tool API as the teacher-student interface.**

### What's novel

**1. Shared typed MCP tool vocabulary** — Teacher (Claude) and student (Qwen3.5-9B) call the same 17 typed tools (`attack("goblin")`, `navigate(188, 157)`, `interact_npc("Blacksmith")`). This eliminates action space mismatch between teacher and student at training time — a structural problem in prior game-agent distillation where teachers write raw code or click pixels the student can't reliably reproduce.

**2. Capability-diverse teacher data** — Claude agents are run under three orthogonal capability archetypes (GRINDER, COMPLETIONIST, EXPLORER_TINKERER) that produce structurally different decision distributions at overlapping game states. The student learns a richer action distribution than any single teacher policy provides. Archetypes are a data-factory mechanism, not a scientific claim — if trajectories collapse, we fall back to two policies (progression and uncertainty/recovery/coverage).

**3. On-policy distillation, straight from the instruct student** — r11 trains with on-policy distillation (OPD) and no SFT init: the student rolls out in-game and a scaffolded larger same-family teacher supervises on the student's *own* visited states (reverse-KL, per-token), co-evolving with harness-affordance improvements rather than freezing the scaffold. The current round distills a scaffolded Qwen3.5-4B teacher into a base-2B student — a capability-instillation test rather than a regression repair (`research/experiments/opd-2b.md`). A label-free preference-learning track (KTO over a 6-dimension automated game-outcome reward — XP, level delta, quest progression, exploration, turn quality, death penalty) is scaffolded as a deferred alternative. Fits the MMORPG setting where there is no binary win condition.

vs. prior work: persistent MMORPG (not episodic), shared typed MCP tools (not categorical labels / raw code / pixel clicks), capability-archetype teacher diversity (not a single teacher), on-policy distillation refinement (not online RL or none), full open source. Detailed comparison table and novelty framing live off-repo.

---

## License

The agent, training, and evaluation tooling in this repository is released under the
[MIT License](LICENSE). It wraps [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open),
which is licensed separately under MPL-2.0.
