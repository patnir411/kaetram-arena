# Adversarial reviewer simulation — updated July 19, 2026

## Current verdict

| Reviewer | Score | Confidence | Decision |
|---|---:|---:|---|
| Methods and reproducibility | 2/10 | 5/5 | Strong reject |
| Agent-learning novelty | 4/10 | 4/5 | Reject; promising idea |
| Empirical design and statistics | 1/10 | 5/5 | Strong reject |
| Overall today | 2/10 | — | Do not submit |
| Potential after required work | 7/10 | — | Competitive case study/method paper |

## Pass 4 — full-stack red team, July 19

The verdict remains **2/10, strong reject**, but the failure surface is now sharper:

1. The dedicated eval lane has two independent correctness failures: the post-April-25 database
   mismatch and an internal-quest-key/display-name scoring mismatch that can turn a completed
   Core-3 quest into zero.
2. The public “12 to 18 weights-only, same harness” claim was false. The 18/30 run used recovery;
   12/15/17/18 are one unmatched historical run per cell and their raw bundles are absent.
3. The 20 frozen-checkpoint evaluation clusters do not replicate training. Only fresh training
   seeds can support a method-level uncertainty claim.
4. A stacked launcher could have made an unfaithful Guided-OPD approximation executable. PRs
   #48--#51 remain blocked; PR #52 validates the faithful role/bundle contract but still blocks
   because the live collector and actor-conditioned objective backend are absent.
5. The copy-prior evidence does not contain the four paired cells required for a preference
   reversal. It supports, at most, a reported positive teacher-over-student advantage for one
   malformed continuation.
6. CCOPD directly preempts the broad clean-context/self-anchored-drift framing; SOD directly covers
   unreliable supervision after erroneous tool calls; Privileged Information Distillation covers
   the selector's use of training-time privileged signal; TRB is a missing occupancy baseline.
7. The paper is format-clean but scientifically incomplete: two descriptive tables, zero figures,
   one historical run per arm, and no executed confirmatory result.

The complete severity and release-gate ledger is in
`research/paper/gargantua-review-2026-07-19.md`.

## Fatal findings

1. Round one versus round two is not a causal state-seeding experiment. Initialization, collected data, policy history, data volume, and training differ.
2. There is one full run per OPD arm. The three prompt variants are clustered observations, not independent replications.
3. Historical render contracts and immutable raw bundles are incomplete. Prospective PRs do not retroactively repair old checkpoints.
4. The 18/30 configuration combines weights and recovery. One 17-versus-18 contrast does not identify main or interaction effects.
5. TCOD, Guided-OPD, ReOPD, and SCoRe preempt broad intermediate-state, state-distribution, and student-failure curriculum novelty.
6. The copy-prior probe is post-hoc, small, single-family, and uses a length-sensitive score.
7. No held-out quest, no-walkthrough transfer, retention suite, or serious matched baseline has results.
8. Historical r10 raw inputs are missing, its analysis script fails, and the model-visible render contract differs.
9. The old report’s capacity and development-envelope claims mix durations and selected runs.
10. PRs #39–#42 add infrastructure and launchers, not scientific evidence.
11. The April 25–July 18 dedicated `run-eval.sh` path reset and snapshotted a different Mongo database from its game servers. Results from that dedicated lane are quarantined. The headline r10 and June OPD paths used the separate database-aligned orchestrator lane, so this specific mismatch does not implicate them; their raw bundles are still missing.

## Required experiments

- Matched natural OPD versus reachability-targeted persistent-player-state OPD from the same checkpoint.
- Random-valid, progress-matched, visitation-only, and teacher-advantage-only reset controls.
- Matched TCOD-B2F and Guided-OPD curricula; preferably SCoRe-style first-error prefixes.
- Corrected-interface SFT baseline.
- Independent environment and inference seeds; run-level analysis and power calculation.
- Base/round-two/round-three weights crossed with recovery off/on.
- Held-out quest, with/without walkthrough, and general-capability retention.
- Paired copy-prior probes across defects, states, and multiple teachers.
- Clean-clone regeneration from immutable artifacts.

## Reviewer questions the paper must answer

