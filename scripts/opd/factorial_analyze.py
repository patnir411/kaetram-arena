#!/usr/bin/env python3
"""Validate and analyze a completed OPD weights x recovery factorial.

The independent unit is a DB-reset replicate.  The three personality lanes are
summed within each replicate x arm before any contrast is computed; they are
never reported as independent samples.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import statistics
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.opd.factorial_eval import (  # noqa: E402
    Cell,
    ExperimentPlan,
    ManifestError,
    build_plan,
    validate_completed_inventory,
    validate_cell_result,
)


DEFAULT_BOOTSTRAP_SAMPLES = 20_000
DEFAULT_BOOTSTRAP_SEED = 20_260_718


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _numeric(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f"{label} must be a finite numeric value")
    return float(value)


def load_cell_metric(plan: ExperimentPlan, cell: Cell, metric: str) -> dict[str, Any]:
    validate_cell_result(plan, cell)
    path = Path(cell.run_dir) / cell.cell_id / "results.json"
    results = json.loads(path.read_text())
    meta = results["meta"]
    episode = results["episodes"][0]
    if meta.get("endpoint") != f"env:{cell.endpoint_env}":
        raise ManifestError(
            f"cell {cell.cell_id} endpoint reference mismatch: {meta.get('endpoint')!r}"
        )
    git_sha = meta.get("git_sha")
    if not isinstance(git_sha, str) or not git_sha.strip():
        raise ManifestError(f"cell {cell.cell_id} has no source git SHA")
    if git_sha != plan.source_git_commit:
        raise ManifestError(
            f"cell {cell.cell_id} source git SHA does not match the registered source commit"
        )
    if episode.get("returncode") != 0:
        raise ManifestError(f"cell {cell.cell_id} episode returncode is not zero")
    turns = episode.get("turns_played")
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        raise ManifestError(f"cell {cell.cell_id} has no completed agent turns")
    value = _numeric(episode.get(metric), label=f"cell {cell.cell_id} metric {metric}")
    if metric == "core3_stages_advanced" and (not value.is_integer() or not 0 <= value <= 10):
        raise ManifestError(
            f"cell {cell.cell_id} core3_stages_advanced must be an integer in [0, 10]"
        )
    provenance_keys = (
        "inference_seed", "factorial_schedule_algorithm", "factorial_schedule_seed",
        "factorial_schedule_index", "factorial_batch_index", "factorial_cluster_id",
        "factorial_pair_id", "environment_seed_mechanism", "environment_seed",
        "environment_seed_reason",
    )
    return {
        "cell_id": cell.cell_id,
        "pair_id": cell.pair_id,
        "cluster_id": cell.cluster_id,
        "replicate": cell.replicate,
        "weight": cell.weight,
        "recovery": cell.recovery,
        "personality": cell.personality,
        "metric": metric,
        "value": value,
        "turns_played": turns,
        "git_sha": git_sha,
        "results_path": str(path),
        "results_sha256": _sha256(path),
        "randomization_provenance": {
            key: meta[key] for key in provenance_keys if key in meta
        },
    }


def cluster_rows(cell_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, bool], list[dict[str, Any]]] = {}
    for row in cell_rows:
        grouped.setdefault((row["replicate"], row["weight"], row["recovery"]), []).append(row)
    clusters = []
    for (replicate, weight, recovery), rows in sorted(grouped.items()):
        personalities = {row["personality"] for row in rows}
        if personalities != {"grinder", "completionist", "explorer_tinkerer"} or len(rows) != 3:
            raise ManifestError(
                f"replicate {replicate} {weight} recovery={recovery} lacks three personality lanes"
            )
        clusters.append({
            "replicate": replicate,
            "weight": weight,
            "recovery": recovery,
            "cluster_value": sum(row["value"] for row in rows),
            "personality_values": {
                row["personality"]: row["value"] for row in sorted(rows, key=lambda item: item["personality"])
            },
            "turns_played": sum(row["turns_played"] for row in rows),
            "cell_ids": sorted(row["cell_id"] for row in rows),
        })
    return clusters


def _bootstrap_ci(
    values: list[float], *, samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    lo = means[int(0.025 * (samples - 1))]
    hi = means[int(0.975 * (samples - 1))]
    return [lo, hi]


def _sign_flip_p(values: list[float]) -> float | None:
    nonzero = [value for value in values if value != 0]
    if len(values) < 5:
        return None
    if not nonzero:
        return 1.0
    exact_values = [Fraction(str(value)) for value in nonzero]
    observed = abs(sum(exact_values))
    distribution = Counter({Fraction(0): 1})
    for value in exact_values:
        updated: Counter[Fraction] = Counter()
        for partial_sum, count in distribution.items():
            updated[partial_sum + value] += count
            updated[partial_sum - value] += count
        distribution = updated
    extreme = sum(count for statistic, count in distribution.items() if abs(statistic) >= observed)
    return extreme / (2 ** len(exact_values))


def summarize_effect(
    *,
    name: str,
    definition: str,
    deltas: list[float],
    analysis_role: str,
    sampling_phase: str,
    comparisons: int | None,
) -> dict[str, Any]:
    p_value = _sign_flip_p(deltas) if analysis_role == "primary" else None
    return {
        "name": name,
        "estimand_definition": definition,
        "analysis_role": analysis_role,
        "n_replicates": len(deltas),
        "paired_deltas": deltas,
        "mean_delta": statistics.fmean(deltas),
        "median_delta": statistics.median(deltas),
        "min_delta": min(deltas),
        "max_delta": max(deltas),
        "bootstrap_95pct_ci_mean": _bootstrap_ci(deltas),
        "exact_two_sided_sign_flip_p": p_value,
        "bonferroni_comparisons": comparisons,
        "bonferroni_adjusted_p": (
            min(1.0, p_value * comparisons)
            if p_value is not None and comparisons is not None else None
        ),
        "inference_status": (
            "confirmatory_preregistered"
            if sampling_phase == "confirmatory" else "preliminary_only"
        ),
    }


def _replicate_deltas(
    by_arm: dict[tuple[int, str, bool], float],
    replicates: list[int],
    fn: Any,
) -> list[float]:
    return [float(fn(replicate, by_arm)) for replicate in replicates]


def build_analysis(plan: ExperimentPlan, metric: str | None = None) -> dict[str, Any]:
    validate_completed_inventory(plan)
    metric = metric or plan.primary_metric
    if metric != plan.primary_metric:
        raise ManifestError(
            f"analysis metric {metric!r} does not match preregistered primary metric "
            f"{plan.primary_metric!r}"
        )
    rows = [load_cell_metric(plan, cell, metric) for cell in plan.cells]
    git_shas = sorted({row["git_sha"] for row in rows})
    if len(git_shas) != 1:
        raise ManifestError(f"factorial cells span multiple source commits: {git_shas}")
    clusters = cluster_rows(rows)
    by_arm = {
        (row["replicate"], row["weight"], row["recovery"]): row["cluster_value"]
        for row in clusters
    }
    replicates = sorted({row["replicate"] for row in clusters})
    comparisons = len(plan.primary_estimands)

    def arm(replicate: int, weight: str, recovery: bool) -> float:
        return by_arm[(replicate, weight, recovery)]

    primary_specs = {
        "r2_minus_base_recovery_off": (
            "E[Y(r2, recovery=off) - Y(base, recovery=off)]",
            lambda r, _b: arm(r, "r2", False) - arm(r, "base", False),
        ),
        "r3_minus_base_recovery_off": (
            "E[Y(r3, recovery=off) - Y(base, recovery=off)]",
            lambda r, _b: arm(r, "r3", False) - arm(r, "base", False),
        ),
        "recovery_on_minus_off_base": (
            "E[Y(base, recovery=on) - Y(base, recovery=off)]",
            lambda r, _b: arm(r, "base", True) - arm(r, "base", False),
        ),
        "recovery_on_minus_off_r2": (
            "E[Y(r2, recovery=on) - Y(r2, recovery=off)]",
            lambda r, _b: arm(r, "r2", True) - arm(r, "r2", False),
        ),
        "recovery_on_minus_off_r3": (
            "E[Y(r3, recovery=on) - Y(r3, recovery=off)]",
            lambda r, _b: arm(r, "r3", True) - arm(r, "r3", False),
        ),
        "r2_minus_base_recovery_interaction": (
            "E[(Y(r2,on)-Y(base,on)) - (Y(r2,off)-Y(base,off))]",
            lambda r, _b: (
                arm(r, "r2", True) - arm(r, "base", True)
                - arm(r, "r2", False) + arm(r, "base", False)
            ),
        ),
        "r3_minus_base_recovery_interaction": (
            "E[(Y(r3,on)-Y(base,on)) - (Y(r3,off)-Y(base,off))]",
            lambda r, _b: (
                arm(r, "r3", True) - arm(r, "base", True)
                - arm(r, "r3", False) + arm(r, "base", False)
            ),
        ),
    }
    primary_effects = [
        summarize_effect(
            name=name,
            definition=primary_specs[name][0],
            deltas=_replicate_deltas(by_arm, replicates, primary_specs[name][1]),
            analysis_role="primary",
            sampling_phase=plan.sampling_phase,
            comparisons=comparisons,
        )
        for name in plan.primary_estimands
    ]

    factorial_specs = (
        (
            "recovery_main_effect",
            "E_w[Y(w,on)-Y(w,off)], equally weighted over base, r2, and r3",
            lambda r, _b: statistics.fmean(
                arm(r, weight, True) - arm(r, weight, False)
                for weight in ("base", "r2", "r3")
            ),
        ),
        (
            "r2_minus_base_main_effect",
            "E_recovery[Y(r2,recovery)-Y(base,recovery)], equally weighted over off/on",
            lambda r, _b: statistics.fmean(
                arm(r, "r2", recovery) - arm(r, "base", recovery)
                for recovery in (False, True)
            ),
        ),
        (
            "r3_minus_base_main_effect",
            "E_recovery[Y(r3,recovery)-Y(base,recovery)], equally weighted over off/on",
            lambda r, _b: statistics.fmean(
                arm(r, "r3", recovery) - arm(r, "base", recovery)
                for recovery in (False, True)
            ),
        ),
        (
            "r3_minus_r2_main_effect",
            "E_recovery[Y(r3,recovery)-Y(r2,recovery)], equally weighted over off/on",
            lambda r, _b: statistics.fmean(
                arm(r, "r3", recovery) - arm(r, "r2", recovery)
                for recovery in (False, True)
            ),
        ),
    )
    factorial_main_effects = [
        summarize_effect(
            name=name,
            definition=definition,
            deltas=_replicate_deltas(by_arm, replicates, fn),
            analysis_role="secondary_factorial",
            sampling_phase=plan.sampling_phase,
            comparisons=None,
        )
        for name, definition, fn in factorial_specs
    ]

    secondary_specs = (
        (
            "r2_minus_base_recovery_on",
            "E[Y(r2,recovery=on)-Y(base,recovery=on)]",
            lambda r, _b: arm(r, "r2", True) - arm(r, "base", True),
        ),
        (
            "r3_minus_base_recovery_on",
            "E[Y(r3,recovery=on)-Y(base,recovery=on)]",
            lambda r, _b: arm(r, "r3", True) - arm(r, "base", True),
        ),
        (
            "r3_minus_r2_recovery_off",
            "E[Y(r3,recovery=off)-Y(r2,recovery=off)]",
            lambda r, _b: arm(r, "r3", False) - arm(r, "r2", False),
        ),
        (
            "r3_minus_r2_recovery_on",
            "E[Y(r3,recovery=on)-Y(r2,recovery=on)]",
            lambda r, _b: arm(r, "r3", True) - arm(r, "r2", True),
        ),
        (
            "r3_minus_r2_recovery_interaction",
            "E[(Y(r3,on)-Y(r2,on))-(Y(r3,off)-Y(r2,off))]",
            lambda r, _b: (
                arm(r, "r3", True) - arm(r, "r2", True)
                - arm(r, "r3", False) + arm(r, "r2", False)
            ),
        ),
    )
    secondary_effects = [
        summarize_effect(
            name=name,
            definition=definition,
            deltas=_replicate_deltas(by_arm, replicates, fn),
            analysis_role="secondary_simple_effect",
            sampling_phase=plan.sampling_phase,
            comparisons=None,
        )
        for name, definition, fn in secondary_specs
    ]

    randomization_fields = (
        "schedule_algorithm", "schedule_seed", "inference_seeds",
        "environment_seed_mechanism", "environment_seed", "environment_seed_reason",
    )
    randomization = {
        field: getattr(plan, field)
        for field in randomization_fields
        if hasattr(plan, field)
    }
    return {
        "schema_version": "kaetram-opd-factorial-analysis-v2",
        "experiment_id": plan.experiment_id,
        "manifest": plan.manifest,
        "manifest_sha256": _sha256(Path(plan.manifest)),
        "metric": metric,
        "preregistered_primary_metric": plan.primary_metric,
        "independent_unit": "DB-reset replicate",
        "personality_handling": "summed within replicate x weight x recovery arm",
        "inference_scope": {
            "population": "evaluation seeds and fresh-world trajectories",
            "checkpoint_treatment": "three fixed registered checkpoint artifacts",
            "conditional_on_fixed_checkpoints": True,
            "excluded_uncertainty": [
                "training-procedure variance",
                "training-seed variance",
            ],
        },
        "n_replicates": len(replicates),
        "n_cells": len(rows),
        "n_cluster_arms": len(clusters),
        "source_git_sha": git_shas[0],
        "sample_size_contract": {
            "phase": plan.sampling_phase,
            "planned_replicates": len(replicates),
            "target_power": plan.target_power,
            "confirmatory_replicates": plan.confirmatory_replicates,
            "power_analysis_artifact": plan.power_analysis_artifact,
            "power_analysis_sha256": plan.power_analysis_sha256,
            "status": (
                "power_preregistered_confirmatory"
                if plan.sampling_phase == "confirmatory" else "pilot_preliminary"
            ),
        },
        "multiple_comparisons": {
            "family": "seven preregistered primary estimands",
            "count": comparisons,
            "adjustment": "Bonferroni",
            "familywise_alpha": plan.familywise_alpha,
            "per_comparison_alpha": plan.familywise_alpha / comparisons,
        },
        "bootstrap": {
            "method": "percentile bootstrap of paired mean delta",
            "samples": DEFAULT_BOOTSTRAP_SAMPLES,
            "seed": DEFAULT_BOOTSTRAP_SEED,
        },
        "primary_estimands": primary_effects,
        "factorial_main_effects": factorial_main_effects,
        "secondary_simple_effects": secondary_effects,
        "effects": primary_effects + factorial_main_effects + secondary_effects,
        "randomization_provenance": randomization or {
            "status": "not_available_in_launcher_manifest_schema"
        },
        "clusters": clusters,
        "cells": rows,
    }


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise ManifestError(f"refusing to overwrite analysis artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_cluster_csv(path: Path, analysis: dict[str, Any]) -> None:
    if path.exists():
        raise ManifestError(f"refusing to overwrite analysis artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("replicate", "weight", "recovery", "cluster_value", "turns_played", "cell_ids"),
        )
        writer.writeheader()
        for row in analysis["clusters"]:
            writer.writerow({
                **{key: row[key] for key in ("replicate", "weight", "recovery", "cluster_value", "turns_played")},
                "cell_ids": ";".join(row["cell_ids"]),
            })


def _cluster_csv_text(analysis: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=("replicate", "weight", "recovery", "cluster_value", "turns_played", "cell_ids"),
    )
    writer.writeheader()
    for row in analysis["clusters"]:
        writer.writerow({
            **{key: row[key] for key in ("replicate", "weight", "recovery", "cluster_value", "turns_played")},
            "cell_ids": ";".join(row["cell_ids"]),
        })
    return output.getvalue()


def publish_analysis_artifacts(
    json_path: Path,
    csv_path: Path,
    analysis: dict[str, Any],
) -> None:
    """Stage both outputs, then publish create-only with rollback on failure."""
    targets = (
        (json_path, json.dumps(analysis, indent=2, sort_keys=True, allow_nan=False) + "\n"),
        (csv_path, _cluster_csv_text(analysis)),
    )
    existing = [str(path) for path, _content in targets if path.exists()]
    if existing:
        raise ManifestError(
            "refusing to overwrite analysis artifact(s): " + ", ".join(existing)
        )

    staged: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((temporary, target))

        for temporary, target in staged:
            os.link(temporary, target)
            published.append(target)
        for directory in {target.parent for _temporary, target in staged}:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, ValueError) as exc:
        for target in reversed(published):
            target.unlink(missing_ok=True)
        raise ManifestError(f"failed to publish complete analysis artifact pair: {exc}") from exc
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--metric",
        help="optional assertion; must equal analysis.primary_metric in the manifest",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clusters-csv", type=Path, required=True)
    args = parser.parse_args()
    try:
        analysis = build_analysis(build_plan(args.manifest), args.metric)
        publish_analysis_artifacts(args.out, args.clusters_csv, analysis)
    except ManifestError as exc:
        parser.error(str(exc))
    print(args.out)
    print(args.clusters_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
