#!/usr/bin/env python3
"""Export an anonymous, score-relevant projection of recovered July logs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import (  # noqa: E402
    canonical_json_bytes,
    capture_git_state,
    sha256_json,
)
from scripts.log_analysis.parse import decode_tool_result_content  # noqa: E402
from scripts.render_july_mechanism_results import (  # noqa: E402
    _load_evidence_manifest,
    build_result_report,
)
from scripts.score_july_public_artifact import (  # noqa: E402
    CORE3_STAGE_COUNTS,
    INDEX_SCHEMA,
    REGISTRY_SCHEMA,
    score_projection,
)


_GENERIC_PRIVATE_PATTERNS = (
    re.compile(r"(?:^|[\"'\s])(?:/Users/|/home/|[A-Za-z]:\\\\Users\\\\)"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:git@github\.com:|https?://github\.com/)", re.IGNORECASE),
)


class ProjectionExportError(RuntimeError):
    """Raised when the public score projection cannot be proven faithful."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_self_identifying_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ProjectionExportError(f"input must not be a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionExportError(f"cannot read input receipt: {path}") from exc
    if not isinstance(value, dict):
        raise ProjectionExportError(f"input receipt is not an object: {path}")
    identity = value.get("manifest_sha256")
    unsigned = copy.deepcopy(value)
    unsigned.pop("manifest_sha256", None)
    if not isinstance(identity, str) or identity != sha256_json(unsigned):
        raise ProjectionExportError(f"input receipt has an invalid identity: {path}")
    return value


