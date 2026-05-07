#!/usr/bin/env python3
"""
eval_compare.py — Statistical comparison of eval harness results.

Reads results JSON files from eval_harness.py, computes bootstrap CIs,
Welch's t-test, Glass's delta, and Bonferroni correction. Outputs a
paper-ready markdown comparison table.

Usage:
    # Compare two models
    python3 eval_compare.py dataset/eval/base/results.json dataset/eval/r9-sft/results.json

    # Compare three models (base vs SFT vs KTO)
    python3 eval_compare.py dataset/eval/base/results.json \
        dataset/eval/r9-sft/results.json \
        dataset/eval/r8-kto/results.json

    # Save to file
    python3 eval_compare.py *.json --output eval_table.md

Statistical methodology (from reference/EVALS.md):
  - Bootstrap CIs: 1000 resamples, 95% percentile intervals
  - Significance: Welch's t-test (unequal variance, unequal N)
  - Effect size: Glass's delta (base model SD as denominator)
  - Multiple comparisons: Bonferroni correction for Tier 1 metrics
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Tier 1 metrics (paper table — Bonferroni corrected)
# ---------------------------------------------------------------------------

TIER1_METRICS = [
    ("core3_stages_advanced", "Core 3 Stages (out of 10)", "higher"),
    ("tool_parse_rate", "Tool Parse Rate", "higher"),
    ("quest_completion_rate", "Quest Completion Rate", "higher"),
    ("xp_per_turn", "XP per Turn (est)", "higher"),
    ("survival_rate", "Survival Rate", "higher"),
    ("deaths_per_session", "Deaths per Session", "lower"),
]

# Family-wise α for Bonferroni-corrected significance over Tier 1.
# Per-test threshold = FWER_ALPHA / (len(TIER1_METRICS) * n_model_pairs).
FWER_ALPHA = 0.05

# Tier 2 metrics (diagnostic/appendix — no Bonferroni)
TIER2_METRICS = [
    ("kills", "Kills", "higher"),
    ("xp_estimated", "XP Estimated", "higher"),
    ("level_reached", "Level Reached", "higher"),
    ("level_delta", "Level Delta", "higher"),
    ("action_entropy", "Action Entropy", "higher"),
    ("stuck_resets", "Stuck Resets", "lower"),
    ("click_tiles", "Click Tiles", "lower"),
    ("success_rate", "Scenario Success Rate", "higher"),
]


# ---------------------------------------------------------------------------
# Statistical functions
# ---------------------------------------------------------------------------

def bootstrap_ci(values: list[float], n_resamples: int = 1000,
                 ci: float = 0.95, seed: int = 42) -> tuple[float, float, float]:
    """Bootstrap mean with percentile confidence interval.

    Returns (mean, ci_low, ci_high).
    """
    if not values:
        return (0.0, 0.0, 0.0)

    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    alpha = (1 - ci) / 2
    lo_idx = max(0, int(alpha * n_resamples))
    hi_idx = min(n_resamples - 1, int((1 - alpha) * n_resamples))

    return (
        sum(values) / n,
        means[lo_idx],
        means[hi_idx],
    )


def welch_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    """Welch's t-test for unequal variances. Returns (t_statistic, p_value).

    Uses the two-tailed t-distribution approximation.
    Does not require scipy — implements the t-distribution CDF via
    regularized incomplete beta function approximation.
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return (0.0, 1.0)

    mean_a = sum(a) / na
    mean_b = sum(b) / nb
    var_a = sum((x - mean_a) ** 2 for x in a) / (na - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (nb - 1)

    se = math.sqrt(var_a / na + var_b / nb)
    if se < 1e-12:
        # Identical distributions
        return (0.0, 1.0)

    t = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / na + var_b / nb) ** 2
    denom = (var_a / na) ** 2 / (na - 1) + (var_b / nb) ** 2 / (nb - 1)
    if denom < 1e-12:
        df = na + nb - 2
    else:
        df = num / denom

    # Two-tailed p-value using t-distribution CDF approximation
    p = _t_survival(abs(t), df) * 2
    return (round(t, 4), min(1.0, p))


def _t_survival(t: float, df: float) -> float:
    """Upper-tail probability of the t-distribution.

    Uses the regularized incomplete beta function relationship:
    P(T > t) = 0.5 * I_{df/(df+t^2)}(df/2, 1/2)

    Approximated via continued fraction expansion of the incomplete beta.
    """
    if df <= 0:
        return 0.5
    x = df / (df + t * t)
    a, b = df / 2.0, 0.5
    return 0.5 * _regularized_beta(x, a, b)


