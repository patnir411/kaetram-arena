#!/usr/bin/env python3
"""Comprehensive log analyzer for Kaetram agent runs.

Default scope: the **latest run per agent**, aggregating every session_*.log
in that run dir. Pass `--run <id>` to scope to a past run, or `--session N`
to drill down to a single session within the resolved run (default for
session-only commands like `recent`/`thinking`: latest session in the
resolved run).

Usage:
    python3 scripts/log_analysis/analyze.py                  # full report (default)
    python3 scripts/log_analysis/analyze.py status           # one-line per agent (run-aggregated)
    python3 scripts/log_analysis/analyze.py runs [-n 10]     # recent runs across all agents
    python3 scripts/log_analysis/analyze.py timeline [-n 30] # chronological events across the run
    python3 scripts/log_analysis/analyze.py quests           # quest delta first→last in run
    python3 scripts/log_analysis/analyze.py quest [name]     # per-quest progression timeline (default: Core 3)
    python3 scripts/log_analysis/analyze.py quest --cross-run [name]  # max-stage histogram across all runs
    python3 scripts/log_analysis/analyze.py tools            # tool counts + error rates across the run
    python3 scripts/log_analysis/analyze.py recent [-n 10]   # last N tool calls (latest session — session-scoped)
    python3 scripts/log_analysis/analyze.py errors [--by-quest]  # categorized errors + next-action transitions across the run
    python3 scripts/log_analysis/analyze.py thinking [-n 5]  # last N reasoning blocks (latest session — session-scoped)
    python3 scripts/log_analysis/analyze.py metrics          # paper metrics (run-scoped, stage-level Core 3 denominator)
    python3 scripts/log_analysis/analyze.py agent <N>        # deep-dive single agent (run-aggregated)

Filters:
    --run <id>      scope to that run dir (parses ALL sessions in it)
    --session N     drill down to one session inside the resolved run
    --stale         include agents whose latest run hasn't been touched in 10+ min
    --by-quest      (errors) slice errors by which Core 3 quest was active
    --cross-run     (quest)  walk every run per agent for a max-stage histogram
    --opencode/--claude  force a parser (default: auto-detect from each session's meta.json)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.log_analysis.parse import (  # noqa: E402
    CORE_3_QUEST_NAMES,
    QuestProgression,
    RunSessionsView,
    SessionView,
    categorize_error,
    core3_total_stage_count,
    deaths,
    first_observe,
    fmt_duration,
    fmt_est,
    latest_observe,
    latest_run,
    list_agent_dirs,
    list_runs,
    parse_run_sessions,
    parse_session_timestamp,
    progression_for_quests,
    quest_stage_counts,
    runs_per_agent,
)


# ── Formatting helpers ──────────────────────────────────────────────────────

def _fmt_pos(p: dict | None) -> str:
    if not isinstance(p, dict):
        return "?"
    return f"({p.get('x','?')},{p.get('y','?')})"


def _fmt_quests(qs: list | None) -> str:
    if not qs:
        return "—"
    parts = []
    for q in qs:
        if not isinstance(q, dict):
            continue
        name = q.get("name", "?")
        stage = q.get("stage")
        sc = q.get("stage_count")
        if stage is not None and sc is not None:
            parts.append(f"{name} {stage}/{sc}")
        else:
            parts.append(name)
    return ", ".join(parts) if parts else "—"


def _fmt_quest_names(qs: list | None) -> list[str]:
    """Names only, defensively (qs may contain strings or dicts)."""
    if not qs:
        return []
    out = []
    for q in qs:
        if isinstance(q, dict):
            n = q.get("name")
            if n:
                out.append(n)
        elif isinstance(q, str):
            out.append(q)
    return out


def _age(path: Path) -> str:
    age_s = dt.datetime.now().timestamp() - path.stat().st_mtime
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 3600:
        return f"{int(age_s/60)}m ago"
    return f"{age_s/3600:.1f}h ago"


def _agent_id_from_dir(agent_dir: Path) -> str:
    return agent_dir.name.replace("agent_", "")


_ARCH_ABBREV = {
    "grinder": "grinder",
    "completionist": "complet",
    "explorer_tinkerer": "explor",
    "explorer": "explor",
}


def _personality_short(name: str | None) -> str:
    if not name:
        return "?"
    return _ARCH_ABBREV.get(name, name[:10])


def _bar(label: str) -> str:
    return f"\n{'─' * 4} {label} {'─' * max(0, 70 - len(label))}\n"


# ── Run-scoped commands (consume RunSessionsView) ────────────────────────────

def cmd_status(rvs: list[RunSessionsView]) -> None:
    """One-line per agent — run-aggregated health check.

    Columns: agent / arch / lvl / hp / pos / total turns across run / total
    errors across run / rate-limit events / cumulative finished quests /
    currently active quests.
    """
    if rvs:
        primary = rvs[0]
        print(f"  Run: {primary.run_id}   started: {fmt_est(primary.started_at)}   "
              f"elapsed: {fmt_duration(primary.duration_s)}   "
              f"sessions: {primary.n_sessions}   "
              f"cost: {_fmt_cost(primary.total_cost_usd)}")
        print()
    print(f"{'agent':<6}{'arch':<9}{'lvl':<5}{'hp':<11}{'pos':<13}"
          f"{'turns':<7}{'errs':<6}{'rl':<4}finished | active")
    print("─" * 110)
    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        arch = _personality_short(rv.personality)
        gs = rv.last_observe_in_run() or {}
        stats = gs.get("stats", {}) or {}
        lvl = stats.get("level", "?")
        hp = f"{stats.get('hp','?')}/{stats.get('max_hp','?')}"
        pos = _fmt_pos(gs.get("pos"))
        turns = rv.total_turns
        err_total = sum(1 for tc in rv.all_tool_calls if tc.is_error)
        rl = sum(sv.rate_limit_events for sv in rv.sessions)
        finished = ", ".join(_fmt_quest_names(gs.get("finished_quests"))) or "—"
        active = _fmt_quests(gs.get("active_quests"))
        print(f"{aid:<6}{arch:<14}{str(lvl):<5}{hp:<11}{pos:<13}"
              f"{str(turns):<7}{str(err_total):<6}{str(rl):<4}{finished} | {active}")
    # Surface malformed-emission spam only when present — it was a hidden
    # run-killer (r3 paralysis) invisible to every other column.
    mal = sum(rv.n_malformed_emit for rv in rvs)
    if mal:
        recov = sum(1 for rv in rvs for tc in rv.all_tool_calls
                    if isinstance(tc.result_raw, str) and tc.result_raw.startswith("[format]"))
        tag = (f"{recov} harness-recovered" if recov
               else "NONE recovered — dropped as no-op spam")
        print(f"  ⚠ {mal} malformed tool-call emissions in text ({tag})")


def cmd_quests(rvs: list[RunSessionsView]) -> None:
    """Quest progression detail per agent — first observe of run vs last
    observe of run, plus NPC interactions and query_quest calls aggregated."""
    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        print(_bar(f"agent_{aid} ({rv.personality})  run={rv.run_id}  ({rv.n_sessions} sessions)"))
        gs0 = rv.first_observe_in_run() or {}
        gs1 = rv.last_observe_in_run() or {}
        f0 = _fmt_quest_names(gs0.get("finished_quests"))
        f1 = _fmt_quest_names(gs1.get("finished_quests"))
        new_completions = sorted(set(f1) - set(f0))
        print(f"  start: finished={f0}  active={_fmt_quest_names(gs0.get('active_quests'))}")
        print(f"  end:   finished={f1}  active={_fmt_quests(gs1.get('active_quests'))}")
        print(f"  NEW completions this run: {new_completions or '—'}")

        npc_count: dict[str, int] = {}
        accepts = 0
        qq_names: list[str] = []
        for tc in rv.all_tool_calls:
            if tc.short_name == "interact_npc":
                npc = tc.input.get("npc_name", "?")
                npc_count[npc] = npc_count.get(npc, 0) + 1
                if isinstance(tc.result_payload, dict) and tc.result_payload.get("quest_accepted"):
                    accepts += 1
            elif tc.short_name == "query_quest":
                qq_names.append(tc.input.get("quest_name", "?"))
        if npc_count:
            top = sorted(npc_count.items(), key=lambda kv: -kv[1])[:8]
            print(f"  NPC talks (top 8): {dict(top)}    quest_accepted events: {accepts}")
        if qq_names:
            print(f"  query_quest ({len(qq_names)}): {qq_names[:12]}{' …' if len(qq_names) > 12 else ''}")


def cmd_tools(rvs: list[RunSessionsView]) -> None:
    """Tool call distribution + error rates aggregated across the run."""
    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        n = rv.total_tool_calls
        print(_bar(f"agent_{aid} ({rv.personality})  {n} calls across {rv.n_sessions} sessions"))
        ec = rv.tool_error_counts()
        for tool, (errs, total) in sorted(ec.items(), key=lambda kv: -kv[1][1]):
            pct_err = (errs / total * 100) if total else 0
            tag = f"  ERR {errs}/{total} ({pct_err:.0f}%)" if errs else ""
            print(f"    {tool:<20} {total:>4}{tag}")


def cmd_errors(rvs: list[RunSessionsView], by_quest: bool = False) -> None:
    """Categorized errors + 'what did the agent do next' transitions across
    the entire run. The next-action transition is THE diagnostic for whether
    a rule landed (e.g. BFS_NO_PATH → warp vs BFS_NO_PATH → navigate retry).

    `by_quest=True` slices each agent's errors by which Core 3 quest was
    active at the time, surfacing per-quest failure modes (e.g. Rick's Roll
    stage-1 STATION_UNREACHABLE vs Herbalist's BFS_NO_PATH on overland walk).
    """
    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        all_calls = rv.all_tool_calls
        errs = [(i, tc) for i, tc in enumerate(all_calls) if tc.is_error]
        if not errs:
            continue
        print(_bar(f"agent_{aid} — {len(errs)} errors of {len(all_calls)} across {rv.n_sessions} sessions"))

        if by_quest:
            # Build a per-quest progression so we know, for each tool-call
            # index, which Core 3 quests were active. Then group errors by
            # the active set at error time.
            progs = progression_for_quests(rv)
            # Replay observes to build a per-run-idx → active set map.
            active_at: list[set[str]] = []
            current: set[str] = set()
            for tc in all_calls:
                if tc.short_name == "observe" and isinstance(tc.result_payload, dict):
                    current = {
                        q.get("name") for q in (tc.result_payload.get("active_quests") or [])
                        if isinstance(q, dict) and q.get("name") in CORE_3_QUEST_NAMES
                    }
                active_at.append(set(current))

            buckets: dict[str, list[tuple[int, "ToolCall"]]] = {}  # noqa: F821
            for i, tc in errs:
                actives = active_at[i] if i < len(active_at) else set()
                if not actives:
                    buckets.setdefault("(none active)", []).append((i, tc))
                else:
                    for qname in actives:
                        buckets.setdefault(qname, []).append((i, tc))

            for qname in sorted(buckets.keys(), key=lambda k: -len(buckets[k])):
                qerrs = buckets[qname]
                cat_counts: dict[str, int] = {}
                next_actions: dict[str, dict[str, int]] = {}
                sample: dict[str, str] = {}
                for i, tc in qerrs:
                    cat = categorize_error(tc.result_error or "")
                    cat_counts[cat] = cat_counts.get(cat, 0) + 1
                    sample.setdefault(cat, f"{tc.short_name}: {(tc.result_error or '')[:100]}")
                    nxt = all_calls[i+1].short_name if i+1 < len(all_calls) else "<end>"
                    next_actions.setdefault(cat, {})
                    next_actions[cat][nxt] = next_actions[cat].get(nxt, 0) + 1
                print(f"  ▸ while {qname} active — {len(qerrs)} errors")
                for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
                    top_next = sorted(next_actions[cat].items(), key=lambda kv: -kv[1])[:3]
                    transitions = ", ".join(f"{n}×{c}" for n, c in top_next)
                    print(f"      [{count}x] {cat:<22} → next: {transitions}")
            continue

        cat_counts: dict[str, int] = {}
        next_actions: dict[str, dict[str, int]] = {}
        sample: dict[str, str] = {}
        for i, tc in errs:
            cat = categorize_error(tc.result_error or "")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            sample.setdefault(cat, f"{tc.short_name}: {(tc.result_error or '')[:120]}")
            nxt = all_calls[i+1].short_name if i+1 < len(all_calls) else "<end>"
            next_actions.setdefault(cat, {})
            next_actions[cat][nxt] = next_actions[cat].get(nxt, 0) + 1
        for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
            top_next = sorted(next_actions[cat].items(), key=lambda kv: -kv[1])[:3]
            transitions = ", ".join(f"{n}×{c}" for n, c in top_next)
            print(f"    [{count}x] {cat:<22} → next: {transitions}")
            print(f"           ex: {sample[cat]}")


# Argument-style error markers — distinguish schema/validation failures from
# game-state errors (NPC_NOT_FOUND, BFS_NO_PATH, etc., which are gameplay
# outcomes, not bad arguments).
_ARG_ERROR_KEYWORDS = (
    "validation", "invalid type", "missing field", "schema",
    "required parameter", "must be one of", "expected ", "unexpected keyword",
    "could not parse", "invalid argument",
)

# Core 3 quest names for stage-completion scoring (the maintainer's metric #4).
_CORE_3_QUEST_NAMES = {
    "Foresting", "Herbalist's Desperation", "Rick's Roll",
}


def cmd_metrics(rvs: list[RunSessionsView]) -> None:
    """Paper metrics scored over the entire run (the maintainer's 5).

    core3 is the **stage** delta first→last observe — captures partial
    progress (e.g. Rick's Roll 1→3 counts as +2 of 4) so the metric moves
    week-over-week instead of remaining 0/3 until a whole quest finishes.
    turn-eff denominator is total_turns across the run.
    """
    total_stages = core3_total_stage_count()
    counts = quest_stage_counts()
    core3_set = set(CORE_3_QUEST_NAMES)

    print(_bar("METRICS — paper-claim scorer (run-scoped)"))
    print(f"{'agent':<7}{'persona':<14}{'calls':<7}"
          f"{'format':<10}{'argument':<11}{'tool-sel':<10}"
          f"{'core3_stages':<14}{'turn-eff':<18}")
    print("─" * 100)

    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        persona = _personality_short(rv.personality)
        all_calls = rv.all_tool_calls
        n_calls = len(all_calls)
        if n_calls == 0:
            print(f"{aid:<7}{persona:<14}{'0':<7}(no tool calls)")
            continue

        # 1. Format accuracy — payload decoded.
        n_format_ok = sum(1 for tc in all_calls if tc.result_payload is not None)
        format_pct = 100 * n_format_ok / n_calls

        # 2. Argument accuracy — format-ok AND no schema/validation error.
        def _is_arg_error(tc) -> bool:
            err = (tc.result_error or "").lower()
            if not err:
                return False
            return any(k in err for k in _ARG_ERROR_KEYWORDS)

        n_arg_ok = sum(
            1 for tc in all_calls
            if tc.result_payload is not None and not _is_arg_error(tc)
        )
        arg_pct = 100 * n_arg_ok / n_calls

        # 3. Tool selection — needs LLM judge; deferred.
        tool_sel = "DEFERRED"

        # 4. Core 3 STAGE completion across the run. Compute first-of-run
        # and last-of-run state for each Core 3 quest (active stage if
        # active, full stage_count if finished, 0 otherwise) and sum the
        # delta — only stages genuinely reached this run count.
        def _stage_state_at(observe_payload: dict) -> dict[str, int]:
            out: dict[str, int] = {n: 0 for n in core3_set}
            for q in (observe_payload.get("finished_quests") or []):
                if isinstance(q, dict):
                    n = q.get("name")
                    if n in core3_set:
                        out[n] = counts.get(n, 0)
            for q in (observe_payload.get("active_quests") or []):
                if isinstance(q, dict):
                    n = q.get("name")
                    if n in core3_set:
                        st = q.get("stage")
                        if isinstance(st, int) and st > out[n]:
                            out[n] = st
            return out

        gs0 = rv.first_observe_in_run() or {}
        gs1 = rv.last_observe_in_run() or {}
        s0 = _stage_state_at(gs0)
        s1 = _stage_state_at(gs1)
        new_stages = sum(max(0, s1[n] - s0[n]) for n in core3_set)
        core3_str = f"{new_stages}/{total_stages}"

        # 5. Turn efficiency — Core 3 stage progress / total turns.
        n_turns = rv.total_turns
        if n_turns and new_stages:
            eff = f"{new_stages}/{n_turns}={new_stages/n_turns:.4f}"
        elif n_turns:
            eff = f"0/{n_turns}"
        else:
            eff = "—"

        print(f"{aid:<7}{persona:<14}{n_calls:<7}"
              f"{format_pct:>5.1f}%    {arg_pct:>5.1f}%     {tool_sel:<10}"
              f"{core3_str:<14}{eff:<18}")
    print()
    print("Caveats:")
    print("  format       = % tool results where the JSON payload parsed (decoder ok)")
    print("  argument     = format-ok AND no schema/validation error in result.")
    print("                 Game-state errors (NPC_NOT_FOUND, BFS_NO_PATH, …) do NOT count.")
    print("  tool-sel     = DEFERRED — needs LLM-judge or hand-labeled sample.")
    print(f"  core3_stages = NEW Core 3 stages reached this run (last − first observe).")
    print(f"                 Denominator {total_stages} = sum of stage counts: "
          + ", ".join(f"{n}={counts.get(n,'?')}" for n in CORE_3_QUEST_NAMES))
    print("  turn-eff     = new stages / total_turns. Higher = better planning.")
    print("                 total_turns sums per-session num_turns across the run.")


def cmd_timeline(rvs: list[RunSessionsView], n: int = 30) -> None:
    """Chronological event stream for one agent across the entire run.

    Walks every session in order, emitting deaths, accepts, BFS-fails, warps,
    query_quests, level-ups, and first-time quest completions.
    """
    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        print(_bar(f"agent_{aid} — run={rv.run_id} ({rv.n_sessions} sessions, "
                   f"started {fmt_est(rv.started_at)})"))
        events: list[tuple[int, int, str]] = []   # (session_idx, tc_idx, msg)
        last_level: int | None = None
        seen_finished: set[str] = set()
        for s_idx, sv in enumerate(rv.sessions):
            ts = parse_session_timestamp(sv.log_path)
            events.append((s_idx, -1, f"━━━ session #{s_idx+1} starts at {fmt_est(ts)} ━━━"))
            for tc in sv.tool_calls:
                p = tc.result_payload if isinstance(tc.result_payload, dict) else {}
                if tc.short_name == "respawn":
                    events.append((s_idx, tc.idx, "💀 respawn"))
                elif tc.short_name == "interact_npc" and tc.input.get("accept_quest_offer") \
                     and isinstance(p, dict) and (p.get("quest_accepted") or p.get("quest_opened")):
                    events.append((s_idx, tc.idx, f"📜 ACCEPT {tc.input.get('npc_name')}"))
                elif tc.short_name == "navigate" and tc.is_error and "BFS" in (tc.result_error or ""):
                    events.append((s_idx, tc.idx, f"🚫 BFS no-path → ({tc.input.get('x')},{tc.input.get('y')})"))
                elif tc.short_name == "warp":
                    events.append((s_idx, tc.idx, f"🌀 warp({tc.input.get('location')})"))
                elif tc.short_name == "query_quest":
                    lgs = (p.get("live_gate_status") or {}) if isinstance(p, dict) else {}
                    tag = " [GATED]" if lgs.get("gated") else ""
                    events.append((s_idx, tc.idx, f"❓ query {tc.input.get('quest_name')}{tag}"))
                if tc.short_name == "observe" and isinstance(p, dict):
                    lvl = (p.get("stats") or {}).get("level")
                    if lvl is not None and last_level is not None and lvl > last_level:
                        events.append((s_idx, tc.idx, f"⭐ LEVEL UP {last_level} → {lvl}"))
                    if lvl is not None:
                        last_level = lvl
                    for q in (p.get("finished_quests") or []):
                        if not isinstance(q, dict):
                            continue
                        name = q.get("name") or "?"
                        if name not in seen_finished:
                            seen_finished.add(name)
                            events.append((s_idx, tc.idx, f"✅ {name}"))
        for s_idx, tc_idx, msg in events[-n:]:
            tag = "──" if tc_idx < 0 else f"#{tc_idx:<3}"
            print(f"  s{s_idx+1} {tag} {msg}")
        if not events:
            print("  (no notable events)")


def cmd_agent_run(rv: RunSessionsView, n_recent: int = 10) -> None:
    """Deep-dive one agent across its run."""
    aid = _agent_id_from_dir(rv.agent_dir)
    print(_bar(f"agent_{aid} ({rv.personality}) — run={rv.run_id} "
               f"({rv.n_sessions} sessions, {fmt_duration(rv.duration_s)})"))
    print(f"  meta: harness={rv.harness}  model={rv.model}")
    n_assistant = sum(sv.n_assistant for sv in rv.sessions)
    n_user = sum(sv.n_user for sv in rv.sessions)
    n_thinking = rv.n_thinking
    n_text = rv.n_text
    rl = sum(sv.rate_limit_events for sv in rv.sessions)
    print(f"  turns: {n_assistant} assistant / {n_user} user / "
          f"{n_thinking} thinking / {n_text} text / "
          f"{rv.total_tool_calls} tool_calls / {rl} rate_limit_events")
    print(f"  cost: {_fmt_cost(rv.total_cost_usd)}    "
          f"tokens: {_fmt_tokens(rv.total_tokens)}    "
          f"terminal: {rv.terminal_reason}{' (synthetic)' if rv.synthetic_summary else ''}")

    gs0 = rv.first_observe_in_run() or {}
    gs1 = rv.last_observe_in_run() or {}
    s0 = (gs0.get("stats") or {})
    s1 = (gs1.get("stats") or {})

    def _chg(a, b):
        try: return f"{a}→{b} (+{b-a})"
        except Exception: return f"{a}→{b}"

    print(f"  level: {_chg(s0.get('level','?'), s1.get('level','?'))}    "
          f"xp: {_chg(s0.get('xp','?'), s1.get('xp','?'))}    "
          f"hp now: {s1.get('hp','?')}/{s1.get('max_hp','?')}")
    print(f"  pos start: {_fmt_pos(gs0.get('pos'))}   pos now: {_fmt_pos(gs1.get('pos'))}")
    print(f"  finished: {_fmt_quest_names(gs1.get('finished_quests'))}")
    print(f"  new this run: {sorted(set(_fmt_quest_names(gs1.get('finished_quests'))) - set(_fmt_quest_names(gs0.get('finished_quests'))))}")
    print(f"  active:   {_fmt_quests(gs1.get('active_quests'))}")
    print(f"  inv items: {len(gs1.get('inventory') or [])}    "
          f"deaths: {len(rv.deaths())}")

    print(f"\n  tool counts: {rv.tool_call_counts()}")

    err_count = sum(1 for tc in rv.all_tool_calls if tc.is_error)
    if err_count:
        print(f"  errors: {err_count} (run `analyze.py errors` for detail)")

    print(f"\n  last {n_recent} tool calls (latest session):")
    last_sv = rv.sessions[-1] if rv.sessions else None
    if last_sv:
        for tc in last_sv.tool_calls[-n_recent:]:
            inp = ", ".join(f"{k}={v!r}" for k, v in list(tc.input.items())[:3])
            err = " [ERR]" if tc.is_error else ""
            print(f"    #{tc.idx:<3} {tc.short_name:<16} {inp}{err}")


def _resolve_quest_filter(quest_arg: str | None) -> list[str]:
    """Translate a `quest <name>` argument into a concrete name list.

    No arg / `core3` → the Core 3. Any other string → resolve against
    quest_walkthroughs.json keys; case-insensitive substring match wins.
    Unknown names fall through as-is so callers always get *something*.
    """
    if not quest_arg or quest_arg.lower() == "core3":
        return list(CORE_3_QUEST_NAMES)
    targets = list(quest_stage_counts().keys())
    needle = quest_arg.lower()
    matches = [n for n in targets if needle in n.lower()]
    return matches or [quest_arg]


def _fmt_progression_block(prog: QuestProgression) -> list[str]:
    """Multi-line render of a single QuestProgression — used by cmd_quest."""
    lines: list[str] = []
    reached, total = prog.stage_fraction
    if prog.finished:
        header = f"  {prog.quest_name}  {reached}/{total}  ✅ FINISHED"
    elif prog.accepted_at_run_idx is None:
        header = f"  {prog.quest_name}  0/{total}  (untouched)"
    else:
        last = prog.last_state or (prog.max_stage_reached, 0)
        header = (
            f"  {prog.quest_name}  {reached}/{total}  "
            f"(active — last seen at stage {last[0]}/{total} sub_stage={last[1]})"
        )
    lines.append(header)
    if prog.stage_events:
        for ev in prog.stage_events:
            args = ev.trigger_args or {}
            arg_hint = (
                args.get("npc_name") or args.get("location")
                or args.get("recipe_key") or args.get("resource_name") or ""
            )
            arg_str = f"({arg_hint})" if arg_hint else ""
            kind_emoji = "📜" if ev.kind == "accept" else ("🎯" if ev.to_stage == prog.stage_count else "↗️")
            lines.append(
                f"    {kind_emoji} s{ev.session_idx+1} #{ev.tc_session_idx}: "
                f"{ev.from_stage}→{ev.to_stage} via {ev.trigger_tool}{arg_str}"
            )
            if ev.thinking:
                snippet = ev.thinking.replace("\n", " ").strip()
                if snippet:
                    lines.append(f"        thinking: {snippet[:200]}")
    if prog.turns_active:
        top_tools = sorted(prog.tools_while_active.items(), key=lambda kv: -kv[1])[:5]
        lines.append(
            f"    {prog.turns_active} active observes; "
            f"top tools: {dict(top_tools)}"
        )
    if prog.npcs_while_active:
        top_npcs = sorted(prog.npcs_while_active.items(), key=lambda kv: -kv[1])[:5]
        lines.append(f"    NPCs talked to: {dict(top_npcs)}")
    if prog.errors_while_active:
        sorted_errs = sorted(prog.errors_while_active.items(), key=lambda kv: -kv[1])
        lines.append(
            f"    errors while active: "
            + ", ".join(f"{cat}×{n}" for cat, n in sorted_errs)
        )
    return lines


def cmd_quest(rvs: list[RunSessionsView], quest_arg: str | None = None,
              cross_run: bool = False, harness_override: str | None = None) -> None:
    """Per-quest progression timeline + diagnostics across the run.

    Default scope: Core 3. Pass a quest name (or substring) to scope to one.
    `--cross-run` switches to a max-stage histogram across every run for
    each agent — answers "where do agents plateau on quest X?".
    """
    targets = _resolve_quest_filter(quest_arg)

    if cross_run:
        _cmd_quest_cross_run(targets, harness_override=harness_override)
        return

    for rv in rvs:
        aid = _agent_id_from_dir(rv.agent_dir)
        print(_bar(f"agent_{aid} ({rv.personality})  run={rv.run_id}  ({rv.n_sessions} sessions)"))
        progs = progression_for_quests(rv, quest_names=targets)
        for name in targets:
            prog = progs.get(name)
            if prog is None:
                print(f"  {name}: (no stage_count in quest_walkthroughs.json)")
                continue
            for line in _fmt_progression_block(prog):
                print(line)
            print()


def _cmd_quest_cross_run(targets: list[str], harness_override: str | None = None) -> None:
    """Histogram of max-stage reached per quest across every run for each
    agent. Answers 'where do agents plateau?'. Heavy — re-parses every run."""
    from collections import Counter
    print(_bar(f"CROSS-RUN max-stage histogram for {len(targets)} quest(s)"))
    counts = quest_stage_counts()
    for agent_dir in list_agent_dirs():
        run_dirs = list_runs(agent_dir)
        if not run_dirs:
            continue
        # Per-quest histogram of max stages reached across the agent's runs.
        per_quest: dict[str, Counter] = {n: Counter() for n in targets}
        finished_count: dict[str, int] = {n: 0 for n in targets}
        runs_seen = 0
        for rd in run_dirs:
            try:
                rv = parse_run_sessions(agent_dir, rd, harness=harness_override)
            except Exception:
                continue
            if not rv.sessions:
                continue
            runs_seen += 1
            progs = progression_for_quests(rv, quest_names=targets)
            for n in targets:
                p = progs.get(n)
                if p is None:
                    continue
                if p.finished:
                    finished_count[n] += 1
                    per_quest[n][p.stage_count] += 1
                else:
                    per_quest[n][p.max_stage_reached] += 1
        aid = _agent_id_from_dir(agent_dir)
        print(f"\n  agent_{aid}  ({runs_seen} runs)")
        for n in targets:
            sc = counts.get(n, 0)
            hist = per_quest[n]
            if not hist:
                continue
            histogram_str = "  ".join(
                f"[{stage}]×{hist[stage]}" for stage in sorted(hist.keys())
            )
            fin = finished_count[n]
            print(f"    {n:<25} {histogram_str}    →  finished {fin}/{runs_seen}")


def cmd_full(rvs: list[RunSessionsView]) -> None:
    """Full report across the run: status + quests + tools + core3 quest progression + errors."""
    print(_bar("STATUS"))
    cmd_status(rvs)
    print(_bar("QUESTS"))
    cmd_quests(rvs)
    print(_bar("TOOLS"))
    cmd_tools(rvs)
    print(_bar("CORE 3 PROGRESSION"))
    cmd_quest(rvs)
    has_errs = any(tc.is_error for rv in rvs for tc in rv.all_tool_calls)
    if has_errs:
        print(_bar("ERRORS"))
        cmd_errors(rvs)


# ── Session-scoped commands (consume SessionView) ────────────────────────────
#
# `recent` and `thinking` semantically want a single session — the user is
# asking "what's the model doing/thinking right now". Run-aggregating those
# loses the temporal locality. Both default to the latest session in the
# resolved run; `--session N` picks a specific session.

def cmd_recent(views: list[tuple[Path, SessionView]], n: int) -> None:
    """Last N tool calls per agent in the resolved session."""
    for agent_dir, sv in views:
        aid = _agent_id_from_dir(agent_dir)
        persona = sv.meta.get("personality", "?")
        print(_bar(f"agent_{aid} ({persona}) — {sv.log_path.name} — last {n} tool calls"))
        for tc in sv.tool_calls[-n:]:
            inp = ", ".join(f"{k}={v!r}" for k, v in list(tc.input.items())[:3])
            err = " [ERR]" if tc.is_error else ""
            err_msg = f"  → {tc.result_error[:80]}" if tc.is_error else ""
            print(f"    #{tc.idx:<3} {tc.short_name:<16} {inp}{err}{err_msg}")


def cmd_thinking(views: list[tuple[Path, SessionView]], n: int) -> None:
    """Last N reasoning blocks per agent in the resolved session."""
    for agent_dir, sv in views:
        aid = _agent_id_from_dir(agent_dir)
        persona = sv.meta.get("personality", "?")
        print(_bar(f"agent_{aid} ({persona}) — {sv.log_path.name} — last {n} thinking blocks"))
        with_think = [tc for tc in sv.tool_calls if tc.thinking]
        for tc in with_think[-n:]:
            think = (tc.thinking or "").replace("\n", " ")[:400]
            print(f"  → before #{tc.idx} ({tc.short_name}): {think}")


# ── Cost / token formatting ──────────────────────────────────────────────────

def _fmt_cost(cost: float | None) -> str:
    if cost is None:
        return "—"
    return f"${cost:.2f}"


def _fmt_tokens(toks: dict | None) -> str:
    if not toks:
        return "—"
    parts = []
    for k in ("input_tokens", "output_tokens", "reasoning_tokens",
              "cache_read_tokens", "cache_write_tokens", "total_tokens"):
        v = toks.get(k)
        if v:
            parts.append(f"{k.replace('_tokens',''):>6}={_humanize(v)}")
    return "  ".join(parts) if parts else "—"


def _humanize(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


# ── `runs` command (run-meta listing — unchanged, uses RunView lite) ─────────

def _run_total_cost_usd_lite(run) -> float:
    """Cheap cost-from-tail. Used by `cmd_runs` to avoid parsing every session
    log just to render a table row. For claude this reads the `result` event;
    for opencode this is N/A (no final result line) and we return 0 — the
    full parse_run_sessions surfaces real opencode cost."""
    total = 0.0
    for p in run.session_paths:
        try:
            with open(p, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("utf-8", errors="ignore")
            for line in reversed(tail.splitlines()):
                if '"type":"result"' in line or '"type": "result"' in line:
                    import json as _json
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        break
                    cost = rec.get("total_cost_usd")
                    if isinstance(cost, (int, float)):
                        total += float(cost)
                    break
        except OSError:
            continue
    return total


def cmd_runs(_views, n: int = 10, all_runs: bool = False) -> None:
    """List recent runs across all agents with key stats from run.meta.json."""
    runs = runs_per_agent()
    runs.sort(key=lambda r: r.run_id, reverse=True)
    if not all_runs:
        runs = runs[:n]
    print(f"{'run_id':<24}{'agent':<7}{'pers':<14}{'harness':<9}"
          f"{'sessions':<10}{'started_at':<26}{'duration':<10}{'cost_usd':<10}")
    print("─" * 110)
    for r in runs:
        m = r.meta
        sessions_meta = m.get("session_count")
        sessions_actual = len(r.session_paths)
        sessions = (f"{sessions_actual}" if sessions_meta == sessions_actual
                    else f"{sessions_actual} (meta:{sessions_meta})")
        cost = _run_total_cost_usd_lite(r)
        cost_str = f"${cost:.2f}" if cost > 0 else "—"
        print(f"{r.run_id:<24}"
              f"{str(m.get('agent_id','?')):<7}"
              f"{(m.get('personality') or '?')[:13]:<14}"
              f"{(m.get('harness') or '?')[:8]:<9}"
              f"{sessions:<10}"
              f"{fmt_est(r.started_at):<26}"
              f"{fmt_duration(r.duration_s):<10}"
              f"{cost_str:<10}")


# ── Driver ───────────────────────────────────────────────────────────────────

_STALE_SECONDS = 600  # 10 minutes — log untouched longer than this is from a prior run


def _resolve_run_dir(agent_dir: Path, run_id: str | None) -> Path | None:
    """Resolve an agent's run dir given an optional run_id filter.

    No run_id → latest run (resolves the `logs/` symlink).
    run_id given → that specific run's dir under the agent.
    """
    if run_id:
        rd = agent_dir / "runs" / run_id
        return rd if rd.is_dir() else None
    return latest_run(agent_dir)


def _load_run_views(only_agent: int | None = None,
                    include_stale: bool = False,
                    harness_override: str | None = None,
                    run_id: str | None = None) -> list[RunSessionsView]:
    """Multi-session view for each agent, scoped to the resolved run."""
    out: list[RunSessionsView] = []
    now = dt.datetime.now().timestamp()
    for agent_dir in list_agent_dirs():
        if only_agent is not None and agent_dir.name != f"agent_{only_agent}":
            continue
        run_dir = _resolve_run_dir(agent_dir, run_id)
        if not run_dir:
            continue
        sessions = sorted(run_dir.glob("session_*.log"))
        if not sessions:
            continue
        # Stale filter: skip agents whose latest session log is >10 min old
        # — applies only when run_id is not explicitly set (live mode).
        if not run_id and not include_stale:
            latest_mtime = max(p.stat().st_mtime for p in sessions)
            if (now - latest_mtime) > _STALE_SECONDS:
                continue
        rv = parse_run_sessions(agent_dir, run_dir, harness=harness_override)
        if rv.sessions:
            out.append(rv)
    return out


def _load_session_views(rvs: list[RunSessionsView],
                        session_n: int | None) -> list[tuple[Path, SessionView]]:
    """Pick one SessionView per agent for session-scoped commands.

    `session_n` is 1-based (matching session_N_... filenames). When None, picks
    the latest session in each run. Out-of-range falls back silently to the
    latest available session.
    """
    out: list[tuple[Path, SessionView]] = []
    for rv in rvs:
        if not rv.sessions:
            continue
        if session_n is None:
            sv = rv.sessions[-1]
        else:
            # Match by session_N_... filename token, not list index.
            picked: SessionView | None = None
            for sv_candidate in rv.sessions:
                m = sv_candidate.log_path.name.split("_", 2)
                if len(m) >= 2:
                    try:
                        if int(m[1]) == session_n:
                            picked = sv_candidate
                            break
                    except ValueError:
                        pass
            sv = picked or rv.sessions[-1]
        out.append((rv.agent_dir, sv))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("quests")
    sub.add_parser("tools")
    pr = sub.add_parser("recent"); pr.add_argument("-n", type=int, default=8)
    pe = sub.add_parser("errors")
    pe.add_argument("--by-quest", action="store_true",
                    help="slice errors by which Core 3 quest was active at the time")
    pt = sub.add_parser("thinking"); pt.add_argument("-n", type=int, default=3)
    pa = sub.add_parser("agent"); pa.add_argument("agent_id", type=int); pa.add_argument("-n", type=int, default=10)
    sub.add_parser("full")
    sub.add_parser("metrics")
    pq = sub.add_parser("quest")
    pq.add_argument("name", nargs="?", default=None,
                    help="quest name (or substring). Defaults to all Core 3.")
    pq.add_argument("--cross-run", action="store_true",
                    help="show max-stage histogram across every run for each agent")
    pn = sub.add_parser("runs"); pn.add_argument("-n", type=int, default=10)
    pl = sub.add_parser("timeline"); pl.add_argument("-n", type=int, default=30)
    p.add_argument("--stale", action="store_true",
                   help="include runs whose latest session log is >10 min old "
                        "(default: only currently-running agents)")
    p.add_argument("--run", dest="run_id", default=None,
                   help="scope to a specific run_id (e.g. run_20260427_135613). "
                        "Without this, looks at the latest run per agent.")
    p.add_argument("--session", dest="session_n", type=int, default=None,
                   help="drill down to one session inside the resolved run "
                        "(1-based, matches session_N_... filename). Default: "
                        "latest session for session-scoped commands.")
    p.add_argument("--all-runs", action="store_true",
                   help="for `runs` cmd: show every run, not just the recent N")
    p.add_argument("--opencode", action="store_const", const="opencode", dest="harness",
                   help="force the OpenCode log parser (default: auto-detect from each session's meta.json)")
    p.add_argument("--claude", action="store_const", const="claude", dest="harness",
                   help="force the Claude log parser (default: auto-detect from each session's meta.json)")
    args = p.parse_args()

    cmd = args.cmd or "full"

    # `runs` lists run dirs directly; doesn't need session views.
    if cmd == "runs":
        cmd_runs(None, n=args.n, all_runs=args.all_runs)
        return 0

    # `quest --cross-run` walks every run for every agent — bypasses the
    # current-run loader since the output is multi-run by definition.
    if cmd == "quest" and getattr(args, "cross_run", False):
        cmd_quest([], quest_arg=args.name, cross_run=True, harness_override=args.harness)
        return 0

    # Single-agent deep dive.
    if cmd == "agent":
        rvs = _load_run_views(only_agent=args.agent_id, harness_override=args.harness,
                              run_id=args.run_id, include_stale=True)
        if not rvs:
            print(f"No run found for agent_{args.agent_id}", file=sys.stderr)
            return 1
        cmd_agent_run(rvs[0], n_recent=args.n)
        return 0

    rvs = _load_run_views(include_stale=args.stale, harness_override=args.harness,
                          run_id=args.run_id)
    if not rvs:
        msg = (f"No agent runs found for run {args.run_id}." if args.run_id
               else "No active agent runs in the last 10 minutes. "
                    "Pass --stale to include older runs.")
        print(msg, file=sys.stderr)
        return 1

    # Header — show run summary per agent
    print(f"# Log analysis  ({dt.datetime.now().strftime('%Y-%m-%d %I:%M %p')})")
    for rv in rvs:
        sessions_str = f"{rv.n_sessions} session{'s' if rv.n_sessions != 1 else ''}"
        cost_str = _fmt_cost(rv.total_cost_usd)
        latest_age = _age(rv.sessions[-1].log_path) if rv.sessions else "?"
        print(f"  agent_{_agent_id_from_dir(rv.agent_dir)}: run={rv.run_id}  "
              f"{sessions_str}, {rv.total_turns} turns, {rv.total_tool_calls} tool calls, "
              f"{cost_str}  (latest session: {latest_age})")

    if cmd == "status":   cmd_status(rvs)
    elif cmd == "quests": cmd_quests(rvs)
    elif cmd == "tools":  cmd_tools(rvs)
    elif cmd == "errors": cmd_errors(rvs, by_quest=getattr(args, "by_quest", False))
    elif cmd == "metrics": cmd_metrics(rvs)
    elif cmd == "timeline": cmd_timeline(rvs, n=args.n)
    elif cmd == "quest":   cmd_quest(rvs, quest_arg=args.name, cross_run=False)
    elif cmd == "full":   cmd_full(rvs)
    elif cmd == "recent":
        svs = _load_session_views(rvs, args.session_n)
        cmd_recent(svs, n=args.n)
    elif cmd == "thinking":
        svs = _load_session_views(rvs, args.session_n)
        cmd_thinking(svs, n=args.n)
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
