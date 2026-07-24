#!/usr/bin/env python3
"""Render deterministic paper tables from a trigger-incidence analysis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
SEED_AUDIT_SCHEMA = "kaetram.local-trigger-incidence-seed-diversity-audit.v1"
SNAPSHOT_LABELS = {
    "base_2b": "Base",
    "opd_r2_2b": "Round 2",
    "opd_r3_2b": "Round 3",
}
EXPECTED_SNAPSHOTS = tuple(SNAPSHOT_LABELS)
CONDITION_COLUMNS = (
    ("python-docs_no-tools", "Python / none"),
    ("python-docs_native-tools", "Python / native"),
    ("canonical-docs_no-tools", "Canonical / none"),
    ("canonical-docs_native-tools", "Canonical / native"),
)
CONTRAST_COLUMNS = (
    ("native_tools_main", "Native schema"),
    ("canonical_docs_main", "Canonical docs"),
    ("interaction", "Interaction"),
)


class RenderError(RuntimeError):
    """Raised when a result cannot be rendered without inventing information."""


def _load_summary(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"cannot read analysis summary: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != ANALYSIS_SCHEMA:
        raise RenderError("unexpected trigger-incidence analysis schema")
    if value.get("analysis_status") != "complete":
        raise RenderError("paper tables require a complete analysis")
    if value.get("failed_requests") != 0:
        raise RenderError("paper tables require zero failed requests")
    return value


def _ordered_snapshots(summary: dict[str, Any]) -> list[str]:
    input_runs = summary.get("input_runs")
    if not isinstance(input_runs, list):
        raise RenderError("analysis summary has no input runs")
    snapshots = [item.get("snapshot") for item in input_runs if isinstance(item, dict)]
    if len(snapshots) != len(set(snapshots)) or not all(
        isinstance(item, str) and item for item in snapshots
    ):
        raise RenderError("analysis summary has invalid snapshot identities")
    if set(snapshots) != set(EXPECTED_SNAPSHOTS):
        raise RenderError("analysis summary does not contain the registered snapshots")
    return list(EXPECTED_SNAPSHOTS)


def _index_unique(
    records: Any,
    keys: tuple[str, ...],
    expected_count: int,
    label: str,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    if not isinstance(records, list):
        raise RenderError(f"analysis summary has no {label}")
    indexed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise RenderError(f"{label} must contain objects")
        key = tuple(record.get(name) for name in keys)
        if key in indexed:
            raise RenderError(f"duplicate {label} record: {key}")
        indexed[key] = record
    if len(indexed) != expected_count:
        raise RenderError(
            f"expected {expected_count} {label} records, found {len(indexed)}"
        )
    return indexed


def _cell_text(
    cell: dict[str, Any],
    collapsed: dict[str, Any] | None = None,
) -> str:
    if collapsed is None:
        count = cell.get("recovery_opportunities")
        successful = cell.get("successful_requests")
        rate = cell.get("opportunity_rate")
    else:
        count = collapsed.get("recovery_opportunity_states")
        successful = collapsed.get("state_outputs")
        rate = collapsed.get("opportunity_rate")
        if (
            collapsed.get("outcome_stable_states") != successful
            or rate != cell.get("opportunity_rate")
        ):
            raise RenderError("collapsed state cell differs from registered rate")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not isinstance(successful, int)
        or isinstance(successful, bool)
        or successful <= 0
        or not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or count < 0
        or count > successful
        or abs(rate - count / successful) > 1e-12
    ):
        raise RenderError("invalid trigger-incidence cell")
    return f"{count}/{successful} ({100 * rate:.1f}%)"


def _effect_text(contrast: dict[str, Any]) -> str:
    effect = contrast.get("effect_rate_difference")
    positive = contrast.get("states_positive")
    negative = contrast.get("states_negative")
    zero = contrast.get("states_zero")
    state_count = contrast.get("finite_grid_states")
    if (
        not isinstance(effect, (int, float))
        or isinstance(effect, bool)
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (positive, negative, zero, state_count)
        )
        or positive + negative + zero != state_count
    ):
        raise RenderError("invalid trigger-incidence contrast")
    return f"{100 * effect:+.1f} pp ({positive}/{negative}/{zero})"


def render_tables(
    summary: dict[str, Any],
    seed_audit: dict[str, Any] | None = None,
) -> tuple[str, str]:
    snapshots = _ordered_snapshots(summary)
    cells = _index_unique(
        summary.get("cells"),
        ("snapshot", "condition_id"),
        len(snapshots) * len(CONDITION_COLUMNS),
        "cell",
    )
    contrasts = _index_unique(
        summary.get("registered_contrasts"),
        ("snapshot", "contrast"),
        len(snapshots) * len(CONTRAST_COLUMNS),
        "contrast",
    )
    expected_cell_keys = {
        (snapshot, condition_id)
        for snapshot in snapshots
        for condition_id, _label in CONDITION_COLUMNS
    }
    expected_contrast_keys = {
        (snapshot, contrast_id)
        for snapshot in snapshots
        for contrast_id, _label in CONTRAST_COLUMNS
    }
    if set(cells) != expected_cell_keys:
        raise RenderError("cell identities do not match the registered grid")
    if set(contrasts) != expected_contrast_keys:
        raise RenderError("contrast identities do not match the registered grid")
    collapsed: dict[tuple[Any, ...], dict[str, Any]] = {}
    if seed_audit is not None:
        if (
            seed_audit.get("schema_version") != SEED_AUDIT_SCHEMA
            or seed_audit.get("study_id") != summary.get("study_id")
            or seed_audit.get("groups_with_multiple_semantic_responses") != 0
            or seed_audit.get("groups_with_identical_semantic_responses")
            != seed_audit.get("state_condition_groups")
        ):
            raise RenderError("seed-diversity audit does not support state collapse")
        collapsed = _index_unique(
            seed_audit.get("collapsed_cells"),
            ("snapshot", "condition_id"),
            len(expected_cell_keys),
            "collapsed cell",
        )
        if set(collapsed) != expected_cell_keys:
            raise RenderError("collapsed cell identities do not match the grid")

    markdown = [
        "### Recovery-opportunity incidence",
        "",
        "| Weights | Python / none | Python / native | Canonical / none | Canonical / native |",
        "|---|---:|---:|---:|---:|",
    ]
    for snapshot in snapshots:
        values = [
            _cell_text(
                cells[(snapshot, condition_id)],
                collapsed.get((snapshot, condition_id)),
            )
            for condition_id, _label in CONDITION_COLUMNS
        ]
        markdown.append(f"| {SNAPSHOT_LABELS.get(snapshot, snapshot)} | " + " | ".join(values) + " |")
    markdown.extend(
        [
            "",
            "### Registered finite-grid contrasts",
            "",
            "| Weights | Native schema | Canonical docs | Interaction |",
            "|---|---:|---:|---:|",
        ]
    )
    for snapshot in snapshots:
        values = [
            _effect_text(contrasts[(snapshot, contrast_id)])
            for contrast_id, _label in CONTRAST_COLUMNS
        ]
        markdown.append(f"| {SNAPSHOT_LABELS.get(snapshot, snapshot)} | " + " | ".join(values) + " |")
    markdown.extend(
        [
            "",
            (
                "Cells report opportunity states/state outputs (rate)."
                if collapsed
                else "Cells report opportunities/successful requests (rate)."
            ),
            "Contrasts are",
            "paired rate differences in percentage points; parenthetical values are",
            "states with positive/negative/zero effects. Descriptive fixed-grid results only.",
            "",
        ]
    )

    latex = [
        "% Deterministically rendered from analysis/analysis-summary.json.",
        "\\begin{table*}[t]",
        "\\centering",
        "\\small",
        "\\caption{Recovery-opportunity incidence on the registered 20-state",
        (
            "finite grid. Five nominal request seeds produced identical semantic"
            if collapsed
            else "finite grid. Cells show opportunities/successful requests (rate)."
        ),
        *(
            [
                "responses within every state--condition group, so cells collapse",
                "duplicates and show opportunity states/state outputs (rate).",
            ]
            if collapsed
            else []
        ),
        "Contrasts are paired rate differences in percentage points; $+/-/0$",
        "counts the states with positive, negative, or zero effects. Descriptive",
        "fixed-grid results only.}",
        "\\label{tab:trigger-incidence}",
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "Weights & Python / none & Python / native & Canonical / none & Canonical / native \\\\",
        "\\midrule",
    ]
    for snapshot in snapshots:
        values = [
            _cell_text(
                cells[(snapshot, condition_id)],
                collapsed.get((snapshot, condition_id)),
            ).replace("%", "\\%")
            for condition_id, _label in CONDITION_COLUMNS
        ]
        latex.append(f"{SNAPSHOT_LABELS.get(snapshot, snapshot)} & " + " & ".join(values) + " \\\\")
    latex.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "",
            "\\vspace{3pt}",
            "\\begin{tabular}{lrrr}",
            "\\toprule",
            "Weights & Native schema & Canonical docs & Interaction \\\\",
            "\\midrule",
        ]
    )
    for snapshot in snapshots:
        values = [
            _effect_text(contrasts[(snapshot, contrast_id)]).replace(" pp", "")
            for contrast_id, _label in CONTRAST_COLUMNS
        ]
        latex.append(f"{SNAPSHOT_LABELS.get(snapshot, snapshot)} & " + " & ".join(values) + " \\\\")
    latex.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{table*}",
            "",
        ]
    )
    return "\n".join(markdown), "\n".join(latex)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--seed-diversity-audit", type=Path)
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--latex-out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.markdown_out == args.latex_out:
        raise RenderError("Markdown and LaTeX outputs must differ")
    if args.markdown_out.exists() or args.latex_out.exists():
        raise RenderError("refusing to overwrite a rendered table")
    summary = _load_summary(args.summary)
    seed_audit = (
        json.loads(args.seed_diversity_audit.read_text())
        if args.seed_diversity_audit is not None
        else None
    )
    markdown, latex = render_tables(summary, seed_audit)
    outputs = (
        (args.markdown_out, markdown),
        (args.latex_out, latex),
    )
    created: list[Path] = []
    try:
        for path, content in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            created.append(path)
    except FileExistsError as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise RenderError("refusing to overwrite a rendered table") from exc
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
