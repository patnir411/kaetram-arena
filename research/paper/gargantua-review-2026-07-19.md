# Gargantua red-team review — 2026-07-19

## Executive verdict

**Decision today: strong reject / do not submit.** The repository now contains a serious
confirmatory protocol and substantial prospective infrastructure, but the proposed method has not
been run under matched controls. The historical June observations are not independently
reproducible from the repository because the raw runs, immutable manifests, exact code snapshots,
checkpoint digests, prompt/render hashes, gameplay seeds, and analysis outputs are absent.

The project is salvageable as a competitive agent-learning case study or method paper if it
crosses three gates:

1. Fix and attest the evaluation path.
2. Execute the matched causal matrix with training-level replication and immutable bundles.
3. Replace protocol-heavy manuscript space with executed results, uncertainty, mechanism tests,
   and held-out transfer.

Until then, every exact historical score is a **reported observation**, not a repository-verified
result or causal effect.

## What the audit changed immediately

- Removed the public README claim that 12 to 18 was a weights-only, same-harness result.
- Reframed the 12/15/17/18 sequence as one unmatched historical run per cell.
- Removed the post-hoc +3 weights / +2 recovery / +1 weights causal decomposition.
- Demoted the copy-prior result from “preference reversal” to a positive
  teacher-over-student advantage for one malformed continuation; the required paired artifact is
  missing.
- Added the most threatening recent work: CCOPD, Privileged Information Distillation/OPSD, TRB,
  Unmasking OPD, and SERL; expanded the SOD distinction.
- Added a repository-root, fail-closed paper build that checks citations, references, overflow,
  fonts/page geometry, and writes the stable PDF artifact.

## P0 — correctness and trust blockers

### P0.1 Dedicated evaluation reads and resets the wrong database

Commit `2a1d8a5` introduced the mismatch on 2026-04-25. `scripts/run-eval.sh` resets and snapshots
`kaetram_devlopment`, then launches the eval servers with `NODE_ENV=eval`, which selects
`kaetram_eval`. `eval_harness.py` also reads the development database. Any result produced through
that dedicated lane after 2026-04-25 must be quarantined until a deployed fix produces an
attested clean run.

This does **not directly implicate** E3-prime (`run_20260612_044933`), E4
(`run_20260613_112422`), or Arm-C (`run_20260613_214956`): the historical notes map them to the
orchestrator lane on ports 9001/9011/9021 using the development database. That conclusion is based
on code-path and port chronology rather than raw run metadata, which is missing.

### P0.2 Core-3 scoring uses display names against internal quest keys

`dashboard/db.py` builds quest-stage snapshots keyed by internal quest IDs such as `foresting`.
`eval_harness.py` looks up display names such as `Foresting`. A deterministic fixture therefore
scores internal `foresting` stage 3 as zero while the display-name spelling scores three. Fix the
mapping, cover all 3+3+4 stages with a regression test, and rerun every prospective score through
the corrected metric. Draft PR #53 implements the internal-key mapping and regression fixtures;
the gate remains closed until the PR is merged and deployed.

### P0.3 Historical headline artifacts are absent

The exact base 12, round-1 12, E3-prime 15, Arm-C 17, and E4 18 values live in narrative reports.
Missing:

- May/June raw session trees and `run.meta.json` files.
- OPD `records.jsonl` and `heldout.jsonl`.
- Exact checkpoints/adapters and model/tokenizer digests.
- Rendered prompt, walkthrough, and tool-schema hashes.
- Environment/game/server revisions and dependency lock.
- Exact training and evaluation commands.
- Copy-prior inputs and raw outputs.
- Recovery pre-rewrite emissions and marker source logs.
- Fixed external test data and analysis outputs.

The OPD implementation first entered Git in the post-run monolithic commit `7b4edc2` on June 14,
after E3-prime, E4, and Arm-C had run. Current code is corroborating documentation, not historical
provenance.

### P0.4 Clean clone cannot regenerate a headline result

On the current archive, `scripts/r10_stats.py` raises `KeyError: agent_0` because its expected raw
inputs are absent. `scripts/r10_credit_diag.py` emits an all-zero analysis and exits successfully,
which is worse than a hard failure. OPD records and held-out data are absent, and there is no
top-level dependency lock. Every analysis entry point must fail closed on missing expected inputs
and identify each missing source.

### P0.5 Reset provenance is not fail closed

The historical reset script deletes/reseeds player rows but warns and continues if MongoDB is
missing. The orchestrator records no reset receipt. The reset is player-scoped and does not attest
a complete shared-world state. Historical wording must remain “reported canonical-start player
state in a restarted server.” Prospective runs need a before/after receipt, database identity,
player-state hash, server revision, and abort-on-failure behavior.

## P1 — scientific blockers

### P1.1 No matched causal result

Round 1 and round 2 differ in checkpoint initialization, collected policy, state source, record
count, and training batch. The reported 12 to 15 sequence cannot isolate state seeding. Train fresh
students from the same checkpoint and cross only the prespecified intervention.

### P1.2 No training-procedure replication

