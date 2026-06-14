# CLAUDE.md — Kaetram AI Agent (Developer Reference)

> This file is for the human developer using Claude Code interactively. The
> agent subprocess launched by `play.sh` does not read this file — its
> instructions live in `prompts/system.md`. Do not add agent behavioral
> instructions here.

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using a
custom MCP server (`mcp_server/` package, entry point `mcp_game_server.py`)
that exposes typed game tools (observe, attack, navigate, etc.). The agent
calls structured tools — never writes JavaScript. Gameplay sessions are
collected as SFT training data for Qwen3.5 9B.

For current run state, training results, and what's in flight: read
`session_log.md`. This file is the stable reference that doesn't change weekly.

---

## Session startup

At the start of every new session:
1. Read this file.
2. Read `session_log.md` (recent decisions and context).
3. Then ask what the user wants to do.

At the end of every session, append to `session_log.md` (keep under 30 lines).
After any big change (training infra, dataset rebuild, architecture shift),
update `session_log.md` immediately, commit, push to GitHub, and sync the
VM if it runs that code.

---

## Multi-machine sync protocol — MANDATORY

Two machines (laptop + remote GPU VM) share `origin/main`. **Stale checkouts
are the leading cause of cross-machine confusion in this project.** Two prior
incident classes motivated this protocol:

- Editing files on a stale VM checkout before pulling — diffs end up looking
  like reverts of upstream commits.
- `scp`'ing a modified file to the VM mid-test, then committing locally and
  never pulling on the VM — the VM working tree shows a dirty file that
  silently diverges from `origin/main`.

These rules are non-negotiable. Follow them on **every machine, every session,
every file edit** — not just for shared code.

### 1. ALWAYS pull before any work — every machine, every session

`git fetch origin && git pull --ff-only` is the **first command** on any
machine before reading code, editing files, running scripts, or spawning
agents. If non-ff: STOP, investigate. Do not force, rebase, or merge without
understanding what diverged.

```bash
# Boilerplate to start every session on every machine:
cd /path/to/kaetram-agent && git fetch origin && git pull --ff-only && git status
```

**Identify which machine you're on before pushing.** Check `hostname` /
internal IP — the GCP VM is `gcp-vm` / `10.0.0.10` / external `vm.example.com`.
Don't `ssh user@vm.example.com` from the VM itself to "sync" — you're already
there; the push IS the sync. Only ssh-pull the *other* machine (laptop).

### 2. NEVER `scp` / `rsync` files between laptop and VM

The hand-copy pattern (`scp` modified file to VM to "test before committing")
creates a dirty working tree on the VM that doesn't match `origin/main` even
after the fix lands via git. This is hard to spot in review and easy to
mistake for a regression.

**Always go through git.** The full loop is:

```bash
# 1. Edit + test locally
# 2. Commit + push
# 3. Immediately on the OTHER machine:
ssh user@vm.example.com "cd /home/user/projects/kaetram-agent && git pull --ff-only"
# 4. Now test on VM with origin/main HEAD, not a hand-copied file
```

If a fix needs VM-side testing before committing, use a `feat/...` branch:
push the WIP branch, pull it on VM, test there, then merge to main when clean.
Never bypass git for "just-this-once" file copies.

### 3. After every push, sync the OTHER machine

If you pushed from laptop, immediately:
```bash
ssh user@vm.example.com "cd /home/user/projects/kaetram-agent && git pull --ff-only"
```

If you pushed from VM (e.g. cron compile-research), pull on laptop next
session start. Don't leave a machine on a stale HEAD overnight — the
auto-compile-research cron runs at 00:07 UTC and will commit `session_log.md`
ahead of you.

### 4. Branch for shared code, direct for solo lanes

Push to `feat/…` / `chore/…` for anything a collaborator might edit
concurrently: `eval_harness.py`, `dashboard/`, `prompts/`, `finetune/`,
`scripts/`, `mcp_server/`. Direct to `main` is fine for solo lanes:
`research/`, `session_log.md`, `.claude/memory/`, personal docs.

### 5. VM sync when unsure — stash first

If you arrive on a machine and `git status` shows unexpected modifications:

