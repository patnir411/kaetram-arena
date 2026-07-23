"""Run-level statistics for every eval arm (r10 era + OPD rounds + hardening arms).

Generalizes scripts/r10_stats.py to the whole program. Everything is re-derived
from session logs (no hard-coded score vectors). The run is the unit of
inference; agents within a run are prompt-variant clusters, not independent
seeds — agent-level tests are printed with that caveat, run-level tests are
primary. Verification mode reproduces the published r10 numbers (Mann-Whitney
exact p=0.029, Fisher Foresting p=0.016) before any new-arm claims are made.

Outputs:
  1. RAW TABLE — every run: arm x agent x per-quest max stage (+ per-run total).
  2. Herbalist WALL passage (stage >= 2, i.e. past the Foraging-5 lily gate):
     per-arm agent-level counts + Fisher exact for pre-registered contrasts.
  3. Stage totals: exact one-sided Mann-Whitney for pre-registered arm pairs.
  4. Monotone trend across ordered OPD arms (permutation test on per-agent
     stage totals over base -> r1 -> r2 -> r3).
  5. Hierarchical bootstrap CI (runs -> agents -> quest stages) per arm —
     descriptive, METR-style; degenerate axes (1 run) resample the lower levels.

Run:  python3 scripts/arm_stats.py            # full report
      python3 scripts/arm_stats.py --verify   # r10 reproduction only
"""
from __future__ import annotations

import argparse
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "log_analysis"))
from parse import (  # noqa: E402
    list_agent_dirs, list_runs, parse_run_sessions, progression_for_quests,
)

try:
    from scipy.stats import fisher_exact, mannwhitneyu
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

CORE3 = ["Foresting", "Herbalist's Desperation", "Rick's Roll"]
AGENTS = ["agent_0", "agent_1", "agent_2"]  # grinder, completionist, explorer
PERSONA = {"agent_0": "grinder", "agent_1": "completionist", "agent_2": "explorer"}

# ---------------------------------------------------------------------------
# Arm registry. Append new runs as they land (E1/E4/E3' etc.). Duration noted
# because stage totals are duration-sensitive across eras; all OPD-block arms
# are 6h and mutually comparable.
# ---------------------------------------------------------------------------
ARMS: dict[str, dict] = {
    # r10 era (pre-R11 harness, 9B) — the powered negative result.
    "r10-base-9B": {"runs": ["run_20260510_173852", "run_20260510_211339",
                             "run_20260519_223921", "run_20260520_143530"],
                    "block": "r10", "dur": "3-6h"},
    "r10-sft-9B": {"runs": ["run_20260520_014319", "run_20260520_044433",
                            "run_20260520_173902"],
                   "block": "r10", "dur": "3h"},
    # R11 OPD block (identical scaffold; 6h; 2B unless noted).
    "base-2B": {"runs": ["run_20260608_185339"], "block": "opd", "dur": "6h"},
    "opd-r1": {"runs": ["run_20260610_140358"], "block": "opd", "dur": "6h"},
    "opd-r2": {"runs": ["run_20260612_044933"], "block": "opd", "dur": "6h"},
    "opd-r2+rec": {"runs": ["run_20260613_214956"], "block": "opd", "dur": "6h"},
    "opd-r3+rec": {"runs": ["run_20260613_112422"], "block": "opd", "dur": "6h"},
    "4B-teacher": {"runs": ["run_20260607_190204"], "block": "opd", "dur": "5h"},
    # Hardening arms (July 2026) — appended as they complete. Both ran on the
    # temporary e2-standard-4 instance (June arms: e2-standard-8) and are scored
    # at the 6h boundary (E1 overran to 8.6h — supervisor death; E4 to 6h14m).
    "base-2B+rec": {"runs": ["run_20260711_065435"], "block": "hardening", "dur": "6h*"},
    "opd-r3-norec": {"runs": ["run_20260711_153427"], "block": "hardening", "dur": "6h*"},
    "opd-r2-noseed": {"runs": ["run_20260713_084905"], "block": "hardening", "dur": "6h"},
    # NOTE: this table does NOT truncate at the 6h protocol boundary — E1
    # ("base-2B+rec") overran to 8.6h (supervisor death) and shows 13/30, wall
    # 2/3 here; the protocol-boundary score is 12/30, wall 1/3 (opd-2b.md).
    # Use the 6h-boundary numbers for any published comparison.
    "opd-r2-uniform": {"runs": ["run_20260713_191230"], "block": "hardening", "dur": "6h"},
}

# Pre-registered contrasts (one-sided: first arm > second on the tested stat).
WALL_CONTRASTS = [
    ("opd-r2", "base-2B"),
    ("opd-r2", "opd-r1"),
    ("opd-r3+rec", "base-2B"),
]
STAGE_CONTRASTS = [
    ("r10-base-9B", "r10-sft-9B"),
    ("opd-r3+rec", "base-2B"),
    ("opd-r2", "base-2B"),
]
TREND_ARMS = ["base-2B", "opd-r1", "opd-r2", "opd-r3+rec"]  # ordered dose


