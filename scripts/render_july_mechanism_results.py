#!/usr/bin/env python3
"""Re-score and bind the recovered July mechanism runs for paper use."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import ManifestError, hash_path, sha256_json  # noqa: E402
from scripts.capture_analysis_provenance import (  # noqa: E402
    AnalysisProvenanceError,
    load_and_verify_analysis_provenance,
)
from scripts.arm_stats import (  # noqa: E402
    AGENTS,
    ARMS,
    CORE3,
    PERSONA,
    collect_arm,
    wall_passes,
)
from scripts.audit_historical_artifacts import CLAIM_RUNS  # noqa: E402


SCHEMA_VERSION = "kaetram.july-mechanism-results.v1"
EVIDENCE_SCHEMA = "kaetram-historical-run-digests-v1"
CLAIM_GROUP = "opd_july_mechanism"
ANALYSIS_FILES = (
    Path("run_manifest.py"),
    Path("scripts/arm_stats.py"),
    Path("scripts/audit_historical_artifacts.py"),
    Path("scripts/capture_analysis_provenance.py"),
    Path("scripts/log_analysis/artifact_requirements.py"),
    Path("scripts/log_analysis/parse.py"),
    Path("scripts/render_july_mechanism_results.py"),
)
JULY_ARM_ORDER = (
    "base-2B+rec",
    "opd-r3-norec",
    "opd-r2-noseed",
    "opd-r2-uniform",
    "opd-r1-uniform",
    "opd-r2-natuni",
    "teacherfree-base",
    "opd-r3-uniform",
    "opd-r1-clean",
)

# These are the protocol-boundary values stated in the dated lab record.  The
# renderer does not use them as input; it independently parses the raw logs and
# fails if the reconstruction disagrees.
DOCUMENTED_AGENT_TOTALS = {
    "base-2B+rec": [3, 4, 5],
    "opd-r3-norec": [6, 6, 6],
    "opd-r2-noseed": [5, 3, 4],
    "opd-r2-uniform": [5, 5, 5],
    "opd-r1-uniform": [4, 4, 5],
    "opd-r2-natuni": [5, 5, 4],
    "teacherfree-base": [4, 4, 4],
    "opd-r3-uniform": [5, 6, 6],
    "opd-r1-clean": [4, 5, 5],
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SESSION_RE = re.compile(r"^session_(\d+)_")
_SEMANTIC_RECORD_TYPES = {"assistant", "user", "text", "reasoning", "tool_use"}


class ResultError(RuntimeError):
    """Raised when historical results cannot be bound without ambiguity."""


def _load_evidence_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ResultError("evidence manifest must not be a symlink")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultError(f"cannot read evidence manifest: {path}") from exc
    if not isinstance(report, dict) or report.get("schema_version") != EVIDENCE_SCHEMA:
        raise ResultError("unexpected evidence-manifest schema")
    identity = report.get("manifest_sha256")
    unsigned = copy.deepcopy(report)
    unsigned.pop("manifest_sha256", None)
    if not isinstance(identity, str) or identity != sha256_json(unsigned):
        raise ResultError("evidence manifest has an invalid self-identity")
    if not report.get("complete") or report.get("missing") != []:
        raise ResultError("evidence manifest is incomplete")

    expected_runs = set(CLAIM_RUNS[CLAIM_GROUP])
    groups = report.get("claim_groups")
    if not isinstance(groups, dict) or set(groups) != {CLAIM_GROUP}:
        raise ResultError("evidence manifest must contain only the July claim group")
    if (
        not isinstance(groups[CLAIM_GROUP], list)
        or len(groups[CLAIM_GROUP]) != len(expected_runs)
        or set(groups[CLAIM_GROUP]) != expected_runs
    ):
        raise ResultError("evidence manifest does not contain the registered July runs")

    bundles = report.get("bundles")
    expected_keys = {
        (run_id, agent)
        for run_id in expected_runs
        for agent in AGENTS
    }
    actual_keys = {
        (record.get("run_id"), record.get("agent"))
        for record in bundles
        if isinstance(record, dict)
    } if isinstance(bundles, list) else set()
    if (
        actual_keys != expected_keys
        or not isinstance(bundles, list)
        or len(bundles) != len(expected_keys)
        or report.get("bundle_count") != len(expected_keys)
    ):
        raise ResultError("evidence manifest does not bind every July agent/run bundle")
    bundle_file_counts: list[int] = []
    for record in bundles:
        if not isinstance(record, dict) or set(record) != {
            "claim_group", "run_id", "agent", "content"
        }:
            raise ResultError("evidence manifest has a malformed bundle record")
        if record["claim_group"] != CLAIM_GROUP:
            raise ResultError("evidence manifest has a bundle in the wrong claim group")
        content = record.get("content") if isinstance(record, dict) else None
        count = content.get("file_count") if isinstance(content, dict) else None
        digest = content.get("sha256") if isinstance(content, dict) else None
        expected_path = f"{record['agent']}/runs/{record['run_id']}"
        if (
            not isinstance(content, dict)
            or set(content) != {
                "kind", "path", "sha256", "size_bytes", "file_count"
            }
            or content.get("kind") != "directory"
            or content.get("path") != expected_path
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
            or not isinstance(content.get("size_bytes"), int)
            or isinstance(content.get("size_bytes"), bool)
            or content["size_bytes"] < 0
        ):
            raise ResultError("evidence manifest has an invalid bundle descriptor")
        bundle_file_counts.append(count)
    if report.get("source_manifest_verified_file_count") != sum(bundle_file_counts):
        raise ResultError("source manifest was not verified against every bundle file")

    source = report.get("source_manifest")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("name"), str)
        or not _SHA256_RE.fullmatch(str(source.get("sha256")))
    ):
        raise ResultError("evidence manifest has no valid source-copy identity")
    return report


def _verify_scored_bundles(
    raw_root: Path,
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rehash every directory immediately before it is scored."""
    records = {
        (record["run_id"], record["agent"]): record
        for record in evidence["bundles"]
    }
    verified: list[dict[str, Any]] = []
    for run_id in sorted(CLAIM_RUNS[CLAIM_GROUP]):
        for agent in AGENTS:
            expected = records[(run_id, agent)]["content"]
            run_dir = raw_root / agent / "runs" / run_id
            try:
                actual = hash_path(run_dir, root=raw_root)
            except ManifestError as exc:
                raise ResultError(f"cannot hash scored bundle {run_id}/{agent}: {exc}") from exc
            if actual != expected:
                raise ResultError(
                    f"scored bundle bytes disagree with evidence manifest: "
                    f"{run_id}/{agent}"
                )
            verified.append({
                "run_id": run_id,
                "agent": agent,
                "content_sha256": actual["sha256"],
                "size_bytes": actual["size_bytes"],
                "file_count": actual["file_count"],
            })
    return verified


