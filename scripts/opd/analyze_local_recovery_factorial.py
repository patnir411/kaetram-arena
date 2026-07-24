#!/usr/bin/env python3
"""Verify and descriptively summarize the local weights × recovery factorial."""
from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _preimport_code_snapshot() -> list[dict]:
    """Hash every tracked Python source before importing analysis dependencies."""
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("could not enumerate tracked analysis source before import")
    records = []
    for raw_relative in result.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = raw_relative.decode("utf-8")
        path = REPO / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"tracked Python source is missing or unsafe: {relative}")
        content = path.read_bytes()
        records.append({
            "path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        })
    if not records:
        raise RuntimeError("tracked Python source inventory is empty")
    return records


PREIMPORT_CODE_SNAPSHOT = _preimport_code_snapshot()


from eval_harness import (  # noqa: E402
    EVAL_RESULTS_SCHEMA_VERSION,
    compute_episode_metrics,
    parse_log,
    validate_eval_session_terminals,
)
from run_manifest import canonical_json_bytes, capture_git_state, sha256_json  # noqa: E402
from scripts.opd.analyze_local_weight_pilot import (  # noqa: E402
    AnalysisError,
    _api_error_count,
    _canonical_start_ok,
    _file_sha256,
    _load_json,
    _ordered_session_logs,
    _validate_cell_attestation,
    _validate_prelaunch,
    _validate_raw_emissions,
    _validate_state_boundaries,
    _verify_artifacts,
)
from scripts.opd.local_weight_pilot import (  # noqa: E402
    INTERMEDIATE_RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    LEGACY_RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    RECOVERY_FACTORIAL_SCHEMA_VERSION,
    RECOVERY_INVENTORY_SCHEMA_VERSION,
    RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    load_manifest,
)
from scripts.opd.recovery_audit import audit_logs  # noqa: E402


WEIGHT_LABEL = {
    "base_2b": "base",
    "opd_r2_2b": "r2",
    "opd_r3_2b": "r3",
}

ANALYSIS_SCHEMA_VERSION = "kaetram.local-weight-recovery-factorial-analysis.v2"
UNBLIND_INTENT_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-unblind-intent.v1"
)
UNBLIND_RECEIPT_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-unblind-receipt.v1"
)

ARM_VALUE_METRICS = (
    "duration_seconds",
    "budget_overrun_seconds",
    "turns",
    "canonical_executed_calls",
    "canonical_executed_calls_per_minute",
    "canonical_tool_bearing_turns",
    "tool_parse_rate",
    "api_errors",
    "sub_sessions",
    "raw_generations",
    "generations_with_structured_call",
    "generations_without_structured_call",
    "structured_call_emission_rate",
    "raw_structured_calls",
    "raw_structured_calls_per_minute",
    "malformed_emissions",
    "recoverable_raw_calls",
    "recovered_calls",
    "recovered_execution_errors",
    "recovered_execution_successes",
    "repeat_recoveries_within_window",
    "core3_stages_advanced",
    "quest_stages_advanced",
    "xp_db_delta",
    "unique_positions",
)


def _validate_recovery_receipts(
    results_root: Path,
    results: dict,
    expected: bool,
    cell_id: str,
    expected_identity: dict,
) -> None:
    meta = results.get("meta")
    if not isinstance(meta, dict) or meta.get("tool_recovery_enabled") is not expected:
        raise AnalysisError(f"{cell_id}: results recovery identity mismatch")
    raw_dir = results_root / "episode_001_raw"
    paths = [raw_dir / "harness_meta_template.json"]
    session_logs = sorted(raw_dir.glob("session_*.log"))
    session_receipts = sorted(raw_dir.glob("session_*.meta.json"))
    expected_receipts = {path.with_suffix(".meta.json") for path in session_logs}
    if not session_logs or set(session_receipts) != expected_receipts:
        raise AnalysisError(f"{cell_id}: no retained session recovery receipts")
    paths.extend(session_receipts)
    for path in paths:
        receipt = _load_json(path)
        required_identity = (
            {
                key: value for key, value in expected_identity.items()
                if key != "tool_schema_source"
            }
            if path.name == "harness_meta_template.json"
            else expected_identity
        )
        mismatches = {
            key: {"expected": value, "actual": receipt.get(key)}
            for key, value in required_identity.items()
            if receipt.get(key) != value
        }
        if receipt.get("tool_recovery_enabled") is not expected:
            mismatches["tool_recovery_enabled"] = {
                "expected": expected,
                "actual": receipt.get("tool_recovery_enabled"),
            }
        if mismatches:
            raise AnalysisError(
                f"{cell_id}: session identity mismatch in "
                f"{path.name} {mismatches}"
            )


def _validate_recovery_accounting(
    session_logs: list[Path],
    raw_metrics: dict,
    canonical_counts: dict,
    recovery_enabled: bool,
) -> dict:
    audit = audit_logs(session_logs)
    if audit.get("schema_version") != "kaetram-recovery-audit-v1":
        raise AnalysisError("recovery audit returned an unknown schema")
    totals = audit.get("totals")
    recovered = audit.get("recovered_by_tool")
    if not isinstance(totals, dict) or not isinstance(recovered, dict):
        raise AnalysisError("recovery audit is malformed")
    audited_sessions = totals.get("sessions")
    malformed = totals.get("malformed_emissions")
    recovered_total = totals.get("recovered_calls")
    recovered_errors = totals.get("recovered_execution_errors")
    repeat_recoveries = totals.get("repeat_recoveries_within_window")
    if not all(type(value) is int and value >= 0 for value in (
        malformed,
        recovered_total,
        recovered_errors,
        repeat_recoveries,
        audited_sessions,
    )):
        raise AnalysisError("recovery audit totals are malformed")
    if audited_sessions != len(session_logs):
        raise AnalysisError("recovery audit session count is inconsistent")
    if recovered_errors > recovered_total:
        raise AnalysisError("recovery execution errors exceed recovered calls")
    if repeat_recoveries > recovered_total:
        raise AnalysisError("repeat recoveries exceed recovered calls")
    if any(not isinstance(name, str) or not name for name in recovered) or any(
        type(value) is not int or value < 0 for value in recovered.values()
    ):
        raise AnalysisError("recovery audit tool counts are malformed")
    if sum(recovered.values()) != recovered_total:
        raise AnalysisError("recovery audit tool counts do not match its total")
    if malformed != raw_metrics["raw_malformed_emissions"]:
        raise AnalysisError(
            "recovery audit malformed count differs from raw endpoint emissions"
        )
    if (
        sum(raw_metrics["raw_recoverable_action_counts"].values())
        != raw_metrics["raw_recoverable_calls"]
    ):
        raise AnalysisError("recoverable raw call accounting is inconsistent")
    if not recovery_enabled and recovered_total:
        raise AnalysisError("recovery-off cell contains recovered calls")
    if recovery_enabled and recovered != raw_metrics["raw_recoverable_action_counts"]:
        raise AnalysisError(
            "recovered calls differ from recoverable raw endpoint emissions"
        )
    expected_counts = Counter(raw_metrics["raw_action_counts"])
    if recovery_enabled:
        expected_counts.update(recovered)
    if dict(expected_counts) != canonical_counts:
        raise AnalysisError(
            "canonical executions differ from raw structured plus recovered calls"
        )
    return {
        "malformed_emissions": raw_metrics["raw_malformed_emissions"],
        "recoverable_raw_calls": raw_metrics["raw_recoverable_calls"],
        "recovered_calls": recovered_total,
        "recovered_execution_errors": recovered_errors,
        "recovered_execution_successes": recovered_total - recovered_errors,
        "repeat_recoveries_within_window": repeat_recoveries,
        "recovered_by_tool": recovered,
    }


