---
description: Trigger when the user asks to analyze a Sonnet/Claude/DeepSeek/etc agent run, score paper metrics, find why agents got stuck on a quest, see per-quest stage progression, plateau histograms across runs, error buckets, BFS→warp compliance, "what happened in the last run", "score this run on the 5 metrics", "did agents finish any Core 3 quests", "where do agents plateau on Rick's Roll", or wants a per-agent summary of recent activity.
---

Analyze the latest (or specified) agent run on the GCP VM and produce a clean
per-agent + per-quest summary, including the 5 paper metrics and Core 3
stage progression.

**Setup — SSH to the VM and locate the repo:**
```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && pwd"
```

`analyze.py` defaults to the latest run per agent and aggregates every
session in that run dir — `status`, `metrics`, `tools`, `errors`, `quests`,
`quest`, `timeline` all report on the whole run. Use `--run <id>` to scope to
a past run, `--session N` to drill into one session, `--stale` to include
runs whose latest session log is >10 min old.

## Step 1 — high-signal one-liner

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale status"
```

Reports run_id, agent level, HP, position, finished quests, active quests,
total turns/errors/cost across the run, per agent. Fastest health check —
answers "is the run still alive and where did each agent get to?".

## Step 2 — paper metrics

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale metrics"
```

Emits per-agent (run-aggregated): format accuracy, argument accuracy,
tool-selection (DEFERRED — needs Claude-as-judge or hand-label), **Core 3
stages newly reached this run** (last_observe stage − first_observe stage
summed across the 3 quests, denominator 10), and turn efficiency (new stages
÷ total turns). Headline number framework for the paper. Subtracting the
first observe defends against `quest_resume.json` replaying prior-run
completions into session 1's prompt; only stages genuinely reached this run
count.

## Step 3 — Core 3 progression timeline

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale quest"
```

Per-quest timeline for each Core 3 (Foresting, Herbalist's Desperation,
Rick's Roll). For each: stage transitions (when 0→1, 1→2, etc. happened,
which session/turn, which tool triggered it), the model's reasoning right
before each advance, NPCs talked to, tool/error breakdown while the quest
was active. **THE primary diagnostic for "why did agent N get stuck on
Rick's Roll?".**

Scope to one quest by substring:
```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale quest rick"
```

## Step 4 — Cross-run plateau histogram

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py quest --cross-run"
```

For each agent, max-stage reached per Core 3 quest across **every run ever**.
Answers "where do agents plateau?" with a histogram like
`Rick's Roll  [0]×88  [1]×9  [4]×1  →  finished 1/98`. Heavy — re-parses
every run dir; expect ~30s.

## Step 5 — Error buckets and recovery

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale errors"
```

Buckets failures (BFS_NO_PATH, NPC_NOT_FOUND, STILL_MOVING, MOB_NOT_FOUND,
COMBAT_BLOCKED_WARP, STATION_UNREACHABLE, MCP_DISCONNECT, SKILL_GATED, …)
and shows the top 3 follow-up actions per bucket. Directly diagnoses
recovery vs loop patterns and rule adoption (BFS_NO_PATH → warp count
vs BFS_NO_PATH → navigate-retry count tells you whether the BFS→warp rule
landed).

Slice by which Core 3 quest was active when the error fired:
```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale errors --by-quest"
```

This is how you tell apart "Rick's Roll active → 9× STATION_UNREACHABLE
(can't reach cooking station)" from generic STATION_UNREACHABLE pooled
across the whole run.

## Step 6 — Quest-level snapshot (delta first→last observe)

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale quests"
```

Quest start/end snapshots across the run, accept_quest_offer counts, NPC
interaction breakdown. Lighter than `quest` (no per-stage timeline) — useful
when you only need the run-level summary.

## Step 7 — Single-agent deep dive

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale agent <N> -n 25"
```

Full run-level breakdown for one agent: meta, stats delta, cost, tokens,
inventory, deaths, recent tool calls (latest session of the run).

## Step 8 — Recent reasoning

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale thinking -n 5"
```

Last N reasoning blocks per agent in the latest session of the run — read
what the model was *trying* before each action. Note that `quest` already
includes the reasoning right before each stage advance, which is more
diagnostic for "why did the agent figure out the quest" questions.

## Step 9 — Synthesize for the user

After running the relevant subset of the above (don't run every step if not
needed — pick by question type), write a tight summary:

- **Run scope:** which run, how long, total cost, did it finish or get terminated mid-flight
- **Per-agent:** final level, deaths, total turns, total cost, **Core 3 stages reached this run**
- **Per-Core-3-quest plateau:** for each agent, did they touch each quest at all? If yes, what stage did they reach? If they got stuck, which error bucket dominated *while that quest was active*?
- **Top error pattern:** which bucket dominated and what agents did after — surfaces rule-adoption (BFS_NO_PATH → navigate-retry vs warp tells you whether the BFS→warp rule landed)
- **Reasoning at stage transitions:** quote the `quest` output's "thinking before stage advance" lines when relevant — agents' self-narration usually contains the answer
- **Recommendation:** what to change before the next run (game patch, prompt rule, tool surfacing)

If the user asks about a specific quest (Q1-Q5), use `quest <substring>`
rather than grepping logs by hand.

## Filters reference

| Flag | Purpose |
|------|---------|
| `--stale` | include agents whose latest run hasn't been touched in 10+ min (default: only currently-running) |
| `--run <id>` | scope to specific historical run; parses every session in it |
| `--session N` | drill down to a single session inside the resolved run (1-based) |
| `--by-quest` | (errors) slice errors by which Core 3 quest was active at the time |
| `--cross-run` | (quest) walk every run per agent for a max-stage histogram |
| `--all-runs` | for `runs` cmd, list every run instead of recent N |
| `--claude` / `--opencode` | force a parser (default: auto-detect from each session's meta.json) |

## Source

- `scripts/log_analysis/analyze.py` — CLI dispatcher (subcommands: status, runs,
  quests, **quest**, tools, recent, errors, thinking, agent, full, **metrics**, timeline)
- `scripts/log_analysis/parse.py` — log parser (handles triple-nested
  tool_result JSON + observe-only `\n\nASCII_MAP:` split for Claude;
  step_finish cost/token aggregation + `<think>` extraction for OpenCode)
- `scripts/log_analysis/README.md` — programmatic API docs
- `prompts/quest_walkthroughs.json` — canonical Core 3 stage counts (used as the metrics denominator)
- Niral's metrics proposal: format / argument / tool-selection / stage
  completion / turn efficiency

## Output expectations

- Always include the run_id in the summary so the user knows what was analyzed.
- For per-agent metrics, name the personality (`grinder` / `completionist` /
  `explorer_tinkerer`).
- When answering "why did agent X plateau on quest Y", quote the per-quest
  reasoning block + the dominant error bucket while that quest was active —
  these two together usually pinpoint the cause.
- For OpenCode/DeepSeek runs, `analyze.py` aggregates `step_finish.cost` across
  every session in the run, so cost is real (not a fallback). Claude runs read
  cost from the `result` event per session.
