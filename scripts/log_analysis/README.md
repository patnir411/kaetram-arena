# scripts/log_analysis

Comprehensive log analyzer for Kaetram agent runs. Parses session JSONL logs
under `dataset/raw/agent_*/runs/run_*/` (Claude *and* OpenCode/DeepSeek/Qwen/
Grok shapes via auto-detect) and reports per-agent status, quest progression,
tool-call distribution, errors, recent activity, and reasoning.

**Default scope:** the **latest run per agent**, aggregating every session
in that run dir. Pass `--run <id>` to scope to a past run, or `--session N`
to drill down to a single session within the resolved run. By default, agents
whose latest run hasn't been touched in 10+ min are skipped — pass `--stale`
to include them.

## Usage

```bash
python3 scripts/log_analysis/analyze.py             # full report (default)
python3 scripts/log_analysis/analyze.py status      # one-line per agent (run-aggregated)
python3 scripts/log_analysis/analyze.py runs -n 10  # last N runs across all agents (run.meta.json)
python3 scripts/log_analysis/analyze.py timeline -n 30   # chronological events across the run
python3 scripts/log_analysis/analyze.py metrics     # paper metrics: format/argument/tool-sel/core3_stages/turn-eff
python3 scripts/log_analysis/analyze.py quests      # quest delta first→last in run + NPC interactions
python3 scripts/log_analysis/analyze.py quest       # per-quest progression timeline (default: Core 3)
python3 scripts/log_analysis/analyze.py quest rick  # scope to a single quest by substring match
python3 scripts/log_analysis/analyze.py quest --cross-run  # max-stage histogram across every run per agent
python3 scripts/log_analysis/analyze.py tools       # tool call counts + error rates across the run
python3 scripts/log_analysis/analyze.py errors      # CATEGORIZED errors + next-action transitions
python3 scripts/log_analysis/analyze.py errors --by-quest    # slice errors by which Core 3 quest was active
python3 scripts/log_analysis/analyze.py recent -n 8 # last N tool calls (latest session — session-scoped)
python3 scripts/log_analysis/analyze.py thinking -n 3   # last N reasoning blocks (latest session — session-scoped)
python3 scripts/log_analysis/analyze.py agent 1 -n 10   # deep-dive single agent (run-aggregated)
```

> **Slash command:** also exposed as `/log-analysis` (`.claude/commands/log-analysis.md`)
> for Claude to invoke when the user asks about run state, paper metrics, or per-agent
> stuck-points. Skill is the canonical entry point — `analyze.py` is the implementation.

### Filters

- `--run <run_id>` — scope to a specific historical run (e.g. `--run run_20260429_003701`). Parses **every** session in that run dir, not just the latest.
- `--session N` — drill down to a single session within the resolved run (1-based, matches `session_N_...` filename token). Use this with run-scoped commands when you want a single-session view.
- `--all-runs` — for `runs` cmd, list every run instead of the recent N.
- `--stale` — include agents whose latest run hasn't been touched in 10+ min.
- `--claude` / `--opencode` — force a parser (default: auto-detect from each session's meta.json).

### Run-scoped vs session-scoped

Most commands aggregate across every session in the run. The exceptions are
`recent` and `thinking` — both want a single session because their semantics
are *temporal* ("what's the model doing/thinking right now"). They default to
the latest session in the resolved run; pass `--session N` to scope to a
specific one.

`status` is the fastest signal — run_id, level, HP, pos, cumulative quest
state across the run, total turns, total errors, total cost. `metrics` emits
the 5 paper-claim numbers per agent. **Core 3 progress is summed in stages
across the 3 quests** (denominator from `prompts/quest_walkthroughs.json`,
currently 10), with the per-quest delta computed as
`last_observe stage − first_observe stage` — so partial progress moves the
metric *and* resume-state replays don't inflate it. Tool-selection is
DEFERRED — needs a Claude-as-judge sample.

`quest` is the per-quest progression view: stage transitions, the trigger
tool call for each, the model's reasoning right before each advance, NPCs
talked to, and tool/error breakdown while each Core 3 quest was active.
Pass a quest name (or substring) to scope to one, or `--cross-run` to
histogram max-stage reached across every run per agent — directly answers
"where do agents plateau?".

`errors` buckets failures into stable categories (BFS_NO_PATH, STILL_MOVING,
NPC_NOT_FOUND, STATION_UNREACHABLE, COMBAT_BLOCKED_WARP, MCP_DISCONNECT,
SKILL_GATED, …) and shows the top 3 follow-up actions per category — directly
diagnoses recovery vs loop, and is where rule-adoption questions like "did
agents warp after BFS_NO_PATH or just retry navigate?" get answered. Pass
`--by-quest` to slice the same data by which Core 3 quest was active when
the error fired (e.g. "Rick's Roll active → 9× STATION_UNREACHABLE means
the agent couldn't reach a cooking station").

## Files

- `parse.py` — log parser. Handles the triple-nested JSON encoding for the
  Claude harness (`tool_result.content` → `{"result": "..."}` → game-state
  JSON, with the observe-only `\n\nASCII_MAP:` split) and the OpenCode shape
  (raw kaetram tool output, no result-event, cost/tokens aggregated from
  `step_finish` parts, `<think>...</think>` extraction from text events). Same
  kernel powers `scripts/export_report.py` so the dashboard report and the
  CLI tooling stay in lock-step.
- `analyze.py` — CLI. Built on top of `parse.py`; report logic lives here.

## Programmatic API

```python
from scripts.log_analysis.parse import (
    list_agent_dirs, latest_run, parse_run_sessions,
    parse_session_auto, latest_observe, categorize_error,
    RunSessionsView, SessionView,
)

# Run-scoped aggregation (the default shape):
for agent_dir in list_agent_dirs():
    rv = parse_run_sessions(agent_dir, latest_run(agent_dir))
    print(rv.run_id, rv.n_sessions, rv.total_turns, rv.total_cost_usd)
    print("first observe:", rv.first_observe_in_run())
    print("last observe :", rv.last_observe_in_run())
    print("tool counts  :", rv.tool_call_counts())
    print("errors       :", rv.tool_error_counts())

# Session-scoped (when you really want one session):
sv = parse_session_auto(some_log_path)
gs = latest_observe(sv)
```
