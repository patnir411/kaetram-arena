# CLAUDE.md — Kaetram AI Agent (Developer Reference)

> This file is for the human developer using Claude Code interactively. The
> agent subprocess launched by `play.sh` does not read this file — its
> instructions live in `prompts/system.md`. Do not add agent behavioral
> instructions here.

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using a
custom MCP server (`mcp_server/` package, entry point `mcp_game_server.py`)
that exposes typed game tools (observe, attack, navigate, etc.). The agent
calls structured tools — never writes JavaScript. Gameplay sessions are
collected as SFT/KTO training data for Qwen3.5 9B.

For current run state, training results, and what's in flight: read
`session_log.md` and `research/INDEX.md`. This file is the stable reference
that doesn't change weekly.

---

## Session startup

At the start of every new session:
1. Read this file.
2. Read `session_log.md` (recent decisions and context).
3. Read `.claude/commands/training-summary/history.json` if it exists (reward trends).
4. Then ask what the user wants to do.

At the end of every session, append to `session_log.md` (keep under 30 lines).
After any big change (training infra, dataset rebuild, architecture shift),
update `session_log.md` immediately, commit, push to GitHub, and sync the
VM if it runs that code.

---

## Multi-machine sync protocol

Two machines (laptop + GCP VM `34.28.111.6`) share `origin/main`. Edits on a
stale checkout produce diffs that look like reverts of missing commits — and
silently are. The 2026-04-17 cofounder incident is why this section exists.

1. **Pull before edit.** `git fetch origin && git pull --ff-only` on the
   machine you're about to touch. If non-ff, investigate; local has diverged.
2. **Branch for shared code, direct for solo lanes.** Push to `feat/…` /
   `chore/…` for anything your cofounder might edit (`eval_harness.py`,
   `dashboard/`, `prompts/`, `finetune/`, `scripts/`). Direct to main only
   for solo lanes (`research/`, `session_log.md`, `.claude/memory/`).
3. **VM sync when unsure:** `git stash push -u -m "safety-$(date +%s)"`
   before pulling. Stash-first means nothing is destroyed if a cofounder
   commit conflicts.

---

## Research knowledge base (`research/`)

Compiled knowledge: `experiments/`, `related-work/`, `decisions/`, `paper/`,
`INDEX.md`. Not stream-of-consciousness — `session_log.md` is the scratchpad
and `.claude/memory/` is per-user context.

After a training run, data rebuild, or design decision, update the matching
file under `research/` and link new files from `INDEX.md`. Maintenance is a
VM cron loop (`scripts/run_research_staleness_check.sh` → `/compile-research`
→ commit + push). Session-local Claude crons die with the session and are
not durable automation.

---

## Architecture

A harness CLI (Claude / Codex / Gemini / OpenCode) talks stdio to the
`mcp_server/` package, which drives Playwright on a Chromium browser pointed
at the Kaetram client (:9000). `state_extractor.js` exposes JS helpers
(`window.__extractGameState`, `__attackMob`, etc.) consumed by MCP tools via
`page.evaluate()`. Session logs flow `extract_turns.py` → `convert_to_qwen.py`
→ Qwen SFT/KTO records. `orchestrate.py` runs N agents in parallel, each
with its own game server, sandbox, MCP process, browser, and Xvfb display
(stride `+10` on the game-server WS port to leave room for `apiPort = P+1`,
dormant unless `API_ENABLED=true`).

**Livestream pipeline.** Each agent runs Xvfb + `ffmpeg x11grab` writing HLS
segments to `/tmp/hls/agent_N/`, served under `/hls/agent_N/*` on :8080 —
decoupled from `observe()` cadence so tiles keep streaming during long
thinking turns. `mcp_server.state_heartbeat` POSTs `window.__latestGameState`
to `/ingest/state` (300 ms) and tails the session log to `/ingest/activity`
(1 s); the dashboard rebroadcasts both over the WebSocket relay on :8081.
Full reference: `dashboard/DASHBOARD.md`.

## Key files