def _regularized_beta(x: float, a: float, b: float, max_iter: int = 200) -> float:
    """Regularized incomplete beta function I_x(a, b) via Lentz's continued fraction."""
    if x < 0 or x > 1:
        return 0.0
    if x == 0:
        return 0.0
    if x == 1:
        return 1.0

    # Use the symmetry relation if x > (a+1)/(a+b+2)
    if x > (a + 1) / (a + b + 2):
        return 1.0 - _regularized_beta(1 - x, b, a, max_iter)

    # Lentz's algorithm for continued fraction
    # Beta CF: I_x(a,b) = x^a * (1-x)^b / (a * B(a,b)) * CF
    log_prefix = a * math.log(x) + b * math.log(1 - x) - _log_beta(a, b) - math.log(a)
    if log_prefix < -500:
        return 0.0
    prefix = math.exp(log_prefix)

    # Evaluate CF using modified Lentz's method
    tiny = 1e-30
    f = tiny
    c = tiny
    d = 1.0 - (a + b) * x / (a + 1)
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    f = d

    for m in range(1, max_iter + 1):
        # Even step: d_{2m}
        num = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + num * d
        if abs(d) < tiny:
            d = tiny
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < tiny:
            c = tiny
        f *= c * d

        # Odd step: d_{2m+1}
        num = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        if abs(d) < tiny:
            d = tiny
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < tiny:
            c = tiny
        delta = c * d
        f *= delta

        if abs(delta - 1.0) < 1e-10:
            break

    result = prefix * f * a
    return max(0.0, min(1.0, result))


def _log_beta(a: float, b: float) -> float:
    """Log of the beta function using lgamma."""
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def glass_delta(treatment: list[float], control: list[float]) -> float:
    """Glass's delta: effect size using control group SD as denominator.

    Glass's delta is preferred over Cohen's d when comparing a trained model
    to a baseline — the baseline variability is the natural reference point.
    """
    if len(control) < 2:
        return 0.0

    mean_t = sum(treatment) / len(treatment) if treatment else 0
    mean_c = sum(control) / len(control) if control else 0
    sd_c = math.sqrt(sum((x - mean_c) ** 2 for x in control) / (len(control) - 1))

    if sd_c < 1e-12:
        # Zero variance in control — return large effect if means differ
        return 10.0 if abs(mean_t - mean_c) > 1e-6 else 0.0

    return (mean_t - mean_c) / sd_c


def bonferroni_correct(p_values: list[float], n_comparisons: int | None = None) -> list[float]:
    """Bonferroni correction for multiple comparisons."""
    n = n_comparisons or len(p_values)
    return [min(1.0, p * n) for p in p_values]


# ---------------------------------------------------------------------------
# Comparison engine
# ---------------------------------------------------------------------------

def load_results(path: str) -> dict:
    """Load eval_harness.py results JSON."""
    with open(path) as f:
        data = json.load(f)
    if "metrics" not in data or "meta" not in data:
        raise ValueError(f"Invalid results file (missing 'metrics' or 'meta'): {path}")
    return data


def compare_metric(
    metric_key: str,
    base_values: list[float],
    treatment_values: list[float],
    direction: str,
) -> dict:
    """Compare one metric between base and treatment models."""
    base_mean, base_lo, base_hi = bootstrap_ci(base_values)
    treat_mean, treat_lo, treat_hi = bootstrap_ci(treatment_values)
    t_stat, p_value = welch_t_test(treatment_values, base_values)
    g_delta = glass_delta(treatment_values, base_values)

    # For "lower is better" metrics, flip the sign of Glass's delta for interpretation
    # (positive delta = improvement)
    display_delta = -g_delta if direction == "lower" else g_delta

    return {
        "metric": metric_key,
        "base_mean": round(base_mean, 4),
        "base_ci": (round(base_lo, 4), round(base_hi, 4)),
        "base_n": len(base_values),
        "treatment_mean": round(treat_mean, 4),
        "treatment_ci": (round(treat_lo, 4), round(treat_hi, 4)),
        "treatment_n": len(treatment_values),
        "t_statistic": t_stat,
        "p_value": round(p_value, 6),
        "glass_delta": round(g_delta, 4),
        "display_delta": round(display_delta, 4),
        "direction": direction,
    }


