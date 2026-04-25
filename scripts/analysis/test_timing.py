#!/usr/bin/env python3
"""Per-test timing summarizer.

Reads reachability test JSONL traces (one file per test, written by
`tests/e2e/quests/reachability/debug.py`) and prints a breakdown of where
the wall-clock time went: phases, tools, idle gaps.

Usage:
  scripts/analysis/test_timing.py                     # all logs in default slot
  scripts/analysis/test_timing.py niral               # specific slot
  scripts/analysis/test_timing.py path/to/file.jsonl  # specific file
  scripts/analysis/test_timing.py --tools             # also break down tool time
  scripts/analysis/test_timing.py --md                # markdown table for docs

JSONL events used:
  - test_start / test_end          — wall-clock total
  - phase_start / phase_end        — explicit phase markers (gather, craft, ...)
  - action {tool, args, ...}       — every MCP tool call
  - All other events have a `t` field (seconds since test_start) so we can
    compute durations from event-to-next-event when phase_end is absent.

NOTE on tool timings: actions are emitted AFTER the tool call returns, so
the per-tool wall time is approximated as `t_action - t_previous_event`
(the gap since the last visible event). This INCLUDES any test-side sleep
inside a polling loop — e.g. `observe` averages ~2-3s per call here even
though `observe` itself is ~50ms because tests poll observe with a 1-2s
delay between calls. Treat the tool table as "time the test code spent
attributable to this tool" rather than pure tool latency.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_DIR = Path(__file__).resolve().parents[2]
# debug.py writes to ../sandbox/<slot>/reachability_logs/ (workspace-level
# sandbox, one level above this repo). Mirror that lookup.
WORKSPACE_DIR = PROJECT_DIR.parent


@dataclass
class TestTiming:
    name: str
    status: str = "?"
    elapsed_s: float = 0.0
    phase_durations: dict[str, float] = None  # type: ignore[assignment]
    tool_counts: dict[str, int] = None  # type: ignore[assignment]
    tool_total_s: dict[str, float] = None  # type: ignore[assignment]
    log_path: Path | None = None

    def __post_init__(self):
        if self.phase_durations is None:
            self.phase_durations = {}
        if self.tool_counts is None:
            self.tool_counts = {}
        if self.tool_total_s is None:
            self.tool_total_s = {}


def _load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        with path.open() as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return events


def _analyze(path: Path) -> TestTiming:
    events = _load_events(path)
    timing = TestTiming(name=path.stem, log_path=path)
    if not events:
        return timing

    # Look for test_end first for the authoritative summary.
    for e in events:
        if e.get("event") == "test_end":
            timing.status = e.get("status", "?")
            timing.elapsed_s = float(e.get("elapsed_s", 0.0) or 0.0)
            for tool, count in (e.get("tool_calls") or {}).items():
                timing.tool_counts[tool] = int(count)

    # Phase breakdown — pair phase_start with matching phase_end (by phase name).
    open_phases: dict[str, float] = {}
    last_action_t: dict[str, float] = {}
    for e in events:
        kind = e.get("event")
        t = float(e.get("t", 0.0) or 0.0)
        if kind == "phase_start":
            open_phases[str(e.get("phase"))] = t
        elif kind == "phase_end":
            phase = str(e.get("phase"))
            elapsed = e.get("elapsed_s")
            if elapsed is None and phase in open_phases:
                elapsed = t - open_phases.pop(phase)
            elif phase in open_phases:
                open_phases.pop(phase, None)
            if elapsed is not None:
                timing.phase_durations[phase] = (
                    timing.phase_durations.get(phase, 0.0) + float(elapsed)
                )
        elif kind == "action":
            tool = str(e.get("tool", "?"))
            # Track time to next event as a rough cost estimate per tool call.
            last_action_t[tool] = t
            timing.tool_counts.setdefault(tool, 0)

    # Phase that was started but never ended — assume it ran until test_end.
    for phase, t0 in open_phases.items():
        timing.phase_durations[phase] = (
            timing.phase_durations.get(phase, 0.0) + max(0.0, timing.elapsed_s - t0)
        )

    # Tool wall-time approximation: actions are logged AFTER the tool call
    # returns, so the call duration is t_action - t_previous_event (the gap
    # since the last visible thing happened).
    sorted_events = [(float(e.get("t", 0.0) or 0.0), e) for e in events]
    sorted_events.sort(key=lambda p: p[0])
    for i, (t, e) in enumerate(sorted_events):
        if e.get("event") != "action":
            continue
        tool = str(e.get("tool", "?"))
        prev_t = sorted_events[i - 1][0] if i > 0 else 0.0
        timing.tool_total_s[tool] = (
            timing.tool_total_s.get(tool, 0.0) + max(0.0, t - prev_t)
        )

    return timing


def _resolve_inputs(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    if not args:
        slot = os.environ.get("KAETRAM_SLOT", "niral")
        d = WORKSPACE_DIR / "sandbox" / slot / "reachability_logs"
        if d.is_dir():
            paths = sorted(d.glob("*.jsonl"))
        return paths
    for arg in args:
        p = Path(arg)
        if p.is_file():
            paths.append(p)
        elif p.is_dir():
            paths.extend(sorted(p.glob("*.jsonl")))
        else:
            # treat as slot name
            d = WORKSPACE_DIR / "sandbox" / arg / "reachability_logs"
            if d.is_dir():
                paths.extend(sorted(d.glob("*.jsonl")))
    return paths


def _fmt(seconds: float) -> str:
    return f"{seconds:.1f}s"


def _print_text(timings: Iterable[TestTiming], show_tools: bool) -> None:
    timings = sorted(timings, key=lambda t: -t.elapsed_s)
    total = sum(t.elapsed_s for t in timings)
    name_w = max((len(t.name) for t in timings), default=10)
    print(f"\n{'TEST':<{name_w}}  {'STATUS':<6}  {'TOTAL':>7}  PHASES")
    print("-" * (name_w + 30))
    for t in timings:
        phase_str = ", ".join(
            f"{p}={_fmt(d)}"
            for p, d in sorted(t.phase_durations.items(), key=lambda kv: -kv[1])[:5]
        ) or "—"
        print(f"{t.name:<{name_w}}  {t.status:<6}  {_fmt(t.elapsed_s):>7}  {phase_str}")
    print("-" * (name_w + 30))
    print(f"{'TOTAL':<{name_w}}  {'':<6}  {_fmt(total):>7}")

    if show_tools:
        agg_count: dict[str, int] = defaultdict(int)
        agg_time: dict[str, float] = defaultdict(float)
        for t in timings:
            for tool, count in t.tool_counts.items():
                agg_count[tool] += count
                agg_time[tool] += t.tool_total_s.get(tool, 0.0)
        print(f"\n{'TOOL':<14}  {'CALLS':>6}  {'TOTAL TIME':>11}  {'AVG':>7}")
        print("-" * 46)
        rows = sorted(agg_count.items(), key=lambda kv: -agg_time[kv[0]])
        for tool, count in rows:
            tot = agg_time[tool]
            avg = tot / count if count else 0.0
            print(f"{tool:<14}  {count:>6}  {_fmt(tot):>11}  {_fmt(avg):>7}")


def _print_md(timings: Iterable[TestTiming]) -> None:
    timings = sorted(timings, key=lambda t: -t.elapsed_s)
    print("\n| Test | Status | Total | Top phase |")
    print("|---|---|---|---|")
    for t in timings:
        top_phase = max(t.phase_durations.items(), key=lambda kv: kv[1], default=("—", 0.0))
        phase_str = f"`{top_phase[0]}` {_fmt(top_phase[1])}" if top_phase[1] else "—"
        print(f"| `{t.name}` | {t.status} | {_fmt(t.elapsed_s)} | {phase_str} |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="JSONL files, directories, or slot names")
    ap.add_argument("--tools", action="store_true", help="also break down per-tool wall time")
    ap.add_argument("--md", action="store_true", help="markdown table output")
    args = ap.parse_args()

    paths = _resolve_inputs(args.inputs)
    if not paths:
        print("no JSONL traces found", file=sys.stderr)
        return 1
    timings = [_analyze(p) for p in paths]

    if args.md:
        _print_md(timings)
    else:
        _print_text(timings, args.tools)
    return 0


if __name__ == "__main__":
    sys.exit(main())
