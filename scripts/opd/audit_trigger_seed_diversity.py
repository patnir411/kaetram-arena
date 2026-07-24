#!/usr/bin/env python3
"""Audit whether registered request seeds changed semantic model responses."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.audit_trigger_incidence_artifact import (
    AuditError,
    audit_artifact,
    load_object,
    sha256_json,
)


SCHEMA = "kaetram.local-trigger-incidence-seed-diversity-audit.v1"


def semantic_response_sha256(message: Any) -> str:
    """Hash model-visible response semantics while dropping generated call IDs."""
    if not isinstance(message, dict):
        raise AuditError("successful row lacks a response object")
    normalized = copy.deepcopy(message)
    tool_calls = normalized.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                call.pop("id", None)
    return sha256_json(normalized)


def _load_rows(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise AuditError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


def summarize_seed_diversity(registration: dict, rows: list[dict]) -> dict:
    snapshots = tuple(registration["snapshots"])
    conditions = tuple(
        condition["condition_id"] for condition in registration["conditions"]
    )
    state_count = int(registration["state_pool"]["state_count"])
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    base_seed = int(registration["sampling"]["base_seed"])
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            raise AuditError("seed-diversity audit requires a complete successful grid")
        key = (
            row.get("snapshot"),
            row.get("condition_id"),
            row.get("state_id"),
        )
        groups[key].append(row)

    expected_keys = {
        (snapshot, condition, f"state-{state_index + 1:02d}")
        for snapshot in snapshots
        for condition in conditions
        for state_index in range(state_count)
    }
    if set(groups) != expected_keys:
        raise AuditError("seed-diversity groups do not match the registered grid")

    group_records = []
    for key in sorted(groups):
        snapshot, condition, state_id = key
        state_index = int(state_id.removeprefix("state-")) - 1
        members = groups[key]
        if len(members) != sample_count:
            raise AuditError(f"{key}: unexpected registered sample count")
        by_sample = {row.get("sample_index"): row for row in members}
        if set(by_sample) != set(range(sample_count)):
            raise AuditError(f"{key}: duplicate or missing sample index")
        expected_seeds = {
            base_seed + 100 * state_index + sample_index
            for sample_index in range(sample_count)
        }
        if {row.get("seed") for row in members} != expected_seeds:
            raise AuditError(f"{key}: request seeds do not match the registration")
        semantic_hashes = {
            semantic_response_sha256(row.get("response_message")) for row in members
        }
        opportunity_values = {
            bool(row.get("recovery_opportunity")) for row in members
        }
        group_records.append(
            {
                "snapshot": snapshot,
                "condition_id": condition,
                "state_id": state_id,
                "registered_samples": sample_count,
                "unique_request_seeds": len(expected_seeds),
                "unique_semantic_responses": len(semantic_hashes),
                "outcome_values": len(opportunity_values),
                "recovery_opportunity": (
                    next(iter(opportunity_values))
                    if len(opportunity_values) == 1
                    else None
                ),
            }
        )

    collapsed_cells = []
    for snapshot in snapshots:
        for condition in conditions:
            subset = [
                record
                for record in group_records
                if record["snapshot"] == snapshot
                and record["condition_id"] == condition
            ]
            if len(subset) != state_count:
                raise AuditError("collapsed cell does not contain every registered state")
            stable = [record for record in subset if record["outcome_values"] == 1]
            collapsed_cells.append(
                {
                    "snapshot": snapshot,
                    "condition_id": condition,
                    "state_outputs": len(subset),
                    "outcome_stable_states": len(stable),
                    "recovery_opportunity_states": sum(
                        bool(record["recovery_opportunity"]) for record in stable
                    ),
                    "opportunity_rate": (
                        sum(
                            bool(record["recovery_opportunity"])
                            for record in stable
                        )
                        / len(stable)
                        if stable
                        else None
                    ),
                }
            )

    return {
        "registered_samples_per_state_condition": sample_count,
        "state_condition_groups": len(group_records),
        "groups_with_identical_semantic_responses": sum(
            record["unique_semantic_responses"] == 1 for record in group_records
        ),
        "groups_with_multiple_semantic_responses": sum(
            record["unique_semantic_responses"] > 1 for record in group_records
        ),
        "groups_with_stable_recovery_outcome": sum(
            record["outcome_values"] == 1 for record in group_records
        ),
        "semantic_response_count_after_within_group_deduplication": sum(
            record["unique_semantic_responses"] for record in group_records
        ),
        "collapsed_cells": collapsed_cells,
    }


def audit_seed_diversity(root: Path) -> dict:
    verified = audit_artifact(root)
    registration = load_object(root / "registration.json")
    rows = []
    for snapshot in registration["snapshots"]:
        rows.extend(_load_rows(root / "runs" / snapshot / "results.jsonl"))
    summary = summarize_seed_diversity(registration, rows)
    analysis = load_object(root / "analysis" / "analysis-summary.json")
    registered_cells = {
        (cell["snapshot"], cell["condition_id"]): cell
        for cell in analysis["cells"]
    }
    for cell in summary["collapsed_cells"]:
        registered = registered_cells[(cell["snapshot"], cell["condition_id"])]
        if cell["opportunity_rate"] != registered["opportunity_rate"]:
            raise AuditError("collapsed state rate differs from registered request rate")
    return {
        "schema_version": SCHEMA,
        "study_id": registration["study_id"],
        "artifact_index_sha256": verified["artifact_index_sha256"],
        **summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = audit_seed_diversity(args.artifact_dir)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is None:
        print(payload, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