def compare_models(
    base_data: dict,
    treatment_data: dict,
    n_model_pairs: int = 1,
) -> dict:
    """Full statistical comparison between two models.

    `n_model_pairs` controls the Bonferroni family size: when this comparison
    is one of K pairwise comparisons across N models (K = N*(N-1)/2), pass
    n_model_pairs=K so the corrected p-values share family-wise α across
    metrics × pairs. Default 1 keeps backward-compat for two-model invocations.
    """
    base_metrics = base_data["metrics"]
    treat_metrics = treatment_data["metrics"]

    # Tier 1 comparisons (with Bonferroni)
    tier1_results = []
    tier1_p_values = []
    for key, name, direction in TIER1_METRICS:
        bv = base_metrics.get(key, [])
        tv = treat_metrics.get(key, [])
        if not bv and not tv:
            continue
        result = compare_metric(key, bv, tv, direction)
        result["display_name"] = name
        result["tier"] = 1
        tier1_results.append(result)
        tier1_p_values.append(result["p_value"])

    # Apply Bonferroni correction to Tier 1 across (metrics × model pairs).
    # FWER target is FWER_ALPHA across the full family — single-test threshold
    # follows from corrected_p = p * (n_metrics * n_model_pairs).
    family_size = max(1, len(TIER1_METRICS) * n_model_pairs)
    corrected_p = bonferroni_correct(tier1_p_values, n_comparisons=family_size)
    for i, result in enumerate(tier1_results):
        result["p_corrected"] = round(corrected_p[i], 6)
        result["significant"] = corrected_p[i] < FWER_ALPHA
    family_meta = {
        "n_metrics": len(TIER1_METRICS),
        "n_model_pairs": n_model_pairs,
        "family_size": family_size,
        "fwer_alpha": FWER_ALPHA,
    }

    # Tier 2 comparisons (no Bonferroni)
    tier2_results = []
    for key, name, direction in TIER2_METRICS:
        bv = base_metrics.get(key, [])
        tv = treat_metrics.get(key, [])
        if not bv and not tv:
            continue
        result = compare_metric(key, bv, tv, direction)
        result["display_name"] = name
        result["tier"] = 2
        result["p_corrected"] = result["p_value"]  # no correction
        result["significant"] = result["p_value"] < 0.05
        tier2_results.append(result)

    return {
        "base_model": base_data["meta"]["model"],
        "treatment_model": treatment_data["meta"]["model"],
        "base_episodes": base_data["meta"]["ok_episodes"],
        "treatment_episodes": treatment_data["meta"]["ok_episodes"],
        "scenario": base_data["meta"].get("scenario", "?"),
        "tier1": tier1_results,
        "tier2": tier2_results,
        "bonferroni": family_meta,
    }


def compare_n_models(models: dict[str, dict]) -> dict:
    """Run all pairwise comparisons across N models and share Bonferroni FWER.

    `models` is `{model_name: results_dict}` (each results_dict is the JSON
    written by eval_harness.py). Returns a flat dict with one entry per
    ordered pair `"<base>_vs_<treatment>"` plus a `_pairs` index list.

    With 3 models there are 3 pairs (e.g. base vs r9, base vs r10, r9 vs r10);
    each compare_models() call gets n_model_pairs=3 so the per-test α is
    FWER_ALPHA / (n_metrics × 3) — the correct family-wise threshold.
    """
    names = list(models.keys())
    n = len(names)
    if n < 2:
        raise ValueError(f"need >=2 models, got {n}")

    pairs: list[tuple[str, str]] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((names[i], names[j]))

    out: dict[str, object] = {
        "_pairs": [f"{a}_vs_{b}" for a, b in pairs],
        "_n_models": n,
        "_n_pairs": len(pairs),
        "_fwer_alpha": FWER_ALPHA,
    }
    for a, b in pairs:
        out[f"{a}_vs_{b}"] = compare_models(
            models[a], models[b], n_model_pairs=len(pairs)
        )
    return out


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _fmt_ci(mean: float, ci: tuple[float, float]) -> str:
    """Format mean with CI: '0.95 [0.92, 0.97]'"""
    return f"{mean:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]"


def _fmt_p(p: float) -> str:
    """Format p-value with significance stars."""
    if p < 0.001:
        return "<0.001***"
    elif p < 0.01:
        return f"{p:.3f}**"
    elif p < 0.05:
        return f"{p:.3f}*"
    return f"{p:.3f}"


def _fmt_delta(d: float) -> str:
    """Format Glass's delta with effect size label."""
    ad = abs(d)
    if ad < 0.2:
        label = "negligible"
    elif ad < 0.5:
        label = "small"
    elif ad < 0.8:
        label = "medium"
    else:
        label = "large"
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.2f} ({label})"