The 20 “replicate clusters” in the analysis stack repeat evaluation trajectories for three frozen
checkpoints with inference/environment seeds. They estimate uncertainty conditional on those
checkpoints. They do **not** replicate training and cannot support a population claim about the
training method. The five fresh-LoRA training seeds in the planned matrix are the relevant
training-procedure replicates.

### P1.3 Guided-OPD execution remains unimplemented

An earlier draft froze teacher-success prefix state and linearly annealed prefix-token
probability. The published method instead collects fresh live mixed-turn rollouts, samples complete
teacher/student turns with a cosine schedule fixed within a trajectory, and applies actor-specific
forward/reverse KL. Because a later generic backend could make the earlier manifest executable,
merge-order prose alone was insufficient. PRs #48--#51 remain fail-closed for Guided-OPD. PR #52
validates the 250-step role schedule and complete mixed-history bundle contract, then reports
`guided_collection_supported_objective_blocked`. The live mixed-turn collector and
actor-conditioned forward/reverse-KL execution backend remain unimplemented.

### P1.4 Copy-prior evidence is not a four-cell reversal

The surviving notes report teacher log probability -0.161 versus student -0.253 for one malformed
continuation after malformed history. The comparison called “correct-form” uses a different state.
Required artifact:

| History | Canonical candidate | Malformed candidate |
|---|---:|---:|
| Clean | teacher/student log probability | teacher/student log probability |
| Malformed | teacher/student log probability | teacher/student log probability |

Use identical raw candidate strings and tokenization, paired log-odds, bootstrap or randomization
intervals over states, multiple defect families, and at least two teacher sizes/families. Separate
teacher endorsement, student generation propensity, and runtime recovery.

### P1.5 Historical recovery counts are not auditable

The report says 405 rewritten sessions have one recovery marker and no later marker. Raw
pre-rewrite emissions are missing and history is rewritten by the affordance. The 335 malformed
emissions and 405 markers use different units. The valid wording is “historical notes report 405
rewritten sessions with one marker,” not model self-correction, cure, or zero relapse.

### P1.6 Held-out split and missingness are weak

The current builder holds out every tenth session in deterministic glob order, silently skips
exceptions/empty sessions, and stores only session filename and turn index. It cannot audit
run/agent stratification, near-duplicate seeded states, or filename collisions. Store run ID,
agent ID, canonical source path/hash, exclusion reason, and content-level duplicate groups. Use a
randomized stratified split frozen before outcomes and emit overlap/missingness reports.

### P1.7 Historical statistics do not support population inference

There is one independent run per principal arm. Prompt personas share weights, harness, launch,
and world and are clustered observations. Episode-level bootstrap/Welch helpers do not create
training-policy replication. Report historical values descriptively. For the confirmatory study,
analyze at the training-run level and publish every run, stopping rule, interval, and exclusion.

## P1 — novelty and positioning blockers

The only plausible method novelty is narrow:

> Direct, prefix-independent restoration of witness-certified persistent player state, selected
> prospectively by a frozen visitation/teacher-advantage/recoverability rule.

Even that claim survives only if it beats generic matched resets, Backplay, TCOD-B2F, Guided-OPD,
and a reliability-aware prefix method under equal teacher-scoring, action-token, training, and
environment-interaction budgets.

| Prior work | Preempted broad claim | Surviving test |
|---|---|---|
| TCOD | Intermediate-state curricula for multi-turn OPD | Direct snapshot versus authentic teacher-success prefix at matched state/history |
| Guided-OPD | Changing live occupancy with teacher turns | Targeted snapshot versus faithful mixed-turn cosine curriculum |
| ReOPD | Reliability-aware prefix/state-distribution design | Persistent latent player state versus offline prefix replay |
| Backplay | Direct intermediate-state restoration | Teacher-advantage selector versus progress-only backward starts |
| TRB | Teacher-near early behavior occupancy with unchanged OPD loss | Targeted low-occupancy state versus trust-region behavior blending |
| CCOPD | Clean teacher context to reduce self-anchored multi-turn drift | Paired wrong-sign tool-syntax diagnostic, if replicated |
| SOD | Tool errors causing state drift and unreliable dense supervision | Explicit candidate-sign evidence beyond divergence reweighting |
| KAT | Agreement on degraded prefixes can be unhelpful | Active preference for a wrong candidate, not merely weak signal |
| Privileged Information Distillation / OPSD | Training-time privileged agent signals | Costed environment-state privilege plus held-out transfer |
| Unmasking OPD | Per-token/per-context diagnostic analysis | Environment/tool-specific sign intervention with downstream generation test |
| SERL | Feedback source and insertion point in multi-turn agents | Related-work context; no broad feedback-selection novelty |

## Minimum confirmatory package

### Primary six-arm matrix

1. Natural OPD.
2. Targeted persistent player states.
3. Random-valid player states.
4. Progress-matched player states / Backplay-style schedule.
5. TCOD-B2F teacher-success prefixes.
6. Faithful Guided-OPD.

