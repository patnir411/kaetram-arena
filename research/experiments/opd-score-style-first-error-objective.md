# SCoRe-style verified first-error objective adapter

`finetune/score_style_first_error.py` consumes the hash-pinned backend result,
backend plan, and normalized records for the `score_first_error_prefixes` arm.
It is a preparation and loss-contract boundary. It does not invoke an
accelerator, create a checkpoint, or report an outcome.

## Scientific scope

The registered baseline is **SCoRe-style**, not a faithful reproduction of the
published SCoRe algorithm. The adapter preserves the two-stage distinction:

1. correction SFT on a teacher-forced correction immediately after a verified
   first-model-visible-error prefix; and
2. short-horizon policy optimization from the Stage-1 checkpoint using a
   target reward at the registered error step.

Static normalized first-error records are sufficient to prepare Stage 1. They
are not evidence that Stage 2 happened and cannot substitute for post-Stage-1
rollouts or target rewards. Every emitted plan therefore sets
`full_published_score_reproduction: false`, `launch_allowed: false`, and
`execution_status: not_run`.

## Stage-1 record and loss boundary

Each normalized record must bind all of the following:

- the same cell, seed, model, teacher, render, held-out registration, source
  artifact, and exact three-axis budget as the backend plan;
- a nonzero first-error-evidence digest and prefix-verifier digest;
- the student trajectory and first-error turn index;
- the exact verified-prefix token count and SHA-256; and
- a non-empty contiguous correction target and its SHA-256.

All labels in the verified prefix are `-100`. All later labels are contiguous,
nonnegative teacher-forced token IDs and must match the corresponding input
IDs. The Stage-1 loss is weighted mean token negative log likelihood over only
those correction labels. Prefix positions contribute to neither numerator nor
denominator. The module includes a lazy PyTorch collator and causal
next-token-aligned loss that project only supervised correction positions
through the language-model head; dry-run validation does not require PyTorch.

## Stage-2 loss boundary and blockers

The module exposes a deterministic short-horizon target-reward policy-loss
adapter, but normalized Stage-1 records are never passed into it as fabricated
samples. A runnable Stage-2 handoff must separately register:

- the exact Stage-1 checkpoint;
- post-Stage-1 short-horizon samples from each verified prefix;
- hash-backed target-reward evidence at the registered error step; and
- reviewed, disjoint Stage-1 and Stage-2 budget allocations.

Until those materials exist, the result status is
`prepared_stage1_stage2_blocked_not_trained`, no checkpoint field is populated,
and exclusive-create outputs prevent a reviewed bundle from being overwritten.
Both framework-independent and lazy PyTorch forms of the Stage-2 loss are
available, but neither function manufactures the required samples or evidence.

## Verification

```bash
python3 finetune/score_style_first_error.py \
  --backend-result <cell-output>/result.json \
  --dry-run
```

Dry-run verifies every referenced hash, identity, token boundary, evidence
binding, record count, and budget total without writing files. A non-dry run
materializes only a Stage-1 record bundle, objective plan, and fail-closed
result; it still performs no training.