- What is randomized, and what is held fixed?
- Is the gain from state coverage, successful replay, extra data, or teacher grading?
- Does targeted selection beat Backplay-like generic resets, TCOD-B2F, and Guided-OPD?
- Are direct snapshots useful beyond replaying a teacher prefix to the identical state?
- Is the state reachable and internally consistent?
- Does the intervention improve states not adjacent to the seed?
- Does it transfer to a quest never used for seeding or grading?
- How do weights and recovery interact?
- What is the minimum detectable run-level effect?
- Are canonical and malformed sequence scores comparable under different tokenizations?
- Can every plotted point be regenerated from an immutable anonymous bundle?

## Manuscript consequence

The 14-page report remains a historical record. The submission draft is organized around the research question, evidence grades, and confirmatory design. It must not be rewritten into success language until the experiments change the evidence status.

## Independent pass 2 — July 19, 2026

Verdict: **reject (confidence 4/5)**. Subscores were soundness 2/5,
excitement 2/5, novelty 2/5, reproducibility 1/5, and clarity 4/5.

The decisive objection was manuscript identity: a method-centered title and
contribution list implied a causal result that the paper does not have. The
draft was therefore reframed as an audited state-visitation failure analysis
plus a confirmatory protocol. This pass also required and triggered:

- explicit joint occupancy state $z=(x,h)$ for latent player state and visible history, with policy $\pi(a\mid h)$;
- direct-snapshot/history, teacher-prefix, matched-history, and Backplay controls;
- corrected-interface SFT, OEC, OPCD, and SCoRe coverage;
- witness-trajectory or invariant-certified reachability, beyond loadability;
- removal of the "clean weights-only" and unquantified "almost never" claims;
- a canonical-start/no-intermediate-state evaluation endpoint with registered gameplay-RNG seeds and explicit six-primary-arm count;
- a power calculation before confirmatory collection, with no default five-run rule;
- demotion of the copy-prior observation from a main mechanism contribution; and
- an explicit warning that `reference/overview.pdf` is historical and not a
  submission artifact.

The verdict remains reject until the matched causal matrix and immutable result
bundles exist. Editorial repair cannot substitute for those experiments.

## Independent pass 3 — July 19, 2026

Verdict: **reject, 2/5 (confidence 4/5)**, with soundness 3/5, novelty 2/5,
clarity 4/5, and reproducibility 2/5. This was a material improvement over pass
2, but no matched state-source training experiment or immutable live result
bundle exists yet.

The pass exposed four concrete contract gaps. The manuscript incorrectly wrote
the model policy as conditioned on latent database state; it now uses
$\pi(a\mid h)$ while joint occupancy tracks $(x,h)$. Paper and protocol language
now limits the intervention to persistent player state rather than the complete
shared world. PR #42 now preserves and seals exact pre-rewrite emissions and a
completed-cell inventory. PR #45 now executes an isolated MCP/Mongo reachability
checker and uses provenance-bound, uncertainty-aware selection trials; it remains
blocked on a preserved live certificate. The game RNG has a reviewable draft patch, but it is
not merged, deployed, or live-attested.

The fatal scientific issue is unchanged: the six primary training arms have not
been run from one base checkpoint under matched budgets. PR #48 freezes 50 core
cells plus 20 separately reported state--history cells, and stacked PR #49 now
hash-verifies and normalizes all registered record types without claiming
training. Stacked PR #51 freezes a common LoRA parameterization and adds an
unexecuted direct-token corrected-interface SFT path. PR #50 prepares only the
first stage of a SCoRe-style condition and fails closed before Stage 2. A
primary-source audit rejected an unfaithful Guided-OPD prefix approximation;
the published baseline requires complete teacher/student turn mixing with a
trajectory-constant, training-step cosine schedule and actor-conditional
forward/reverse KL. PR #52 validates the faithful schedule and mixed-history
record contract but deliberately blocks the legacy trainer; the live collector
and asymmetric objective backend remain unimplemented. Verified training
inputs, faithful Guided execution, and accelerator execution remain unresolved.
The paper remains an audited hypothesis report and confirmatory protocol.
