"""Resample an OPD records.jsonl up to a target train-record count.

The ±seeding ablation (E3': natural-only rebuild of the round-2 corpus) must
hold total records — and therefore optimizer steps at fixed batch size —
constant against the seeded arm, so a scoring or capability difference is not
just a training-budget difference. Records are duplicated by uniform sampling
WITH replacement from the natural build (seed fixed), appended after the
originals; duplicates are exact copies, so per-token advantages/behavior
logprobs stay byte-identical.

Usage:
  python3 scripts/opd/resample_records.py \
      --in dataset/opd_2b/round2_noseed/records.jsonl --target 7024 --seed 42
Writes <in>.resampled.jsonl and prints the manifest line to record.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--target", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    src = Path(args.inp)
    lines = src.read_text().splitlines()
    n = len(lines)
    if n == 0:
        raise SystemExit("no records")
    if n >= args.target:
        raise SystemExit(f"already at/above target ({n} >= {args.target}) — nothing to do")

    rng = random.Random(args.seed)
    extra = [lines[rng.randrange(n)] for _ in range(args.target - n)]
    out = src.with_suffix(".resampled.jsonl")
    out.write_text("\n".join(lines + extra) + "\n")
    print(f"{n} originals + {len(extra)} resampled duplicates -> {args.target} "
          f"(seed {args.seed}) => {out}")


if __name__ == "__main__":
    main()
