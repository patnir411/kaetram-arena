# Matched-training artifact adapter

`scripts/opd/matched_training_backend.py` turns one reviewed cell contract and
one immutable arm bundle into a hash-pinned normalized record bundle. It is a
preparation boundary, not a training job: every successful result is explicitly
`prepared_not_trained`, records `trainer_execution_status: not_run`, and names
any remaining live boundary.

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
deterministically interleaved by registered stratum. Guided-OPD uses fresh live
rollouts rather than restored teacher-success states. Before every complete
turn, a collector calls `scripts/opd/guided_opd_schedule.py`; the hash-pinned
scheduler draws the teacher or student independently using the registered seed,
trajectory ID, and turn index. Its probability comes from the published cosine
training-progress schedule (250 total steps, curriculum ratio 0.8) and is held
fixed throughout a trajectory. The collector records every actor turn in one
append-only shared history. Each turn marks the complete-response boundary
before the next environment observation, hashes its response content, and binds
the exact actor token IDs to the supervised labels. The adapter recomputes every
role draw and probability, rejects rewritten, partial, or incomplete trajectory
histories, requires student/reverse-KL and teacher/forward-KL labels, and checks
the complete matched budget. Backplay annotates the corresponding
witness-distance schedule.

## Honest trainer routes

The legacy OPD trainer can validate a Guided bundle's hashes, identities,
registered seed, scheduler contract, and every role decision, but it then raises
an objective-blocked error before loading the model. Its offline PPO-style loss
cannot faithfully substitute for online mixed trajectories with reverse KL on
student turns and forward KL on teacher turns. This adapter does not invoke the
trainer. Invoking the legacy entrypoint without a plan is also rejected when a
record carries any Guided schema, arm, semantics, curriculum, or history
marker, so omitting the plan cannot silently select the legacy loss. A live
collector and asymmetric objective still need implementation;
SCoRe separately requires a first-error objective extension. Corrected-interface
SFT now has a separate
[`corrected-interface pretokenized adapter`](opd-corrected-interface-sft.md)
that consumes normalized token arrays without re-rendering and preserves the
shared fresh bf16 LoRA contract. The plan records these boundaries instead of
emitting a checkpoint claim.

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
