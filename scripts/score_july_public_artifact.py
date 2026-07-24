#!/usr/bin/env python3
"""Verify and score the anonymous July Core-3 replay artifact."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import canonical_json_bytes, sha256_json  # noqa: E402


REGISTRY_SCHEMA = "kaetram.july-score-projection-registry.v1"
SCORES_SCHEMA = "kaetram.july-score-projection-scores.v1"
INDEX_SCHEMA = "kaetram.july-score-projection-artifact.v1"
CORE3_STAGE_COUNTS = {
    "Foresting": 3,
    "Herbalist's Desperation": 3,
    "Rick's Roll": 4,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PublicScoreError(RuntimeError):
    """Raised when a public score projection is incomplete or inconsistent."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, *, schema: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PublicScoreError(f"artifact input must not be a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicScoreError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise PublicScoreError(f"unexpected artifact schema: {path}")
    identity = value.get("manifest_sha256")
    unsigned = copy.deepcopy(value)
    unsigned.pop("manifest_sha256", None)
    if not isinstance(identity, str) or identity != sha256_json(unsigned):
        raise PublicScoreError(f"invalid self-identity: {path}")
    return value


def load_registry(path: Path) -> dict[str, Any]:
    registry = _load_json(path, schema=REGISTRY_SCHEMA)
    lanes = registry.get("lanes")
    if (
        registry.get("core3_stage_counts") != CORE3_STAGE_COUNTS
        or not isinstance(lanes, list)
        or len(lanes) != 27
        or registry.get("lane_count") != len(lanes)
    ):
        raise PublicScoreError("registry does not contain the frozen 27-lane protocol")
    expected_indexes = list(range(len(lanes)))
    actual_indexes = [lane.get("lane_index") for lane in lanes if isinstance(lane, dict)]
    if actual_indexes != expected_indexes:
        raise PublicScoreError("registry lane indexes are not unique and contiguous")
    for lane in lanes:
        if set(lane) != {
            "lane_index",
            "run_id",
            "agent",
            "started_at",
            "hours_budget",
            "source_bundle_sha256",
            "source_logs",
            "observation_count",
            "observations_sha256",
        }:
            raise PublicScoreError("registry has a malformed lane")
        if (
            lane["hours_budget"] != 6.0
            or not isinstance(lane["run_id"], str)
            or not lane["run_id"].startswith("run_")
            or not isinstance(lane["agent"], str)
            or not lane["agent"].startswith("agent_")
            or not _SHA256_RE.fullmatch(str(lane["source_bundle_sha256"]))
            or not _SHA256_RE.fullmatch(str(lane["observations_sha256"]))
            or not isinstance(lane["observation_count"], int)
            or lane["observation_count"] <= 0
        ):
            raise PublicScoreError("registry has invalid lane provenance")
        try:
            start = datetime.fromisoformat(lane["started_at"])
        except (TypeError, ValueError) as exc:
            raise PublicScoreError("registry has an invalid run start") from exc
        if start.tzinfo is None:
            raise PublicScoreError("registry run starts must be offset-aware")
        source_logs = lane["source_logs"]
        if not isinstance(source_logs, list) or not source_logs:
            raise PublicScoreError("registry lane has no source-log inventory")
        for index, source in enumerate(source_logs):
            if (
                not isinstance(source, dict)
                or set(source) != {"source_log_index", "path", "sha256"}
                or source["source_log_index"] != index
                or not isinstance(source["path"], str)
                or source["path"].startswith("/")
                or ".." in Path(source["path"]).parts
                or not _SHA256_RE.fullmatch(str(source["sha256"]))
            ):
                raise PublicScoreError("registry has an invalid source-log record")
    return registry


