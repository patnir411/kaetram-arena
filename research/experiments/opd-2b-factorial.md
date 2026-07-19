# Frozen 2B weights × recovery Core-3 confirmatory protocol

This launcher preregisters a six-hour, canonical-state Core-3 comparison of
three frozen 2B checkpoints (`base`, `r2`, `r3`) with recovery off/on and a
registered server RNG stream. It is infrastructure and a protocol, not a
result. No live evaluation was run while preparing it.

The reviewed input is
[`opd-2b-factorial.example.json`](opd-2b-factorial.example.json). It expands 20
independent fresh-world evaluation-trajectory clusters into the complete 3 weights × 2
recovery × 3 fixed personality-lane design: 360 six-hour cell-episodes. Each
lane runs exactly once after its own DB reset. The launcher rejects any other
duration, noncanonical state initialization, extra episodes, missing arm,
different personality set, noncanonical tool schema, or incomplete
preregistration. The legacy protocol field `canonical_unseeded` means no
external-state injection; it does not mean that gameplay RNG is unregistered.

## Registered outcome and estimands

The primary metric is `core3_stages_advanced`. Within each
`replicate × weights × recovery` arm, sum the DB-authoritative stage deltas from
the grinder, completionist, and explorer/tinkerer lanes. The replicate-arm
outcome is therefore bounded 0–30. A lane is not an independent observation.

All uncertainty in this factorial is conditional on the three registered,
fixed checkpoint artifacts. The repetitions resample evaluation seeds and
fresh-world trajectories; they do not retrain any method, estimate
training-seed variance, or support inference about a training procedure's
across-run variability. Fresh training runs require the separate matched
training protocol.

### Randomness controls

- `schedule_seed` deterministically orders launch batches; it does not seed a
  game or model.
- `inference_seeds` registers one base sampling seed per replicate. All arms in
  that replicate share it, and `play_qwen.py` derives a distinct 31-bit request
  seed from `sha256-session-turn-v1:<base>:<session>:<turn>` before sending the
  OpenAI-compatible `seed` field. The base, r2, and r3 endpoints validate that
  field and pass it to SGLang generation.
- `environment_seed` registers one game-world seed per replicate. Every arm in
  a replicate shares that seed. The exact Kaetram revision must implement
  `kaetram-environment-rng-attestation/v2`; the launcher verifies the clean
  checkout, build-time source attribution, and exact `dist/main.js` digest.
  The harness then verifies that same executed-bundle digest in the
  server-written seed attestation before the first episode.
The seven ordered primary estimands are frozen in the manifest:

1. r2 − base with recovery off;
2. r3 − base with recovery off;
3. recovery on − off for base;
4. recovery on − off for r2;
5. recovery on − off for r3;
6. the r2-versus-base recovery interaction; and
7. the r3-versus-base recovery interaction.

Familywise alpha is 0.05 across these seven contrasts. The prospective
assumption-driven power record is
[`opd-2b-factorial-power-v1.json`](opd-2b-factorial-power-v1.json). It freezes
20 replicate clusters for 80% target power under a minimum relevant paired
difference of 3 stages and paired-difference SD of at most 3. Those assumptions
are not presented as an empirical variance estimate. Do not reduce the sample
after looking at outcomes.

## Mandatory immutable inputs

PR #40's canonical model-visible schema/render contract and PR #41's immutable
provenance machinery are prerequisites. Before any cell starts, the launcher
now validates and seals all of the following in a create-only
`prelaunch.json`:

- exact clean Git commit and experiment-manifest SHA-256;
- every prompt/personality file digest and the full canonical tool-schema
  digest;
- the power-analysis artifact digest;
- one PR-#41 checkpoint provenance sidecar per weight;
- one endpoint attestation per weight containing immutable deployment ID,
  checkpoint SHA-256, tokenizer SHA-256, and render-contract SHA-256; and
- held-out quest name, registration path, and registration digest (empty for
  this Core-3 protocol, retained explicitly so it cannot silently disappear).

Environment variables still keep endpoint URLs out of commands and artifacts,
but their names are not accepted as model identity. At launch the code queries
each endpoint's `/health` response and requires its `attestation` object to
match the reviewed file exactly. The checked-in provenance files are visibly
marked `unresolved_example`, so the example can be inspected and dry-run but
cannot launch.

A deployable endpoint must return this shape:

```json
{
  "status": "ok",
  "attestation": {
    "deployment_id": "immutable-deployment-id",
    "api_model": "2b-opd-r2",
    "checkpoint_sha256": "<64 lowercase hex>",
    "tokenizer_sha256": "<64 lowercase hex>",
    "render_contract_sha256": "<64 lowercase hex>"
  }
}
```

Replace every example checkpoint/endpoint sidecar with real hash-verified
records, set `protocol.source_git_commit` to the exact clean commit, and only
then review a copy with `execution.allow_launch=true`.

## Safe preflight and launch interlock

Endpoint URLs belong only in the operator environment:

```bash
export KAETRAM_QWEN_2B_BASE_ENDPOINT=...
export KAETRAM_QWEN_2B_R2_ENDPOINT=...
export KAETRAM_QWEN_2B_R3_ENDPOINT=...
```

Dry-run performs no endpoint call, MongoDB access, process launch, or run-dir
creation:

```bash
python3 scripts/opd/factorial_eval.py \
  research/experiments/opd-2b-factorial.example.json \
  --dry-run
```