```bash
git stash push -u -m "safety-$(date +%s)"   # nothing destroyed
git fetch origin && git pull --ff-only
git stash list                               # decide per-stash to pop or drop
```

Stash-first means nothing is destroyed if an upstream commit conflicts. If
the dirty diff turns out to match an already-pushed commit, `git checkout --
<file>` then pull — the working-tree change was redundant.

### 6. Quick recovery checklist (when you spot a dirty VM)

```bash
ssh user@vm.example.com "cd /home/user/projects/kaetram-agent && git status"
# If files modified that are already in origin/main:
ssh user@vm.example.com "cd /home/user/projects/kaetram-agent && \
    git checkout -- <files> && git pull --ff-only && git status"
# Verify clean working tree before doing anything else.
```

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
→ Qwen SFT records. `orchestrate.py` runs N agents in parallel, each
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
| `mcp_server/` | Modular MCP package (7 root + 10 `tools/` Python files excl. `__init__`, 17 model-visible tools). See `mcp_server/README.md`. |
| `mcp_game_server.py` | 30-line stub — entry point that imports `mcp_server.tools` and runs the FastMCP loop. |
| `.mcp.template.json` | Template with placeholders (`__VENV_PYTHON__`, `__PROJECT_DIR__`, …). Resolved per-sandbox to `.mcp.json` by `cli_adapter.py` / `play.sh`. |
| `cli_adapter.py` | Harness abstraction: `ClaudeAdapter`, `CodexAdapter`, `GeminiAdapter`, `OpenCodeAdapter`, `QwenAdapter`. |
| `bootstrap.py` | Single source of truth for the user bootstrap message Claude saw at training. Used by orchestrate (collection), convert_to_qwen (SFT records), play_qwen (runtime), play.sh. |
| `orchestrate.py` | Multi-agent launcher: spawns game servers, Xvfb, ffmpeg, MCP, harness; supervises restarts; tracks rate limits + budget. |
| `play.sh` | Single-agent loop. |
| `state_extractor.js` | Browser-side helpers exposed via `window.__extractGameState()` etc. Called by `mcp_server` only — never by the agent. |
| `mcp_server/resource_gates.py` | Loads resource→skill+level requirements from Kaetram-Open data files at MCP startup. `gather()` uses it to surface a structured `gate` block when "no items collected" is actually a skill-level gate. Override the data dir via `KAETRAM_DATA_DIR`. |
| `mcp_server/mob_stats.py` | Same pattern for mobs.json. `observe()` enriches each `nearby.mobs[]` entry with `level` + `aggressive` so the agent doesn't have to recall the MOB PROGRESSION table by name. |
| `extract_turns.py` | JSONL log → OODA turn extraction. |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT/GRPO format. |
| `prompts/system.md` | Agent system prompt (~75 lines, XML-tagged). |
| `prompts/game_knowledge.md` | Quest guides, NPC coords, mob stats. |
| `prompts/personalities/*.md` | Archetype overrides (`grinder.md`, `completionist.md`, `explorer_tinkerer.md`). |
| `dashboard/server.py` | Dashboard entry point (HTTP :8080 + WS :8081). Full reference: `dashboard/DASHBOARD.md`. |
| `eval_harness.py` + `scripts/run-eval.sh` | Eval orchestrator: r10-sft vs base on dedicated ports 9061 / 9071. |
| `play_qwen.py` | In-house Qwen3.5-9B harness — Modal SGLang endpoint + MCP server. Multi-agent via `orchestrate.py --qwen-sft N` (finetuned) or `--qwen-base N` (unfinetuned); peer to Claude/Codex/Gemini/OpenCode. Mixable in one run. Solo dev invokes directly. |
| `tests/e2e/quests/` | Reachability tier — per-step playthrough tests for Core 3 (Herbalist's Desperation, Rick's Roll). Foresting is exercised under `tests/e2e/game/`. Each step seeds the cumulative state an agent has at that point per game_knowledge.md. |

## Ports

Game-server port `P` reserves `P+1` for `apiPort` (currently dormant; matches
`start-test-kaetram.sh:45` and `orchestrate.py`). Agents stride by `+10`
(`orchestrate.py:65-67`).