def load_observations(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise PublicScoreError("observation stream must not be a symlink")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise PublicScoreError("cannot read observation stream") from exc
    for line_number, line in enumerate(lines, 1):
        if not line:
            raise PublicScoreError(f"blank observation line at {line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicScoreError(
                f"malformed observation JSON at line {line_number}"
            ) from exc
        if not isinstance(row, dict) or set(row) != {
            "lane_index",
            "sequence",
            "timestamp",
            "source_log_index",
            "source_line_number",
            "source_record_sha256",
            "active_quests",
            "finished_quests",
        }:
            raise PublicScoreError(f"malformed observation at line {line_number}")
        if (
            not isinstance(row["lane_index"], int)
            or isinstance(row["lane_index"], bool)
            or not isinstance(row["sequence"], int)
            or isinstance(row["sequence"], bool)
            or row["sequence"] < 0
            or not isinstance(row["source_log_index"], int)
            or isinstance(row["source_log_index"], bool)
            or row["source_log_index"] < 0
            or not isinstance(row["source_line_number"], int)
            or isinstance(row["source_line_number"], bool)
            or row["source_line_number"] <= 0
            or not _SHA256_RE.fullmatch(str(row["source_record_sha256"]))
            or not isinstance(row["active_quests"], list)
            or not isinstance(row["finished_quests"], list)
        ):
            raise PublicScoreError(f"invalid observation fields at line {line_number}")
        rows.append(row)
    if not rows:
        raise PublicScoreError("observation stream is empty")
    return rows


def _active_signature(value: object) -> dict[str, int]:
    if not isinstance(value, list):
        raise PublicScoreError("active_quests must be a list")
    out: dict[str, int] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "stage", "sub_stage"}
            or not isinstance(item["name"], str)
            or not isinstance(item["stage"], int)
            or isinstance(item["stage"], bool)
            or item["stage"] < 0
            or not isinstance(item["sub_stage"], int)
            or isinstance(item["sub_stage"], bool)
            or item["sub_stage"] < 0
            or item["name"] in out
        ):
            raise PublicScoreError("invalid or duplicate active quest")
        out[item["name"]] = item["stage"]
    return out


def _finished_names(value: object) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(name, str) for name in value)
        or len(value) != len(set(value))
    ):
        raise PublicScoreError("invalid or duplicate finished quest")
    return set(value)


def score_projection(
    registry: dict[str, Any],
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute every lane's inclusive six-hour Core-3 score."""
    lanes = registry["lanes"]
    by_lane: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(lanes))}
    for row in observations:
        lane_index = row["lane_index"]
        if lane_index not in by_lane:
            raise PublicScoreError("observation references an unknown lane")
        by_lane[lane_index].append(row)

    lane_scores: list[dict[str, Any]] = []
    for lane in lanes:
        lane_index = lane["lane_index"]
        rows = by_lane[lane_index]
        if len(rows) != lane["observation_count"]:
            raise PublicScoreError("lane observation count disagrees with registry")
        if sha256_json(rows) != lane["observations_sha256"]:
            raise PublicScoreError("lane observation bytes disagree with registry")
        if [row["sequence"] for row in rows] != list(range(len(rows))):
            raise PublicScoreError("lane observation sequence is not contiguous")
        if any(
            row["source_log_index"] >= len(lane["source_logs"])
            for row in rows
        ):
            raise PublicScoreError("observation references an unknown source log")

        start = datetime.fromisoformat(lane["started_at"]).astimezone(timezone.utc)
        cutoff = start + timedelta(hours=lane["hours_budget"])
        previous: datetime | None = None
        first_visible_core3: set[str] | None = None
        maxima = {name: 0 for name in CORE3_STAGE_COUNTS}
        included = 0
        for row in rows:
            try:
                parsed = datetime.fromisoformat(row["timestamp"])
            except (TypeError, ValueError) as exc:
                raise PublicScoreError("observation timestamp is invalid") from exc
            if parsed.tzinfo is not None:
                raise PublicScoreError("July projection requires naive UTC timestamps")
            timestamp = parsed.replace(tzinfo=timezone.utc)
            if previous is not None and timestamp < previous:
                raise PublicScoreError("lane observation clock moves backwards")
            if previous is None:
                start_delay = (timestamp - start).total_seconds()
                if not 0 <= start_delay <= 900:
                    raise PublicScoreError(
                        "lane observation clock does not align with its run start"
                    )
            previous = timestamp
            active = _active_signature(row["active_quests"])
            finished = _finished_names(row["finished_quests"])
            if first_visible_core3 is None:
                first_visible_core3 = (
                    set(active) | finished
                ) & set(CORE3_STAGE_COUNTS)
            if timestamp > cutoff:
                continue
            included += 1
            for name in CORE3_STAGE_COUNTS:
                if name in finished:
                    maxima[name] = CORE3_STAGE_COUNTS[name]
                elif name in active:
                    if active[name] > CORE3_STAGE_COUNTS[name]:
                        raise PublicScoreError("Core-3 stage exceeds its protocol count")
                    maxima[name] = max(maxima[name], active[name])

        if first_visible_core3:
            raise PublicScoreError("first observation has visible Core-3 progress")
        if included == 0:
            raise PublicScoreError("lane has no observations inside its cutoff")
        lane_scores.append({
            "lane_index": lane_index,
            "run_id": lane["run_id"],
            "agent": lane["agent"],
            "included_observation_count": included,
            "stages": maxima,
            "total": sum(maxima.values()),
            "herbalist_wall_pass": maxima["Herbalist's Desperation"] >= 2,
        })

    report = {
        "schema_version": SCORES_SCHEMA,
        "registry_manifest_sha256": registry["manifest_sha256"],
        "score_source": (
            "anonymous observe-result projection through each lane's inclusive "
            "offset-aware six-hour cutoff"
        ),
        "lane_scores": lane_scores,
    }
    report["manifest_sha256"] = sha256_json(report)
    return report