def _paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve(strict=False)
    right = second.resolve(strict=False)
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _normalize_active_quests(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        if name in seen:
            raise ProjectionExportError(f"duplicate active quest in source payload: {name}")
        seen.add(name)
        sub_stage = item.get("sub_stage")
        if sub_stage is None:
            sub_stage = item.get("subStage")
        try:
            stage = int(item.get("stage") or 0)
            sub = int(sub_stage or 0)
        except (TypeError, ValueError) as exc:
            raise ProjectionExportError("source quest stage is not integer-like") from exc
        if stage < 0 or sub < 0:
            raise ProjectionExportError("source quest stage is negative")
        normalized.append({"name": name, "stage": stage, "sub_stage": sub})
    return normalized


def _normalize_finished_quests(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: set[str] = set()
    for item in value:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return sorted(names)


def _extract_lane_observations(
    run_dir: Path,
    *,
    raw_root: Path,
    lane_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project successful observe results while retaining source-byte receipts."""
    observations: list[dict[str, Any]] = []
    source_logs: list[dict[str, Any]] = []
    for log_path in sorted(
        run_dir.glob("session_*.log"),
        key=lambda path: (
            int(match.group(1))
            if (match := re.match(r"session_(\d+)_", path.name))
            else 10**12,
            path.name,
        ),
    ):
        pending: dict[str, str] = {}
        projected_from_log: list[dict[str, Any]] = []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ProjectionExportError(f"cannot read source log: {log_path}") from exc
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProjectionExportError(
                    f"malformed source JSON at {log_path}:{line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise ProjectionExportError("source JSONL record is not an object")
            message = record.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if not isinstance(blocks, list):
                continue
            if record.get("type") == "assistant":
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = block.get("id")
                    name = block.get("name")
                    if not isinstance(tool_id, str) or not isinstance(name, str):
                        raise ProjectionExportError("source tool_use lacks id or name")
                    if tool_id in pending:
                        raise ProjectionExportError("duplicate pending tool-use id")
                    pending[tool_id] = name.rsplit("__", 1)[-1]
                continue
            if record.get("type") != "user":
                continue
            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp.strip():
                raise ProjectionExportError("source tool result lacks a timestamp")
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str):
                    raise ProjectionExportError("source tool result lacks a tool-use id")
                name = pending.pop(tool_id, None)
                if name != "observe":
                    continue
                content = block.get("content")
                if not isinstance(content, str):
                    raise ProjectionExportError("source observe result is not text")
                payload, _ascii_map = decode_tool_result_content(content)
                if not isinstance(payload, dict) or payload.get("error"):
                    continue
                projected_from_log.append({
                    "lane_index": lane_index,
                    "sequence": len(observations) + len(projected_from_log),
                    "timestamp": timestamp.strip(),
                    "source_log_index": len(source_logs),
                    "source_line_number": line_number,
                    "source_record_sha256": hashlib.sha256(
                        canonical_json_bytes(record)
                    ).hexdigest(),
                    "active_quests": _normalize_active_quests(
                        payload.get("active_quests")
                    ),
                    "finished_quests": _normalize_finished_quests(
                        payload.get("finished_quests")
                    ),
                })
        if projected_from_log:
            source_logs.append({
                "source_log_index": len(source_logs),
                "path": log_path.relative_to(raw_root).as_posix(),
                "sha256": _sha256_file(log_path),
            })
            observations.extend(projected_from_log)
    if not observations:
        raise ProjectionExportError(f"lane has no successful observations: {run_dir}")
    return observations, source_logs


def _expected_lane_scores(result: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for arm in result.get("arms", []):
        if not isinstance(arm, dict):
            raise ProjectionExportError("result receipt has a malformed arm")
        run_ids = arm.get("run_ids")
        if not isinstance(run_ids, list) or len(run_ids) != 1:
            raise ProjectionExportError("result receipt arm does not identify one run")
        run_id = run_ids[0]
        for row in arm.get("agent_results", []):
            key = (run_id, row.get("agent"))
            if key in expected:
                raise ProjectionExportError("duplicate lane in result receipt")
            expected[key] = {
                "stages": row.get("stages"),
                "total": row.get("total"),
                "herbalist_wall_pass": row.get("herbalist_wall_pass"),
            }
    if len(expected) != 27:
        raise ProjectionExportError("result receipt does not contain 27 lanes")
    return expected


def _verify_projected_scores(
    public_scores: dict[str, Any],
    result: dict[str, Any],
) -> None:
    expected = _expected_lane_scores(result)
    actual = {
        (row["run_id"], row["agent"]): {
            "stages": row["stages"],
            "total": row["total"],
            "herbalist_wall_pass": row["herbalist_wall_pass"],
        }
        for row in public_scores["lane_scores"]
    }
    if actual != expected:
        raise ProjectionExportError(
            "anonymous score projection disagrees with the sealed raw-log result"
        )


def _scan_public_files(paths: Iterable[Path], forbidden: Iterable[str]) -> None:
    fragments = tuple(
        unicodedata.normalize("NFKC", item).casefold()
        for item in forbidden
        if item
    )
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProjectionExportError(f"public artifact is not UTF-8: {path}") from exc
        normalized = unicodedata.normalize("NFKC", text)
        folded = normalized.casefold()
        for fragment in fragments:
            if fragment in folded:
                raise ProjectionExportError(
                    f"forbidden identity fragment in public artifact: {fragment}"
                )
        for pattern in _GENERIC_PRIVATE_PATTERNS:
            if match := pattern.search(normalized):
                raise ProjectionExportError(
                    f"private identity pattern in public artifact: {match.group(0)!r}"
                )


def export_projection(
    *,
    raw_root: Path,
    evidence_manifest: Path,
    analysis_provenance: Path,
    result_receipt: Path,
    output_dir: Path,
    forbidden_fragments: tuple[str, ...] = (),
) -> dict[str, Any]:
    inputs = (raw_root, evidence_manifest, analysis_provenance, result_receipt)
    if any(_paths_overlap(output_dir, source) for source in inputs):
        raise ProjectionExportError("output directory overlaps a sealed input")
    if output_dir.exists() or output_dir.is_symlink():
        raise ProjectionExportError(f"refusing to overwrite output: {output_dir}")
    git = capture_git_state(REPO)
    if git.get("dirty"):
        raise ProjectionExportError(
            "public export requires a clean worktree; dirty paths: "
            + ", ".join(git.get("dirty_paths") or [])
        )

    result = _load_self_identifying_json(result_receipt)
    regenerated = build_result_report(
        raw_root,
        evidence_manifest,
        analysis_provenance,
    )
    if regenerated != result:
        raise ProjectionExportError(
            "checked-in result receipt differs from sealed raw-log regeneration"
        )
    evidence = _load_evidence_manifest(evidence_manifest)
    bundle_by_key = {
        (record["run_id"], record["agent"]): record["content"]
        for record in evidence["bundles"]
    }

    lanes: list[dict[str, Any]] = []
    all_observations: list[dict[str, Any]] = []
    for lane_index, key in enumerate(sorted(bundle_by_key)):
        run_id, agent = key
        run_dir = raw_root / agent / "runs" / run_id
        try:
            run_meta = json.loads((run_dir / "run.meta.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionExportError(f"cannot read run metadata: {key}") from exc
        observations, source_logs = _extract_lane_observations(
            run_dir,
            raw_root=raw_root,
            lane_index=lane_index,
        )
        lanes.append({
            "lane_index": lane_index,
            "run_id": run_id,
            "agent": agent,
            "started_at": run_meta.get("started_at"),
            "hours_budget": run_meta.get("hours_budget"),
            "source_bundle_sha256": bundle_by_key[key]["sha256"],
            "source_logs": source_logs,
            "observation_count": len(observations),
            "observations_sha256": sha256_json(observations),
        })
        all_observations.extend(observations)

    registry = {
        "schema_version": REGISTRY_SCHEMA,
        "projection_scope": (
            "Successful observe results projected to quest name/stage fields; "
            "prompts, actions, maps, inventory, and unrelated player state omitted."
        ),
        "claim_boundary": (
            "This reviewer-accessible projection reproduces historical Core-3 "
            "scores but cannot attest historical checkpoint, corpus, reset, "
            "render contract, environment revision, or random seeds."
        ),
        "evidence_manifest_sha256": evidence["manifest_sha256"],
        "analysis_provenance_sha256": result["analysis_provenance"]["manifest_sha256"],
        "raw_result_manifest_sha256": result["manifest_sha256"],
        "core3_stage_counts": CORE3_STAGE_COUNTS,
        "lane_count": len(lanes),
        "lanes": lanes,
    }
    registry["manifest_sha256"] = sha256_json(registry)
    scores = score_projection(registry, all_observations)
    _verify_projected_scores(scores, result)

    readme = """# July mechanism score-replay artifact

This anonymous artifact contains only the fields needed to replay the historical
Core-3 quest scores: offset-aware run starts and successful `observe` results
projected to quest names and stages. It omits prompts, model actions, maps,
inventory, endpoint addresses, and unrelated player state.

Verify it from the repository root:

```bash
python3.12 scripts/score_july_public_artifact.py \\
  --artifact-dir research/artifacts/july-score-replay-v1
```

The artifact proves that the nine documented descriptive scores are present in
the content-bound recovered logs. It does not prove treatment identity or a
causal training effect; see `registry.json` for the exact claim boundary.
"""

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()
    try:
        (output_dir / "README.md").write_text(readme, encoding="utf-8")
        (output_dir / "registry.json").write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with (output_dir / "observations.jsonl").open("x", encoding="utf-8") as handle:
            for row in all_observations:
                handle.write(canonical_json_bytes(row).decode("utf-8") + "\n")
        (output_dir / "scores.json").write_text(
            json.dumps(scores, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        public_files = sorted(path for path in output_dir.iterdir() if path.is_file())
        _scan_public_files(public_files, forbidden_fragments)
        files = [{
            "path": path.name,
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        } for path in public_files]
        index = {
            "schema_version": INDEX_SCHEMA,
            "study_id": "recovered-july-mechanism-score-replay-v1",
            "export_source_git_commit": git["commit"],
            "export_script_sha256": _sha256_file(Path(__file__).resolve()),
            "scorer_script_sha256": _sha256_file(
                REPO / "scripts" / "score_july_public_artifact.py"
            ),
            "evidence_manifest_sha256": evidence["manifest_sha256"],
            "raw_result_manifest_sha256": result["manifest_sha256"],
            "registry_manifest_sha256": registry["manifest_sha256"],
            "scores_manifest_sha256": scores["manifest_sha256"],
            "files": files,
            "tree_sha256": sha256_json(files),
        }
        index["manifest_sha256"] = sha256_json(index)
        (output_dir / "artifact-index.json").write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _scan_public_files([output_dir / "artifact-index.json"], forbidden_fragments)
        return index
    except Exception:
        shutil.rmtree(output_dir)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--analysis-provenance", type=Path, required=True)
    parser.add_argument("--result-receipt", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    args = parser.parse_args(argv)
    defaults = (
        str(Path.home()),
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        "barath",
        "patnir",
    )
    try:
        index = export_projection(
            raw_root=args.raw_root,
            evidence_manifest=args.evidence_manifest,
            analysis_provenance=args.analysis_provenance,
            result_receipt=args.result_receipt,
            output_dir=args.out_dir,
            forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
        )
    except ProjectionExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(index, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