def _agent_dirs() -> dict:
    return {ad.name: ad for ad in list_agent_dirs()}


def collect_arm(arm: str) -> list[dict]:
    """Per (run, agent): {'run', 'agent', 'stages': {quest: max_stage}, 'total'}.

    Missing run dirs are skipped loudly (a hardening arm may still be running).
    """
    ads = _agent_dirs()
    rows = []
    for run_id in ARMS[arm]["runs"]:
        for an in AGENTS:
            rd = [r for r in list_runs(ads[an]) if r.name == run_id]
            if not rd:
                print(f"  [warn] {arm}: {run_id}/{an} not found — skipped", file=sys.stderr)
                continue
            rv = parse_run_sessions(ads[an], rd[0])
            prog = progression_for_quests(rv, quest_names=CORE3)
            stages = {q: (prog[q].max_stage_reached if prog.get(q) else 0) for q in CORE3}
            rows.append({"run": run_id, "agent": an,
                         "stages": stages, "total": sum(stages.values())})
    return rows


def run_totals(rows: list[dict]) -> list[int]:
    """Per-run Core-3 totals (summed over agents) — the run-level unit."""
    per_run: dict[str, int] = {}
    for r in rows:
        per_run[r["run"]] = per_run.get(r["run"], 0) + r["total"]
    return [per_run[k] for k in sorted(per_run)]


def wall_passes(rows: list[dict]) -> list[bool]:
    """Herbalist stage >= 2 per agent-run (past the Foraging-5 lily wall)."""
    return [r["stages"]["Herbalist's Desperation"] >= 2 for r in rows]


def raw_table(arms: list[str]) -> None:
    print("\n=== RAW TABLE (every run; per-agent max stage per quest) ===")
    print(f"{'arm':<14}{'run':<24}{'agent':<15}{'For':>4}{'Herb':>5}{'Rick':>5}{'tot':>5}")
    for arm in arms:
        for r in collect_arm(arm):
            s = r["stages"]
            print(f"{arm:<14}{r['run']:<24}{PERSONA[r['agent']]:<15}"
                  f"{s['Foresting']:>4}{s[CORE3[1]]:>5}{s[CORE3[2]]:>5}{r['total']:>5}")


def wall_report(arms: list[str]) -> None:
    print("\n=== HERBALIST WALL (stage >= 2) — agent-level passage per arm ===")
    print("(agents are prompt variants sharing one policy: clustered, not independent;")
    print(" Fisher on agent-attempts is reported with that caveat)")
    passes = {}
    for arm in arms:
        w = wall_passes(collect_arm(arm))
        passes[arm] = w
        if w:
            print(f"  {arm:<14} {sum(w)}/{len(w)}")
    if not HAVE_SCIPY:
        return
    for hi, lo in WALL_CONTRASTS:
        if hi not in passes or lo not in passes or not passes[hi] or not passes[lo]:
            continue
        a, b = passes[hi], passes[lo]
        table = [[sum(a), len(a) - sum(a)], [sum(b), len(b) - sum(b)]]
        odds, p = fisher_exact(table, alternative="greater")
        print(f"  Fisher exact {hi} vs {lo}: {sum(a)}/{len(a)} vs {sum(b)}/{len(b)}"
              f"  one-sided p = {p:.4f}")


def stage_tests(arms_present: set[str]) -> None:
    print("\n=== STAGE TOTALS — run-level exact Mann-Whitney (one-sided) ===")
    for hi, lo in STAGE_CONTRASTS:
        if hi not in arms_present or lo not in arms_present:
            continue
        a = run_totals(collect_arm(hi))
        b = run_totals(collect_arm(lo))
        if not a or not b:
            continue
        note = ""
        if len(a) < 2 or len(b) < 2:
            note = "  [n=1 arm: no distributional test possible — descriptive only]"
        print(f"  {hi} {a} vs {lo} {b}{note}")
        if HAVE_SCIPY and len(a) >= 2 and len(b) >= 2:
            u = mannwhitneyu(a, b, alternative="greater", method="exact")
            print(f"    exact p = {u.pvalue:.4f}")


def trend_test(n_perm: int = 100_000, seed: int = 0) -> None:
    """DESCRIPTIVE monotone-trend display across TREND_ARMS.

    The within-agent permutation p-value printed here is NOT valid inference
    about checkpoints: the treatment (a training round) is applied once per
    checkpoint to all three personas jointly, so persona-wise label shuffles
    fabricate independence that does not exist (a joint shuffle of arm labels
    gives ~2/24 at best, and arm order was never randomized). Codex review
    2026-07-13. The p-value is retained for exploratory use only — do NOT cite
    it in the paper; report the per-persona 4->4->5->6 pattern descriptively.
    """
    data = {}
    for arm in TREND_ARMS:
        rows = collect_arm(arm)
        if len(rows) != len(AGENTS):
            print(f"\n[trend] {arm} incomplete — skipping trend test")
            return
        data[arm] = {r["agent"]: r["total"] for r in rows}
    ranks = list(range(len(TREND_ARMS)))

    def stat(assignment: dict[str, list[int]]) -> float:
        return sum(ranks[i] * assignment[an][i]
                   for an in AGENTS for i in range(len(TREND_ARMS)))

    observed_vals = {an: [data[arm][an] for arm in TREND_ARMS] for an in AGENTS}
    obs = stat(observed_vals)
    rng = random.Random(seed)
    perms = list(itertools.permutations(range(len(TREND_ARMS))))
    count = 0
    for _ in range(n_perm):
        shuffled = {}
        for an in AGENTS:
            p = perms[rng.randrange(len(perms))]
            shuffled[an] = [observed_vals[an][j] for j in p]
        if stat(shuffled) >= obs:
            count += 1
    p = (count + 1) / (n_perm + 1)
    print(f"\n=== MONOTONE TREND across {' -> '.join(TREND_ARMS)} ===")
    for an in AGENTS:
        print(f"  {PERSONA[an]:<15} totals: {observed_vals[an]}")
    print(f"  permutation p (within-agent label shuffle, {n_perm} draws) = {p:.4f}")