| Port | What |
|------|------|
| 9000 | Kaetram client (HTTP, shared) |
| 9001 + N×10, N ∈ [0,8] | Multi-agent game-server WS. **Standard run is 3 agents — one per archetype** (grinder + completionist + explorer-tinkerer): 9001 / 9011 / 9021. |
| 9191 | Test-lane Kaetram (db `kaetram_e2e`, `TEST_AGENT_ID=99`, Xvfb `:198`) — isolated from data-collection lanes; dashboard Tests tab runs headed pytest against it |
| 9061, 9071 | Eval game servers (r10-sft, base) |
| 27017 | MongoDB (`kaetram-mongo`); per-lane isolation by db name |
| 8080 | Dashboard HTTP (UI + `/hls/agent_N/*` + `/ingest/{state,activity}`) |
| 8081 | Dashboard WebSocket relay (state, activity, heartbeat) |
| 8889 | NIM SSE-rewriting proxy (NVIDIA NIM Qwen reasoning capture) — booted by `restart-agent.sh` / `orchestrate.py` when any opencode agent uses an NVIDIA Qwen model |
| 8890 | DeepSeek SSE-rewriting proxy (DeepSeek V4 reasoning capture) — same role for `deepseek-v4-pro` / `deepseek-v4-flash`; required because opencode 1.14.29 doesn't read DeepSeek's `delta.reasoning_content` |

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
`--gemini` (Gemini 3 Flash), and `--opencode` run the same
orchestrator/dashboard/log paths but their turns are excluded from training
until validated. Use them for cross-harness comparisons, not training data.

`--opencode` is multi-model — pass `--opencode-model <alias|id>` to pick.
Aliases (resolved by `cli_adapter.OPENCODE_MODEL_ALIASES`):