def verify_artifact(root: Path) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise PublicScoreError("artifact root must be a regular directory")
    index = _load_json(root / "artifact-index.json", schema=INDEX_SCHEMA)
    files = index.get("files")
    if not isinstance(files, list) or not files:
        raise PublicScoreError("artifact index has no file inventory")
    expected_paths = {
        "README.md",
        "registry.json",
        "observations.jsonl",
        "scores.json",
    }
    actual_paths = {
        path.name
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != expected_paths | {"artifact-index.json"}:
        raise PublicScoreError("artifact directory has unexpected or missing files")
    if {record.get("path") for record in files if isinstance(record, dict)} != expected_paths:
        raise PublicScoreError("artifact index has an unexpected file inventory")
    for record in files:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "sha256", "size_bytes"}
            or not _SHA256_RE.fullmatch(str(record["sha256"]))
            or not isinstance(record["size_bytes"], int)
            or isinstance(record["size_bytes"], bool)
            or record["size_bytes"] < 0
        ):
            raise PublicScoreError("artifact index has a malformed file record")
        path = root / record["path"]
        if path.is_symlink() or not path.is_file():
            raise PublicScoreError("artifact file is absent or symlinked")
        if path.stat().st_size != record["size_bytes"] or _sha256_file(path) != record["sha256"]:
            raise PublicScoreError("artifact file bytes disagree with the index")
    if index.get("tree_sha256") != sha256_json(files):
        raise PublicScoreError("artifact tree identity is invalid")
    if (
        not _GIT_SHA_RE.fullmatch(str(index.get("export_source_git_commit", "")))
        or not _SHA256_RE.fullmatch(str(index.get("export_script_sha256", "")))
        or not _SHA256_RE.fullmatch(str(index.get("scorer_script_sha256", "")))
    ):
        raise PublicScoreError("artifact implementation provenance is invalid")
    if index["scorer_script_sha256"] != _sha256_file(Path(__file__).resolve()):
        raise PublicScoreError("artifact scorer bytes do not match this verifier")
    exporter_path = REPO / "scripts" / "export_july_score_artifact.py"
    if (
        not exporter_path.is_file()
        or exporter_path.is_symlink()
        or index["export_script_sha256"] != _sha256_file(exporter_path)
    ):
        raise PublicScoreError("artifact exporter bytes do not match this checkout")

    registry = load_registry(root / "registry.json")
    observations = load_observations(root / "observations.jsonl")
    generated = score_projection(registry, observations)
    recorded = _load_json(root / "scores.json", schema=SCORES_SCHEMA)
    if generated != recorded:
        raise PublicScoreError("recorded scores disagree with public replay")
    if index.get("registry_manifest_sha256") != registry["manifest_sha256"]:
        raise PublicScoreError("artifact index cites the wrong registry")
    if index.get("scores_manifest_sha256") != recorded["manifest_sha256"]:
        raise PublicScoreError("artifact index cites the wrong scores")
    if index.get("evidence_manifest_sha256") != registry.get(
        "evidence_manifest_sha256"
    ):
        raise PublicScoreError("artifact index cites the wrong evidence manifest")
    if index.get("raw_result_manifest_sha256") != registry.get(
        "raw_result_manifest_sha256"
    ):
        raise PublicScoreError("artifact index cites the wrong raw result")
    return {
        "artifact_index": index,
        "registry": registry,
        "scores": recorded,
        "observation_count": len(observations),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verified = verify_artifact(args.artifact_dir)
    except PublicScoreError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "artifact_manifest_sha256": verified["artifact_index"]["manifest_sha256"],
        "observation_count": verified["observation_count"],
        "scores_manifest_sha256": verified["scores"]["manifest_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
