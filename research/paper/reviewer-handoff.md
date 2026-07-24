# Paper 1 reviewer handoff

Updated: July 19, 2026

## The short version

Paper 1 now has both a historical technical report and an evidence-safe ACL-format working manuscript. It is not a finished conference submission. The strongest current observation is that a state-augmented training round was followed by passage of the prior Herbalist wall in all three clustered prompt variants of one canonical-start run with no intermediate-state initialization, with the reported Core-3 score moving from 12/30 to 15/30 and recovery off. Its gameplay-RNG seed was not preserved. Exact historical parity is unavailable, the run is not independently replicated, and round one versus round two differs in more than state source.

The working target is NAACL 2027 through the October 12, 2026 ARR cycle. ICLR 2027 is only a stretch option because its official call is not yet published. See `venue-and-submission-guide.md`.

Do not launch expensive confirmatory experiments until the correctness PRs below are merged and the P0 launch checklist is complete.

## Pull-request map and review order

Review and merge in this order:

1. [PR #36 — save time-budgeted evaluation results](https://github.com/patnir411/kaetram-arena/pull/36)
   - Fixes a deterministic post-episode `KeyError` that prevented `results.json` from being saved.
   - Does not change experiment semantics.
2. [PR #37 — align evaluation resets with the eval database](https://github.com/patnir411/kaetram-arena/pull/37)
   - Makes resets and DB-authoritative metrics use the same `kaetram_eval` database as both eval game servers.
   - Fails closed if a player reset cannot be confirmed.
3. [PR #38 — fail closed on incomplete paired evaluations](https://github.com/patnir411/kaetram-arena/pull/38)
   - Preserves real child exit codes, validates both result artifacts, and atomically updates the regular-file `dataset/eval/latest-run.txt` pointer only after both arms are complete.
4. [PR #40 — freeze and enforce the tool render contract](https://github.com/patnir411/kaetram-arena/pull/40)
   - Freezes the complete 17-tool schema, adds a live MCP signature handshake, versions native versus historical rendering, and makes fresh unversioned checkpoints fail closed.
   - Does not retrofit r10 or the published 2B/OPD endpoints.
5. [PR #41 — immutable manifests and clean-clone reproduction](https://github.com/patnir411/kaetram-arena/pull/41)
   - Adds create-only run manifests, artifact/dataset/checkpoint hashes, secret-safe provenance, exact-commit checkout, and external-bundle reproduction preflight.
6. [PR #42 — 2B factorial launcher and held-out evaluation](https://github.com/patnir411/kaetram-arena/pull/42)
   - Adds the registered three-checkpoint weights × recovery evaluation matrix
     and the held-out/no-walkthrough condition.
   - Its evaluation clusters quantify frozen-checkpoint behavior; they do not
     replicate training.
   - Depends on PR #40's canonical-schema mode and must not be used for live compute before the P0 smoke gate.
7. [PR #53 — correct Core-3 quest-key scoring](https://github.com/patnir411/kaetram-arena/pull/53)
   - Uses the internal quest identifiers returned by the database and tolerates
     partial snapshots without fabricating progress.
   - Must merge before any prospective Core-3 score is trusted.
8. [PR #39 — recovery and copy-prior diagnostics](https://github.com/patnir411/kaetram-arena/pull/39)
   - Audits malformed emissions and harness recovery, and scores paired malformed/canonical continuations under repaired history/docs.
   - Records whether it reproduces the historical no-schema grading context or PR #40's canonical native-schema context; do not pool those interfaces.
9. [PR #43 — historical-evidence validation](https://github.com/patnir411/kaetram-arena/pull/43)
   - Makes historical analysis fail closed on missing, corrupt, or semantically
     trivial session evidence.
10. [PR #44 — registered factorial analysis](https://github.com/patnir411/kaetram-arena/pull/44)
    - Verifies the registered protocol and sealed inputs before producing
      outputs; it does not turn evaluation repeats into training replication.
11. [PR #45 — targeted-state curriculum](https://github.com/patnir411/kaetram-arena/pull/45)
    - Adds prospective reachability and selector contracts; no live
      reachability certificate exists yet.
12. [PR #46 — clean-clone bootstrap](https://github.com/patnir411/kaetram-arena/pull/46)
    - Pins the prospective CPU test environment and documents service/data
      skips; it does not reproduce a historical result.
13. [PR #47 — randomness contracts](https://github.com/patnir411/kaetram-arena/pull/47)
    - Registers paired inference/environment seeds and a game-startup
      attestation; it depends on Kaetram-Open PR #333.
14. [PR #48 — matched-training launcher](https://github.com/patnir411/kaetram-arena/pull/48), then stacked
    [#49](https://github.com/patnir411/kaetram-arena/pull/49),
    [#51](https://github.com/patnir411/kaetram-arena/pull/51), and finally sibling
    [#50](https://github.com/patnir411/kaetram-arena/pull/50) /
    [#52](https://github.com/patnir411/kaetram-arena/pull/52)
    - #48--#51 remain fail-closed preparation/training infrastructure.
    - #50 is an incomplete SCoRe-style condition and blocks before Stage 2.
    - #52 validates the published 250-step role schedule and complete
      mixed-history bundle contract, then blocks the legacy trainer. The live
      collector and actor-conditioned objective backend are still absent.
15. [PR #35 — paper audit and research plan](https://github.com/patnir411/kaetram-arena/pull/35) (this PR)
   - Adds the evidence-safe ACL-format manuscript, refreshed technical-report PDF, venue audit, adversarial reviewer simulation, claim audit, and minimum experiment package.

PRs #36–#38 and #53 intentionally separate result serialization, database
semantics, wrapper completion behavior, and metric semantics. PRs #39–#41 are
separate review units. PR #42 is stacked on #40; #44, #45, and #47 then inherit
the launcher contract. The training stack is #48 -> #49 -> #51, with #50 and
#52 as sibling descendants of #51. No new training, deployment, database
mutation, or live inference was performed while preparing or auditing these
branches.

## Historical scope of PR #37's database-lane bug

The wrong-database path was introduced with the `scripts/run-eval.sh` / `eval_harness.py` lane on April 25, 2026, and remained until PR #37. That path used ports 9061/9071 while resetting and reading a different database from the eval servers.

The checked headline OPD and r10 numbers were not produced through that lane:

- base 2B `run_20260608_185339`, r1 `run_20260610_140358`, r2 `run_20260612_044933`, r3 `run_20260613_112422`, and r2 plus recovery `run_20260613_214956` were orchestrated on ports 9001/9011/9021 and scored from raw session logs;
- the r10 base `[7,7,7,7]` versus SFT `[3,1,2]` comparison was likewise scored from orchestrator logs; and
- checked-in dataset/evaluation artifacts used by the report predate the April 25 lane change.

Therefore PR #37 does not invalidate E3-prime, E4, Arm-C, or the historical r10 headline values.
It does invalidate trust in any un-audited result produced through `run-eval.sh` on main between
April 25 and PR #37's July 22 merge, unless the run attests the July 18 fixed branch. The May/June
raw bundles were subsequently recovered read-only from the original VM
and bound to a SHA-256 inventory. `research/audits/historical-initial-state.json` verifies that the
first recorded action in all 15 OPD and all 21 R10 agent-runs is `observe`, with the exact canonical
level-1 starter state and no state anomaly. The R10 statistics and credit diagnostic also rerun
from the recovered external paths. The remaining caveat is narrower: the historical reset command,
full environment revision, and OPD training artifacts are not yet sealed.

## Linear execution board

Project: [Paper 1 — Reproducible OPD Submission](https://linear.app/niral/project/paper-1-reproducible-opd-submission-74a055f466fd)

Existing historical/context tickets linked into the project:

- [KAE-32 — Paper 1 draft](https://linear.app/niral/issue/KAE-32/paper-1-write-arxiv-draft)
- [KAE-74 — r11/OPD direction](https://linear.app/niral/issue/KAE-74/r11-plan-scaffold-on-policy-distillation-approach-post-narrative-open)
- [KAE-49 — design-variable audit](https://linear.app/niral/issue/KAE-49/catalog-and-defend-every-design-variable-for-the-paper)

Current execution tickets:

- P0: [KAE-76](https://linear.app/niral/issue/KAE-76/paper-p0-review-and-merge-eval-correctness-prs-36-38), [KAE-77](https://linear.app/niral/issue/KAE-77/paper-p0-immutable-run-manifests-and-clean-clone-reproduction), [KAE-78](https://linear.app/niral/issue/KAE-78/paper-p0-version-and-enforce-the-model-visible-tool-render-contract)
- Experiments: [KAE-79](https://linear.app/niral/issue/KAE-79/paper-p1-replicate-the-2b-weights-recovery-factorial) through [KAE-85](https://linear.app/niral/issue/KAE-85/paper-p7-implement-one-strong-matched-budget-alternative)

KAE-79 through KAE-85 are blocked on their relevant P0 tickets so the board does not encourage spending compute on an unfrozen protocol.

## What is currently supported

- Round two is the least-confounded historical recovery-off observation: the score was 15/30 versus the base run's 12/30, and wall passage was 3/3 versus 0/3 among clustered prompt variants. Exact checkpoint/configuration parity, the gameplay-RNG seed, and independent reproduction remain unavailable, so it is not a clean weights-only comparison.
- The reported 18/30 round-three configuration includes a model-interface recovery affordance. It must be labeled weights plus recovery, not a pure weight result.
- Round-two weights plus recovery was reported at 17/30 in one unmatched
  historical run. It is not a controlled ablation or factorial estimate.
- The malformed-history copy-prior observation is a mechanism hypothesis: one
  targeted state reportedly gave a malformed continuation positive
  teacher-over-student distillation advantage. It did not compare both
  canonical and malformed candidates in the same state and therefore does not
  establish that the teacher preferred malformed syntax.
- The historical r10 base/SFT comparison is exploratory evidence of regression, not a clean causal SFT baseline.

## What is not currently supported

- A general claim that reachability-targeted OPD outperforms ordinary on-policy distillation or modern prefix/guidance curricula.
- A general claim that OPD outperforms matched off-policy SFT or outcome RL.
- A clean base-versus-SFT causal comparison: training and serving do not share an identical model-visible tool-schema render.
- Independent statistical replication of the 2B OPD result.
- Generalization beyond seeded Core-3 quest procedures or beyond Kaetram.
- Continual learning, autonomous skill learning, world-model learning, or embodied-agent claims.

## P0 launch gate

Before spending additional training or evaluation compute:

- Merge PRs #36–#38 and #53 and run their focused tests in a clean clone.
- Pin the Kaetram-Arena and Kaetram-Open commits, environment image, dependencies, model/tokenizer revisions, prompts, decode settings, and database lane.
- Freeze a normalized full tool-schema snapshot and hash.
- Introduce an explicit, versioned render contract shared by new training and serving code.
- Preserve historical r10 and OPD render behavior under explicit legacy labels; do not silently change old endpoints.
- Make every run write an immutable manifest with checkpoint, prompt, schema, harness, environment, sampling, seed, recovery, and artifact hashes.
- Restore or explicitly mark unavailable the raw inputs behind every reported historical number.

The historical r10 checkpoint cannot be repaired by merely passing native tools at serving time. That would create a new train/serve mismatch. Interface parity requires a newly rendered dataset and newly trained checkpoint.

## Minimum publishable experiment package

### WP1 — Frozen-harness weights × recovery factorial

Evaluate base 2B, r2, and r3 weights with recovery off and on under identical fresh-world, prompt, schema, duration, sampling, and hardware conditions. Use independent complete runs as the statistical unit. Preserve raw model emissions before any recovery rewrite.

### WP2 — Natural visitation versus targeted persistent-player-state OPD

Train fresh students from the same checkpoint. Hold teacher, optimizer, scored-token budget, environment interactions, recovery setting, and training seeds fixed. Compare natural visitation, targeted persistent player states, random-valid player states, progress-matched player states, TCOD-B2F, and faithful Guided-OPD. For Guided-OPD, sample each complete turn from teacher or student, keep the teacher probability constant within a trajectory, decay it by training step with the published cosine curriculum, and apply forward KL to teacher turns and reverse KL to student turns. This is the central causal test; it is not a complete shared-world-state intervention.

### WP3 — Corrected same-family SFT baseline

Collect successful same-family 4B trajectories and train a fresh 2B student with the same native schema render used at evaluation. Match the OPD action-token or compute budget. The historical Claude-to-9B r10 result is not a substitute.

Draft PR #51 supplies a direct-token, hash-pinned execution path and freezes the
shared fresh-LoRA parameterization, but no verified trajectory bundle or
accelerator run exists. Draft PR #50 prepares SCoRe-style correction SFT and
blocks before its second stage; it is not a completed SCoRe baseline. Draft PR
#52 freezes the published 250-step cosine role schedule and validates complete
mixed-history records, but intentionally reports
`guided_collection_supported_objective_blocked`. The live mixed-rollout
collector and actor-conditioned forward/reverse-KL execution backend remain
unimplemented; no model or accelerator run occurred.

### WP4 — Recovery-mechanism controls

Compare no recovery, dirty-history retry, canonical-history rewrite plus retry, and current recovery plus execution. Add grammar-constrained decoding if practical. Measure syntax validity, semantic tool accuracy, relapse, quest stages, and calls per hour.

### WP5 — Privileged-context teacher ablation

From matched Rick's Roll states, compare the plain 4B teacher, 4B with a verified successful trajectory in grading context, and a stronger same-family teacher if available. Measure teacher action preference and downstream pole, shrimp, cook, door, and completion milestones.

### WP6 — Held-out transfer

Pre-register at least one quest never used for state seeding or grading. Evaluate with and without walkthrough knowledge. Add a compact general-capability retention suite before and after training.

### WP7 — One strong alternative baseline

Implement either an agent-specific divergence/reliability baseline or matched-interaction outcome RL. A representative implemented baseline is more useful than citing many unimplemented 2026 methods.

## First iteration after merging the correctness PRs

1. Create one immutable smoke-run manifest for base 2B with recovery off.
2. Run a short paired smoke evaluation and verify that both arms save, reset the correct database, fail closed, and preserve raw emissions.
3. Review the artifact bundle before scaling duration or run count.
4. Run the preliminary replicated factorial with at least five independent runs per arm.
5. Use observed run-level variance for a power analysis before the confirmatory run count is locked.

`scripts/run-eval.sh` currently targets the historical base/r10-SFT paired lane. Do not treat it as the 2B factorial launcher without adding explicit 2B endpoint, checkpoint, schema, and recovery configuration to the manifest. Existing OPD operational notes live in `dataset/opd_2b/ROUND1_RUNBOOK.md`; they are historical provenance, not a clean confirmatory protocol.

## Review checklist for the collaborator

- Confirm each correctness PR reproduces its stated failure on `main` and fixes only that failure.
- Decide whether missing historical raw artifacts can be recovered from the original machines, Modal volumes, or backups.
- Approve the explicit render-contract design before any new SFT or OPD dataset is built.
- Choose the held-out quest and freeze it before new training data is generated.
- Approve the primary endpoint and independent run definition before replicated evaluation.
- Agree on a compute ceiling for the preliminary factorial and matched training baselines.
- Keep the technical report language conservative until those results land.

## Paper rewrite gate

The evidence-safe manuscript shell already exists in `reference/naacl_submission.tex`; it must remain explicitly exploratory until WP1–WP3 and WP6 have complete immutable artifacts. WP4, WP5, and WP7 determine whether it can make a mechanism/method claim or must remain a carefully scoped case study. The final manuscript must remain in the official ACL template, fit eight content pages, include a required Limitations section, and truthfully disclose material LLM assistance.
