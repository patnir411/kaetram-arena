# Matched six-arm OPD training protocol

This is the preregistration and launcher contract for the causal training
comparison missing from the current case study. It defines six primary arms,
four mechanism/baseline arms, and a separately reported four-condition history
ablation. It does not report a result, deploy a model, or spend
accelerator/inference compute.

The reviewed example manifest is
[`opd-matched-training.example.json`](opd-matched-training.example.json). Its
artifact registry is
[`opd-matched-training-artifacts.example.json`](opd-matched-training-artifacts.example.json).
The example expands five shared training seeds into 50 core cells plus 20
history-ablation cells (70 total). The latter are not silently counted as
mechanism arms.

## Registered arms

| Role | Arm | State source | Model-visible history |
|---|---|---|---|
| Primary | Natural OPD | Fresh canonical online world | Authentic online history |
| Primary | Targeted persistent state | Hash-verified DB snapshot restore | Visible-field reconstruction |
| Primary | Random-valid state | Uniform registered valid-snapshot pool | Same reconstruction as targeted |
| Primary | Progress-matched state | Registered progress-stratified pool | Same reconstruction as targeted |
| Primary | TCOD-B2F prefixes | Evidence-backed teacher-success boundary | Authentic prefix from the same trajectory |
| Primary | Guided-OPD | Fresh canonical live rollout | Shared history of complete teacher- and student-generated turns |
| Mechanism | Visitation only | Visitation-only persistent-state pool | Matched visible-field reconstruction |
| Mechanism | Teacher advantage only | Teacher-advantage-only persistent-state pool | Matched visible-field reconstruction |
| Baseline | Corrected-interface SFT | Corrected-interface teacher trajectory replay | History rendered from that corrected trajectory |
| Baseline | SCoRe first-error prefixes | Verified state immediately before the first model-visible student error | Verified prefix from the same student trajectory |

Visitation-only and teacher-advantage-only isolate the two proposed selection
mechanisms. Corrected-interface SFT controls for learning from corrected teacher
behavior without OPD. SCoRe requires hash-backed evidence that every prefix ends
at the first model-visible student error. Random-valid and progress-matched arms
test whether any legal initialization, or progress alone, explains the
targeted-arm effect.

## Separate state-by-history ablation

| Condition | State construction | Model-visible history |
|---|---|---|
| Snapshot + minimal history | Hash-verified targeted snapshot | One post-restore observation |
| Teacher replay + authentic prefix | Replay a witness to the identical state | Prefix from that witness |
| Snapshot + matched reconstructed history | Same snapshot family | Visible-field reconstruction |
| Backplay witness annealing | Restore progressively earlier witness states | Matching authentic witness prefix |

Backplay moves from success toward the canonical start across the entire shared
action-token budget. These 20 cells test state/history coupling and are reported
as a distinct crossed-history module, not as substitutes for the four core
mechanism/baseline arms.

## What is forced to match

The manifest has one global base checkpoint, teacher attestation/environment
variable, parameterization, optimizer, action-token budget,
teacher-scoring-token budget, environment-interaction budget, seed schedule,
held-out registration, and rendered-interface contract. Arm records cannot
override any of them. Every generated cell embeds the identical shared
contract.

Every arm initializes a fresh bf16 LoRA over the same frozen base checkpoint:
rank 64, alpha 64, zero dropout, no bias, and exactly `q_proj`, `k_proj`,
`v_proj`, `o_proj`, `gate_proj`, `up_proj`, and `down_proj`. Base parameters
remain frozen. The normalized parameterization contract and its SHA-256 are
copied into every cell so a baseline cannot silently full-finetune or reuse an
adapter while another arm starts fresh.

The checked optimizer contract is AdamW 8-bit, learning rate `5e-5`, cosine
scheduling, 3% warmup, gradient norm 1, effective batch 32, and one epoch. The
example freezes per-cell budgets at 2M action tokens, 50M teacher-scoring tokens,
and 600k environment interactions. These numbers are protocol inputs, not a
claim that the run is affordable or powered; review them before resolving the
manifest.