def _session_sort_key(path: Path) -> tuple[int, str]:
    match = _SESSION_RE.match(path.name)
    return (int(match.group(1)) if match else 10**12, path.name)


def _validate_record_clock(run_dir: Path, *, run_id: str, agent: str) -> dict[str, Any]:
    """Show that the naive JSONL clock aligns with the offset-aware run start.

    The July logger omitted offsets from semantic-record timestamps.  We admit
    the UTC interpretation only when the first semantic event in session 1 is
    within 15 minutes after the independently offset-aware run start and the
    entire semantic stream is monotonic on that same naive clock.
    """
    meta_path = run_dir / "run.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        start = datetime.fromisoformat(meta["started_at"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ResultError(f"{run_id}/{agent}: invalid run start metadata") from exc
    if start.tzinfo is None:
        raise ResultError(f"{run_id}/{agent}: run start has no UTC offset")
    start_utc = start.astimezone(timezone.utc)

    first: datetime | None = None
    previous: datetime | None = None
    semantic_count = 0
    paths = sorted(run_dir.glob("session_*.log"), key=_session_sort_key)
    if not paths:
        raise ResultError(f"{run_id}/{agent}: no session logs for clock validation")
    for log_path in paths:
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ResultError(f"{run_id}/{agent}: unreadable session log") from exc
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResultError(
                    f"{run_id}/{agent}: malformed JSON at {log_path.name}:{line_no}"
                ) from exc
            if not isinstance(record, dict) or record.get("type") not in _SEMANTIC_RECORD_TYPES:
                continue
            raw = record.get("timestamp")
            if not isinstance(raw, str) or not raw.strip():
                raise ResultError(
                    f"{run_id}/{agent}: semantic record lacks a timestamp"
                )
            try:
                parsed = datetime.fromisoformat(raw.strip())
            except ValueError as exc:
                raise ResultError(
                    f"{run_id}/{agent}: semantic record timestamp is invalid"
                ) from exc
            if parsed.tzinfo is not None:
                raise ResultError(
                    f"{run_id}/{agent}: mixed timestamp contract; expected naive July records"
                )
            timestamp = parsed.replace(tzinfo=timezone.utc)
            if previous is not None and timestamp < previous:
                raise ResultError(
                    f"{run_id}/{agent}: semantic record clock moves backwards"
                )
            first = timestamp if first is None else first
            previous = timestamp
            semantic_count += 1

    if first is None:
        raise ResultError(f"{run_id}/{agent}: no semantic timestamps to validate")
    delay_seconds = (first - start_utc).total_seconds()
    if not 0 <= delay_seconds <= 900:
        raise ResultError(
            f"{run_id}/{agent}: naive record clock does not align with the "
            f"offset-aware run start ({delay_seconds:.6f}s)"
        )
    return {
        "run_id": run_id,
        "agent": agent,
        "semantic_record_count": semantic_count,
        "start_to_first_semantic_seconds": round(delay_seconds, 6),
    }


