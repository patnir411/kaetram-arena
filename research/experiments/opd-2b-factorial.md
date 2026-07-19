# Frozen 2B weights × recovery Core-3 confirmatory protocol

This launcher preregisters a six-hour, canonical-unseeded Core-3 comparison of
three frozen 2B checkpoints (`base`, `r2`, `r3`) with recovery off/on. It is
infrastructure and a protocol, not a result. No live evaluation was run while
preparing it.

The reviewed input is
[`opd-2b-factorial.example.json`](opd-2b-factorial.example.json). It expands 20
independent fresh-world evaluation-trajectory clusters into the complete 3 weights × 2
recovery × 3 fixed personality-lane design: 360 six-hour cell-episodes. Each
lane runs exactly once after its own DB reset. The launcher rejects any other
duration, a seeded world, extra episodes, missing arm, different personality
set, noncanonical tool schema, or incomplete preregistration.

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

## Fail-closed clustered analysis

After every cell completes, analyze the manifest-registered primary metric with
the three personality lanes summed inside each independent DB-reset replicate:

```bash
python3 scripts/opd/factorial_analyze.py \
  research/experiments/opd-2b-factorial.example.json \
  --out artifacts/opd-factorial-analysis.json \
  --clusters-csv artifacts/opd-factorial-clusters.csv
```

The analyzer rejects a missing/failed cell, zero-turn episode, endpoint
misattribution, absent or out-of-range metric, mixed source commits,
non-canonical schema, a metric override, or an attempt to overwrite an existing
analysis artifact. It reports `n=20` independent replicate clusters—not `n=360`
cells—and verifies that the manifest's primary metric and ordered estimands are
the preregistered contract used for analysis.

The seven ordered primary estimands are frozen in `analysis.primary_estimands`:
the r2 and r3 weights effects versus base with recovery off, recovery on-minus-off
within each of base/r2/r3, and the r2-vs-base and r3-vs-base recovery
difference-in-differences interactions. They receive exact two-sided sign-flip
tests, deterministic percentile-bootstrap intervals, and one Bonferroni family.
The analyzer also reports equally weighted factorial main effects over recovery
and weights, plus the remaining simple effects, as explicitly secondary
estimates without confirmatory p-values.

## Isolation and pairing

Every cell has a unique username, server port, sandbox, output directory, and
cell ID. Preflight rejects an incomplete/duplicate grid, missing recovery mate,
duplicate isolation value, unexpected weight label, invalid port range, or
unlocked held-out registration. Recovery is set explicitly per child process:
the off cell removes `KAETRAM_TOOL_RECOVERY`; the on cell sets it to `1`.
`execution.max_parallel` is a hard launch cap; the checked-in design uses six.

The independent unit is the `replicate`, not an individual agent episode. Each
replicate contains the three fixed historical personality lanes
(`grinder`, `completionist`, `explorer_tinkerer`) under all six weights ×
recovery arms. Each lane runs exactly one DB-reset episode; the launcher rejects
`episodes != 1`, so additional independent observations must be added through
`design.replicates`. Aggregate the three personality strata within each
`replicate × arm`, then make paired arm comparisons across replicate clusters.
Do not report the 360 lane cells as `n=360`; the registered sample is `n=20`
independent replicate clusters per arm. `pair_id` pairs recovery off/on
within a replicate, weight, and personality; `cluster_id` groups all three
personality lanes for a replicate and weight.

## Cost and current blockers

The frozen plan is 2,160 cell-hours. With six simultaneous cells the nominal
lower bound is 360 elapsed hours, before startup and retry overhead; endpoint
capacity and dollar cost must be reviewed before enablement. Real checkpoint
and tokenizer hashes, deployed `/health` attestations, restored endpoints,
MongoDB/game-server infrastructure, and the exact clean execution commit are
still required. The checked-in example intentionally proves that none of these
can be guessed or bypassed.
