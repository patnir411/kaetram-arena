#!/usr/bin/env python3
"""Independently audit a published trigger-incidence artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


PUBLIC_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v1"
REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-registration.v1"
ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
CALL_IN_TAG = re.compile(r"<function=([A-Za-z_]\w*)\(([^>\n]*)\)\s*>")
CANONICAL_FUNCTION = re.compile(
    r"<function=([A-Za-z_]\w*)>\n?(.*?)</function>",
    re.DOTALL,
)
MALFORMED = re.compile(
    r"<parameter=[^>\n]*=[^>\n]*>|<function=[A-Za-z_]\w*\("
)
OUTCOME_FIELDS = {
    "response_message",
    "structured_tool_call_count",
    "has_structured_tool_call",
    "no_structured_tool_call",
    "has_content",
    "malformed_emission",
    "malformed_families",
    "recovery_opportunity",
    "recoverable_calls",
}
TOOL_PARAMETER_ORDER = {
    item["function"]["name"]: tuple(
        item["function"].get("parameters", {}).get("properties", {})
    )
    for item in MODEL_VISIBLE_TOOL_DEFINITIONS
}


class AuditError(RuntimeError):
    """Raised when raw rows do not independently reproduce the release result."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise AuditError(f"expected regular JSON file: {path}")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be an object: {path}")
    return value


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise AuditError("artifact path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AuditError(f"unsafe artifact path: {value!r}")
    if pure.as_posix() != value:
        raise AuditError(f"non-canonical artifact path: {value!r}")
    return Path(*pure.parts)


def verify_outer_inventory(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise AuditError("artifact root must be a regular directory")
    index = load_object(root / "artifact-index.json")
    if index.get("schema_version") != PUBLIC_SCHEMA:
        raise AuditError("unexpected public artifact schema")
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise AuditError("public artifact has no file inventory")
    seen: set[str] = set()
    normalized = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise AuditError("invalid public file record")
        relative = _safe_relative_path(record["path"])
        text = relative.as_posix()
        if text == "artifact-index.json" or text in seen:
            raise AuditError(f"duplicate public file record: {text}")
        seen.add(text)
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise AuditError(f"public file is missing or linked: {text}")
        size = record["size_bytes"]
        digest = record["sha256"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or path.stat().st_size != size
            or sha256_file(path) != digest
        ):
            raise AuditError(f"public file digest mismatch: {text}")
        normalized.append(
            {"path": text, "size_bytes": size, "sha256": digest}
        )
    if [item["path"] for item in normalized] != sorted(seen):
        raise AuditError("public file inventory is not ordered")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != {*seen, "artifact-index.json"}:
        raise AuditError("public artifact contains missing or unindexed files")
    if index.get("tree_sha256") != sha256_json(normalized):
        raise AuditError("public artifact tree digest mismatch")
    return index


def _split_arguments(text: str) -> list[str] | None:
    values: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote: str | None = None
    for character in text:
        if quote:
            buffer.append(character)
            if character == quote:
                quote = None
            continue
        if character in "\"'":
            quote = character
            buffer.append(character)
        elif character in "([{":
            depth += 1
            buffer.append(character)
        elif character in ")]}":
            depth -= 1
            if depth < 0:
                return None
            buffer.append(character)
        elif character == "," and depth == 0:
            values.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
    if quote is not None or depth != 0:
        return None
    tail = "".join(buffer).strip()
    if tail:
        values.append(tail)
    return values


def independently_recoverable(content: str) -> bool:
    if not content:
        return False
    for match in CALL_IN_TAG.finditer(content):
        name, arguments = match.group(1), match.group(2).strip()
        order = TOOL_PARAMETER_ORDER.get(name)
        if order is None:
            continue
        parsed = _split_arguments(arguments)
        if parsed is None:
            continue
        positional_count = sum(
            re.match(r"^[A-Za-z_]\w*\s*=", value, re.DOTALL) is None
            for value in parsed
        )
        if positional_count <= len(order):
            return True
    for match in CANONICAL_FUNCTION.finditer(content):
        if match.group(1) in TOOL_PARAMETER_ORDER:
            return True
    return False


def classify_message(message: Any) -> dict:
    if not isinstance(message, dict):
        raise AuditError("successful row lacks a response object")
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = ""
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    structured = bool(tool_calls)
    return {
        "structured_tool_call_count": len(tool_calls),
        "has_structured_tool_call": structured,
        "no_structured_tool_call": not structured,
        "has_content": bool(content),
        "malformed_emission": bool(MALFORMED.search(content)),
        "recovery_opportunity": (
            independently_recoverable(content) if not structured else False
        ),
    }


def _load_rows(path: Path) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise AuditError(f"expected regular JSONL file: {path}")
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


def _expected_schedule(registration: dict, design: dict) -> dict[tuple, dict]:
    conditions = registration["conditions"]
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    base_seed = int(registration["sampling"]["base_seed"])
    expected: dict[tuple, dict] = {}
    for snapshot in registration["snapshots"]:
        schedule_index = 0
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(sample_count):
                block_index = state_index * sample_count + sample_index
                offset = block_index % len(conditions)
                for condition in conditions[offset:] + conditions[:offset]:
                    key = (
                        snapshot,
                        condition["condition_id"],
                        state["state_id"],
                        sample_index,
                    )
                    expected[key] = {
                        "snapshot": snapshot,
                        "schedule_index": schedule_index,
                        "state_id": state["state_id"],
                        "state_index": state_index,
                        "sample_index": sample_index,
                        "seed": base_seed + 100 * state_index + sample_index,
                        "condition_id": condition["condition_id"],
                        "documentation": condition["documentation"],
                        "native_tool_schema": condition["native_tool_schema"],
                    }
                    schedule_index += 1
    return expected


def _load_and_check_rows(root: Path, registration: dict, design: dict) -> dict:
    expected = _expected_schedule(registration, design)
    observed: dict[tuple, dict] = {}
    for snapshot in registration["snapshots"]:
        run_dir = root / "runs" / snapshot
        prelaunch = load_object(run_dir / "prelaunch.json")
        postflight = load_object(run_dir / "postflight.json")
        completed = load_object(run_dir / "completed.json")
        if (
            prelaunch.get("snapshot") != snapshot
            or postflight.get("snapshot") != snapshot
            or completed.get("snapshot") != snapshot
            or not completed.get("endpoint_identity_stable")
            or not postflight.get("endpoint_identity_stable")
        ):
            raise AuditError(f"{snapshot}: endpoint/run identity is not stable")
        for row in _load_rows(run_dir / "results.jsonl"):
            key = (
                row.get("snapshot"),
                row.get("condition_id"),
                row.get("state_id"),
                row.get("sample_index"),
            )
            if key in observed or key not in expected:
                raise AuditError(f"unexpected or duplicate scheduled row: {key}")
            if any(row.get(name) != value for name, value in expected[key].items()):
                raise AuditError(f"scheduled metadata mismatch: {key}")
            status = row.get("status")
            if status == "ok":
                recomputed = classify_message(row.get("response_message"))
                if any(row.get(name) != value for name, value in recomputed.items()):
                    raise AuditError(f"stored outcome mismatch: {key}")
            elif status == "failed":
                if OUTCOME_FIELDS.intersection(row):
                    raise AuditError(f"failed row carries outcome data: {key}")
            else:
                raise AuditError(f"unknown row status: {key}")
            observed[key] = row
    if set(observed) != set(expected):
        raise AuditError("raw rows do not cover the registered schedule")
    return observed


def recompute_summary(registration: dict, design: dict, rows: dict) -> dict:
    state_ids = [state["state_id"] for state in design["states"]]
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    cells = []
    for snapshot in registration["snapshots"]:
        for condition in registration["conditions"]:
            subset = [
                rows[(snapshot, condition["condition_id"], state_id, sample_index)]
                for state_id in state_ids
                for sample_index in range(sample_count)
            ]
            successful = [row for row in subset if row["status"] == "ok"]
            opportunities = sum(
                bool(row.get("recovery_opportunity")) for row in successful
            )
            cells.append(
                {
                    "snapshot": snapshot,
                    "condition_id": condition["condition_id"],
                    "documentation": condition["documentation"],
                    "native_tool_schema": condition["native_tool_schema"],
                    "requests": len(subset),
                    "successful_requests": len(successful),
                    "failures": len(subset) - len(successful),
                    "recovery_opportunities": opportunities,
                    "opportunity_rate": (
                        opportunities / len(successful) if successful else None
                    ),
                    "malformed_emissions": sum(
                        bool(row.get("malformed_emission")) for row in successful
                    ),
                    "structured_tool_responses": sum(
                        bool(row.get("has_structured_tool_call")) for row in successful
                    ),
                    "no_structured_tool_call_responses": sum(
                        bool(row.get("no_structured_tool_call")) for row in successful
                    ),
                }
            )
    complete = all(row["status"] == "ok" for row in rows.values())
    contrasts = []
    if complete:
        condition_lookup = {
            (item["documentation"], item["native_tool_schema"]): item["condition_id"]
            for item in registration["conditions"]
        }
        for snapshot in registration["snapshots"]:
            numerators = {
                "native_tools_main": [],
                "canonical_docs_main": [],
                "interaction": [],
            }
            for state_id in state_ids:
                counts = {}
                for docs in ("python_docs", "canonical_docs"):
                    for tools in ("absent", "present"):
                        condition = condition_lookup[(docs, tools)]
                        counts[(docs, tools)] = sum(
                            bool(
                                rows[
                                    (snapshot, condition, state_id, sample_index)
                                ].get("recovery_opportunity")
                            )
                            for sample_index in range(sample_count)
                        )
                numerators["native_tools_main"].append(
                    counts[("python_docs", "present")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("canonical_docs", "absent")]
                )
                numerators["canonical_docs_main"].append(
                    counts[("canonical_docs", "absent")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("python_docs", "present")]
                )
                numerators["interaction"].append(
                    counts[("canonical_docs", "present")]
                    - counts[("canonical_docs", "absent")]
                    - counts[("python_docs", "present")]
                    + counts[("python_docs", "absent")]
                )
            denominators = {
                "native_tools_main": 2 * sample_count,
                "canonical_docs_main": 2 * sample_count,
                "interaction": sample_count,
            }
            for name in (
                "native_tools_main",
                "canonical_docs_main",
                "interaction",
            ):
                values = numerators[name]
                denominator = denominators[name]
                effects = [value / denominator for value in values]
                contrasts.append(
                    {
                        "snapshot": snapshot,
                        "contrast": name,
                        "finite_grid_states": len(state_ids),
                        "effect_rate_difference": (
                            sum(values) / (denominator * len(state_ids))
                        ),
                        "states_positive": sum(value > 0 for value in effects),
                        "states_negative": sum(value < 0 for value in effects),
                        "states_zero": sum(value == 0 for value in effects),
                        "state_effect_min": min(effects),
                        "state_effect_max": max(effects),
                    }
                )
    successes = sum(row["status"] == "ok" for row in rows.values())
    return {
        "analysis_status": "complete" if complete else "incomplete",
        "scheduled_requests": len(rows),
        "successful_requests": successes,
        "failed_requests": len(rows) - successes,
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity"))
            for row in rows.values()
            if row["status"] == "ok"
        ),
        "cells": cells,
        "registered_contrasts": contrasts,
    }


def audit_artifact(root: Path) -> dict:
    outer = verify_outer_inventory(root)
    registration = load_object(root / "registration.json")
    design = load_object(root / "design" / "design.json")
    analysis = load_object(root / "analysis" / "analysis-summary.json")
    if registration.get("schema_version") != REGISTRATION_SCHEMA:
        raise AuditError("unexpected registration schema")
    if analysis.get("schema_version") != ANALYSIS_SCHEMA:
        raise AuditError("unexpected analysis schema")
    rows = _load_and_check_rows(root, registration, design)
    recomputed = recompute_summary(registration, design, rows)
    for field, value in recomputed.items():
        if analysis.get(field) != value:
            raise AuditError(f"independent analysis mismatch: {field}")
    return {
        "schema_version": "kaetram.local-trigger-incidence-independent-audit.v1",
        "study_id": registration["study_id"],
        "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
        "artifact_tree_sha256": outer["tree_sha256"],
        "audit_script_sha256": sha256_file(Path(__file__).resolve()),
        **{
            name: recomputed[name]
            for name in (
                "scheduled_requests",
                "successful_requests",
                "failed_requests",
                "recovery_opportunities",
            )
        },
        "cell_count": len(recomputed["cells"]),
        "contrast_count": len(recomputed["registered_contrasts"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(audit_artifact(args.artifact_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