| Path | Purpose |
|------|---------|
| `mcp_server/` | Modular MCP package (15+ files, 17 model-visible tools). See `mcp_server/README.md`. |
| `mcp_game_server.py` | 19-line stub — entry point that imports `mcp_server.tools` and runs the FastMCP loop. |
| `.mcp.template.json` | Template with placeholders (`__VENV_PYTHON__`, `__PROJECT_DIR__`, …). Resolved per-sandbox to `.mcp.json` by `cli_adapter.py` / `play.sh`. |
| `cli_adapter.py` | Harness abstraction: `ClaudeAdapter`, `CodexAdapter`, `GeminiAdapter`, `OpenCodeAdapter`. |
| `orchestrate.py` | Multi-agent launcher: spawns game servers, Xvfb, ffmpeg, MCP, harness; supervises restarts; tracks rate limits + budget. |
| `play.sh` | Single-agent loop. |
| `state_extractor.js` | Browser-side helpers exposed via `window.__extractGameState()` etc. Called by `mcp_server` only — never by the agent. |
| `extract_turns.py` | JSONL log → OODA turn extraction. |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT/GRPO format. |
| `prompts/system.md` | Agent system prompt (~100 lines, XML-tagged). |
| `prompts/game_knowledge.md` | Quest guides, NPC coords, mob stats. |
| `prompts/personalities/*.md` | Archetype overrides (`grinder.md`, `completionist.md`, `explorer_tinkerer.md`). |
| `dashboard/server.py` | Dashboard entry point (HTTP :8080 + WS :8081). Full reference: `dashboard/DASHBOARD.md`. |
| `eval_harness.py` + `scripts/run-eval.sh` | Eval orchestrator: r9-sft vs base on dedicated ports 9061 / 9071. |
| `play_qwen.py` / `play_qwen.sh` | Finetuned-model harness — calls Modal SGLang endpoint, spawns the same MCP server. |
| `tests/e2e/quests/` | Tiered quest test suite (`core` / `bonus` / `extra` / `skip` / `reachability`). See `tests/e2e/quests/README.md`. |

## Ports

Game-server port `P` reserves `P+1` for `apiPort` (currently dormant; matches
`start-test-kaetram.sh:45` and `orchestrate.py`). Agents stride by `+10`
(`orchestrate.py:65-67`).

| Port | What |
|------|------|
| 9000 | Kaetram client (HTTP, shared) |
| 9001 + N×10, N ∈ [0,8] | Multi-agent game-server WS (today: 9001 / 9011 / 9021) |
| 9061, 9071 | Eval game servers (r9-sft, base) |
| 9191 | E2E test-lane game server (`scripts/start-test-kaetram.sh`, db `kaetram_e2e`) |
| 27017 | MongoDB (`kaetram-mongo`); per-lane isolation by db name |
| 8080 | Dashboard HTTP (UI + `/hls/agent_N/*` + `/ingest/{state,activity}`) |
| 8081 | Dashboard WebSocket relay (state, activity, screenshot, heartbeat) |

---

## Managing training runs

| Script | Purpose |
|--------|---------|
| `scripts/restart-agent.sh [N] [H]` | **Primary command.** Kill all agents, reset MongoDB to Level 1, clear sandbox state, relaunch N agents for H hours (0 = no limit). |
| `scripts/resume-agent.sh` | Resume without DB reset. |
| `scripts/restart-single-agent.sh <ID>` | Restart one agent without touching the others. Always clears `.session_counter`. |
| `scripts/nuke-agents.sh` | SIGKILL everything agent-related. |
| `scripts/reset-state.sh [N] [--force]` | Reset Mongo player data without restart. |
| `scripts/start-kaetram.sh` | Single-agent dev game server (Node 20). |
| `scripts/start-test-kaetram.sh` | E2E test-lane server (port 9191, db `kaetram_e2e`). Safe alongside data collection. |
| `scripts/start-nim-proxy.sh` | NIM SSE-rewriting proxy (required for OpenCode reasoning capture; see Gotchas). |
| `scripts/collect_sft_data.sh N H` | End-to-end: orchestrate → extract → convert. |