def hierarchical_bootstrap(arm: str, n_boot: int = 10_000, seed: int = 0) -> tuple:
    """Bootstrap over runs -> agents -> quest cells. VALID ONLY with >=2 runs
    per arm (run = top-level unit). With one run it manufactures variation by
    resampling three fixed personas and three non-exchangeable quests — those
    intervals are NOT uncertainty about repeat runs and must not appear in the
    paper (Codex review 2026-07-13). main() skips single-run arms."""
    rows = collect_arm(arm)
    if not rows:
        return None
    runs = sorted({r["run"] for r in rows})
    by_run = {rid: [r for r in rows if r["run"] == rid] for rid in runs}
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        sampled_runs = [runs[rng.randrange(len(runs))] for _ in runs]
        total = 0.0
        for rid in sampled_runs:
            agents = by_run[rid]
            sampled_agents = [agents[rng.randrange(len(agents))] for _ in agents]
            for a in sampled_agents:
                qs = list(a["stages"].values())
                total += sum(qs[rng.randrange(len(qs))] for _ in qs)
        stats.append(total / len(sampled_runs))
    stats.sort()
    lo, hi = stats[int(0.025 * n_boot)], stats[int(0.975 * n_boot)]
    mean = sum(stats) / len(stats)
    return mean, lo, hi


def verify_r10() -> bool:
    """Reproduce the published r10 numbers before trusting anything else."""
    print("=== VERIFICATION: r10 published numbers ===")
    base = run_totals(collect_arm("r10-base-9B"))
    sft = run_totals(collect_arm("r10-sft-9B"))
    print(f"  base per-run totals: {base}   sft: {sft}")
    ok = True
    if base != [7, 7, 7, 7]:
        print("  [FAIL] base totals != [7,7,7,7]"); ok = False
    if sorted(sft) != [1, 2, 3]:
        print("  [FAIL] sft totals != {1,2,3}"); ok = False
    if HAVE_SCIPY:
        u = mannwhitneyu(base, sft, alternative="greater", method="exact")
        print(f"  Mann-Whitney exact one-sided p = {u.pvalue:.4f}  (published: 0.0286)")
        if abs(u.pvalue - 1 / 35) > 1e-6:
            print("  [FAIL] p != 1/35"); ok = False
        bf, sf = [], []
        for arm, sink in (("r10-base-9B", bf), ("r10-sft-9B", sf)):
            for r in collect_arm(arm):
                sink.append(r["stages"]["Foresting"] >= 3)
        odds, pf = fisher_exact([[sum(bf), len(bf) - sum(bf)],
                                 [sum(sf), len(sf) - sum(sf)]], alternative="greater")
        print(f"  Fisher Foresting {sum(bf)}/{len(bf)} vs {sum(sf)}/{len(sf)}: "
              f"p = {pf:.4f}  OR = {odds:.1f}  (published: p=0.016, OR=16)")
    print(f"  verification: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="r10 reproduction only")
    args = ap.parse_args()

    if not verify_r10():
        print("\nAborting: r10 reproduction failed — fix the parser/arm registry first.")
        sys.exit(1)
    if args.verify:
        return

    present = set()
    for arm in ARMS:
        if collect_arm(arm):
            present.add(arm)
    opd_arms = [a for a in ARMS if ARMS[a]["block"] in ("opd", "hardening") and a in present]

    raw_table(list(present & set(ARMS)) and [a for a in ARMS if a in present])
    wall_report(opd_arms)
    stage_tests(present)
    trend_test()

    print("\n=== HIERARCHICAL BOOTSTRAP (>=2-run arms only; run = top-level unit) ===")
    for arm in [a for a in ARMS if a in present]:
        if len(ARMS[arm]["runs"]) < 2:
            print(f"  {arm:<14} single run — descriptive only, no interval")
            continue
        hb = hierarchical_bootstrap(arm)
        if hb:
            mean, lo, hi = hb
            print(f"  {arm:<14} mean {mean:5.1f}  CI [{lo:.1f}, {hi:.1f}]"
                  f"  ({len(ARMS[arm]['runs'])} runs)")


if __name__ == "__main__":
    main()
