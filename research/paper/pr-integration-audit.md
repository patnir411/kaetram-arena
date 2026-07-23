# Pull-request integration audit — July 19, 2026

## Current verdict

Do not start confirmatory compute from any individual open PR. GitHub reports the
branches as mergeable one at a time, but the scientific protocol does not yet
compose across the stack.

## Required merge and rebase order

1. #36 result saving
2. #37 database-lane alignment
3. #38 paired-evaluation completion checks
4. #53 internal-key Core-3 scoring
5. #40 model-visible render contract
6. rebase and resolve #42 factorial launcher
7. functionally integrate #41 immutable manifests and #47 randomness contract
8. rebase #44 factorial analysis and #45 targeted-state curriculum independently
9. merge #48 matched-training launcher after #40/#41 contracts stabilize
10. review and merge stacked #49 preparation adapter after #48; preserve its
   explicit `prepared_not_trained` boundary
11. review and merge stacked #51 after #49; it freezes the shared LoRA
    parameterization and adds the direct-token corrected-interface SFT path
12. review #50 and #52 on top of #51 so SCoRe-style Stage 1 and the Guided-OPD
    contract inherit the identical parameterization contract
13. merge structurally independent #39, #43, and #46 when reviewed
14. merge #35 paper/audit after its cited code contracts stabilize

#42 already contains #40. #44 and #45 each contain #42/#40, so their diffs
should narrow after prerequisites merge.

## Manual-union conflicts

- #36 and #42/#44/#45 all edit `eval_harness.py` result metadata. Preserve the
  time-budget save fix together with held-out/protocol metadata.
- #37 and #42/#44/#45 all edit `eval_harness.py` preflight output. Preserve the
  Mongo database lane together with knowledge/held-out state.
- Choosing either side wholesale drops a required safety property.

## Protocol gates implemented prospectively

- #42 now freezes the six-hour canonical-start Core-3 protocol with no
  intermediate-state evaluation initialization and registered gameplay-RNG seeds, seven
  estimands, familywise alpha, an assumption-driven 20-replicate power contract,
  checkpoint/tokenizer/render/deployment attestations, and a create-only
  prelaunch ledger. It also seals each completed cell's resolved prompt, exact
  pre-rewrite emissions, parsed transition transcript, state-boundary snapshots,
  results, and an exact requested/completed-cell inventory.
- #44 now verifies the hashed protocol, computes factorial marginal effects and
  difference-in-differences interactions, and cannot promote a five-run pilot
  to confirmatory status.
- The resulting weights-by-recovery plan is 20 evaluation replicate clusters,
  360 six-hour cells, or 2,160 cell-hours. These repeats estimate uncertainty
  conditional on frozen checkpoints; they are not training-procedure
  replication. Capacity and cost require explicit operator review.

These are draft implementations pending maintainer review and ordered merge;
they do not make the historical results reproducible.

## Compute gates still open

- Replace all unresolved example checkpoint/tokenizer/render/deployment hashes
  with real immutable attestations from restored endpoints.
- Set and verify the exact clean execution commit.
- Restore Mongo, game services, and model endpoints, then pass the live health
  attestation before any cell starts.
- #47 records paired per-replicate inference/environment seeds and verifies the
  game startup attestation. Kaetram-Open draft PR #333 implements the game-side
  seeded RNG, but it must be reviewed, merged, deployed, and live-attested.
- #45 now executes a hash-pinned isolated MCP/Mongo reachability checker and binds visitation and
  teacher-advantage estimates to immutable trial artifacts with minimum-count
  and Wilson-bound gates. The checker verifies exact game/harness revisions,
  every action boundary, and a separately canonicalized target player. It
  remains fail-closed because no live certificate exists and schema-v1 candidate
  data must be regenerated.
- The current seeder restores persistent player state rather than complete
  shared-world state; experimental and paper labels must preserve that boundary.
- #48 freezes six primary and four separate mechanism/baseline training arms
  across five shared seeds (50 core cells) and four separately reported
  state--history conditions (20 more cells).