def _build_cell_row(
    *,
    cell: dict,
    duration: float,
    duration_budget: int,
    episode: dict,
    recomputed: dict,
    raw_metrics: dict,
    recovery_metrics: dict,
    api_errors: int,
    sub_sessions: int,
) -> dict:
    executed_calls = sum(recomputed["action_counts"].values())
    return {
        "cell_id": cell["cell_id"],
        "replicate": cell["replicate"],
        "weight": WEIGHT_LABEL[cell["snapshot"]],
        "recovery": cell["recovery"],
        "schedule_index": cell["schedule_index"],
        "duration_seconds": duration,
        "budget_overrun_seconds": round(duration - duration_budget, 3),
        "turns": int(episode["turns_played"]),
        "canonical_executed_calls": executed_calls,
        "canonical_executed_calls_per_minute": round(
            executed_calls / (duration / 60), 6
        ),
        "canonical_tool_bearing_turns": int(episode["tool_calls_valid"]),
        "tool_parse_rate": float(episode["tool_parse_rate"]),
        "api_errors": api_errors,
        "sub_sessions": sub_sessions,
        "core3_stages_advanced": int(episode["core3_stages_advanced"]),
        "quest_stages_advanced": int(episode["quest_stages_advanced"]),
        "xp_db_delta": int(episode["xp_db_delta"]),
        "unique_positions": int(episode["unique_positions"]),
        "canonical_action_counts": recomputed["action_counts"],
        "raw_generations": raw_metrics["raw_generations"],
        "generations_with_structured_call": raw_metrics[
            "generations_with_structured_call"
        ],
        "generations_without_structured_call": raw_metrics[
            "generations_without_structured_call"
        ],
        "structured_call_emission_rate": round(
            raw_metrics["generations_with_structured_call"]
            / raw_metrics["raw_generations"],
            6,
        ),
        "raw_structured_calls": raw_metrics["emitted_structured_calls"],
        "raw_structured_calls_per_minute": round(
            raw_metrics["emitted_structured_calls"] / (duration / 60), 6
        ),
        "raw_action_counts": raw_metrics["raw_action_counts"],
        **recovery_metrics,
    }


def _pair_differences(rows: list[dict]) -> dict:
    indexed = {
        (row["replicate"], row["weight"], row["recovery"]): row for row in rows
    }
    pairs = []
    incomplete = []
    for replicate in (1, 2, 3):
        for weight in ("base", "r2", "r3"):
            off = indexed.get((replicate, weight, False))
            on = indexed.get((replicate, weight, True))
            if off is None or on is None:
                incomplete.append({
                    "replicate": replicate,
                    "weight": weight,
                    "missing": [
                        label for label, row in (("off", off), ("on", on))
                        if row is None
                    ],
                })
                continue
            pairs.append({
                "replicate": replicate,
                "weight": weight,
                "pair_order": (
                    "on-first"
                    if on["schedule_index"] < off["schedule_index"]
                    else "off-first"
                ),
                "off_schedule_index": off["schedule_index"],
                "on_schedule_index": on["schedule_index"],
                "on_minus_off": {
                    metric: round(on[metric] - off[metric], 6)
                    for metric in (
                        "canonical_executed_calls",
                        "canonical_executed_calls_per_minute",
                        "raw_structured_calls",
                        "malformed_emissions",
                        "recovered_calls",
                        "core3_stages_advanced",
                        "quest_stages_advanced",
                        "xp_db_delta",
                        "unique_positions",
                    )
                },
            })
    return {"complete_pairs": pairs, "incomplete_pairs": incomplete}


def _summarize(rows: list[dict]) -> dict:
    result = {}
    for weight in ("base", "r2", "r3"):
        for recovery in (False, True):
            group = sorted(
                (
                    row for row in rows
                    if row["weight"] == weight and row["recovery"] is recovery
                ),
                key=lambda row: row["replicate"],
            )
            key = f"{weight}-recovery-{'on' if recovery else 'off'}"
            values = {
                metric: [row[metric] for row in group]
                for metric in ARM_VALUE_METRICS
            }
            means = {
                metric: (
                    round(statistics.mean(metric_values), 6)
                    if metric_values else None
                )
                for metric, metric_values in values.items()
            }
            result[key] = {
                "n_valid": len(group),
                "n_registered": 3,
                "cell_ids": [row["cell_id"] for row in group],
                "replicates": [row["replicate"] for row in group],
                "missing_replicates": sorted(
                    {1, 2, 3} - {row["replicate"] for row in group}
                ),
                "schedule_indices": [row["schedule_index"] for row in group],
                "values": values,
                "means": means,
                "canonical_executed_calls": [
                    row["canonical_executed_calls"] for row in group
                ],
                "mean_canonical_executed_calls_per_minute": round(
                    statistics.mean(
                        row["canonical_executed_calls_per_minute"] for row in group
                    ),
                    6,
                ) if group else None,
                "raw_generations": sum(row["raw_generations"] for row in group),
                "generations_with_structured_call": sum(
                    row["generations_with_structured_call"] for row in group
                ),
                "generations_without_structured_call": sum(
                    row["generations_without_structured_call"] for row in group
                ),
                "pooled_structured_call_emission_rate": round(
                    sum(
                        row["generations_with_structured_call"] for row in group
                    ) / sum(row["raw_generations"] for row in group),
                    6,
                ) if group else None,
                "raw_structured_calls": sum(
                    row["raw_structured_calls"] for row in group
                ),
                "malformed_emissions": sum(
                    row["malformed_emissions"] for row in group
                ),
                "recovered_calls": sum(row["recovered_calls"] for row in group),
                "recovered_execution_successes": sum(
                    row["recovered_execution_successes"] for row in group
                ),
                "api_errors": sum(row["api_errors"] for row in group),
                "zero_turn_cells": sum(row["turns"] == 0 for row in group),
                "core3_stages_advanced": [
                    row["core3_stages_advanced"] for row in group
                ],
                "quest_stages_advanced": [
                    row["quest_stages_advanced"] for row in group
                ],
            }
    return result


def _verify_completed_cell_artifacts(
    cell_root: Path,
    retained: dict,
) -> tuple[str, int]:
    inventory_sha = retained.get("artifact_inventory_sha256")
    if not isinstance(inventory_sha, str) or not inventory_sha:
        raise AnalysisError(
            f"{cell_root.name}: completed cell lacks a sealed artifact inventory"
        )
    files_checked = _verify_artifacts(cell_root, inventory_sha)
    sealed_status = _load_json(cell_root / "cell-status.json")
    completed_status = {
        key: value for key, value in retained.items()
        if key != "artifact_inventory_sha256"
    }
    if sealed_status != completed_status:
        raise AnalysisError(
            f"{cell_root.name}: completed receipt differs from sealed cell status"
        )
    return inventory_sha, files_checked


def _reverify_analysis_inputs(
    *,
    root: Path,
    completed_by_id: dict,
    expected_prelaunch_sha256: str,
    expected_completed_sha256: str,
    expected_files_checked: int,
) -> None:
    if _file_sha256(root / "prelaunch.json") != expected_prelaunch_sha256:
        raise AnalysisError("prelaunch ledger changed during analysis")
    if _file_sha256(root / "completed-inventory.json") != expected_completed_sha256:
        raise AnalysisError("completed inventory changed during analysis")
    files_checked = 0
    for cell_id in sorted(completed_by_id):
        _, rehashed = _verify_completed_cell_artifacts(
            root / cell_id,
            completed_by_id[cell_id],
        )
        files_checked += rehashed
    if files_checked != expected_files_checked:
        raise AnalysisError("artifact inventory cardinality changed during analysis")


def _require_complete_estimands(
    rows: list[dict],
    arm_summary: dict,
    pair_summary: dict,
) -> None:
    """Refuse to release a partial version of the registered descriptive report."""
    if len(rows) != 18:
        raise AnalysisError(
            "registered descriptive estimands require all 18 launcher-valid cells"
        )
    if set(arm_summary) != {
        f"{weight}-recovery-{state}"
        for weight in ("base", "r2", "r3")
        for state in ("off", "on")
    }:
        raise AnalysisError("registered arm family is incomplete")
    if any(
        arm["n_valid"] != 3
        or arm["replicates"] != [1, 2, 3]
        or arm["missing_replicates"]
        for arm in arm_summary.values()
    ):
        raise AnalysisError("every registered arm must contain replicates 1, 2, and 3")
    if (
        len(pair_summary["complete_pairs"]) != 9
        or pair_summary["incomplete_pairs"]
    ):
        raise AnalysisError("all nine registered paired contrasts are required")


