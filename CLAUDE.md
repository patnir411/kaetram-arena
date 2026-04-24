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
After any big update (training infra change, dataset rebuild, new automation, major design shift), update `session_log.md` immediately as well — do not wait for session end.
After any big update that changes repo files, commit it promptly, push to GitHub, and sync the VM if the VM is expected to run that code or read that documentation.

---

## MULTI-MACHINE SYNC PROTOCOL

Two-machine setup (laptop + GCP VM `34.28.111.6`) means origin/main is the only source of truth. Files on laptop and VM drift the moment someone pushes without the other pulling. If an agent edits files on a stale checkout, the resulting diff looks like a revert of the missing commits — because it silently is one.

**Rules (both machines, every agent session):**

1. **Pull before edit.** First thing every session / before spawning any agent: `git fetch origin && git pull --ff-only` on the machine you're about to touch. If non-ff, investigate — local has diverged and blindly forcing loses work.

2. **Branch for shared code, direct for solo lanes.** Push to `feat/…` / `chore/…` branches for anything your cofounder might edit (`eval_harness.py`, `dashboard/`, `prompts/`, `finetune/`, `scripts/`). Direct-to-main only for your own lane (`research/`, `session_log.md`, `.claude/memory/`, personal docs).

3. **Safe VM sync** (when VM may be stale or you're unsure what's uncommitted):
   ```bash
   ssh patnir41@34.28.111.6
   cd /home/patnir41/projects/kaetram-agent
   git stash push -u -m "safety-$(date +%Y%m%d_%H%M)"   # nothing destructive
   git fetch origin && git checkout main && git pull --ff-only
   git stash list                                        # decide per-stash to pop or drop
   tmux ls                                               # running evals are unaffected by working-tree changes
   ```
   Stash-first means nothing is ever destroyed — if a pull brings in a cofounder's work that conflicts with local edits, the stash preserves them for manual reconciliation instead of silently overwriting.

**Why this section exists (incident 2026-04-17):** An agent edited files on the VM before pulling Niral's two just-landed commits (`c7fe0b8` DB-authoritative quest tracking + `eff051f` Qwen Live tab removal). The edits wrote the older version of each file back, so the resulting diff looked like a revert of his work. His commits were still safe on `origin/main`, and nothing ever hit main from the stale tree — but the diff was confusing enough to trigger a cofounder argument. Fix was `git checkout origin/main -- <files>`. The rules above make this impossible to repeat.

---

## RESEARCH KNOWLEDGE BASE (`research/`)

A compiled knowledge base for this project, inspired by Karpathy's LLM Knowledge Bases pattern. Contains conclusions, decisions, experiment outcomes, and paper references — not stream-of-consciousness notes.

**Structure:**
```
research/
├── INDEX.md              — Navigation hub + gaps list
├── experiments/           — Training runs, data quality, ablation results
├── related-work/          — Paper surveys compiled by topic
├── decisions/             — WHY we made key choices (KTO, MCP, personalities, etc.)
└── paper/                 — ICLR 2027 contribution, outline, figures needed
```

**Maintenance rule (MANDATORY — this is what keeps the wiki alive):**
- After any **training run**: update `research/experiments/training-runs.md` with params, results, failures
- After any **data rebuild**: update `research/experiments/data-quality.md` with before/after metrics
- After any **design decision**: update or create a file in `research/decisions/`
- After any **paper-related discussion**: update `research/paper/contribution.md`
- If no file fits: create a new one and link it from `research/INDEX.md`
- On **explicit "health check" or `/compile-research`**: scan all files for stale information, contradictions, missing citations, and update

**Maintenance loop (what is actually reliable):**
- Manual LLM compile pass: `.claude/commands/compile-research.md`
- VM-safe staleness check: `python3 scripts/check_research_staleness.py`
- VM-safe staleness check with email nudge: `python3 scripts/check_research_staleness.py --notify`
- VM cron-friendly wrapper: `scripts/run_research_staleness_check.sh` (sources `~/.kaetram_notify_env` if present, auto-runs `claude -p "/compile-research"` with `claude-opus-4-6` when stale, then stages `research/` + `session_log.md`, commits, rebases, and pushes if changes were made; if Claude CLI is unavailable, it falls back to email)
- Do **not** rely on session-local Claude cron jobs. They die with the session and should not be treated as durable automation. The durable loop is VM cron + the wrapper script.

