"""Reproducible significance numbers for the r10 base-vs-SFT eval.

Re-derives the per-run Core-3 stage totals and Foresting completion counts
directly from the session logs (no hard-coded vectors), then runs:

  1. Per-run Core-3 stages: Mann-Whitney U, exact one-sided. Base scores are
     all 7 (ties), so scipy's default method='auto' falls back to the normal
     approximation; we request method='exact' explicitly. At n=4/3 with perfect
     separation the exact p is 1/C(7,3) = 0.029.

  2. Foresting completion (binary, per agent): Fisher's exact test, one-sided.
     Agents are clustered within runs, so this 2x2 treats agent-attempts as
     independent -- a secondary number with that caveat.

  3. Completionist tool-mix: training-target vs SFT-inference vs base-inference.

To extend to the n=5 base / n=5 SFT cap, add the new run IDs to BASE / SFT and
re-run. With perfect separation the exact Mann-Whitney floor drops to
1/C(10,5) = 0.004.

Run:  python3 scripts/r10_stats.py
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.log_analysis.parse import (  # noqa: E402
    list_agent_dirs, list_runs, parse_run_sessions, progression_for_quests,
)
from scripts.log_analysis.artifact_requirements import (  # noqa: E402
    MissingEvidenceError,
    require_agent_run_logs,
    require_files,
)

try:
    from scipy.stats import fisher_exact, mannwhitneyu
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

CORE3 = ["Foresting", "Herbalist's Desperation", "Rick's Roll"]  # max 3+3+4 = 10/agent
AGENTS = ["agent_0", "agent_1", "agent_2"]  # grinder, completionist, explorer
BASE = ["run_20260510_173852", "run_20260510_211339",
        "run_20260519_223921", "run_20260520_143530"]
SFT = ["run_20260520_014319", "run_20260520_044433", "run_20260520_173902"]
# Base ratio/percentage comparison uses the three 3h runs only (the 6h run
# inflates per-run counts); SFT runs are all 3h.
BASE_3H = ["run_20260510_173852", "run_20260519_223921", "run_20260520_143530"]
# Teacher-demonstration corpus = the 5 Claude source runs (per metadata.json).
CORPUS = ["run_20260504_140418", "run_20260504_172157", "run_20260504_221206",
          "run_20260505_150033", "run_20260505_214542"]
TOOLS = ["observe", "navigate", "interact_npc", "query_quest", "attack", "gather"]


def validate_inputs(raw_root: Path, train_json: Path):
    require_agent_run_logs(
        raw_root,
        agents=AGENTS,
        run_ids=[*BASE, *SFT],
        analysis="r10 base-vs-SFT statistics",
    )
    require_files(
        [train_json],
        analysis="r10 completionist tool-mix analysis",
    )


def run_stage_total_and_foresting(run_id, raw_root: Path):
    """Return (sum of Core-3 stages over the 3 agents, list of per-agent
    Foresting-completed bools) for one run, read from logs."""
    ads = {ad.name: ad for ad in list_agent_dirs(raw_root)}
    total, forest = 0, []
    for an in AGENTS:
        rd = [r for r in list_runs(ads[an]) if r.name == run_id]
        if not rd:
            forest.append(None)
            continue
        rv = parse_run_sessions(ads[an], rd[0])
        prog = progression_for_quests(rv, quest_names=CORE3)
        agent_total, fstage = 0, 0
        for q in CORE3:
            qp = prog.get(q)
            ms = qp.max_stage_reached if qp else 0
            agent_total += ms
            if q == "Foresting":
                fstage = ms
        total += agent_total
        forest.append(fstage >= 3)
    return total, forest


def completionist_eval_mix(run_ids, raw_root: Path):
    """% of each tool among the completionist (agent_1) eval tool calls."""
    import collections
    ads = {ad.name: ad for ad in list_agent_dirs(raw_root)}
    c = collections.Counter()
    for rd in list_runs(ads["agent_1"]):
        if rd.name not in run_ids:
            continue
        rv = parse_run_sessions(ads["agent_1"], rd)
        for sv in rv.sessions:
            for tc in sv.tool_calls:
                c[tc.short_name] += 1
    tot = sum(c.values()) or 1
    return {t: 100 * c[t] / tot for t in TOOLS}, c, tot


def completionist_train_target_mix(path: str | Path = "dataset/qwen_sft/train.json"):
    """% of each tool among the completionist training TARGET actions
    (assistant tool calls in the SFT records) -- what the model was trained
    to emit. This is the corpus column in the tool-mix table."""
    import json
    import collections
    c = collections.Counter()
    with Path(path).open(encoding="utf-8") as handle:
        records = json.load(handle)
    for r in records:
        if r.get("personality") != "completionist":
            continue
        for m in r["messages"]:
            if m["role"] == "assistant":
                for tc in m.get("tool_calls", []):
                    c[tc["function"]["name"]] += 1
    tot = sum(c.values()) or 1
    return {t: 100 * c[t] / tot for t in TOOLS}


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("dataset/raw"),
        help="raw agent-log root (default: dataset/raw)",
    )
    parser.add_argument(
        "--train-json",
        type=Path,
        default=Path("dataset/qwen_sft/train.json"),
        help="r10 SFT training records used for tool-mix analysis",
    )
    args = parser.parse_args(argv)

    try:
        validate_inputs(args.raw_root, args.train_json)
    except MissingEvidenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    base_stage = [run_stage_total_and_foresting(r, args.raw_root)[0] for r in BASE]
    sft_stage = [run_stage_total_and_foresting(r, args.raw_root)[0] for r in SFT]
    base_f = [
        result
        for run_id in BASE
        for result in run_stage_total_and_foresting(run_id, args.raw_root)[1]
        if result is not None
    ]
    sft_f = [
        result
        for run_id in SFT
        for result in run_stage_total_and_foresting(run_id, args.raw_root)[1]
        if result is not None
    ]

    print(f"Per-run Core-3 stages  base (n={len(base_stage)}): {base_stage}")
    print(f"Per-run Core-3 stages  sft  (n={len(sft_stage)}): {sft_stage}")
    print(f"  base mean={sum(base_stage)/len(base_stage):.2f}  "
          f"sft mean={sum(sft_stage)/len(sft_stage):.2f}  "
          f"ratio={ (sum(base_stage)/len(base_stage)) / (sum(sft_stage)/len(sft_stage)):.2f}x")

    if HAVE_SCIPY:
        u_exact = mannwhitneyu(base_stage, sft_stage, alternative="greater", method="exact")
        u_asym = mannwhitneyu(base_stage, sft_stage, alternative="greater", method="asymptotic")
        print(f"\nMann-Whitney U (one-sided): exact p = {u_exact.pvalue:.4f}   "
              f"(asymptotic/normal-approx p = {u_asym.pvalue:.4f}; ties trigger the "
              f"approximation under method='auto')")

    bc, bi = sum(base_f), len(base_f) - sum(base_f)
    sc, si = sum(sft_f), len(sft_f) - sum(sft_f)
    print(f"\nForesting completion   base: {bc}/{len(base_f)} ({100*bc/len(base_f):.0f}%)   "
          f"sft: {sc}/{len(sft_f)} ({100*sc/len(sft_f):.0f}%)")
    if HAVE_SCIPY:
        odds, p_f = fisher_exact([[bc, bi], [sc, si]], alternative="greater")
        print(f"  Fisher exact (one-sided): p = {p_f:.4f}   OR = {odds:.1f}")
        if sc > 0:
            print(f"  completion-rate drop: {(bc/len(base_f)) / (sc/len(sft_f)):.1f}x")

    # --- Tool-mix fingerprint (completionist) ---
    corpus = completionist_train_target_mix(args.train_json)
    sft_mix, _, _ = completionist_eval_mix(SFT, args.raw_root)
    base_mix, _, _ = completionist_eval_mix(BASE_3H, args.raw_root)
    print("\nCompletionist tool-mix (% of tool calls):")
    print(f"  {'tool':<14}{'corpus':>8}{'SFT':>8}{'base3h':>8}{'|SFT-corpus|':>14}")
    for t in TOOLS:
        d = abs(sft_mix[t] - corpus[t])
        print(f"  {t:<14}{corpus[t]:>7.1f}%{sft_mix[t]:>7.1f}%{base_mix[t]:>7.1f}%{d:>13.1f}")
    print("  (corpus = completionist training-target distribution; SFT inference"
          " tracks it within ~1pp on interact_npc/query_quest/navigate, NOT observe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
