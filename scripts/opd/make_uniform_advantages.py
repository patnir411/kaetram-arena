"""Build the Arm-C control corpus: uniform clipped self-imitation.

Rewrites an OPD records.jsonl so every NONZERO advantage becomes a single
pre-registered positive constant +c, leaving zero advantages untouched. Zeros
in the r2 records mark exactly the tokens that must not train (context
positions, unscorable Nones, and the malformed-span abstention mask), so
replacing only nonzero values preserves the trained-token support and the mask
geometry byte-for-byte while erasing all teacher-derived per-token structure.

Trained with the unchanged IS-clipped trainer this yields *uniform clipped
self-imitation of behavior-policy tokens* (NOT plain SFT — the PPO-style ratio
clip still bounds the update; that is deliberate: the control changes only the
advantage pattern and preserves init, records, behavior logprobs, clipping,
step weighting, and optimizer). If it matches seeded-OPD's eval, teacher
grading was unnecessary for the r2 lift; if it returns to the natural-only
arm's level while OPD replicates, teacher weighting contributes beyond state
coverage.

c is PRE-REGISTERED as the corpus mean |advantage| over nonzero tokens: at
init the importance ratio is ~1, so the first-step gradient magnitude is
proportional to the advantage scale, and matching mean |adv| matches the
initial update magnitude without post-hoc tuning. The chosen c and corpus
stats are printed and written to a manifest next to the output.

Usage:
  python3 scripts/opd/make_uniform_advantages.py \
      --in dataset/opd_2b/round2_uniform/records_r2_original.jsonl \
      --out dataset/opd_2b/round2_uniform/records.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    args = ap.parse_args()

    src, dst = Path(args.inp), Path(args.out)

    # Pass 1: pre-register c from the corpus itself.
    total_abs = 0.0
    n_nonzero = n_zero = n_records = 0
    with open(src) as f:
        for line in f:
            rec = json.loads(line)
            n_records += 1
            for a in rec["advantages"]:
                if a != 0.0:
                    total_abs += abs(a)
                    n_nonzero += 1
                else:
                    n_zero += 1
    c = total_abs / n_nonzero
    print(f"records={n_records}  nonzero_adv_tokens={n_nonzero}  zero(kept)={n_zero}")
    print(f"pre-registered c = corpus mean |advantage| = {c:.6f}")

    # Pass 2: rewrite.
    with open(src) as f, open(dst, "w") as g:
        for line in f:
            rec = json.loads(line)
            rec["advantages"] = [c if a != 0.0 else 0.0 for a in rec["advantages"]]
            g.write(json.dumps(rec) + "\n")

    manifest = {
        "control": "uniform-clipped-self-imitation",
        "source": str(src), "output": str(dst),
        "c": c, "c_rule": "corpus mean |advantage| over nonzero tokens (pre-registered)",
        "n_records": n_records, "n_nonzero_tokens": n_nonzero, "n_zero_tokens_kept": n_zero,
    }
    mpath = dst.with_suffix(".manifest.json")
    mpath.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {dst} + {mpath}")


if __name__ == "__main__":
    main()