def format_markdown_table(comparison: dict) -> str:
    """Format comparison results as a paper-ready markdown table."""
    base = comparison["base_model"]
    treat = comparison["treatment_model"]
    n_base = comparison["base_episodes"]
    n_treat = comparison["treatment_episodes"]

    lines = []
    lines.append(f"## {treat} vs {base} — Scenario {comparison['scenario']}")
    lines.append("")
    lines.append(f"### Tier 1 Metrics (Bonferroni-corrected, alpha=0.01)")
    lines.append("")
    lines.append(f"| Metric | {base} (N={n_base}) | {treat} (N={n_treat}) | Glass's d | p (corrected) |")
    lines.append("|--------|" + "-" * 20 + "|" + "-" * 20 + "|-----------|---------------|")

    for r in comparison["tier1"]:
        lines.append(
            f"| {r['display_name']} | "
            f"{_fmt_ci(r['base_mean'], r['base_ci'])} | "
            f"{_fmt_ci(r['treatment_mean'], r['treatment_ci'])} | "
            f"{_fmt_delta(r['display_delta'])} | "
            f"{_fmt_p(r['p_corrected'])} |"
        )

    lines.append("")
    lines.append(f"### Tier 2 Metrics (diagnostic, alpha=0.05)")
    lines.append("")
    lines.append(f"| Metric | {base} (N={n_base}) | {treat} (N={n_treat}) | Glass's d | p-value |")
    lines.append("|--------|" + "-" * 20 + "|" + "-" * 20 + "|-----------|---------|")

    for r in comparison["tier2"]:
        lines.append(
            f"| {r['display_name']} | "
            f"{_fmt_ci(r['base_mean'], r['base_ci'])} | "
            f"{_fmt_ci(r['treatment_mean'], r['treatment_ci'])} | "
            f"{_fmt_delta(r['display_delta'])} | "
            f"{_fmt_p(r['p_value'])} |"
        )

    # Effect size interpretation guide
    lines.append("")
    lines.append("**Effect sizes:** |d| < 0.2 = negligible, 0.2-0.5 = small, 0.5-0.8 = medium, > 0.8 = large")
    lines.append(f"**Significance:** *** p<0.001, ** p<0.01, * p<0.05 (Tier 1 uses Bonferroni with {len(TIER1_METRICS)} comparisons)")

    return "\n".join(lines)


def format_json(comparison: dict) -> str:
    """Format comparison as JSON for programmatic consumption."""
    return json.dumps(comparison, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Statistical comparison of eval harness results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The first results file is treated as the BASE (control) model.
All subsequent files are compared against the base.

Bonferroni correction is applied across (n_metrics × n_model_pairs):
  - Default mode: n_model_pairs = (n_results - 1)  [base vs each treatment]
  - --all-pairs: n_model_pairs = C(n_results, 2)   [every pair, both directions excluded]

Examples:
  python3 eval_compare.py dataset/eval/base/results.json dataset/eval/r9-sft/results.json
  python3 eval_compare.py base.json sft.json kto.json --output comparison.md
  python3 eval_compare.py base.json r9-sft.json r10-sft.json --all-pairs
        """,
    )
    parser.add_argument(
        "results", nargs="+", type=str,
        help="Results JSON files (first = base/control, rest = treatment)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save markdown table to file (default: print to stdout)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of markdown",
    )
    parser.add_argument(
        "--all-pairs", action="store_true",
        help="Compare every model pair (not just base-vs-each). "
             "Bonferroni FWER shared across C(N,2) comparisons.",
    )
    args = parser.parse_args()

    if len(args.results) < 2:
        print("Error: need at least 2 results files (base + treatment)")
        sys.exit(1)

    # Load all results once
    loaded = {}
    for path in args.results:
        data = load_results(path)
        name = data["meta"]["model"]
        loaded[name] = data
        print(f"Loaded: {name} ({data['meta']['ok_episodes']} episodes)")

    all_output = []

    if args.all_pairs:
        # Symmetric all-vs-all: use the dedicated wrapper so FWER is shared.
        n_pairs = len(loaded) * (len(loaded) - 1) // 2
        print(f"\nMode: --all-pairs ({n_pairs} comparisons, "
              f"Bonferroni family = {len(TIER1_METRICS)} metrics × {n_pairs} pairs "
              f"= {len(TIER1_METRICS) * n_pairs} tests)")
        n_model = compare_n_models(loaded)
        for pair_key in n_model["_pairs"]:
            comparison = n_model[pair_key]
            if args.json:
                all_output.append(format_json(comparison))
            else:
                all_output.append(format_markdown_table(comparison))
    else:
        # Base-vs-each: Bonferroni family = metrics × (n_results - 1).
        base_name = list(loaded.keys())[0]
        base_data = loaded[base_name]
        treatments = [name for name in loaded if name != base_name]
        n_pairs = max(1, len(treatments))
        print(f"\nMode: base-vs-each ({n_pairs} comparisons, "
              f"Bonferroni family = {len(TIER1_METRICS)} metrics × {n_pairs} pairs "
              f"= {len(TIER1_METRICS) * n_pairs} tests)")
        for treat_name in treatments:
            treat_data = loaded[treat_name]
            comparison = compare_models(base_data, treat_data, n_model_pairs=n_pairs)
            if args.json:
                all_output.append(format_json(comparison))
            else:
                all_output.append(format_markdown_table(comparison))

    output = "\n\n---\n\n".join(all_output)

    if args.output:
        Path(args.output).write_text(output)
        print(f"\nSaved to: {args.output}")
    else:
        print(f"\n{output}")


if __name__ == "__main__":
    main()