| Alias                | Provider | Model |
|----------------------|----------|-------|
| `grok-4-1-fast`      | xAI direct                    | `xai/grok-4-1-fast-reasoning` |
| `qwen3.5-35a3b`      | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3.5-35b-a3b` |
| `qwen3.5-397a17b`    | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3.5-397b-a17b` |
| `qwen3-80a3b`        | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3-next-80b-a3b-thinking` |
| `deepseek-v4-flash`  | DeepSeek (proxy :8890)        | `deepseek/deepseek-v4-flash` |
| `deepseek-v4-pro`    | DeepSeek (proxy :8890)        | `deepseek/deepseek-v4-pro` |

Provider blocks live in `opencode.template.json`. NIM-routed Qwen models
require `scripts/start-nim-proxy.sh` (port 8889); DeepSeek-routed models
require `scripts/start-deepseek-proxy.sh` (port 8890). Both are idempotent
and `restart-agent.sh` / `orchestrate.py` boot whichever the active
harness mix needs. DeepSeek also requires `DEEPSEEK_API_KEY` in env; xAI
uses `XAI_API_KEY`.

### Archetypes

Three orthogonal axes injected into `system.md` via `__PERSONALITY_BLOCK__`:
`--grinder` (combat/leveling), `--completionist` (progression), and
`--explorer-tinkerer` / `--explorer` (world + systems coverage). They're a
*data-factory* mechanism for trajectory diversity, not a paper claim — if
trajectories collapse we drop to two policies.

### SFT pipeline

`logs/session_*.log → extract_turns.py → convert_to_qwen.py →
dataset/qwen_sft/{train,val,metadata}.json`. Active corpus is **Claude only**.
Out-of-corpus raw runs and superseded builds live under `dataset/raw/_archive/`
and `dataset/_archive/` respectively — invisible to the live pipeline.
`metadata.json` carries provenance (`version`, `built_at`, `prompt_commit`,
`source_runs[]`, record counts) — see `dataset/qwen_sft/README.md` for
build/inspect/rebuild commands. Full action vocabulary, modes, and historical
lessons: `dataset/DATA.md` and `research/experiments/training-runs.md`.

---

## Gotchas

- **Node 16/18/20 only** (uWS.js). `nvm use 20` before starting the server. Node 24/25 crashes.
- **`yarn build` after every Kaetram-Open patch.** `yarn start` alone fails. Any quest/mob/map JSON edit under `Kaetram-Open/` needs a rebuild.
- **Game-server port override.** `PORT=X yarn start` doesn't work — Kaetram reads `.env`, not `process.env`. Use `node dist/main.js --port X`. `orchestrate.py` does this.
- **`.mcp.template.json` vs `.mcp.json`.** The template is checked in; `.mcp.json` is the per-sandbox resolved copy. Claude reads the resolved copy via `--mcp-config --strict-mcp-config`.
- **OpenCode reasoning needs an SSE-rewriting proxy.** OpenCode 1.14.29's `@ai-sdk/openai-compatible` provider reads `delta.content` only — providers that stream reasoning via `delta.reasoning_content` (NVIDIA NIM Qwen, DeepSeek V4) lose CoT without `scripts/nim_proxy.py` in front. Two daemons: NIM (`scripts/start-nim-proxy.sh`, :8889) and DeepSeek (`scripts/start-deepseek-proxy.sh`, :8890). Both reuse `nim_proxy.py` and are idempotent; `restart-agent.sh` / `orchestrate.py` start whichever the harness mix needs. The proxy also strips wrapped `<think>...</think>` from assistant message history before forwarding — DeepSeek otherwise echoes prior reasoning and emits malformed `<that>` close tags on subsequent turns.
- **Tool API auto-actions (since 2026-04-29).** `attack` auto-loots on kill (response includes `auto_loot: {looted, target}`), `buy_item` auto-walks to NPC + opens shop (do NOT call `interact_npc` first — races the shop flow), `craft_item` auto-walks to the nearest station on the current map (do NOT manually `navigate` first; if no station on this map it errors and you `warp` elsewhere). `interact_npc` returns four disambiguated quest fields: `quest_opened` (panel appeared), `quest_accepted` (we passed `accept_quest_offer=True` and clicked through), `quest_offered` (offer name), `quest_state_changed` (any quest-list delta — covers turn-ins/stage advances). The old `quest_opened or quest_changed` conflation is gone. Live tool description is in `prompts/system.md`; older agent training data may still reference manual nav-to-station / manual-loot patterns.
- **Base-Qwen state-aware scaffold.** `query_quest` leads with a `current_step` block (`mcp_server/tools/quest.py` `_build_current_step`): canonical FACTS (`accepted`/`stage`/`needed`/`have`/`remaining`) plus an ADVISORY `recommended_action` + `preconditions`. It is advisory, not an oracle — the agent verifies preconditions against `observe`. `current_step` and `live_gate_status` derive the active/finished split from the raw `quests` array via `utils.normalize_quest_lists` (the same split `observe.js` produces); reading raw `__latestGameState` without it makes accepted quests look un-accepted. `observe` tags each active quest with `items_progress:{have,remaining}` (`observe._enrich_active_quests`).
- **`KAETRAM_OBSERVE_COMPACT`.** When set, `observe` drops the ASCII-map grid (redundant with the structured `nearby` block) but keeps STUCK_CHECK — roughly halves the per-turn payload for more turns/session. OFF by default (preserves train/eval observe-shape parity); `restart-agent.sh --qwen-base` sets it and `play_qwen` forwards it to the MCP subprocess. `scripts/log_analysis/parse.py` handles both the full and compacted observe shapes.
- **Base-Qwen cross-session note.** `play_qwen._build_session_note` writes a small PROGRAMMATIC (not model-authored) advisory note from the last observed state at session rollover ("working 'X' stage N, still need {…}") and injects it into the next bootstrap to cut the re-derivation tax. The next session still `observe`s first, which confirms or invalidates it.
- **rsLoRA + `alpha=r` is an 8x LR trap.** rsLoRA scales `1/sqrt(r)` not `1/r`. With `r=alpha=64`, effective LR is 8x. r7 diverged. Keep `use_rslora=False` (the comment on `train_modal.py:293` is load-bearing).
- **Counting running agents.** `pgrep -fa "claude -p"` self-matches the shell that ran it (the pattern appears in its own cmdline). Count unique bot IDs from the output (`ClaudeBot[0-9]+`, `CodexBot[0-9]+`, `GeminiBot[0-9]+`, `QwenGrinder` / `QwenCompletionist` / `QwenExplorer`, or for opencode: `BigQwenBot[0-9]+` / `GrokBot[0-9]+` / `DeepSeekBot[0-9]+` / `OpenCodeBot[0-9]+` depending on `--opencode-model`), or cross-check against listening game-server ports (`9001 + N×10`) — those are authoritative.
- **OpenCode bot username depends on the model.** The opencode harness splits its in-game username + Mongo player row by model family so dashboard / log analysis can distinguish runs: `*qwen*` → `BigQwenBot` (separate from the in-house Qwen harness), `*grok*` → `GrokBot`, `*deepseek*` → `DeepSeekBot`, otherwise `OpenCodeBot`. Logic lives in `cli_adapter.opencode_bot_prefix()` and is mirrored in `restart-single-agent.sh` + `play.sh`.
- **Qwen bot username depends on the personality, not agent ID.** The in-house Qwen harness (`orchestrate.py --qwen-sft` / `--qwen-base`) names agents by personality so the in-game bot maps 1:1 to the personality variant being evaluated: `grinder` → `QwenGrinder`, `completionist` → `QwenCompletionist`, `explorer_tinkerer` → `QwenExplorer`. SFT vs base is reflected in `metadata.json::model` (`r10-sft` / `kaetram-base`), not the username, so a 3-agent SFT run and a 3-agent base run share the same Mongo player rows. Logic lives in `orchestrate.Orchestrator.setup` (qwen_username_map) and is mirrored in `restart-single-agent.sh` + `restart-agent.sh` Mongo seeding.
- **Qwen3.5 chat template drops `<think>` on intermediate turns** (QwenLM/Qwen3 #1831, still open against Qwen3.5 as of May 2026). `patch_qwen_chat_template` in `finetune/render.py` is the single source of truth — imported by `convert_to_qwen.py` (truncation gate), `train_modal.py`, `serve_modal.py`, `serve_modal_base.py` (`train_kto_modal.py` is a deferred planning stub and will re-import it when implemented). If you touch the tokenizer, re-run `tests/unit/test_think_roundtrip.py` to verify CoT survives `apply_chat_template` on every assistant turn.
- **`world/` is deprecated — not in use.** The forward-dynamics model (`world/extract_transitions.py`, `world/schema.py`, `world/mcts.py`, `world/train_modal.py`) targets the older `browser_run_code` log shape and is not maintained against the current MCP harness. `world/extract_transitions.classify_action` greps for `__attackmob` / `__interactnpc` JS calls that current logs no longer contain; running it on the live corpus would emit zero transitions. Don't update these files when refactoring the SFT pipeline — leave them as-is.

---

## Agent prompt design principles

Editing `prompts/system.md`, `prompts/game_knowledge.md`, or
`prompts/personalities/*.md`? Full research basis: `reference/SOTA_PROMPTING_CC_DR_4122026.md` and `reference/SOTA_PROMPTING_OpenAI_DR_4282026.md`.
Operating rules: total prompt under ~3K tokens; XML tags for structure
(Claude is trained on them); calm directives (Claude 4.6 over-triggers on
"CRITICAL/MUST"); explain WHY not just WHAT; reference data at top,
decisions at end (middle 40-60% is underweighted); personality = priority
modifiers only, never new rules; one tool per turn; keep the model-visible
tool surface in the high teens.

## Log analysis (`scripts/log_analysis/`)

Primary tool for "how are the agents doing" — parses session JSONL logs
under `dataset/raw/agent_*/runs/run_*/` (with `logs/` symlink to the latest
run) and reports per-agent status, quests, tool distribution, categorized
errors, rule-adoption signals, and reasoning. The active corpus is **Claude only**
— out-of-corpus and non-Claude runs live under `dataset/raw/_archive/<harness>/agent_N/run_*`
and are invisible to `analyze.py` (the parser's `list_runs()` does not recurse into `_archive/`).
**Prefer this over LLM subagents for live status / behavioral audit** — it parses fields directly (`active_quests`,
`live_gate_status.gated`, `inventory_summary.full`, mob `level`, etc.), so the
answer is ground truth not an inference, and it doesn't burn tokens.

**Default scope:** the **latest run per agent**, aggregating every
session_*.log in that run dir — so `metrics`, `tools`, `errors`, `status`,
`quests`, `timeline` all report on the whole run. Use `--run <id>` to scope
to a past run, `--session N` to drill into one session, `--stale` to include
agents whose latest run hasn't been touched in 10+ min. Both Claude and
OpenCode (DeepSeek/Qwen/Grok) logs parse via auto-detect.

```bash
# Live snapshot (run-aggregated)
python3 scripts/log_analysis/analyze.py            # full report
python3 scripts/log_analysis/analyze.py status     # one-line per agent + run header (turns/errors/cost summed across sessions)

