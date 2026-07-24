#!/usr/bin/env python3
"""Inventory the raw run bundles needed by the paper's historical claims."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.log_analysis.artifact_requirements import missing_agent_run_logs


AGENTS = ("agent_0", "agent_1", "agent_2")
CLAIM_RUNS = {
    "r10_base": (
        "run_20260510_173852", "run_20260510_211339",
        "run_20260519_223921", "run_20260520_143530",
    ),
    "r10_sft": (
        "run_20260520_014319", "run_20260520_044433",
        "run_20260520_173902",
    ),
    "opd_2b": (
        "run_20260608_185339", "run_20260610_140358",
        "run_20260612_044933", "run_20260613_112422",
        "run_20260613_214956",
    ),
    "teacher_and_capacity_references": (
        "run_20260606_205254", "run_20260607_150306",
        "run_20260607_190204",
    ),
}


def build_inventory(raw_root: Path) -> dict:
    groups = {}
    for name, run_ids in CLAIM_RUNS.items():
        missing = missing_agent_run_logs(raw_root, agents=AGENTS, run_ids=run_ids)
        expected = len(AGENTS) * len(run_ids)
        groups[name] = {
            "run_ids": list(run_ids),
            "expected_agent_run_bundles": expected,
            "present_agent_run_bundles": expected - len(missing),
            "complete": not missing,
            "missing": missing,
        }
    complete = all(group["complete"] for group in groups.values())
    return {
        "schema_version": "kaetram-historical-artifact-inventory-v1",
        "raw_root": str(raw_root),
        "complete": complete,
        "groups": groups,
        "warning": (
            "A complete path inventory establishes availability, not authenticity. "
            "Cryptographic manifests are still required for evidentiary use."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=Path("dataset/raw"))
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--report-only", action="store_true",
        help="return success even when bundles are missing",
    )
    args = parser.parse_args()
    report = build_inventory(args.raw_root)
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