**What goes here vs elsewhere:**
- `research/` = compiled knowledge (survives across sessions, serves the paper)
- `session_log.md` = scratchpad (recent decisions, gets overwritten)
- `.claude/memory/` = session context (user prefs, git rules, startup)
- Linear = task tracking (what to do, not what we learned)

---

## GOTCHAS

**Node.js version** — Kaetram requires Node 16/18/20. Node 24/25 crashes on startup (uWS.js incompatibility). Always `nvm use 20` before starting the server.

**Port conflicts** — If the server is restarted without killing old processes, the client binds to a random port instead of 9000. Kill all node processes first.

**yarn build required** — After cloning, `yarn start` alone fails. Run `yarn build` first.

**MCP server uses Python venv** — `mcp_game_server.py` requires `.venv` with `mcp[cli]` and `playwright` installed. The `.mcp.json` template references `__VENV_PYTHON__` which resolves to `.venv/bin/python3`.

**`.mcp.json` is a template** — Contains placeholders (`__VENV_PYTHON__`, `__PROJECT_DIR__`, `__SERVER_PORT__`, `__USERNAME__`, `__SCREENSHOT_DIR__`). Resolved by `cli_adapter.py` or `play.sh` at launch time. Claude Code uses `--mcp-config` + `--strict-mcp-config` to read the resolved copy from the sandbox, NOT the project-level template.

**rsLoRA + `alpha=r` is an 8x LR trap** — rsLoRA scales adapters by `1/sqrt(r)` instead of `1/r`. With our standard config `r=alpha=64` that means effective scaling is `64/sqrt(64)=8.0` instead of `64/64=1.0` — an 8x effective LR bump for free. r7 diverged immediately the first time we enabled it. Keep `use_rslora=False` on `finetune/train_modal.py` unless you also rebalance alpha. The comment on line 359 is load-bearing.