# Historical / cross-run
python3 scripts/log_analysis/analyze.py runs -n 10            # last N runs across all agents
python3 scripts/log_analysis/analyze.py --all-runs runs       # every run ever (--all-runs must precede subcommand)
python3 scripts/log_analysis/analyze.py --run <run_id> status # full breakdown of a past run (parses ALL its sessions)

# Behavioral audits (use these when assessing whether prompt/tool changes worked)
python3 scripts/log_analysis/analyze.py errors                # CATEGORIZED errors (BFS_NO_PATH, STILL_MOVING, NPC_NOT_FOUND, STATION_UNREACHABLE, …) + top next-action transitions — also where you read off rule-adoption (e.g. BFS→warp vs BFS→navigate retry)
python3 scripts/log_analysis/analyze.py errors --by-quest     # same, sliced by which Core 3 quest was active at the error
python3 scripts/log_analysis/analyze.py timeline -n 30        # chronological event stream across the run, with session boundaries

# Quest progression (where the agent actually got stuck)
python3 scripts/log_analysis/analyze.py quest                 # per-Core-3 stage timeline + reasoning at each advance
python3 scripts/log_analysis/analyze.py quest rick            # scope to one quest by substring match
python3 scripts/log_analysis/analyze.py quest --cross-run     # max-stage histogram across every run per agent — answers "where do agents plateau?"