def _validate_record_clocks(raw_root: Path) -> dict[str, Any]:
    lanes: list[dict[str, Any]] = []
    for run_id in sorted(CLAIM_RUNS[CLAIM_GROUP]):
        for agent in AGENTS:
            lanes.append(
                _validate_record_clock(
                    raw_root / agent / "runs" / run_id,
                    run_id=run_id,
                    agent=agent,
                )
            )
    delays = [lane["start_to_first_semantic_seconds"] for lane in lanes]
    return {
        "contract": (
            "Historical semantic timestamps are naive. UTC is admitted only "
            "after each lane's monotonic semantic clock begins 0..900 seconds "
            "after its independently offset-aware run start."
        ),
        "lane_count": len(lanes),
        "semantic_record_count": sum(lane["semantic_record_count"] for lane in lanes),
        "minimum_start_delay_seconds": min(delays),
        "maximum_start_delay_seconds": max(delays),
        "lanes": lanes,
    }


def _validate_output_path(
    output: Path,
    *,
    raw_root: Path,
    input_files: tuple[Path, ...],
) -> None:
    if output.is_symlink():
        raise ResultError(f"output must not be a symlink: {output}")
    resolved = output.resolve()
    raw_resolved = raw_root.resolve()
    if resolved == raw_resolved or resolved.is_relative_to(raw_resolved):
        raise ResultError("output must not be inside the scored raw root")
    if any(resolved == item.resolve() for item in input_files):
        raise ResultError("output must not overwrite an input receipt")