All restart/resume scripts accept `--claude` / `--codex` / `--gemini` /
`--opencode` and `--grinder` / `--completionist` / `--explorer` (plus
`--hours`, counts per archetype). Run `scripts/restart-agent.sh --help`
for the full surface — examples have been moved out of this file to
prevent drift.

### Harnesses

`--claude` is the primary data-collection harness — fully integrated and the
only one whose turns flow into Qwen SFT training. `--codex` (GPT-5.4),
`--gemini` (Gemini 2.5 Flash), and `--opencode` (NVIDIA Qwen free API via
OpenCode CLI + NIM proxy) run the same orchestrator/dashboard/log paths but
their turns are excluded from training until validated. Use them for
cross-harness comparisons, not training data.

### Archetypes

Three orthogonal axes injected into `system.md` via `__PERSONALITY_BLOCK__`:
`--grinder` (combat/leveling), `--completionist` (progression), and
`--explorer-tinkerer` / `--explorer` (world + systems coverage). They're a
*data-factory* mechanism for trajectory diversity, not a paper claim — if
trajectories collapse we drop to two policies. Legacy vibe flags
(`--aggressive / --methodical / --curious`) are removed.

### SFT pipeline

`logs/session_*.log → extract_turns.py → convert_to_qwen.py →
dataset/qwen_sft/{train,val}.json`. Full action vocabulary, modes, record
counts, and lessons from r4-r10: `dataset/DATA.md` and
`research/experiments/training-runs.md`.

---

## Gotchas

- **Node 16/18/20 only** (uWS.js). `nvm use 20` before starting the server. Node 24/25 crashes.
- **`yarn build` after every Kaetram-Open patch.** `yarn start` alone fails. Any quest/mob/map JSON edit under `Kaetram-Open/` needs a rebuild.
- **Game-server port override.** `PORT=X yarn start` doesn't work — Kaetram reads `.env`, not `process.env`. Use `node dist/main.js --port X`. `orchestrate.py` does this.
- **`.mcp.template.json` vs `.mcp.json`.** The template is checked in; `.mcp.json` is the per-sandbox resolved copy. Claude reads the resolved copy via `--mcp-config --strict-mcp-config`.
- **OpenCode reasoning needs the NIM proxy.** NVIDIA NIM streams Qwen reasoning via `delta.reasoning_content`; OpenCode only reads `delta.content`. `scripts/nim_proxy.py` rewrites SSE so reasoning is captured. Start it before `--opencode`.
- **rsLoRA + `alpha=r` is an 8x LR trap.** rsLoRA scales `1/sqrt(r)` not `1/r`. With `r=alpha=64`, effective LR is 8x. r7 diverged. Keep `use_rslora=False` (the comment on `train_modal.py:359` is load-bearing).
- **Qwen3 chat template drops `<think>` on intermediate turns** (QwenLM/Qwen3 #1831). Pre-r10 multi-turn records trained action-only on follow-ups. If you touch the tokenizer, re-run `tests/unit/test_think_roundtrip.py` to verify CoT survives `apply_chat_template`.

---

## Agent prompt design principles

Editing `prompts/system.md`, `prompts/game_knowledge.md`, or
`prompts/personalities/*.md`? Full research basis: `reference/SOTA_PROMPTING.md`.
Operating rules: total prompt under ~3K tokens; XML tags for structure
(Claude is trained on them); calm directives (Claude 4.6 over-triggers on
"CRITICAL/MUST"); explain WHY not just WHAT; reference data at top,
decisions at end (middle 40-60% is underweighted); personality = priority
modifiers only, never new rules; one tool per turn; keep the model-visible
tool surface in the high teens.

## Slash commands (`.claude/commands/`)

`/game-session` (stack status), `/verify-pipeline` (confirm data flow),
`/training-summary` (dataset stats), `/compile-research` (refresh `research/`,
also runs from VM cron).

Storage: Kaetram-Open is ~1.3-2 GB installed. See `TEARDOWN.md` for uninstall
or "keep but trim" (~1 GB reclaimed via `node_modules/dist` deletion).