# Drill-downs (recent/thinking are session-scoped — pass --session N to pick)
python3 scripts/log_analysis/analyze.py quests
python3 scripts/log_analysis/analyze.py tools
python3 scripts/log_analysis/analyze.py recent -n 8                # latest session of latest run
python3 scripts/log_analysis/analyze.py --session 1 recent -n 8    # session 1 of latest run
python3 scripts/log_analysis/analyze.py thinking -n 3
python3 scripts/log_analysis/analyze.py agent 1 -n 10              # full per-agent run dive
```

**When to reach for which command:**
- Just stopped/restarted agents → `status` to confirm they're up + see run_id/elapsed/cost
- "Did my prompt fix actually change behavior?" → `errors` (next-action transitions show whether rules landed — BFS_NO_PATH → warp vs retry-navigate)
- "Why is agent N looping?" → `errors` shows what failed + what it did next
- "How much real Core 3 progress this run?" → `metrics` — uses last-vs-first-observe DELTA so resume-state replays don't inflate the count
- "What did agent N do today?" → `timeline` for an emoji-tagged event stream across sessions
- "How does this run compare to last week's?" → `runs -n 20` or `--all-runs runs`

See `scripts/log_analysis/README.md` for the log-shape reference. To write a
custom one-off analysis, import from `parse.py`:

```python
from scripts.log_analysis.parse import (
    list_agent_dirs, latest_run, parse_run_sessions,
    parse_session_auto, latest_observe, categorize_error,
    RunSessionsView, SessionView,
)
for ad in list_agent_dirs():
    rv = parse_run_sessions(ad, latest_run(ad))
    print(rv.run_id, rv.n_sessions, rv.total_turns, rv.total_cost_usd)
```

`scripts/export_report.py` uses the same parser kernel — the dashboard
JSON report and the CLI stay in lock-step.

## Slash commands (`.claude/commands/`)

`/log-analysis` (per-agent run analysis, Core 3 progression, 5 paper metrics),
`/compile-research` (refresh `research/`, also runs from VM cron).

Storage: Kaetram-Open is ~1.3-2 GB installed. See `TEARDOWN.md` for uninstall
or "keep but trim" (~1 GB reclaimed via `node_modules/dist` deletion).