def _analysis_code_provenance() -> dict:
    git = capture_git_state(REPO)
    if git["dirty_paths"]:
        raise AnalysisError(
            "analysis must run from a clean Git worktree; dirty paths: "
            + ", ".join(git["dirty_paths"])
        )
    current_files = []
    for record in PREIMPORT_CODE_SNAPSHOT:
        relative = record["path"]
        path = REPO / relative
        if not path.is_file() or path.is_symlink():
            raise AnalysisError(f"analysis source is missing or unsafe: {relative}")
        current_files.append({
            "path": relative,
            "sha256": _file_sha256(path),
            "size_bytes": path.stat().st_size,
        })
    if current_files != PREIMPORT_CODE_SNAPSHOT:
        raise AnalysisError(
            "tracked Python source changed after the pre-import analysis snapshot"
        )
    return {
        "source_git_commit": git["commit"],
        "dirty_paths": [],
        "python_runtime": {
            "implementation": sys.implementation.name,
            "version": ".".join(str(part) for part in sys.version_info[:3]),
            "executable_sha256": _file_sha256(Path(sys.executable)),
        },
        "files": PREIMPORT_CODE_SNAPSHOT,
        "inventory_sha256": sha256_json(PREIMPORT_CODE_SNAPSHOT),
    }


def _csv_text(rows: list[dict], fieldnames: tuple[str, ...]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        cooked = {
            key: (
                json.dumps(value, sort_keys=True, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
            )
            for key, value in row.items()
        }
        writer.writerow(cooked)
    return output.getvalue()


def _paired_rows(report: dict) -> list[dict]:
    rows = []
    for pair in report["paired_differences"]["complete_pairs"]:
        rows.append({
            "replicate": pair["replicate"],
            "weight": pair["weight"],
            "pair_order": pair["pair_order"],
            "off_schedule_index": pair["off_schedule_index"],
            "on_schedule_index": pair["on_schedule_index"],
            **pair["on_minus_off"],
        })
    return rows


def _paper_table_markdown(report: dict) -> str:
    if not report["descriptive_results_released"]:
        return "\n".join([
            "# Local weights × recovery exploratory factorial",
            "",
            "Descriptive results withheld: the registered 18-cell estimand is incomplete.",
            "",
            "Launcher-invalid cells:",
            "",
            *[
                f"- `{cell['cell_id']}`: {cell['error'] or 'unspecified launcher failure'}"
                for cell in report["invalid_cell_receipts"]
            ],
            "",
            f"Bundle index: `{report['bundle_index_sha256']}`.",
            "",
        ])
    lines = [
        "# Local weights × recovery exploratory factorial",
        "",
        (
            "Descriptive only: three paired replicate/seed blocks per arm; "
            "no superiority test, confidence interval, or confirmatory estimate."
        ),
        "",
        "| Weights | Recovery | Valid n | Calls/min values | Mean calls/min | "
        "Structured-call rate | Malformed | Recovered | Core-3 values | Quest values |",
        "|---|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for weight, label in (("base", "Base"), ("r2", "Round 2"), ("r3", "Round 3")):
        for recovery, state in ((False, "off"), (True, "on")):
            arm = report["by_arm"][f"{weight}-recovery-{state}"]
            values = arm["values"]
            lines.append(
                f"| {label} | {state} | {arm['n_valid']} | "
                f"{', '.join(str(value) for value in values['canonical_executed_calls_per_minute'])} | "
                f"{arm['means']['canonical_executed_calls_per_minute']} | "
                f"{arm['pooled_structured_call_emission_rate']} | "
                f"{arm['malformed_emissions']} | {arm['recovered_calls']} | "
                f"{', '.join(str(value) for value in values['core3_stages_advanced'])} | "
                f"{', '.join(str(value) for value in values['quest_stages_advanced'])} |"
            )
    lines.extend([
        "",
        (
            f"Bundle index: `{report['bundle_index_sha256']}`. "
            f"Files rehashed: {report['files_rehashed']}."
        ),
        "",
    ])
    return "\n".join(lines)


def _tex_values(values: list) -> str:
    return ", ".join(str(value) for value in values)


def _paper_table_tex(report: dict) -> str:
    if not report["descriptive_results_released"]:
        return "\n".join([
            "% Generated by analyze_local_recovery_factorial.py; do not edit.",
            "% Descriptive table withheld because the registered estimand is incomplete.",
            "",
        ])
    rows = []
    for weight, label in (("base", "Base"), ("r2", "Round 2"), ("r3", "Round 3")):
        for state in ("off", "on"):
            arm = report["by_arm"][f"{weight}-recovery-{state}"]
            values = arm["values"]
            rows.append(
                f"{label} & {state} & "
                f"{_tex_values(values['canonical_executed_calls_per_minute'])} & "
                f"{arm['means']['canonical_executed_calls_per_minute']} & "
                f"{arm['pooled_structured_call_emission_rate']} & "
                f"{arm['malformed_emissions']} & {arm['recovered_calls']} & "
                f"{_tex_values(values['core3_stages_advanced'])} \\\\"
            )
    return "\n".join([
        "% Generated by analyze_local_recovery_factorial.py; do not edit.",
        "\\begin{table*}[t]",
        "\\centering",
        "\\small",
        "\\caption{Preregistered 30-minute local weights $\\times$ recovery "
        "exploratory factorial. Values follow replicate order. Descriptive only.}",
        "\\label{tab:local-recovery-factorial}",
        "\\begin{tabular}{lllr rrrl}",
        "\\toprule",
        "Weights & Recovery & Calls/min (all replicates) & Mean & "
        "Structured rate & Malformed & Recovered & Core-3 \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
        "",
    ])


def _write_new_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _write_new_text(path: Path, content: str) -> None:
    _write_new_bytes(path, content.encode("utf-8"))


def _write_or_verify(path: Path, content: bytes) -> None:
    try:
        _write_new_bytes(path, content)
    except FileExistsError:
        if not path.is_file() or path.is_symlink() or path.read_bytes() != content:
            raise AnalysisError(
                f"existing transaction artifact differs from expected bytes: {path}"
            )


def _verify_existing_bytes(path: Path, content: bytes) -> None:
    if (
        not path.is_file()
        or path.is_symlink()
        or path.read_bytes() != content
    ):
        raise AnalysisError(
            f"existing published artifact differs from expected bytes: {path}"
        )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any existing target."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise AnalysisError(
            "atomic no-replace directory publication is unavailable on this platform"
        )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in (errno.EEXIST, errno.ENOTEMPTY):
            raise AnalysisError(
                f"analysis output appeared during publication: {destination}"
            )
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_unblind_registry() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return (base / "kaetram-arena" / "unblind").resolve()


def _sealed_bundle_index(root: Path) -> dict:
    prelaunch_path = root / "prelaunch.json"
    completed_path = root / "completed-inventory.json"
    if not prelaunch_path.is_file():
        raise AnalysisError("prelaunch.json is absent")
    if not completed_path.is_file():
        raise AnalysisError("completed-inventory.json is absent; run remains blinded")
    completed = _load_json(completed_path)
    cells = completed.get("cells")
    if not isinstance(cells, list) or len(cells) != 18:
        raise AnalysisError("completed inventory does not contain 18 cell receipts")
    cell_hashes = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise AnalysisError("completed inventory contains a malformed cell receipt")
        cell_id = cell.get("cell_id")
        inventory_sha = cell.get("artifact_inventory_sha256")
        if (
            not isinstance(cell_id, str)
            or not cell_id
            or not isinstance(inventory_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", inventory_sha)
            or cell_id in cell_hashes
        ):
            raise AnalysisError("completed inventory has invalid cell artifact identity")
        cell_hashes[cell_id] = inventory_sha
    return {
        "prelaunch_sha256": _file_sha256(prelaunch_path),
        "completed_inventory_sha256": _file_sha256(completed_path),
        "cell_artifact_inventory_sha256": {
            cell_id: cell_hashes[cell_id] for cell_id in sorted(cell_hashes)
        },
    }


def _intent_bytes(intent: dict) -> bytes:
    return canonical_json_bytes(intent) + b"\n"


def _reserve_unblind(
    root: Path,
    output_dir: Path,
    manifest: dict,
    manifest_sha256: str,
    code: dict,
    registry_dir: Path,
) -> tuple[Path, Path, dict]:
    receipt_path = root / "analysis-unblind-receipt.json"
    intent_path = root / "analysis-unblind-intent.json"
    if receipt_path.exists():
        raise AnalysisError(
            "this bundle already has a completed unblind receipt; refusing rerun"
        )
    if intent_path.exists():
        if not intent_path.is_file() or intent_path.is_symlink():
            raise AnalysisError("existing root unblind intent is unsafe")
        raise AnalysisError(
            "this bundle already has an unblind intent; resume with "
            f"--resume-unblind-intent {_file_sha256(intent_path)}"
        )
    try:
        output_dir.relative_to(root)
    except ValueError:
        pass
    else:
        raise AnalysisError("analysis output directory must be outside the sealed run root")
    if output_dir.exists():
        raise AnalysisError(f"analysis output already exists: {output_dir}")
    bundle_index = _sealed_bundle_index(root)
    bundle_index_sha256 = sha256_json(bundle_index)
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_intent_path = registry_dir / f"{bundle_index_sha256}.intent.json"
    registry_receipt_path = registry_dir / f"{bundle_index_sha256}.receipt.json"
    if registry_receipt_path.exists():
        raise AnalysisError(
            "this sealed bundle identity already has a completed local unblind "
            "registry receipt"
        )
    if registry_intent_path.exists():
        if (
            not registry_intent_path.is_file()
            or registry_intent_path.is_symlink()
        ):
            raise AnalysisError("existing local unblind registry intent is unsafe")
        raise AnalysisError(
            "this sealed bundle identity already has a local unblind registry "
            "entry; resume the original transaction with "
            f"--resume-unblind-intent {_file_sha256(registry_intent_path)}"
        )
    intent = {
        "schema_version": UNBLIND_INTENT_SCHEMA_VERSION,
        "pilot_id": manifest["pilot_id"],
        "created_at_utc": _utc_now(),
        "manifest_sha256": manifest_sha256,
        "bundle_index": bundle_index,
        "bundle_index_sha256": bundle_index_sha256,
        "analysis_code_inventory_sha256": code["inventory_sha256"],
        "analysis_source_git_commit": code["source_git_commit"],
        "run_root_realpath_sha256": hashlib.sha256(
            str(root).encode("utf-8")
        ).hexdigest(),
        "output_directory_name": output_dir.name,
        "output_directory_realpath_sha256": hashlib.sha256(
            str(output_dir).encode("utf-8")
        ).hexdigest(),
        "confirmation": manifest["pilot_id"],
    }
    content = _intent_bytes(intent)
    _write_new_bytes(registry_intent_path, content)
    _fsync_directory(registry_dir)
    _write_new_bytes(intent_path, content)
    _fsync_directory(root)
    return intent_path, registry_intent_path, intent


def _resume_unblind(
    root: Path,
    output_dir: Path,
    manifest: dict,
    manifest_sha256: str,
    code: dict,
    registry_dir: Path,
    expected_intent_sha256: str,
) -> tuple[Path, Path, dict]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_intent_sha256):
        raise AnalysisError("--resume-unblind-intent requires a lowercase SHA-256")
    if (root / "analysis-unblind-receipt.json").exists():
        raise AnalysisError("the original unblind transaction is already complete")
    try:
        output_dir.relative_to(root)
    except ValueError:
        pass
    else:
        raise AnalysisError("analysis output directory must be outside the sealed run root")
    bundle_index = _sealed_bundle_index(root)
    bundle_index_sha256 = sha256_json(bundle_index)
    registry_intent_path = registry_dir / f"{bundle_index_sha256}.intent.json"
    if not registry_intent_path.is_file() or registry_intent_path.is_symlink():
        raise AnalysisError("no local unblind intent exists for this bundle identity")
    if _file_sha256(registry_intent_path) != expected_intent_sha256:
        raise AnalysisError("local unblind intent digest differs from the resume token")
    intent = _load_json(registry_intent_path)
    expected = {
        "schema_version": UNBLIND_INTENT_SCHEMA_VERSION,
        "pilot_id": manifest["pilot_id"],
        "manifest_sha256": manifest_sha256,
        "bundle_index": bundle_index,
        "bundle_index_sha256": bundle_index_sha256,
        "analysis_code_inventory_sha256": code["inventory_sha256"],
        "analysis_source_git_commit": code["source_git_commit"],
        "run_root_realpath_sha256": hashlib.sha256(
            str(root).encode("utf-8")
        ).hexdigest(),
        "output_directory_name": output_dir.name,
        "output_directory_realpath_sha256": hashlib.sha256(
            str(output_dir).encode("utf-8")
        ).hexdigest(),
        "confirmation": manifest["pilot_id"],
    }
    mismatches = {
        key: {"expected": value, "actual": intent.get(key)}
        for key, value in expected.items()
        if intent.get(key) != value
    }
    if not isinstance(intent.get("created_at_utc"), str) or not intent["created_at_utc"]:
        mismatches["created_at_utc"] = {
            "expected": "nonempty timestamp",
            "actual": intent.get("created_at_utc"),
        }
    if mismatches:
        raise AnalysisError(f"unblind resume identity mismatch: {mismatches}")
    intent_path = root / "analysis-unblind-intent.json"
    _write_or_verify(intent_path, registry_intent_path.read_bytes())
    _fsync_directory(root)
    return intent_path, registry_intent_path, intent


def _validate_publication_inputs(root: Path, report: dict) -> None:
    if _sealed_bundle_index(root) != report["bundle_index"]:
        raise AnalysisError("sealed bundle identity changed during publication")
    completed = _load_json(root / "completed-inventory.json")
    completed_by_id = {
        cell["cell_id"]: cell for cell in completed["cells"]
    }
    _reverify_analysis_inputs(
        root=root,
        completed_by_id=completed_by_id,
        expected_prelaunch_sha256=report["bundle_index"]["prelaunch_sha256"],
        expected_completed_sha256=report["bundle_index"][
            "completed_inventory_sha256"
        ],
        expected_files_checked=report["files_rehashed"],
    )
    if _analysis_code_provenance() != report["analysis_code_provenance"]:
        raise AnalysisError("analysis source or runtime changed during publication")


def _validate_intent_report_identity(
    intent: dict,
    report: dict,
    output_dir: Path,
) -> None:
    expected = {
        "pilot_id": report["pilot_id"],
        "manifest_sha256": report["manifest_sha256"],
        "bundle_index": report["bundle_index"],
        "bundle_index_sha256": report["bundle_index_sha256"],
        "analysis_code_inventory_sha256": report[
            "analysis_code_provenance"
        ]["inventory_sha256"],
        "analysis_source_git_commit": report[
            "analysis_code_provenance"
        ]["source_git_commit"],
        "output_directory_name": output_dir.name,
        "output_directory_realpath_sha256": hashlib.sha256(
            str(output_dir).encode("utf-8")
        ).hexdigest(),
    }
    mismatches = {
        key: {"expected": value, "actual": intent.get(key)}
        for key, value in expected.items()
        if intent.get(key) != value
    }
    if mismatches:
        raise AnalysisError(f"unblind intent differs from analysis report: {mismatches}")


def _publish_analysis(
    root: Path,
    output_dir: Path,
    report: dict,
    intent_path: Path,
    registry_intent_path: Path,
    intent: dict,
) -> dict:
    try:
        output_dir.relative_to(root)
    except ValueError:
        pass
    else:
        raise AnalysisError("analysis output directory must be outside the sealed run root")
    _validate_intent_report_identity(intent, report, output_dir)
    scalar_cell_fields = (
        "cell_id", "replicate", "weight", "recovery", "schedule_index",
        "duration_seconds", "budget_overrun_seconds", "turns",
        "canonical_executed_calls", "canonical_executed_calls_per_minute",
        "canonical_tool_bearing_turns", "tool_parse_rate", "api_errors",
        "sub_sessions", "raw_generations", "generations_with_structured_call",
        "generations_without_structured_call", "structured_call_emission_rate",
        "raw_structured_calls", "raw_structured_calls_per_minute",
        "malformed_emissions", "recoverable_raw_calls", "recovered_calls",
        "recovered_execution_errors", "recovered_execution_successes",
        "repeat_recoveries_within_window", "core3_stages_advanced",
        "quest_stages_advanced", "xp_db_delta", "unique_positions",
        "canonical_action_counts", "raw_action_counts", "recovered_by_tool",
    )
    pair_fields = (
        "replicate", "weight", "pair_order", "off_schedule_index",
        "on_schedule_index", "canonical_executed_calls",
        "canonical_executed_calls_per_minute", "raw_structured_calls",
        "malformed_emissions", "recovered_calls", "core3_stages_advanced",
        "quest_stages_advanced", "xp_db_delta", "unique_positions",
    )
    artifacts = {
        "analysis-report.json": (
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ),
        "cells.csv": _csv_text(report["rows"], scalar_cell_fields),
        "paired-differences.csv": _csv_text(_paired_rows(report), pair_fields),
        "paper-table.md": _paper_table_markdown(report),
        "paper-table.tex": _paper_table_tex(report),
    }
    index_records = [
        {
            "path": name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "size_bytes": len(content.encode("utf-8")),
        }
        for name, content in sorted(artifacts.items())
    ]
    artifact_index = {
        "schema_version": "kaetram.local-weight-recovery-analysis-artifacts.v1",
        "pilot_id": report["pilot_id"],
        "files": index_records,
        "files_sha256": sha256_json(index_records),
    }
    expected_files = {
        **{name: content.encode("utf-8") for name, content in artifacts.items()},
        "artifact-index.json": canonical_json_bytes(artifact_index) + b"\n",
    }
    expected_intent_content = _intent_bytes(intent)
    _write_or_verify(registry_intent_path, expected_intent_content)
    _write_or_verify(intent_path, expected_intent_content)
    intent_sha256 = hashlib.sha256(expected_intent_content).hexdigest()
    staging_dir = (
        output_dir.parent
        / f".{output_dir.name}.staging-{intent_sha256[:16]}"
    )
    if output_dir.exists():
        if staging_dir.exists():
            raise AnalysisError("both final and staging analysis directories exist")
        if not output_dir.is_dir() or output_dir.is_symlink():
            raise AnalysisError("analysis output target is not a safe directory")
        publication_dir = output_dir
        already_published = True
    else:
        staging_dir.mkdir(parents=True, exist_ok=True)
        if staging_dir.is_symlink():
            raise AnalysisError("analysis staging directory is a symlink")
        publication_dir = staging_dir
        already_published = False

    for name, content in expected_files.items():
        if already_published:
            _verify_existing_bytes(publication_dir / name, content)
        else:
            _write_or_verify(publication_dir / name, content)
    _validate_publication_inputs(root, report)
    _write_or_verify(registry_intent_path, expected_intent_content)
    _write_or_verify(intent_path, expected_intent_content)

    receipt_core = {
        "schema_version": UNBLIND_RECEIPT_SCHEMA_VERSION,
        "pilot_id": report["pilot_id"],
        "intent_sha256": intent_sha256,
        "intent": intent,
        "analysis_report_sha256": hashlib.sha256(
            expected_files["analysis-report.json"]
        ).hexdigest(),
        "artifact_index_sha256": hashlib.sha256(
            expected_files["artifact-index.json"]
        ).hexdigest(),
        "artifact_files_sha256": artifact_index["files_sha256"],
        "bundle_index_sha256": report["bundle_index_sha256"],
        "analysis_status": report["analysis_status"],
        "descriptive_results_released": report["descriptive_results_released"],
        "analysis_code_inventory_sha256": report[
            "analysis_code_provenance"
        ]["inventory_sha256"],
        "analysis_source_git_commit": report[
            "analysis_code_provenance"
        ]["source_git_commit"],
        "output_directory_name": output_dir.name,
    }
    registry_receipt_path = registry_intent_path.with_name(
        registry_intent_path.name.replace(".intent.json", ".receipt.json")
    )
    internal_receipt_path = publication_dir / "analysis-unblind-receipt.json"
    if already_published and not internal_receipt_path.is_file():
        raise AnalysisError(
            "existing analysis output lacks its embedded atomic publication receipt"
        )
    receipt_candidates = []
    for candidate_path in (registry_receipt_path, internal_receipt_path):
        if not candidate_path.exists():
            continue
        if not candidate_path.is_file() or candidate_path.is_symlink():
            raise AnalysisError(f"existing analysis receipt is unsafe: {candidate_path}")
        candidate = _load_json(candidate_path)
        if {
            key: value for key, value in candidate.items()
            if key != "completed_at_utc"
        } != receipt_core:
            raise AnalysisError("existing analysis receipt differs from analysis")
        if not isinstance(candidate.get("completed_at_utc"), str):
            raise AnalysisError("existing analysis receipt lacks its completion time")
        receipt_candidates.append(candidate)
    if receipt_candidates:
        if any(candidate != receipt_candidates[0] for candidate in receipt_candidates[1:]):
            raise AnalysisError("existing analysis receipts disagree")
        receipt = receipt_candidates[0]
    else:
        receipt = {**receipt_core, "completed_at_utc": _utc_now()}
    receipt_content = canonical_json_bytes(receipt) + b"\n"
    expected_files["analysis-unblind-receipt.json"] = receipt_content
    if already_published:
        _verify_existing_bytes(internal_receipt_path, receipt_content)
    else:
        _write_or_verify(internal_receipt_path, receipt_content)

    _validate_publication_inputs(root, report)
    _write_or_verify(registry_intent_path, expected_intent_content)
    _write_or_verify(intent_path, expected_intent_content)
    actual_names = sorted(path.name for path in publication_dir.iterdir())
    if actual_names != sorted(expected_files):
        raise AnalysisError("analysis publication directory has unexpected contents")
    for name, content in expected_files.items():
        if already_published:
            _verify_existing_bytes(publication_dir / name, content)
        else:
            _write_or_verify(publication_dir / name, content)
    _fsync_directory(publication_dir)
    if not already_published:
        _rename_directory_noreplace(staging_dir, output_dir)
        _fsync_directory(output_dir.parent)

    _write_or_verify(registry_receipt_path, receipt_content)
    _fsync_directory(registry_receipt_path.parent)
    _write_or_verify(root / "analysis-unblind-receipt.json", receipt_content)
    _fsync_directory(root)
    return receipt


def _load_validated_envelope(
    root: Path,
    manifest: dict,
    manifest_sha256: str,
    *,
    allow_legacy_v1: bool,
) -> tuple[Path, Path, dict, dict, dict, list, dict, int, int]:
    prelaunch_path = root / "prelaunch.json"
    completed_path = root / "completed-inventory.json"
    prelaunch = _load_json(prelaunch_path)
    completed = _load_json(completed_path)
    if (
        prelaunch.get("manifest_sha256") != manifest_sha256
        or completed.get("manifest_sha256") != manifest_sha256
    ):
        raise AnalysisError("manifest digest differs across sealed ledgers")
    preflight = _validate_prelaunch(
        manifest,
        prelaunch,
        expected_schema=RECOVERY_PRELAUNCH_SCHEMA_VERSION,
        intermediate_schema=INTERMEDIATE_RECOVERY_PRELAUNCH_SCHEMA_VERSION,
        legacy_schema=LEGACY_RECOVERY_PRELAUNCH_SCHEMA_VERSION,
        allow_legacy_v1=allow_legacy_v1,
    )
    contract = manifest["artifact_contract"]
    if (
        prelaunch.get("resolved_system_prompt_sha256")
        != contract["system_prompt_sha256"]
        or preflight["tokenizer_sha256"] != contract["tokenizer_sha256"]
        or preflight["render_contract_sha256"] != contract["render_contract_sha256"]
        or preflight["chat_template_sha256"] != contract["chat_template_sha256"]
        or preflight["game_revision"] != contract["game_revision"]
        or preflight["game_bundle_sha256"] != contract["game_bundle_sha256"]
        or preflight["checkpoint_sha256"]
        != {
            snapshot: model["checkpoint_sha256"]
            for snapshot, model in manifest["models"].items()
        }
    ):
        raise AnalysisError("prelaunch artifacts differ from the registration")
    if (
        completed.get("schema_version") != RECOVERY_INVENTORY_SCHEMA_VERSION
        or completed.get("pilot_id") != manifest["pilot_id"]
        or completed.get("claim_boundary") != manifest["claim_boundary"]
    ):
        raise AnalysisError("completed factorial ledger identity is invalid")
    completed_cells = completed.get("cells")
    if not isinstance(completed_cells, list) or len(completed_cells) != 18:
        raise AnalysisError("completed inventory does not contain 18 cells")
    completed_by_id = {
        cell.get("cell_id"): cell for cell in completed_cells
        if isinstance(cell, dict)
    }
    expected_ids = {cell["cell_id"] for cell in manifest["cells"]}
    if len(completed_by_id) != 18 or set(completed_by_id) != expected_ids:
        raise AnalysisError("completed cell IDs differ from registration")
    valid_receipts = sum(
        cell.get("status") == "valid" for cell in completed_cells
    )
    invalid_receipts = len(completed_cells) - valid_receipts
    if (
        completed.get("valid_cells") != valid_receipts
        or completed.get("invalid_cells") != invalid_receipts
    ):
        raise AnalysisError("completed valid/invalid counts differ from cell receipts")
    return (
        prelaunch_path,
        completed_path,
        prelaunch,
        completed,
        preflight,
        completed_cells,
        completed_by_id,
        valid_receipts,
        invalid_receipts,
    )


def verify_sealed_bundle_integrity(
    root: Path,
    manifest_path: Path,
    *,
    allow_legacy_v1: bool = False,
    analysis_code_provenance: dict | None = None,
) -> dict:
    """Rehash the sealed bundle without parsing result-bearing artifacts."""
    manifest, manifest_sha256 = load_manifest(manifest_path)
    if manifest.get("schema_version") != RECOVERY_FACTORIAL_SCHEMA_VERSION:
        raise AnalysisError("manifest is not the reviewed recovery factorial")
    prelaunch_sha256_before = _file_sha256(root / "prelaunch.json")
    completed_sha256_before = _file_sha256(root / "completed-inventory.json")
    (
        _prelaunch_path,
        _completed_path,
        _prelaunch,
        _completed,
        preflight,
        _completed_cells,
        completed_by_id,
        valid_receipts,
        invalid_receipts,
    ) = _load_validated_envelope(
        root,
        manifest,
        manifest_sha256,
        allow_legacy_v1=allow_legacy_v1,
    )

    files_checked = 0
    for cell in manifest["cells"]:
        cell_id = cell["cell_id"]
        retained = completed_by_id[cell_id]
        if (
            retained.get("snapshot") != cell["snapshot"]
            or retained.get("schedule_index") != cell["schedule_index"]
            or retained.get("recovery_assignment") is not cell["recovery"]
        ):
            raise AnalysisError(f"{cell_id}: cell receipt identity mismatch")
        status = retained.get("status")
        if status not in {"valid", "invalid"}:
            raise AnalysisError(f"{cell_id}: unknown launcher status")
        if status == "valid" and (
            retained.get("returncode") != 0
            or retained.get("tool_recovery_enabled") is not cell["recovery"]
        ):
            raise AnalysisError(
                f"{cell_id}: valid cell receipt is technically inconsistent"
            )
        _inventory_sha, rehashed = _verify_completed_cell_artifacts(
            root / cell_id,
            retained,
        )
        files_checked += rehashed

    _reverify_analysis_inputs(
        root=root,
        completed_by_id=completed_by_id,
        expected_prelaunch_sha256=prelaunch_sha256_before,
        expected_completed_sha256=completed_sha256_before,
        expected_files_checked=files_checked,
    )
    code = analysis_code_provenance or _analysis_code_provenance()
    index_record = {
        "prelaunch_sha256": prelaunch_sha256_before,
        "completed_inventory_sha256": completed_sha256_before,
        "cell_artifact_inventory_sha256": {
            cell_id: completed_by_id[cell_id]["artifact_inventory_sha256"]
            for cell_id in sorted(completed_by_id)
        },
    }
    return {
        "schema_version": (
            "kaetram.local-weight-recovery-factorial-integrity-check.v1"
        ),
        "integrity_status": "verified",
        "outcome_values_parsed": False,
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_sha256": manifest_sha256,
        "provenance_tier": preflight["provenance_tier"],
        "analysis_code_provenance": code,
        "bundle_index": index_record,
        "bundle_index_sha256": sha256_json(index_record),
        "registered_cells": len(manifest["cells"]),
        "launcher_valid_cells": valid_receipts,
        "launcher_invalid_cells": invalid_receipts,
        "all_registered_cells_launcher_valid": invalid_receipts == 0,
        "files_rehashed": files_checked,
    }


def analyze(
    root: Path,
    manifest_path: Path,
    *,
    allow_legacy_v1: bool = False,
    analysis_code_provenance: dict | None = None,
) -> dict:
    manifest, manifest_sha256 = load_manifest(manifest_path)
    if manifest.get("schema_version") != RECOVERY_FACTORIAL_SCHEMA_VERSION:
        raise AnalysisError("manifest is not the reviewed recovery factorial")
    prelaunch_sha256_before = _file_sha256(root / "prelaunch.json")
    completed_sha256_before = _file_sha256(root / "completed-inventory.json")
    (
        prelaunch_path,
        completed_path,
        prelaunch,
        completed,
        preflight,
        completed_cells,
        completed_by_id,
        valid_receipts,
        invalid_receipts,
    ) = _load_validated_envelope(
        root,
        manifest,
        manifest_sha256,
        allow_legacy_v1=allow_legacy_v1,
    )
    contract = manifest["artifact_contract"]

    invalid_cells = []
    files_checked = 0
    for cell in manifest["cells"]:
        cell_id = cell["cell_id"]
        cell_root = root / cell_id
        retained = completed_by_id[cell_id]
        recovery = cell["recovery"]
        if (
            retained.get("snapshot") != cell["snapshot"]
            or retained.get("schedule_index") != cell["schedule_index"]
            or retained.get("recovery_assignment") is not recovery
        ):
            raise AnalysisError(f"{cell_id}: cell receipt identity mismatch")
        _inventory_sha, rehashed = _verify_completed_cell_artifacts(
            cell_root, retained
        )
        files_checked += rehashed
        if retained.get("status") != "valid":
            invalid_cells.append({
                "cell_id": cell_id,
                "replicate": cell["replicate"],
                "weight": WEIGHT_LABEL[cell["snapshot"]],
                "recovery": recovery,
                "schedule_index": cell["schedule_index"],
                "returncode": retained.get("returncode"),
                "error": retained.get("error"),
                "artifacts_sealed": True,
            })
    if invalid_cells:
        _reverify_analysis_inputs(
            root=root,
            completed_by_id=completed_by_id,
            expected_prelaunch_sha256=prelaunch_sha256_before,
            expected_completed_sha256=completed_sha256_before,
            expected_files_checked=files_checked,
        )
        code = analysis_code_provenance or _analysis_code_provenance()
        index_record = {
            "prelaunch_sha256": prelaunch_sha256_before,
            "completed_inventory_sha256": completed_sha256_before,
            "cell_artifact_inventory_sha256": {
                cell_id: completed_by_id[cell_id]["artifact_inventory_sha256"]
                for cell_id in sorted(completed_by_id)
            },
        }
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_status": "incomplete_launcher_invalid_cells",
            "descriptive_results_released": False,
            "pilot_id": manifest["pilot_id"],
            "claim_boundary": manifest["claim_boundary"],
            "manifest_sha256": manifest_sha256,
            "provenance_tier": preflight["provenance_tier"],
            "result_artifact_schema_versions": [],
            "analysis_code_provenance": code,
            "bundle_index": index_record,
            "bundle_index_sha256": sha256_json(index_record),
            "valid_cells": valid_receipts,
            "invalid_cells": invalid_receipts,
            "invalid_cell_receipts": invalid_cells,
            "files_rehashed": files_checked,
            "rows": [],
            "by_arm": {},
            "paired_differences": {
                "complete_pairs": [],
                "incomplete_pairs": [],
            },
            "overall": {},
        }

    rows = []
    result_schema_versions = set()
    protocol = manifest["protocol"]
    legacy_bundle = preflight["provenance_tier"] == "legacy_v1_unattested"
    for cell in manifest["cells"]:
        cell_id = cell["cell_id"]
        cell_root = root / cell_id
        retained = completed_by_id[cell_id]
        recovery = cell["recovery"]
        if (
            retained.get("returncode") != 0
            or retained.get("tool_recovery_enabled") is not recovery
        ):
            raise AnalysisError(f"{cell_id}: valid cell receipt is inconsistent")
        endpoint, endpoint_sha = _validate_cell_attestation(
            cell_root,
            cell["snapshot"],
            manifest["models"][cell["snapshot"]],
            preflight,
        )
        results_root = cell_root / "eval" / cell_id
        results = _load_json(results_root / "results.json")
        results_schema = results.get("schema_version")
        if results_schema is None and legacy_bundle and allow_legacy_v1:
            results_schema = "legacy_unversioned"
        elif results_schema != EVAL_RESULTS_SCHEMA_VERSION:
            raise AnalysisError(
                f"{cell_id}: unsupported result artifact schema {results_schema!r}"
            )
        result_schema_versions.add(results_schema)
        meta = results.get("meta")
        episodes = results.get("episodes")
        if not isinstance(meta, dict) or not isinstance(episodes, list) or len(episodes) != 1:
            raise AnalysisError(f"{cell_id}: malformed result shape")
        expected_meta = {
            "model": cell_id,
            "scenario": protocol["scenario"],
            "duration_seconds_budget": protocol["duration_seconds"],
            "include_game_knowledge": protocol["include_game_knowledge"],
            "tool_schema_source": protocol["tool_schema_source"],
            "prompt_agent_name": protocol["prompt_agent_name"],
            "protocol_id": manifest["pilot_id"],
            "experiment_manifest_sha256": manifest_sha256,
            "git_sha": preflight["source_git_commit"],
            "inference_seed": cell["inference_seed"],
            "endpoint_attestation_sha256": endpoint_sha,
            "checkpoint_sha256": endpoint["checkpoint_sha256"],
            "tokenizer_sha256": preflight["tokenizer_sha256"],
            "render_contract_sha256": preflight["render_contract_sha256"],
            "factorial_schedule_algorithm": protocol["schedule_algorithm"],
            "factorial_schedule_seed": protocol["schedule_seed"],
            "factorial_schedule_index": cell["schedule_index"],
            "factorial_batch_index": cell["replicate"] - 1,
            "factorial_cluster_id": f"pilot-rep{cell['replicate']:02d}",
            "factorial_pair_id": (
                f"pilot-rep{cell['replicate']:02d}-"
                f"{WEIGHT_LABEL[cell['snapshot']]}"
            ),
            "environment_seed_mechanism": protocol["environment_seed_mechanism"],
            "environment_seed": cell["environment_seed"],
            "environment_rng_algorithm": protocol["environment_rng_algorithm"],
            "environment_game_revision": preflight["game_revision"],
            "environment_game_bundle_sha256": preflight["game_bundle_sha256"],
            "environment_seed_reason": protocol["environment_seed_reason"],
            "tool_recovery_enabled": recovery,
        }
        database_attestation = preflight.get("game_database_attestation")
        if database_attestation is not None:
            expected_meta.update({
                "game_database_attestation": database_attestation,
                "game_database_attestation_sha256": preflight[
                    "game_database_attestation_sha256"
                ],
            })
        mismatches = {
            key: {"expected": value, "actual": meta.get(key)}
            for key, value in expected_meta.items() if meta.get(key) != value
        }
        if mismatches:
            raise AnalysisError(f"{cell_id}: result provenance mismatch {mismatches}")
        rng = meta.get("environment_rng_attestation")
        expected_rng = {
            "schema": protocol["environment_seed_mechanism"],
            "algorithm": protocol["environment_rng_algorithm"],
            "gameRevision": preflight["game_revision"],
            "serverBundleSha256": preflight["game_bundle_sha256"],
            "drawsAtAttestation": 0,
            "seedSha256": hashlib.sha256(
                str(cell["environment_seed"]).encode()
            ).hexdigest(),
        }
        if not isinstance(rng, dict) or any(
            rng.get(key) != value for key, value in expected_rng.items()
        ):
            raise AnalysisError(f"{cell_id}: environment RNG attestation mismatch")
        expected_session_identity = {
            "personality": protocol["personality"],
            "harness": "qwen",
            "model": manifest["models"][cell["snapshot"]]["api_model"],
            "tool_schema_source": protocol["tool_schema_source"],
            "inference_seed": cell["inference_seed"],
            "protocol_id": manifest["pilot_id"],
            "experiment_manifest_sha256": manifest_sha256,
            "endpoint_attestation_sha256": endpoint_sha,
            "checkpoint_sha256": endpoint["checkpoint_sha256"],
            "tokenizer_sha256": preflight["tokenizer_sha256"],
            "render_contract_sha256": preflight["render_contract_sha256"],
            "factorial_schedule_algorithm": protocol["schedule_algorithm"],
            "factorial_schedule_seed": protocol["schedule_seed"],
            "factorial_schedule_index": cell["schedule_index"],
            "factorial_batch_index": cell["replicate"] - 1,
            "factorial_cluster_id": f"pilot-rep{cell['replicate']:02d}",
            "factorial_pair_id": (
                f"pilot-rep{cell['replicate']:02d}-"
                f"{WEIGHT_LABEL[cell['snapshot']]}"
            ),
            "environment_seed_mechanism": protocol["environment_seed_mechanism"],
            "environment_seed": cell["environment_seed"],
            "environment_rng_algorithm": protocol["environment_rng_algorithm"],
            "environment_game_revision": preflight["game_revision"],
            "environment_game_bundle_sha256": preflight["game_bundle_sha256"],
            "environment_seed_reason": protocol["environment_seed_reason"],
            "environment_rng_attestation": expected_rng,
            "tool_recovery_enabled": recovery,
        }
        if database_attestation is not None:
            expected_session_identity.update({
                "game_database_attestation": database_attestation,
                "game_database_attestation_sha256": preflight[
                    "game_database_attestation_sha256"
                ],
            })
        _validate_recovery_receipts(
            results_root,
            results,
            recovery,
            cell_id,
            expected_session_identity,
        )
        if (
            _file_sha256(results_root / "system_prompt.md")
            != contract["system_prompt_sha256"]
        ):
            raise AnalysisError(f"{cell_id}: resolved system prompt drifted")

        episode = episodes[0]
        if episode.get("status") != "ok" or episode.get("returncode") != 0:
            raise AnalysisError(f"{cell_id}: episode terminal status is invalid")
        state = _load_json(results_root / "episode_001_state.json")
        if not _canonical_start_ok(state):
            raise AnalysisError(f"{cell_id}: canonical first observation mismatch")
        player_before, player_after, qa_before, qa_after = _validate_state_boundaries(
            state, cell_id
        )
        raw_dir = results_root / "episode_001_raw"
        session_logs = _ordered_session_logs(raw_dir)
        try:
            validate_eval_session_terminals(session_logs)
        except RuntimeError as exc:
            raise AnalysisError(f"{cell_id}: invalid terminal chain: {exc}") from exc
        if len(session_logs) != episode.get("sub_sessions"):
            raise AnalysisError(f"{cell_id}: raw session count mismatch")
        entries = []
        for session_log in session_logs:
            entries.extend(parse_log(session_log))
        recomputed = compute_episode_metrics(
            entries, player_before, player_after, qa_before, qa_after
        )
        metric_mismatches = {
            key: {"expected": value, "actual": episode.get(key)}
            for key, value in recomputed.items() if episode.get(key) != value
        }
        if metric_mismatches:
            raise AnalysisError(
                f"{cell_id}: derived metrics mismatch {metric_mismatches}"
            )
        raw_metrics = _validate_raw_emissions(session_logs)
        recovery_metrics = _validate_recovery_accounting(
            session_logs,
            raw_metrics,
            recomputed["action_counts"],
            recovery,
        )
        duration = float(episode["duration_seconds"])
        if duration < protocol["duration_seconds"]:
            raise AnalysisError(f"{cell_id}: episode ended before fixed budget")
        rows.append(_build_cell_row(
            cell=cell,
            duration=duration,
            duration_budget=protocol["duration_seconds"],
            episode=episode,
            recomputed=recomputed,
            raw_metrics=raw_metrics,
            recovery_metrics=recovery_metrics,
            api_errors=_api_error_count(cell_root),
            sub_sessions=len(session_logs),
        ))

    _reverify_analysis_inputs(
        root=root,
        completed_by_id=completed_by_id,
        expected_prelaunch_sha256=prelaunch_sha256_before,
        expected_completed_sha256=completed_sha256_before,
        expected_files_checked=files_checked,
    )
    arm_summary = _summarize(rows)
    pair_summary = _pair_differences(rows)
    _require_complete_estimands(rows, arm_summary, pair_summary)
    code = analysis_code_provenance or _analysis_code_provenance()
    index_record = {
        "prelaunch_sha256": prelaunch_sha256_before,
        "completed_inventory_sha256": completed_sha256_before,
        "cell_artifact_inventory_sha256": {
            cell_id: completed_by_id[cell_id]["artifact_inventory_sha256"]
            for cell_id in sorted(completed_by_id)
        },
    }
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_status": "complete_descriptive",
        "descriptive_results_released": True,
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_sha256": manifest_sha256,
        "provenance_tier": preflight["provenance_tier"],
        "result_artifact_schema_versions": sorted(result_schema_versions),
        "analysis_code_provenance": code,
        "bundle_index": index_record,
        "bundle_index_sha256": sha256_json(index_record),
        "valid_cells": len(rows),
        "invalid_cells": len(invalid_cells),
        "invalid_cell_receipts": invalid_cells,
        "files_rehashed": files_checked,
        "rows": rows,
        "by_arm": arm_summary,
        "paired_differences": pair_summary,
        "overall": {
            "raw_generations": sum(row["raw_generations"] for row in rows),
            "raw_structured_calls": sum(row["raw_structured_calls"] for row in rows),
            "generations_with_structured_call": sum(
                row["generations_with_structured_call"] for row in rows
            ),
            "generations_without_structured_call": sum(
                row["generations_without_structured_call"] for row in rows
            ),
            "pooled_structured_call_emission_rate": round(
                sum(row["generations_with_structured_call"] for row in rows)
                / sum(row["raw_generations"] for row in rows),
                6,
            ) if rows else None,
            "canonical_executed_calls": sum(
                row["canonical_executed_calls"] for row in rows
            ),
            "malformed_emissions": sum(
                row["malformed_emissions"] for row in rows
            ),
            "recovered_calls": sum(row["recovered_calls"] for row in rows),
            "recovered_execution_errors": sum(
                row["recovered_execution_errors"] for row in rows
            ),
            "recovered_execution_successes": sum(
                row["recovered_execution_successes"] for row in rows
            ),
            "api_errors": sum(row["api_errors"] for row in rows),
            "zero_turn_cells": sum(row["turns"] == 0 for row in rows),
            "cells_with_core3_progress": sum(
                row["core3_stages_advanced"] > 0 for row in rows
            ),
            "cells_with_any_quest_progress": sum(
                row["quest_stages_advanced"] > 0 for row in rows
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "research/experiments/local-weight-recovery-30m.json",
    )
    parser.add_argument(
        "--expected-bundle-index-sha256",
        help="Fail if the sealed-ledger root differs from this digest.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Create-only directory for the sealed JSON, CSV, Markdown, and "
            "LaTeX analysis artifacts; it must be outside the run root."
        ),
    )
    parser.add_argument(
        "--confirm-unblind",
        help=(
            "Must exactly equal the registered pilot_id. The analyzer records "
            "a create-only intent before opening result-bearing artifacts."
        ),
    )
    parser.add_argument(
        "--integrity-only",
        action="store_true",
        help=(
            "Validate ledgers and rehash every sealed artifact without parsing "
            "result-bearing files or creating an unblind intent."
        ),
    )
    parser.add_argument(
        "--resume-unblind-intent",
        help=(
            "Resume the original staged transaction after interruption. Supply "
            "the SHA-256 of its local registry intent; identity changes fail."
        ),
    )
    parser.add_argument(
        "--allow-legacy-v1",
        action="store_true",
        help=(
            "Explicitly analyze a pre-v2 bundle as legacy_v1_unattested; "
            "never use this for a new launch."
        ),
    )
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        manifest_path = args.manifest.resolve()
        manifest, manifest_sha256 = load_manifest(manifest_path)
        if manifest.get("schema_version") != RECOVERY_FACTORIAL_SCHEMA_VERSION:
            raise AnalysisError("manifest is not the reviewed recovery factorial")
        if args.integrity_only:
            if args.resume_unblind_intent:
                raise AnalysisError(
                    "--integrity-only cannot resume an unblind transaction"
                )
            report = verify_sealed_bundle_integrity(
                root,
                manifest_path,
                allow_legacy_v1=args.allow_legacy_v1,
            )
            expected = args.expected_bundle_index_sha256
            if expected is not None and report["bundle_index_sha256"] != expected:
                raise AnalysisError("bundle-index digest differs from expected root")
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.output_dir is None or args.confirm_unblind is None:
            raise AnalysisError(
                "unblinding requires both --output-dir and --confirm-unblind"
            )
        output_dir = args.output_dir.resolve()
        if args.confirm_unblind != manifest["pilot_id"]:
            raise AnalysisError(
                "--confirm-unblind must exactly equal the registered pilot_id"
            )
        _load_validated_envelope(
            root,
            manifest,
            manifest_sha256,
            allow_legacy_v1=args.allow_legacy_v1,
        )
        sealed_bundle_index = _sealed_bundle_index(root)
        sealed_bundle_index_sha256 = sha256_json(sealed_bundle_index)
        expected = args.expected_bundle_index_sha256
        if (
            expected is not None
            and sealed_bundle_index_sha256 != expected
        ):
            raise AnalysisError("bundle-index digest differs from expected root")
        code = _analysis_code_provenance()
        registry_dir = _default_unblind_registry()
        if args.resume_unblind_intent:
            intent_path, registry_intent_path, intent = _resume_unblind(
                root,
                output_dir,
                manifest,
                manifest_sha256,
                code,
                registry_dir,
                args.resume_unblind_intent,
            )
        else:
            intent_path, registry_intent_path, intent = _reserve_unblind(
                root,
                output_dir,
                manifest,
                manifest_sha256,
                code,
                registry_dir,
            )
        print(
            f"UNBLIND_INTENT_SHA256={_file_sha256(intent_path)}",
            file=sys.stderr,
            flush=True,
        )
        report = analyze(
            root,
            manifest_path,
            allow_legacy_v1=args.allow_legacy_v1,
            analysis_code_provenance=code,
        )
        if report["bundle_index_sha256"] != sealed_bundle_index_sha256:
            raise AnalysisError("analyzed bundle index differs from pre-unblind identity")
        if _analysis_code_provenance() != code:
            raise AnalysisError("analysis source or runtime changed during analysis")
        receipt = _publish_analysis(
            root,
            output_dir,
            report,
            intent_path,
            registry_intent_path,
            intent,
        )
    except (AnalysisError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "published",
        "output_dir": str(output_dir),
        "bundle_index_sha256": report["bundle_index_sha256"],
        "artifact_index_sha256": receipt["artifact_index_sha256"],
        "unblind_receipt": str(root / "analysis-unblind-receipt.json"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