**Qwen3 chat template silently drops `<think>` on intermediate turns** — Stock Qwen3.5 chat template strips `<think>` reasoning from all assistant messages before `last_query_index` in multi-turn conversations (QwenLM/Qwen3 issue #1831). ~70% of our records are multi-turn windows, so pre-r7 runs were training on "action only, no reasoning" for every intermediate turn. If you ever touch the tokenizer/template, re-verify that intermediate-turn CoT survives round-tripping through `apply_chat_template`.

---

## MANAGING TRAINING RUNS

### Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/restart-agent.sh [N] [H]` | **Primary command.** Kills everything, resets DB (fresh Level 1), clears state, relaunches N agents for H hours. Default: 3 agents, 24h. Use `0` for no time limit. Supports personality and harness flags. |
| `./scripts/resume-agent.sh` | Resume agents without DB reset. Preserves character progress. Supports personality and harness flags. |
| `./scripts/restart-single-agent.sh <ID>` | Restart one running agent (agent 0-2) without affecting others. Clears session counter for fresh start. Supports `--reset`, personality, and harness switches. |
| `./scripts/nuke-agents.sh` | **Stop all agents.** SIGKILL everything agent-related — orchestrator, claude agents, MCP servers, browsers, game servers. Always use this to stop. |
| `./scripts/reset-state.sh [N] [--force]` | Reset MongoDB player data only (no restart). Use `--force` to skip safety check. |
| `./scripts/start-kaetram.sh` | Start Kaetram game server (single-agent mode, Node 20 required). |

### Harness Flags

**Production-ready:** `--claude`, `--codex`, `--gemini`, `--opencode` are fully integrated end-to-end (MCP, dashboard, health checks, log parsing, data isolation). Kimi and Qwen Code are WIP.

- `--claude` → Sonnet (Claude Code) — primary data collection harness
- `--codex` → GPT-5.4 (OpenAI Codex) — uses stop hook for turn continuation
- `--gemini` → Gemini 2.5 Flash (Google Gemini CLI) — uses maxSessionTurns in settings.json
- `--opencode` → local Ollama via OpenCode CLI — reads `opencode.json` (resolved from `opencode.template.json`) + `AGENTS.md` from sandbox CWD. Model defaults to `ollama/kaetram`; override with `OPENCODE_MODEL=provider/model`. Provider registered in `opencode.template.json` points at local Ollama on :11434 — run `ollama serve` first.
- `--kimi` → Kimi K2 — WIP
- `--qwen-code` → Qwen3-Coder — WIP

### Personality archetypes (data-factory policies)

The three personality prompts map to concrete gameplay-capability axes, not
vibes. Each archetype produces a distinct tool-use signature so
outcome-filtered trajectories cover complementary capabilities for SFT/KTO.

| Flag | File | Axis | Signature tools |
|---|---|---|---|
| `--completionist` | `prompts/personalities/completionist.md` | progression | `interact_npc`, `query_quest`, `gather`, `craft_item` |
| `--grinder` | `prompts/personalities/grinder.md` | combat / leveling | `attack`, `loot`, `equip_item`, `eat_food` |
| `--explorer_tinkerer` (or `--explorer`) | `prompts/personalities/explorer_tinkerer.md` | world + systems coverage | `navigate`, `interact_npc` (non-quest too), `buy_item`, `craft_item` (novel), `warp` |

Legacy vibe flags are kept as aliases so existing `scripts/restart-agent.sh`
invocations keep working: `--aggressive → grinder`, `--methodical →
completionist`, `--curious → explorer_tinkerer`, `--efficient → completionist`.

### Quick start (multi-agent)

```bash
# Default: 3 Claude agents, 24h
./scripts/restart-agent.sh 3 0

# 3 Codex agents with mixed archetypes
./scripts/restart-agent.sh --codex 3 --grinder 1 --completionist 1 --explorer 1 --hours 3

# 3 Gemini agents
./scripts/restart-agent.sh --gemini 3 --grinder 1 --completionist 1 --explorer 1 --hours 1

# Mixed harnesses
./scripts/restart-agent.sh --claude 1 --codex 1 --gemini 1 --hours 0

# With legacy vibe flags (aliased — still works)
./scripts/restart-agent.sh --aggressive 2 --curious 2 --hours 24

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
4. Clears sandbox state (screenshots, game_state.json)
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
- `--personality {aggressive,methodical,curious}` — Change playstyle

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
   ./scripts/start-dashboard.sh       # Start (kills existing first)
   ./scripts/stop-dashboard.sh        # Stop
   ./scripts/restart-dashboard.sh     # Restart (after template/code changes)
   ```
   Log: `/tmp/dashboard.log`. Exit code 144 from zsh on `kill` is normal.

3. **Terminal 3 — Agent loop** — MUST be separate terminal (never subprocess)
   ```bash
   ./play.sh                    # Claude (default)
   ./play.sh --kimi --curious   # Kimi with thinking
   ./play.sh --qwen-code        # Qwen Code
   ./play.sh --codex            # Codex
   ```

### Multi-agent mode (scaled data collection)

```bash
# 3 agents, no time limit (round-robin personalities)
./scripts/restart-agent.sh 3 0

# 2 agents, 8 hours
./scripts/restart-agent.sh 2 8

# One of each personality
./scripts/restart-agent.sh --aggressive 1 --methodical 1 --curious 1 --hours 0
```

Port allocation: agent N gets server WS port `9001 + N*10` (9001, 9011, 9021). All agents share the static client on port 9000. Each agent logs in as `ClaudeBotN`.

**Agent slots:**
- **agent_0–2**: Claude Code agents (data collection, training data source)

Claude agents use sandboxes at `/tmp/kaetram_agent_0/` through `/tmp/kaetram_agent_2/` with ports 9001, 9011, 9021.

**Eval slots** (separate from data collection, managed by `eval_harness.py` / `scripts/run-eval.sh`):
- **eval r9-sft**: port 9061, username `evalbotSFT`, sandbox `/tmp/kaetram_eval_*`
- **eval base**: port 9071, username `evalbotBase`, sandbox `/tmp/kaetram_eval_*`

**GOTCHA — Kaetram game server port override:** `PORT=X yarn start` does NOT work. Kaetram's config reads PORT from the `.env` file, not `process.env`. Use `node --enable-source-maps dist/main.js --port X` from `packages/server/` instead (the `--port` CLI arg overrides config directly). This is how `orchestrate.py` starts game servers.

**Agent playstyles:** Each agent gets a playstyle that defines its DECIDE priorities in `system.md`. Playstyle files in `prompts/personalities/` are injected via the `__PERSONALITY_BLOCK__` placeholder. All agents get `game_knowledge.md` appended. Dashboard shows playstyle badges (red=AGGRESSIVE, amber=METHODICAL, blue=CURIOUS). Active collection uses 3 agents. Each agent's sandbox gets a `metadata.json` with its playstyle.

| Flag | Playstyle | Color | Approach |
|------|-----------|-------|----------|
| `--aggressive` | Aggressive | Red | HP threshold 30%, attacks above-level mobs, pushes new zones early |
| `--methodical` | Methodical | Amber | HP threshold 60%, needs 2+ food before quest mobs, infrastructure quest order |
| `--curious` | Curious | Blue | NPC-first, enters every building, zone rotation every 30 turns |

**Note:** EFFICIENT personality deprecated (April 3). Active: agent_0=AGGRESSIVE, agent_1=METHODICAL, agent_2=CURIOUS.

**Resource budget (3 agents on this VM):** ~2.5 GB RAM, ~27% CPU, ~4.5 GB disk/24h — comfortable on 32 GB / 8 vCPU (`e2-standard-8`).

**Database**: MongoDB (`kaetram-mongo` Docker container, port 27017, db `kaetram_devlopment`) persists player state across 9 collections (`player_info`, `player_skills`, `player_equipment`, `player_inventory`, `player_bank`, `player_quests`, `player_achievements`, `player_statistics`, `player_abilities`). Note: MongoDB only saves on autosave/logout — positions and HP go stale during gameplay.

**Dashboard game state**: Two-source merge. Live volatile state (position, HP, entities) comes from `game_state.json` written by each MCP server's `observe()` tool. Persistent data (quests, skills, equipment, inventory) comes from MongoDB via `pymongo`. The dashboard merges both — file for what's happening now, DB for accumulated progress. If `game_state.json` is stale (>2min), falls back to DB-only, then log parsing. **Full dashboard reference (architecture, all endpoints, editing guide, gotchas): `dashboard/DASHBOARD.md`.**

### End-to-end data collection pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 3 24    # 3 agents, 24 hours
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

**Action vocabulary** (used in `<action>` tags — these mirror the curated model-visible tool surface, not every legacy fallback still present in the MCP server):

Core loop:
- `observe()` — game state JSON + ASCII map + stuck check

Combat:
- `attack(mob_name)` — target and attack nearest alive mob by name
- `set_attack_style(style)` — "hack", "chop", "defensive", "stab", "slash"
- `respawn()` — respawn after death, clears state + warps to Mudwich
- `eat_food(slot)` — consume edible item
- `loot()` — pick up ground items + lootbags after combat

Movement:
- `navigate(x, y)` — BFS pathfinding (max ~100 tiles — warp for longer)
- `warp(location)` — fast travel ("mudwich", "aynor", "lakesworld", "crullfield", "patsow", "undersea")
- `cancel_nav()` — cancel active navigation
- `stuck_reset()` — reset stuck detection

Quest / NPC:
- `interact_npc(npc_name)` — walk to NPC, talk through all dialogue, auto-accept quest
- `query_quest(quest_name)` — look up detailed walkthrough from `game_knowledge.md`

Inventory / economy:
- `buy_item(npc_name, item_index, count)` — buy from NPC shop (must be adjacent)
- `equip_item(slot)` — equip item from inventory slot
- `drop_item(slot)` — drop item to free space
- `gather(resource_name)` — gather from tree/rock/bush/fish spot
- `craft_item(skill, recipe_key, count)` — open the right production interface and craft/cook/smelt/fletch/brew

Note: `extract_turns.py` still normalizes historical logs containing legacy actions (`move`, `talk_npc`, `accept_quest`, `clear_combat`, `click_tile`, plus aliases like `heal` and `set_style`). Those old tools remain parseable in data, but they are no longer part of the preferred model-visible action surface.

**Verified on current data (r10, 2026-04-18):** 636 raw Claude session logs across agents 0-2 (216 + 213 + 207) → **12,900 train / 1,470 val** Qwen3.5 SFT records. Tool-call distribution: observe 57.1%, navigate 12.4%, attack 9.4%, interact_npc 4.5%, warp 3.6%, cancel_nav 3.5%. Observe is now first-class (r9 had 0; see CURRENT STATUS).

**Prior data point (r7, 2026-04-09):** 6,423 train / 646 val, 21,976 tool calls, no observe supervision. r10 has 47,267 tool calls on the same raw logs — the observe-as-turn fix more than doubles effective supervision density.

**r7 fixes applied to this dataset** (see `research/experiments/training-runs.md` for full detail):
1. **Chat template fix** (QwenLM/Qwen3 #1831) — stock Qwen3.5 template silently drops `<think>` reasoning from all assistant messages before `last_query_index` in multi-turn conversations. ~70% of our records are multi-turn windows, so intermediate-turn CoT was being stripped during training ("action-only" learning on follow-up turns). Patched `convert_to_qwen.py` / tokenizer formatting to always emit `<think>` when `reasoning_content` is present.
2. **Personality labels** — `detect_personality()` was returning `None` for every record (metadata.json path mismatch). Added fallback mapping from `agent_N` directory → personality. Dataset is now labeled 39% aggressive / 31% methodical / 29% curious, which unblocks per-personality paraphrase augmentation.
3. **rsLoRA attempted and reverted** — `use_rslora=True` was tried for r=64/alpha=64 (per Kalajdzievski 2023, to stabilize at high rank), but training diverged because rsLoRA's `1/sqrt(r)` scaling combined with `alpha=r` gave ~8x effective LR. Reverted to `use_rslora=False` with standard `alpha/r=1.0` scaling — see the comment on `train_modal.py:359`.

---

## CURRENT STATUS

**r10 dataset READY (Apr 17).** Two P0 fixes on top of r9's alignment work:
1. **Observe supervision.** r9 `dataset/qwen_sft/train.json` had 21,976 assistant tool calls and 0 `observe` calls — `extract_turns.py` was consuming Sonnet's observe tool_use blocks to populate `game_state` and discarding them. r10 emits observe as a first-class turn; `convert_to_qwen.py:build_user_message` no longer injects `<game_state>` (state arrives via observe's tool_result). Result: 26,995 observe calls (57.1% of 47,267 total tool calls).
2. **Personality prompt parity.** r9 training used a 2-sentence `PERSONALITY_SUFFIXES` dict; eval loaded the full ~1.5 KB `prompts/personalities/<name>.md` file. r10 routes both paths through the same `.md` files and substitutes at the `__PERSONALITY_BLOCK__` placeholder — byte-exact match verified by tests. `PERSONALITY_INSTRUCTION_VARIANTS` dict deleted from `finetune/train_modal.py` + `finetune/train_kto_modal.py` (it was overriding metadata).

Dataset: **12,900 train / 1,470 val** (vs 5,871/575 in r9 — +120%). 23 new regression tests (`tests/test_prompt_parity.py`, `tests/test_observe_supervision.py`, plus assertions in `tests/test_dataset_filters.py`). Experiment name `kaetram-qwen3.5-9b-r10` set. Launch pending window-size review.

**r9 SFT COMPLETE but lost to base in early curious eval** (2 episodes: base 2.5 quests / 26.5 kills / L20 vs r9-sft 1.5 quests / 28.5 kills / L24, higher combat churn). Root-cause diagnosis surfaced the two P0s r10 fixes. r9 was trained Apr 15-16 with aligned system prompt + 100% reasoning + MAX_SEQ_LEN 16384 but still had the observe and personality gaps. LoRA config unchanged for r10 (r=64/alpha=64, use_rslora=False).

**r8 SFT DONE but underperformed base due to train/inference mismatch.** r8 fixed loss masking (`train_on_responses_only`) but trained on a system prompt that differed from the inference-time prompt — the model learned a different task framing than it saw at serve time. r9 addressed the prompt alignment; r10 addresses the observe + personality alignment.

**Loss masking history:** r4 used explicit `DataCollatorForCompletionOnlyLM` (worked). r5-r7 switched to `completion_only_loss=True` flag (silently broken — trained on all tokens). r8 uses Unsloth's `train_on_responses_only()` which scans tokenized input_ids for `<|im_start|>assistant\n` markers — handles multi-turn correctly. Tool results (role:tool) are correctly masked because Qwen3.5 renders them as `<|im_start|>user`, so the scanner treats them as user turns.

**Why not TRL's `assistant_only_loss=True`?** Requires `{% generation %}` Jinja tags in chat template. Qwen team declined to add them (HuggingFace-specific extension). TRL v1.1.0 auto-substitutes a training template with these tags, but Unsloth caps TRL at <=0.24.0 which lacks this feature.

**Personalities finalized (April 3).** Dropped EFFICIENT after audit. 3 orthogonal axes confirmed working in logs: combat approach / HP-gated preparation / exploration-first. Active: agent_0=AGGRESSIVE, agent_1=METHODICAL, agent_2=CURIOUS.

**Eval harness set up.** `eval_harness.py` runs `play_qwen.py` episodes with controlled conditions. Eval uses dedicated ports (9061 r9-sft, 9071 base) and sandboxes (`/tmp/kaetram_eval_*`). Dashboard Eval tab shows live side-by-side streams + results.

**KTO pipeline validated, full run pending.** r6-KTO smoke test ran 10/10 steps cleanly. Will rebuild on r9 SFT merged weights. Comparison (base vs r9 vs r9-KTO) is the paper result.

**Compile-research cron loop working.** `scripts/run_research_staleness_check.sh` via VM cron. Last auto-compile: 2026-04-11. Do not rely on session-local Claude cron — that dies with the session.

**Qwen eval harness:**
- `play_qwen.py` / `play_qwen.sh` — Calls finetuned model via OpenAI-compatible Modal endpoint. Spawns `mcp_game_server.py` as MCP subprocess for all 17 model-visible game tools. Used by `eval_harness.py` for automated evaluation.
- `QwenCodeAdapter` in `cli_adapter.py` — wraps the `qwen` CLI (Gemini CLI fork). Uses Playwright MCP, `stream-json` output. **This is NOT the finetuned model** — it calls the Qwen Code CLI which hits the Qwen API.

**World model (WIP concept).** Experimental 2.2M param Transformer forward dynamics model in `world/`. Not prioritized — see `world/README.md` for details.

**Remote agent setup:**
- **GCP VM** (`34.28.111.6`): Hosts Kaetram game server (:9001 WS) + client (:9000 HTTP). This is the game world.
- **GPU VM** (`73.173.11.56:1738` via SSH): Runs finetuned `kaetram` model in Ollama (RTX 3060 12GB) + agent harness via Playwright. This is the agent brain.
- Agent on GPU VM connects browser to `http://34.28.111.6:9000` and plays via Playwright.

### Remote access
| Machine | IP | SSH | Purpose |
|---------|------|------|---------|
| GCP VM (this) | 34.28.111.6 | patnir41@34.28.111.6 | Game server + client, data collection, training pipeline |
| GPU VM (3060) | 73.173.11.56 | pnir41@73.173.11.56 -p 1738 | Finetuned model inference, agent harness (OpenCode) |

---

## Architecture

```
Custom MCP Server (all harnesses):
  Claude/Codex/Gemini CLI ──► mcp_game_server.py (FastMCP) ──► Playwright Python ──► browser
                                  │                                    │
                             17 typed tools                     page.evaluate()
                             (observe, attack,                  calls window.__helperFn()
                              navigate, warp,                   from state_extractor.js
                              gather, loot...)

  MCP config per harness:
    Claude  → sandbox/.mcp.json (--mcp-config flag)
    Codex   → sandbox/.codex/config.toml (CODEX_HOME env var)
    Gemini  → sandbox/.gemini/settings.json (auto-discovered from cwd)

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
| 9001, 9011, 9021 | Game server WS (multi-agent, agent_0–2) |
| 9061 | Eval game server WS (r9-sft) |
| 9071 | Eval game server WS (base) |
| 8080 | Dashboard (overview, activity, eval) |
| 8081 | Dashboard WebSocket relay (realtime screenshot push) |

## Key files

| File | Purpose |
|------|---------|
| `mcp_game_server.py` | **Custom MCP server** — FastMCP Python, 17 exported tools, manages Playwright browser. The exported tool set now matches the curated model-visible surface so deprecated wrappers do not crowd out core actions. |
| `.mcp.json` | MCP config **template** — placeholders resolved at launch. Claude uses `--mcp-config` + `--strict-mcp-config`. |
| `play.sh` | Single-agent loop (resolves `.mcp.json` template via sed) |
| `cli_adapter.py` | **Harness abstraction** — ClaudeAdapter, CodexAdapter, GeminiAdapter (+ Kimi, Qwen WIP). Each resolves MCP config per-sandbox. |
| `scripts/codex_stop_hook.py` | Codex Stop Hook — forces continuation up to max_turns (Codex exec is one-shot by default) |
| `orchestrate.py` | Multi-agent launcher + health monitor + rate limit detection + budget enforcement |
| `state_extractor.js` | Injected into browser via `context.add_init_script()` — exposes `window.__extractGameState()`, `window.__attackMob()`, etc. Called by MCP server internally, never by agent. |
| `extract_turns.py` | JSONL log → OODA turn extraction (parses MCP tool calls) |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT/GRPO format |
| `prompts/system.md` | Agent system prompt (~100 lines, no JS — just tool names + decision tree) |
| `prompts/game_knowledge.md` | Game knowledge (mob stats, quest guides, NPC coords) |
| `prompts/personalities/*.md` | Playstyle overrides (`aggressive.md`, `methodical.md`, `curious.md` — EFFICIENT was deprecated April 3 and its file is gone) |
| `dashboard.py` | Live web dashboard (port 8080) |
| `dashboard/parsers.py` | Session log parser — classifies MCP tool calls for activity feed |
| `dashboard/api.py` | API endpoints — `/api/game-state`, `/api/agents`, `/api/activity`, `/api/eval/live`, `/api/eval/latest` |
| `play_qwen.py` | Qwen eval harness — MCP tools via `mcp_game_server.py`, OpenAI-compatible Modal endpoint. Used by `eval_harness.py`. |
| `play_qwen.sh` | Session loop wrapper for `play_qwen.py` — system prompt substitution, auto-restart |
| `eval_harness.py` | Eval orchestrator — runs N episodes per model, resets DB between episodes, outputs results JSON |
| `scripts/run-eval.sh` | Eval launcher — starts game servers on 9061/9071, runs r9-sft vs base in parallel |
| `finetune/serve_modal.py` | Modal SGLang endpoint for finetuned r9 model (A100, `/v1/chat/completions`) |
| `finetune/serve_modal_base.py` | Modal SGLang endpoint for base Qwen3.5-9B (A100, baseline comparison) |

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

Custom skills live in `.claude/commands/`:

| Skill | When to trigger |
|-------|----------------|
| `/game-session` | Check stack status, startup guide, port status |
| `/verify-pipeline` | Confirm data is flowing, inspect training records |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |
| `/compile-research` | Compile/refresh `research/` wiki. Also invoked automatically by the VM cron via `scripts/run_research_staleness_check.sh`. |

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
- **The curated tool surface should stay in the high teens, not the low 20s.** Paper (arXiv 2505.03275) shows graceful degradation past ~30 tools and sharp drop past ~100, but local Kaetram results also show unnecessary wrappers wasting prompt budget. Merge overlapping actions before adding new ones, and keep the model-visible subset tighter than the raw MCP export list.

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