Live execution requires all three independent operator actions: a reviewed
manifest with `execution.allow_launch=true`, `--execute`, and
`--confirm-launch` exactly equal to its experiment ID. The launcher refuses
missing endpoints, unresolved or mismatched attestations, dirty/wrong Git,
drifted prompt/power/provenance files, any existing cell directory, or an
existing prelaunch ledger.

The fourth fail-closed interlock is the game-server attestation. Before launch,
the launcher requires the Kaetram checkout to match the exact registered commit
and requires its built server artifact. Each cell then starts a new server with
the registered seed, required-attestation mode, a unique no-clobber attestation
path, and the registered game revision. Listening on the port is insufficient:
the harness hashes the manifest seed and verifies schema, algorithm, digest,
revision, and zero pre-attestation draws. A pre-existing server, absent file, or
field mismatch aborts the cell. The implementation under review is
[Kaetram-Open PR #333](https://github.com/Kaetram/Kaetram-Open/pull/333).

The attestation has deliberately bounded scope. It covers the server/common
gameplay random helpers audited at the pinned revision, but not network/input
arrival order, event-loop/timer ordering, database contents or unsorted query
order, wall-clock behavior, clients, inference, or external services. Matching
seeds therefore establish a controlled RNG stream, not bit-for-bit trajectory
replay after agent actions diverge.

Endpoint URLs remain environment-indirected through the launcher,
`eval_harness.py`, and `play_qwen.py`. Process arguments, preflight plans,
session init records, and result metadata contain only `env:VARIABLE_NAME`, not
the URL. Set `KAETRAM_GAME_DIR` when the pinned game checkout is not at
`~/projects/Kaetram-Open`.

The preflight plan and `results.json` record `tool_schema_source`, the registered
inference seed, schedule algorithm/seed/index, batch, cluster and pair IDs, the
environment seed, game revision, algorithm, and verified attestation core. The
launcher validates all of those fields before accepting a cell artifact.
Verify the canonical schema and provenance before analysis.

## Isolation and pairing

Every cell has a unique username, server port, sandbox, output directory, and
cell ID. Preflight rejects an incomplete/duplicate grid, missing recovery mate,
duplicate isolation value, unexpected weight label, invalid port range, or
held-out metadata drift. Recovery is set explicitly per child process:
the off cell removes `KAETRAM_TOOL_RECOVERY`; the on cell sets it to `1`.
`execution.max_parallel` is a hard launch cap; the checked-in design uses six.
Schema v2 requires exactly six: each randomized batch is one complete
`replicate × weight` cluster. SHA-256 ranking randomizes whole cluster order,
then personality-pair order within a cluster, while keeping each recovery
off/on pair adjacent. This preserves both recovery pairing and the registered
cluster analysis regardless of schedule seed.

The confirmatory unit is the independent `replicate`, not an individual agent
episode. Each replicate contains the three fixed historical personality lanes
(`grinder`, `completionist`, `explorer_tinkerer`) under all six weights ×
recovery arms. Each lane runs exactly one DB-reset episode; the launcher rejects
`episodes != 1`, so additional independent observations must be added through
`design.replicates`. Aggregate the three personality strata within each
`replicate × arm`, then make paired arm comparisons across the 20 replicate
clusters. Do not report the 360 lane cells as `n=360`; the confirmatory sample
is `n=20` independent replicate clusters per arm. `pair_id` pairs recovery off/on
within a replicate, weight, and personality; `cluster_id` groups all three
personality lanes for a replicate and weight.

## Operational caveats

- The example requests 20 independent replicates × six arms × three fixed
  personality lanes × one six-hour episode (360 cell-episodes total); review
  the resulting time and endpoint cost before enabling it.
- Ports must be free, MongoDB must be reachable, and the Kaetram server tree
  expected by `eval_harness.py` must exist on the execution host.
- The launcher runs bounded batches of at most `execution.max_parallel` cells.
  A child-launch exception terminates already-started batch siblings, but
  created run directories remain as an audit trail and must not be reused.
- This change adds infrastructure only; no endpoint was deployed and no live
  evaluation was run while preparing it.

Every cell result must report the same protocol ID, 21,600-second budget,
manifest digest, endpoint-attestation digest, checkpoint digest, tokenizer
digest, render-contract digest, held-out metadata, canonical schema source, and
one successful episode. The launcher then create-only seals a self-hashed cell
bundle containing the resolved prompt, raw endpoint emissions before recovery
rewrites, parsed tool-transition transcript, player/quest state-boundary
snapshots, launcher log, results, and hashes for every artifact. After all 360
cells pass, it seals an exact requested/completed-cell inventory. Missing,
rewritten-only, misattributed, or overwritten artifacts fail the batch.
Every later validation re-hashes every sealed artifact and the inventory; a
post-run change to any prompt, raw/parsed log, state snapshot, result, bundle,
or cell list invalidates the experiment rather than silently updating a summary.

## Cost and current blockers

The frozen plan is 2,160 cell-hours. With six simultaneous cells the nominal
lower bound is 360 elapsed hours, before startup and retry overhead; endpoint
capacity and dollar cost must be reviewed before enablement. Real checkpoint
and tokenizer hashes, deployed `/health` attestations, restored endpoints,
MongoDB/game-server infrastructure, and the exact clean execution commit are
still required. The checked-in example intentionally proves that none of these
can be guessed or bypassed.
