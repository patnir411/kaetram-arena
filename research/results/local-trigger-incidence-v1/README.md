# Local trigger-incidence result

This zero-cost, preregistered diagnostic asks whether two model-visible
interface choices change the chance that an executable tool call lands in
assistant text instead of the structured tool-call channel.

All 1,200 scheduled local requests completed: three fixed checkpoints, four
interface conditions, 20 historical decision states, and five nominal request
seeds. Checkpoint identity remained stable and no request failed. The sealed
analysis was reproduced byte for byte, then a separately implemented auditor
recomputed all message labels, cells, and registered contrasts from the public
raw rows.

## Result

The native model-visible tool schema increased content-only recoverable-call
incidence by 30, 22.5, and 15 percentage points for Base, round 2, and round 3.
The documentation rewrite was inconsistent: 0, -7.5, and +10 points.

The five nominal seeds produced the same semantic response within every one of
the 240 state-condition groups. We therefore collapse duplicates and report 20
distinct state outputs per cell. This does not change any cell percentage or
registered state-level contrast, but it means the study provides no stochastic
sampling uncertainty. A request seed had no observable effect under this local
serving path.

This establishes a fixed-grid interface mismatch under the tested renderer. It
does not show checkpoint or training-method superiority, a recovery benefit,
learned self-correction, quest improvement, or generalization.

## Clean verification

From the repository root in the pinned unit-test environment:

```bash
python scripts/opd/verify_trigger_incidence_artifact.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1

python scripts/opd/audit_trigger_incidence_artifact.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1

python scripts/opd/audit_trigger_seed_diversity.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1
```

The public artifact-index SHA-256 is
`fe117a98c506be441be12c07e4f467b00751807ee8f473e8026998fa257c1560`;
its content-tree SHA-256 is
`8595c66194138e931aade908fe0d0e2b2ba3ac5a1c6cb1de5016729bfb2af9f4`.

The generated paper table is
[`paper-table-public.md`](paper-table-public.md). The post-outcome duplicate
audit is [`seed-diversity-audit.json`](seed-diversity-audit.json).