- Stacked #49 adds a hash-pinned preparation adapter for all 14 registered arm
  and history conditions. It verifies source hashes, held-out exclusions,
  arm-specific evidence, budgets, and frozen interfaces, then emits create-only
  normalized records with `prepared_not_trained`/`not_run` status. It does not
  supply verified arm bundles or accelerator execution.
- Stacked #51 freezes one hash-pinned fresh-bf16-LoRA contract across all 70
  cells and adds a direct-token corrected-interface SFT adapter/trainer. The
  trainer consumes frozen token IDs and labels without a tokenizer or render
  pass and remains `not_run` pending verified inputs, execution dependencies,
  compute approval, and an accelerator.
- Stacked #50 prepares correction-SFT records for a SCoRe-style first-error
  condition and validates a short-horizon target-reward loss contract. It
  deliberately fails closed before Stage 2 until a Stage-1 checkpoint,
  post-Stage-1 rollouts and reward evidence, and disjoint stage budgets exist;
  it is not a faithful reproduction or outcome claim for published SCoRe.
- A primary-source audit rejected the first Guided-OPD draft before PR because
  it treated guidance as a teacher prefix before a student turn. Stacked #52
  now samples complete actor-turn roles, freezes the published 250-step cosine
  curriculum with an 0.8 decay ratio, holds its probability fixed within each
  trajectory, and binds each hashed complete pre-observation response to its
  action-position token IDs, labels, append-only mixed history, and
  teacher/forward-KL or student/reverse-KL metadata. #52 validates these
  prospective records and then reports
  `guided_collection_supported_objective_blocked`; the live collector and
  asymmetric execution backend remain unimplemented.

## Historical database-bug scope

The April 25 database split affects the dedicated `run-eval.sh` lane. Headline
r10 and June OPD paths used the separate database-aligned orchestrator and are
not implicated by this specific mismatch. Missing raw bundles still prevent an
independent replay of those headline values.

## Audit checks performed

Final audited heads and focused verification:

| PR | Head | Local verification |
|---|---|---:|
| #36 | `bc70920b` | 7 passed |
| #37 | `79c57bed` | 6 passed |
| #38 | `1c3d83bd` | 13 passed |
| #39 | `2e75d700` | 8 passed |
| #40 | `76bcbe69` | 16 passed; compile check |
| #41 | `b9c19314` | 19 passed |
| #42 | `5ff089b2` | 41 process/episode + 22 launcher/manifest passed |
| #43 | `3874f59f` | 9 passed |
| #44 | `78187ac3` | 51 passed |
| #45 | `4e1f8905` | 41 passed |
| #46 | `d92fcf41` | 7 passed |
| #47 | `718f6761` | 61 passed |
| #48 | `c51a749a` | 18 passed |
| #49 | `71e5ed5e` | 28 passed |
| #50 | `e87a39d5` | 52 passed |
| #51 | `3d910398` | 43 passed |
| #52 | `7f26a785` | 50 passed |
| #53 | `6bb7feec` | 3 passed |
| Kaetram-Open #333 | `0b1f5e4c` | 2 server tests; build; emitted-bundle digest |

On the final GitHub sweep, Arena PRs #35--#53 were individually
`MERGEABLE/CLEAN`; Kaetram-Open #333 was `MERGEABLE/BLOCKED` by repository
review policy. All twenty PRs had zero unresolved review threads. None reported
a hosted status check, so no hosted-CI result is being claimed.

These counts record local tests at the audited commits. They are not hosted-CI
results and do not attest unavailable services, datasets, endpoints, or GPUs.

## Residual implementation boundaries

- #45 does not transactionally roll back a partially applied external-state
  restoration.
- #46 does not provide a fully hash-locked offline wheel/artifact set.
- #39 publishes each file atomically, but a process crash between two hard
  links is not a group transaction.
- #40 assumes experiment identifiers are immutable; mutation behind the same
  identifier cannot be detected by the cache key.
- #48--#51 remain intentionally fail-closed pending registered immutable
  artifacts and explicit compute approval.
- #52 lacks the live mixed-rollout collector and actor-conditioned asymmetric
  objective backend.
- #333 still requires maintainer review, merge, deployment, and live startup
  attestation.
