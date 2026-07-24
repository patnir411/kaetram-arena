#!/usr/bin/env python3
"""Audit malformed tool emissions and harness recovery from session logs.

This is an offline diagnostic: it never calls a model endpoint or mutates a run.
It reports malformed text emissions, recovered tool calls, execution errors, and
near-term repeat recoveries. The latter is a relapse proxy, not a causal claim.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "log_analysis"))

from parse import parse_session_auto  # noqa: E402


def audit_logs(log_paths: list[Path], relapse_window: int = 5) -> dict:
    sessions = []
    tools: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    totals = Counter()

    for path in sorted(log_paths):
        view = parse_session_auto(path)
        recovered = [
            call for call in view.tool_calls
            if isinstance(call.result_raw, str)
            and call.result_raw.startswith("[format]")
        ]
        repeated = 0
        recovered_indices = {
            call.idx: call.short_name for call in recovered
        }
        for idx, tool in recovered_indices.items():
            if any(
                recovered_indices.get(next_idx) == tool
                for next_idx in range(idx + 1, idx + relapse_window + 1)
            ):
                repeated += 1

        for call in recovered:
            tools[call.short_name] += 1
            if call.is_error:
                error = call.result_error or "unknown_error"
                errors[error[:160]] += 1

        row = {
            "log": str(path),
            "malformed_emissions": view.n_malformed_emit,
            "recovered_calls": len(recovered),
            "recovered_execution_errors": sum(call.is_error for call in recovered),
            "repeat_recoveries_within_window": repeated,
        }
        sessions.append(row)
        totals.update({
            "sessions": 1,
            "malformed_emissions": row["malformed_emissions"],
            "recovered_calls": row["recovered_calls"],
            "recovered_execution_errors": row["recovered_execution_errors"],
            "repeat_recoveries_within_window": repeated,
        })

    recovered_calls = totals["recovered_calls"]
    return {
        "schema_version": "kaetram-recovery-audit-v1",
        "relapse_proxy": {
            "definition": (
                "a recovered call followed by another recovered call to the "
                "same tool within N subsequent tool calls"
            ),
            "window_tool_calls": relapse_window,
            "warning": "This is a repeat-recovery proxy, not proof of causal relapse.",
        },
        "totals": {
            **dict(totals),
            "recovered_execution_success_rate": (
                (recovered_calls - totals["recovered_execution_errors"])
                / recovered_calls if recovered_calls else None
            ),
        },
        "recovered_by_tool": dict(tools.most_common()),
        "recovered_errors": dict(errors.most_common()),
        "sessions": sessions,
    }


def resolve_logs(repo: Path, run_id: str | None, logs: list[str]) -> list[Path]:
    paths = [Path(item).resolve() for item in logs]
    if run_id:
        paths.extend(
            repo.glob(f"dataset/raw/agent_*/runs/{run_id}/session_*.log")
        )
    return sorted({path for path in paths if path.is_file()})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Run under dataset/raw/agent_*/runs/")
    parser.add_argument("--log", action="append", default=[], help="Explicit session log; repeatable")
    parser.add_argument("--relapse-window", type=int, default=5)
    parser.add_argument("--out", type=Path, help="Write JSON report (stdout when omitted)")
    args = parser.parse_args()

    if args.relapse_window < 1:
        parser.error("--relapse-window must be positive")
    paths = resolve_logs(REPO, args.run_id, args.log)
    if not paths:
        parser.error("no session logs found")

    report = audit_logs(paths, args.relapse_window)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered)
        print(args.out)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