def build_result_report(
    raw_root: Path,
    evidence_manifest: Path,
    analysis_provenance: Path,
) -> dict[str, Any]:
    """Parse each run at its recorded six-hour boundary and build a receipt."""
    if raw_root.is_symlink():
        raise ResultError("scored raw root must not be a symlink")
    evidence = _load_evidence_manifest(evidence_manifest)
    try:
        analysis = load_and_verify_analysis_provenance(
            analysis_provenance,
            repo_root=REPO,
            expected_files=ANALYSIS_FILES,
        )
    except AnalysisProvenanceError as exc:
        raise ResultError(str(exc)) from exc
    verified_bundles = _verify_scored_bundles(raw_root, evidence)
    clock_validation = _validate_record_clocks(raw_root)
    arms: list[dict[str, Any]] = []
    for arm in JULY_ARM_ORDER:
        rows = collect_arm(arm, raw_root)
        expected_run_ids = ARMS[arm]["runs"]
        if len(rows) != len(AGENTS) * len(expected_run_ids):
            raise ResultError(f"{arm}: incomplete or quarantined result evidence")
        rows_by_agent = {row["agent"]: row for row in rows}
        if set(rows_by_agent) != set(AGENTS):
            raise ResultError(f"{arm}: duplicate or missing agent lanes")
        ordered_rows = [rows_by_agent[agent] for agent in AGENTS]
        reconstructed = [row["total"] for row in ordered_rows]
        if reconstructed != DOCUMENTED_AGENT_TOTALS[arm]:
            raise ResultError(
                f"{arm}: reconstructed totals {reconstructed} disagree with "
                f"documented protocol totals {DOCUMENTED_AGENT_TOTALS[arm]}"
            )
        arms.append({
            "arm": arm,
            "run_ids": list(expected_run_ids),
            "protocol_boundary_hours": ARMS[arm]["boundary_hours"],
            "agent_results": [{
                "agent": row["agent"],
                "persona": PERSONA[row["agent"]],
                "stages": {quest: row["stages"][quest] for quest in CORE3},
                "total": row["total"],
                "herbalist_wall_pass": row["stages"][CORE3[1]] >= 2,
            } for row in ordered_rows],
            "core3_total": sum(reconstructed),
            "herbalist_wall_passes": sum(wall_passes(ordered_rows)),
            "agent_lane_count": len(ordered_rows),
        })

    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis_status": "complete",
        "score_source": "raw JSONL observe results through each lane's inclusive cutoff",
        "timestamp_contract": clock_validation,
        "evidence_manifest": {
            "path": "research/audits/july-mechanism-run-digests.json",
            "manifest_sha256": evidence["manifest_sha256"],
            "source_manifest": evidence["source_manifest"],
            "verified_scored_bundles": verified_bundles,
            "verified_scored_bundle_count": len(verified_bundles),
            "verified_scored_bundles_sha256": sha256_json(verified_bundles),
        },
        "analysis_provenance": {
            "path": "research/audits/july-mechanism-analysis-provenance.json",
            "manifest_sha256": analysis["manifest_sha256"],
            "source_git_commit": analysis["source_git_commit"],
            "python_version": analysis["python_version"],
            "implementation_sha256": analysis["implementation_sha256"],
        },
        "arms": arms,
        "claim_boundary": (
            "Recovered logs independently reproduce these historical descriptive "
            "scores. One checkpoint/training run per arm and missing checkpoint, "
            "reset, seed, and environment attestations preclude a causal effect estimate."
        ),
    }
    report["manifest_sha256"] = sha256_json(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--analysis-provenance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _validate_output_path(
            args.out,
            raw_root=args.raw_root,
            input_files=(args.evidence_manifest, args.analysis_provenance),
        )
        report = build_result_report(
            args.raw_root,
            args.evidence_manifest,
            args.analysis_provenance,
        )
    except (ResultError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out.exists():
        if not args.out.is_file():
            print(f"ERROR: output is not a regular file: {args.out}", file=sys.stderr)
            return 2
        if args.out.read_bytes() != rendered.encode("utf-8"):
            print(f"ERROR: refusing to overwrite different result: {args.out}", file=sys.stderr)
            return 2
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(rendered.encode("utf-8"))
    print(args.out)
    print(f"manifest_sha256={report['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
