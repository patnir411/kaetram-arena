# Historical evidence audits

These derived reports contain no raw session text. They are intended to be
regenerated against an external, immutable artifact recovery.

## Initial player state

`historical-initial-state.json` checks the first model-visible action in every
headline R10 and OPD agent-run. A bundle passes only when:

1. its first recorded tool call is a successful `observe`; and
2. the returned persistent state exactly matches the canonical level-1,
   post-tutorial benchmark start.

The report covers 36 agent-runs and is bound to the external file inventory by
the SHA-256 digest recorded in `source_manifest`.

Regenerate it from the repository root:

```bash
python3 scripts/audit_historical_initial_state.py \
  --raw-root /path/to/recovery/dataset/raw \
  --source-manifest /path/to/recovery/SHA256SUMS \
  --out research/audits/historical-initial-state.json
```

This verifies the state visible to the model before any action. It does not
retroactively attest which database command produced the state, the game
server revision, or shared-world state outside the player snapshot.

## Per-run content digests

`historical-run-digests.json` binds each recovered agent/run directory to its
relative path, file count, byte count, and deterministic content digest. It
covers the 60 bundles used by the R10 source-corpus diagnostic, R10 comparison,
OPD sequence, and teacher/capacity references. The report is self-identifying
and also records the digest of the full external `SHA256SUMS` inventory.

Regenerate it:

```bash
python3 scripts/manifest_historical_runs.py \
  --raw-root /path/to/recovery/dataset/raw \
  --source-manifest /path/to/recovery/SHA256SUMS \
  --out research/audits/historical-run-digests.json
```

## July mechanism campaign

`july-mechanism-run-digests.json` separately binds all 27 agent/run
directories from nine July mechanism arms. Its source inventory covers 19,005
files and is distinct from the earlier R10/June recovery. Generation rehashes
every selected file and requires an exact match to its source-inventory entry;
merely recording the inventory's own digest is insufficient.

`july-mechanism-analysis-provenance.json` binds the clean Git revision, Python
runtime, and exact bytes of every scoring dependency. The result renderer
verifies that receipt, rehashes the 27 raw directories against the evidence
manifest immediately before parsing, and validates the historical naive record
clock against each offset-aware run start.

`july-mechanism-results.json` then parses those verified JSONL logs through each
lane's inclusive six-hour boundary. It fails closed on malformed, missing, or
invalid semantic records; weak run metadata; an unaligned or non-monotonic
record clock; incomplete or substituted bundles; changed analysis bytes; or
disagreement with the dated protocol totals. The output contains only stage
totals and provenance identities, never raw prompts or endpoint addresses.

Regenerate both reports:

```bash
python3 scripts/manifest_historical_runs.py \
  --raw-root /path/to/july-recovery/dataset/raw \
  --raw-root-label dataset/raw \
  --source-manifest /path/to/july-recovery/SOURCE_SHA256SUMS \
  --groups opd_july_mechanism \
  --out research/audits/july-mechanism-run-digests.json

python3.12 scripts/capture_analysis_provenance.py \
  --implementation-file run_manifest.py \
  --implementation-file scripts/arm_stats.py \
  --implementation-file scripts/audit_historical_artifacts.py \
  --implementation-file scripts/capture_analysis_provenance.py \
  --implementation-file scripts/log_analysis/artifact_requirements.py \
  --implementation-file scripts/log_analysis/parse.py \
  --implementation-file scripts/render_july_mechanism_results.py \
  --out research/audits/july-mechanism-analysis-provenance.json

python3.12 scripts/render_july_mechanism_results.py \
  --raw-root /path/to/july-recovery/dataset/raw \
  --evidence-manifest research/audits/july-mechanism-run-digests.json \
  --analysis-provenance research/audits/july-mechanism-analysis-provenance.json \
  --out research/audits/july-mechanism-results.json
```

Capture the analysis receipt from a clean checkout with the exact Python patch
version named by the checked-in receipt (currently 3.12.12). All three commands
are create-or-identical-only and reject output paths that overlap their inputs.

This reproduces historical descriptive scores. It does not reconstruct the
checkpoint, training corpus, reset, game revision, render contract, or random
seeds used by the original launches.

### Anonymous July score replay

`research/artifacts/july-score-replay-v1/` is the reviewer-facing derivative.
It contains 21,524 successful score-relevant observations projected to quest
names and stages, plus source-record and source-log hashes. It excludes prompts,
model actions, maps, inventory, endpoint addresses, filesystem paths, and
unrelated player state. Verify the committed artifact from a clean checkout:

```bash
python3.12 scripts/score_july_public_artifact.py \
  --artifact-dir research/artifacts/july-score-replay-v1
```

The verifier checks every file and row binding, reconstructs the inclusive
six-hour cutoffs, and requires the nine expected arm scores. The artifact
manifest is
`26f0f7f8e22757b1af1e90af93e9baf71969b32e7bb77e847a6c5b54ddb5818d`.
This supports independent score replay. Because the complete historical
transcripts remain private, it does not let a reviewer independently repeat the
raw-to-projection extraction or recover missing launch attestations.

### Public trigger-incidence bundle

`research/artifacts/local-trigger-incidence-v1/` contains the complete
identity-scrubbed output of the registered local interface grid: frozen rendered
states, registration, endpoint attestations, all 1,200 raw response rows, the
sealed analysis, and content indexes. Verify it three ways:

```bash
python scripts/opd/verify_trigger_incidence_artifact.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1

python scripts/opd/audit_trigger_incidence_artifact.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1

python scripts/opd/audit_trigger_seed_diversity.py \
  --artifact-dir research/artifacts/local-trigger-incidence-v1
```

The producer verifier replays the registered analysis, while the independent
auditor recomputes message labels, cells, and contrasts without calling the
producer analyzer. The seed audit records a post-outcome limitation: all five
nominal request seeds produced the same semantic response within every
state-condition group. The paper therefore collapses duplicates to 20 state
outputs per cell. The artifact-index SHA-256 is
`fe117a98c506be441be12c07e4f467b00751807ee8f473e8026998fa257c1560`.

The failure is isolated in
[`mlx-request-seed-root-cause.md`](mlx-request-seed-root-cause.md). MLX-LM's
background serving thread accepted the request seed but did not change its
sampling stream. The prospective explicit-key repair is source-hashed and
startup-tested; it does not alter or upgrade the v1 evidence.

To verify both checked-in paper evidence bundles in one command:

```bash
python scripts/verify_public_paper_evidence.py
```