Train fresh students from the same base checkpoint. Use at least five registered training seeds per
arm. Match LoRA parameterization, optimizer, action-token budget, teacher-scoring budget,
environment interactions, wall-clock policy, and data exclusions. Evaluate each trained policy
from independently reset, attested canonical player states with registered inference and gameplay
seeds.

### Separate mechanism/baseline arms

- Visitation-only selector.
- Teacher-advantage-only selector.
- Corrected-interface direct-token SFT.
- SCoRe-style first-error condition after the full second stage exists.

Do not silently count these as additional primary arms. Prespecify the smallest effect of interest
and failure condition.

### State-by-history ablation

For the same certified player state compare:

1. Minimal canonical history.
2. Authentic teacher prefix.
3. Matched reconstructed history.
4. Backplay along the witness trajectory.

This is the experiment that decides whether persistent player state contributes anything beyond
textual prefix information.

### Weights-by-recovery factorial

Evaluate base, round-2-like, and round-3-like weights with recovery off and on. Preserve raw
pre-rewrite emissions. Estimate score, wall passage, malformed-call rate, recovery rate, and the
interaction without cross-run arithmetic.

### Held-out and generalization package

- Hold out at least one quest from discovery, selector construction, and training.
- Cross full-walkthrough and no-walkthrough prompts.
- Report reach bottleneck, cross conditional on reach, and finish conditional on cross.
- Add a second model family or environment before making a broad agent-learning claim.
- Count selector teacher queries, environment interactions, and privileged state fields.

## Paper audit

### Current format

- ACL style files are byte-identical to the current official repository.
- Current draft is ten A4 pages; main content ends on page seven, references
  begin on page eight, and appendices occupy pages nine and ten.
- Fonts are embedded; no broken references, undefined citations, clipping, or overfull boxes were
  found in the pre-review render.
- A repository-root build script now fails on LaTeX errors, unresolved citations/references,
  overflow, and non-A4 output.

### Current scientific structure

- Two descriptive tables, zero figures.
- One historical run per arm.
- Roughly 65 source lines describe an unexecuted protocol.
- The abstract culminates in future controls rather than an executed result.

Accepted best/outstanding papers reviewed for structure use dense executed evidence: multiple
models/tasks, repeated trials, matched controls, human or component validation, confidence
analysis, ablations, and failure audits. Cosmetic figures will not close this gap.

After the experiments, replace most launcher/blocker prose in the main paper with:

1. Selector/system schematic.
2. Six-arm result table with run-level intervals and all seeds.
3. State-by-history mechanism figure.
4. Held-out/generalization table.
5. Paired copy-prior figure.

Move detailed contracts, launch gates, and implementation blockers to the appendix/artifact docs.

## Venue decision

**Primary:** NAACL 2027 through the 2026-10-12 ARR cycle, only if the matched study and clean-clone
artifact are complete. The paper fits LLM agents, ML for NLP, engineering experiments, analysis,
reproduction, and negative-results categories, but ARR requires completed work.

**Fallback:** TMLR if the final contribution is a rigorous mechanism/case-study analysis rather
than a sufficiently broad matched method result.

**Not viable now:** AAAI-27 is too close; ICLR 2027 has no confirmed official call yet. Do not
submit the current protocol as a position paper to evade the evidence requirement.

## Release gates

- **G0 — metric correctness:** DB-lane and Core-3 key bugs fixed; regression fixtures pass.
- **G1 — immutable inputs:** every arm materializes with source hashes, schema parity, and no
  unresolved exclusions.
- **G2 — faithful algorithms:** Guided-OPD live collector/objective and SCoRe second stage exist;
  blocked placeholders cannot execute.
- **G3 — reachability:** isolated replay/certificate validates every targeted state.
- **G4 — compute:** fresh training smoke, checkpoint digest, resume semantics, and failure cleanup
  pass on the actual accelerator backend.
- **G5 — study:** all registered training seeds and evaluation seeds finish; no silent reruns.
- **G6 — reproduction:** a clean clone regenerates every main table/figure from immutable bundles.
- **G7 — submission:** paper claims matrix contains no unsupported outcome and independent
  reviewers score the work at least weak accept without relying on author explanation.

## Stop conditions

The narrow method claim is falsified or must be reframed if any of the following occurs:

- Random-valid or progress-matched resets tie the targeted selector within the prespecified margin.
- TCOD-B2F, faithful Guided-OPD, TRB, or Backplay ties or exceeds it at matched budget.
- State-by-history results show the gain comes from textual prefix information rather than player
  state.
- Teacher reliability at selected states is low or selector advantage does not transfer to held-out
  quests.
- Gains disappear when evaluation starts from an attested canonical player state.
- Training-seed variance dominates the reported arm difference.

## Source-of-truth policy

Use `research/paper/reviewer-handoff.md` for collaborator sequencing,
`research/paper/claims-evidence-matrix.md` for claim wording, and this document for the severity
ledger. Historical `session_log.md`, `reference/overview.tex`, and experiment narratives are
development records; where they disagree with the audited evidence boundary, the audited boundary
wins.
