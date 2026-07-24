# Author review guide — start here

## What exists now

- Historical technical report: `reference/overview.tex`.
- Evidence-safe NAACL/ARR draft: `reference/naacl_submission.tex`.
- Current rendered draft: `output/pdf/kaetram-opd-naacl-working-draft.pdf`.
- Claim rules: `research/paper/claims-evidence-matrix.md`.
- Venue rules and dates: `research/paper/venue-and-submission-guide.md`.
- Harsh-review record: `research/paper/reviewer-simulation.md`.
- Experiment design: `research/paper/experiment-plan.md`.

## What the draft says

The current historical observations are base 12/30, natural-OPD round one 12/30, state-augmented round two 15/30 without recovery, round-two weights plus recovery 17/30, and round-three weights plus recovery 18/30. There is one complete run per principal arm. The paper therefore calls these exploratory observations, not replicated effects.

The candidate method is reachability-targeted persistent-player-state initialization for OPD. It is not yet validated causally and does not restore a complete shared world. TCOD already covers success-prefix temporal curricula, Guided-OPD changes occupancy with decaying teacher turns, ReOPD designs prefix distributions, and SCoRe targets verified pre-error prefixes. Random/progress resets, TCOD-B2F, and Guided-OPD must be direct baselines.

## What your collaborator should review first

1. Approve the NAACL/ARR target and the October 12 internal schedule.
2. Review and merge P0 PRs before spending compute.
3. Freeze the primary endpoint, held-out quest, seed schedule, and baseline budgets.
4. Confirm that every new run preserves raw emissions and immutable artifacts.
5. Refuse any headline change until the corresponding gate in the evidence matrix changes.

## Current PR order

1. Correctness and evaluation fixes: #36, #37, #38, #53.
2. Canonical tool/render contract: #40.
3. Immutable run manifests: #41.
4. Recovery/copy-prior diagnostics: #39.
5. Factorial/held-out launcher: #42 after #40; then #44/#45/#47.
6. Matched-training stack: #48 -> #49 -> #51, then sibling #50/#52.
7. Historical evidence and clean clone: #43/#46.
8. Central paper and audit package: #35 after the supporting PRs settle.

## What is still missing

- Fresh matched seeded-versus-natural training.
- Random/progress reset, TCOD-B2F, Guided-OPD, and corrected-SFT baselines.
- Full weights × recovery results.
- Held-out/no-walkthrough and retention results.
- Replicated copy-prior diagnostics.
- Clean-clone reproduction from published immutable bundles.
- Final figures and run-level confidence intervals.

## One-sentence status

The infrastructure and evidence-safe paper shell are reviewable; the publishable scientific result still depends on new compute and immutable replicated data.