The interface contract hashes `prompts/system.md`, `prompts/game_knowledge.md`,
and `finetune/render.py`. This follows PR #40's versioned render-contract shape.
The registry and payload-digest rules follow PR #41's immutable provenance
principle. The `allow_launch` + `--execute` + exact-confirmation interlock,
create-only cell directories, source-commit check, and prelaunch seal follow PR
#42's confirmatory launcher pattern without requiring those draft branches to be
merged first.

## Evidence and exclusion gates

Every arm's training artifact is an immutable registry reference with a payload
URI and SHA-256. Every training artifact must bind to the same held-out quest
registration and carry a completed exclusion scan with a positive scanned-record
count. Snapshot-based arms additionally require a passed witness-trajectory or
invariant-certificate reachability record; “loadable by the server” is not
treated as evidence of legal reachability.

TCOD-B2F alone accepts teacher-success prefix artifacts with a hash-backed,
DB-authoritative quest-completion record and moves backward from success over
action-token progress. Guided-OPD instead requires fresh live mixed rollouts.
Its published curriculum is frozen to 250 training steps: the probability of a
complete teacher-generated turn follows cosine decay from 1 to 0 during the
first 80% of training and is then zero. The probability is held fixed within a
trajectory, while the actor is drawn independently for every turn. Student
turns are registered for reverse KL and teacher turns for forward KL. Missing
or unresolved evidence remains visible in dry-run and blocks execution.

SCoRe accepts only verified first-error-prefix artifacts. Its evidence record
must identify the first model-visible student error and hash both the evidence
and prefix verifier. All four history-ablation artifacts additionally require
passed legal-reachability evidence.

## Safe preflight

```bash
python3 scripts/opd/matched_training.py \
  research/experiments/opd-matched-training.example.json \
  --dry-run
```

Dry-run does not resolve the teacher endpoint, create output directories, or
start a worker. The checked-in example intentionally reports blockers: real base
checkpoint and teacher attestations, fourteen core/history artifacts and exclusion records,
reachability and teacher-success evidence, an exact clean source commit, and a
reviewed absolute artifact root are not present. The preparation adapter itself
is hash-locked in the example.

## Live handoff contract

The materialization adapter accepts:

```text
python <hash-locked-adapter> --cell-config <create-only-cell-config.json>
```

The cell config contains the arm-specific state/history constructor and the
shared immutable contract; the teacher endpoint remains environment-indirected.
After every placeholder is replaced and independently reviewed, live execution
still requires `execution.allow_launch=true`, `--execute`, and
`--confirm-launch <exact-experiment-id>`. The launcher then verifies a clean,
exact Git commit, creates a prelaunch seal, and starts at most
`execution.max_parallel` workers. The hash-pinned preparation adapter is
documented in
[`opd-matched-training-backend.md`](opd-matched-training-backend.md). It verifies
and normalizes immutable arm bundles but does not pretend the existing
single-arm Modal trainer implements every registered objective. Guided-OPD now
has a deterministic complete-turn role scheduler and exact mixed-trajectory
trace validation. The legacy offline OPD trainer deliberately rejects these
bundles because it lacks the published online asymmetric objective: reverse KL
on student turns and forward KL on teacher turns. Connecting the scheduler to
live teacher/student endpoints, implementing that objective, SCoRe, and reviewed
Modal execution remain explicit boundaries. The separate
[`corrected-interface SFT adapter`](opd-corrected-interface-sft.md) emits a
pretokenized, render-identity-bound bundle; that direct-token route is
`executable_pending_compute`.

## Current blockers

- No immutable base-checkpoint or teacher-deployment attestation is available in
  this clone.
- No hash-verified training bundles exist for the ten core arms or four history
  conditions.
- Held-out scans, legal-reachability witnesses, and DB-backed teacher-success
  evidence are unresolved examples.
- The Guided-OPD scheduler and bundle contract are implemented offline, but no
  live collector has produced endpoint-backed trajectories and the existing
  trainer is explicitly objective-blocked. Corrected-interface SFT has a
  pretokenized fresh-LoRA path; live Guided/SCoRe objective work and reviewed
  execution remain.
- No cost/power review has approved 50 core plus 20 history-ablation cells.

No expensive compute was run while adding this protocol.
