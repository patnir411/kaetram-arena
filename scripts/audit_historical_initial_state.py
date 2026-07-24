#!/usr/bin/env python3
"""Verify that historical headline runs began from the canonical clean state.

This is an artifact-level check, independent of MongoDB availability.  It
requires the first recorded model-visible tool call in every agent/run bundle
to be a successful ``observe`` and compares its persistent player state with
the canonical post-tutorial benchmark start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.audit_historical_artifacts import AGENTS, CLAIM_RUNS
from scripts.log_analysis.parse import parse_run_sessions
from canonical_start import (  # noqa: E402
    CANONICAL_INITIAL_STATE,
    initial_state_projection,
    state_mismatches,
)


HEADLINE_GROUPS = ("r10_base", "r10_sft", "opd_2b")
def build_initial_state_audit(
    raw_root: Path,
    *,
    groups: Iterable[str] = HEADLINE_GROUPS,
    claim_runs: Mapping[str, Iterable[str]] = CLAIM_RUNS,
    agents: Iterable[str] = AGENTS,
    source_manifest: Path | None = None,
) -> dict:
    """Audit the first model-visible action and state in each requested bundle."""
    selected_groups = tuple(groups)
    unknown = sorted(set(selected_groups) - set(claim_runs))
    if unknown:
        raise ValueError(f"unknown claim group(s): {', '.join(unknown)}")

    report_groups: dict[str, dict] = {}
    for group in selected_groups:
        run_ids = tuple(claim_runs[group])
        bundles: list[dict] = []
        for run_id in run_ids:
            for agent in agents:
                run_dir = raw_root / agent / "runs" / run_id
                record = {
                    "agent": agent,
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "clean": False,
                    "mismatches": [],
                }
                if not run_dir.is_dir():
                    record["error"] = "missing_run_directory"
                    bundles.append(record)
                    continue

                view = parse_run_sessions(raw_root / agent, run_dir)
                if not view.all_tool_calls:
                    record["error"] = "no_recorded_tool_calls"
                    bundles.append(record)
                    continue

                first = view.all_tool_calls[0]
                record["first_tool"] = first.short_name
                if first.short_name != "observe":
                    record["error"] = "first_tool_is_not_observe"
                    bundles.append(record)
                    continue
                if not isinstance(first.result_payload, dict):
                    record["error"] = "first_observe_has_no_structured_result"
                    bundles.append(record)
                    continue

                projection = initial_state_projection(first.result_payload)
                record["observed"] = projection
                record["mismatches"] = state_mismatches(projection)
                record["clean"] = not record["mismatches"]
                bundles.append(record)

        clean_count = sum(bundle["clean"] for bundle in bundles)
        report_groups[group] = {
            "run_ids": list(run_ids),
            "expected_agent_run_bundles": len(bundles),
            "clean_agent_run_bundles": clean_count,
            "complete": clean_count == len(bundles),
            "anomalies": [bundle for bundle in bundles if not bundle["clean"]],
        }

    report = {
        "schema_version": "kaetram-historical-initial-state-audit-v1",
        "raw_root": str(raw_root),
        "canonical_initial_state": CANONICAL_INITIAL_STATE,
        "groups": report_groups,
        "complete": all(group["complete"] for group in report_groups.values()),
        "interpretation": (
            "This verifies the first model-visible action and player-state snapshot "
            "in each recovered bundle. It does not by itself prove the database "
            "reset command that produced that state."
        ),
    }
    if source_manifest is not None:
        digest = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
        report["source_manifest"] = {
            "name": source_manifest.name,
            "sha256": digest,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=Path("dataset/raw"))
    parser.add_argument(
        "--groups",
        nargs="+",
        default=list(HEADLINE_GROUPS),
        choices=sorted(CLAIM_RUNS),
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        help="optional immutable source inventory to bind into the report",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="return success even when a bundle is missing or non-canonical",
    )
    args = parser.parse_args(argv)

    report = build_initial_state_audit(
        args.raw_root,
        groups=args.groups,
        source_manifest=args.source_manifest,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(args.out)
    else:
        print(rendered, end="")
    return 0 if report["complete"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
