# Matched-training artifact adapter

`scripts/opd/matched_training_backend.py` turns one reviewed cell contract and
one immutable arm bundle into a hash-pinned normalized record bundle. It is a
preparation boundary, not a training job: every successful result is explicitly
`prepared_not_trained`, records `trainer_execution_status: not_run`, and names
any trainer extension still required.

## Material boundary

The manifest declares a specific absolute `execution.artifact_root`. Large
checkpoint and data bundles can therefore live outside Git, but every `file:`
payload must remain beneath that root and match its registry SHA-256. Parent
traversal and symlink traversal are rejected. The artifact registry, reviewed
manifest, adapter, cell configs, and rendered-interface files remain inside and
hash-bound to the repository.

The checked-in example leaves the artifact root unresolved. This is an
intentional launch blocker: reviewers must select the immutable storage mount
before any materialization run.

## Source record contract

Each JSONL record uses `kaetram.arm-source-record.v1` and carries:

- base-checkpoint, teacher, render-contract, and held-out-registration identity;
- explicit state and model-visible-history constructors plus content hashes;
- token IDs, masked labels, and objective-appropriate OPD arrays;
- exact per-record action-token, teacher-scoring-token, and environment counts;
- arm-specific evidence (snapshot/reachability, matched progress, teacher
  success, corrected-interface trajectory, or verified first-error prefix).

The adapter rechecks the held-out exclusion registration, scans model-visible
state/history for held-out aliases, rejects duplicate records, and requires the
aggregate source bundle to exactly fill all three registered budgets.

## Curriculum handling

TCOD-B2F records are ordered backward from success. Progress-matched records are
deterministically interleaved by registered stratum. Guided-OPD annotates the
linear teacher-prefix probability over the complete action-token budget, and
Backplay annotates the corresponding witness-distance schedule. Seeded ordering
is deterministic and recorded in the normalized output.

## Honest trainer routes

The current OPD collator can consume ordinary normalized OPD arrays, but this
adapter does not invoke it. Guided-OPD still requires a sampling extension,
corrected-interface SFT requires a pretokenized SFT adapter, and SCoRe requires a
first-error objective extension. The backend plan records these compatibility
states instead of emitting a checkpoint claim.

## Verification

```bash
python3 scripts/opd/matched_training_backend.py \
  --cell-config <create-only-cell-config.json> \
  --dry-run
```

Dry-run validates material and prints the plan without writing outputs. A
non-dry invocation creates `normalized-records.jsonl`, `backend-plan.json`, and
`result.json` with exclusive-create semantics, so reruns cannot overwrite a
reviewed bundle.
