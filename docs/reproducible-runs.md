# Immutable run manifests

`run.meta.json` remains the live dashboard/session counter. It is intentionally
mutable and is not evidence that a result can be reproduced. A completed run
can now be sealed with a separate `kaetram.run-manifest.v1` document containing:

- the full Git commit, branch, dirty flag, and dirty paths;
- byte hashes for the resolved prompt and run configuration;
- a canonical hash of every model-visible tool name, description, and JSON schema;
- independently sealed dataset and checkpoint provenance records;
- hashes for every declared result/log artifact; and
- an argv array for the reproduction command.

Writes are create-only and atomic. Existing manifests are never updated in
place. If a run changes, seal a new manifest with a new run ID.

Manifests are intended to be publishable. Never put credentials, signed URLs,
or live inference endpoints in them. The CLI rejects obvious HTTP(S) endpoint
values and common secret-bearing arguments in `reproduction.argv`. Record an
environment variable **name** or local config path instead; inject its value only
when executing in the reproduction environment.

## 1. Seal the inputs

Input provenance lives outside the content it hashes to avoid a circular
directory digest. Dataset content must be locally available. A remote checkpoint
must have a real content SHA-256 exported by the checkpoint-producing job; a
model name, mutable volume path, or endpoint URL is not a digest.

```bash
python3 scripts/run_manifest.py input \
  --kind dataset \
  --name opd-r2-eval-inputs \
  --reference dataset/opd_2b/round2/records.jsonl \
  --path /bundle/root/dataset/opd_2b/round2/records.jsonl \
  --source "opd_2b_data.py round 2; source run IDs recorded in build log" \
  --artifact-root /bundle/root \
  --output /bundle/root/provenance/opd-r2.dataset.json

python3 scripts/run_manifest.py input \
  --kind checkpoint \
  --name kaetram-qwen3.5-2b-opd-r2 \
  --reference modal://kaetram-model-vol/checkpoints/kaetram-qwen3.5-2b-opd-r2/merged \
  --sha256 "$CHECKPOINT_SHA256" \
  --source "finetune/train_opd_2b.py round 2" \
  --artifact-root /bundle/root \
  --output /bundle/root/provenance/opd-r2.checkpoint.json
```

The command rejects a dirty repository by default. `--allow-dirty` exists for
diagnostic captures, but such a record is unsuitable for confirmatory results.

## 2. Seal a completed run

Use a resolved prompt file, a complete JSON/YAML run configuration, and explicit
artifacts. Do not hash a directory containing the manifest being written.

```bash
python3 scripts/run_manifest.py create \
  --run-id run_20260718_120000 \
  --model 2b-opd-r2 \
  --harness qwen \
  --scenario core3-6h-unseeded \
  --artifact-root /bundle/root \
  --prompt /bundle/root/artifacts/run_20260718_120000/resolved-system-prompt.md \
  --config /bundle/root/artifacts/run_20260718_120000/run-config.json \
  --dataset-provenance /bundle/root/provenance/opd-r2.dataset.json \
  --checkpoint-provenance /bundle/root/provenance/opd-r2.checkpoint.json \
  --artifact logs=/bundle/root/artifacts/run_20260718_120000/logs \
  --artifact results=/bundle/root/artifacts/run_20260718_120000/results.json \
  --reproduce-argv '["python3","{repo_root}/scripts/my_reproducer.py","--input","{artifact_root}/artifacts/run_20260718_120000/logs"]' \
  --output /bundle/root/run_20260718_120000.manifest.json
```

Add `--recovery-enabled` only when the generation-time recovery affordance was
actually active. Dataset/checkpoint records are embedded and their sidecar bytes
are also hashed. Validation therefore fails if either the content or its stated
provenance changes.

`--repo-root` defaults to the checked-out Kaetram repository and is used only
for Git provenance. `--artifact-root` controls portable paths in the external
bundle. Keeping these roots separate lets source-code cleanliness remain a
meaningful invariant.

## 3. Clean-clone preflight and reproduction

Keep the manifest bundle outside the source checkout so its files do not dirty
the clone. The bootstrap command creates a new detached checkout at the exact
recorded commit, then runs the checked-in preflight script:

```bash
python3 scripts/bootstrap_reproduction.py /bundle/run.manifest.json \
  --bundle-root /bundle/root --clone-to /tmp/kaetram-reproduction

# Add --execute only after reviewing the recorded reproduction.argv.
python3 scripts/bootstrap_reproduction.py /bundle/run.manifest.json \
  --bundle-root /bundle/root --clone-to /tmp/kaetram-reproduction-execute --execute
```

`--bundle-root` must have the same relative layout used when the manifest was
created (for example, `artifacts/...` and `provenance/...`). The repository URL
is captured without credentials, query strings, or fragments.

Each recorded argv entry may contain literal `{repo_root}` and
`{artifact_root}` placeholders. Preflight expands only those strings and passes
the resulting argv directly to the process—there is no shell evaluation. Use
`{artifact_root}` for raw logs or other bundle inputs so execution does not look
for them inside the clean clone.

The default preflight fails on a dirty tree, a commit mismatch, schema drift,
missing files, dataset/checkpoint provenance mismatch, or any changed artifact.
It prints the recorded command but does not execute it without `--execute`.
`--allow-dirty` and `--allow-commit-mismatch` are diagnostic escape hatches, not
valid paper reproduction settings.

For schema-only review when the artifact bundle is intentionally unavailable:

```bash
python3 scripts/run_manifest.py validate run.manifest.json --structure-only
```

## 4. Fetch the public model snapshots

The immutable Hub lock at
`research/experiments/provenance/public-hf-snapshots.lock.json` records exact
revisions and every file identity for Base 2B, OPD R1/R2/R3, and the 4B
teacher. Large Git-LFS objects use their content SHA-256; ordinary Git files
use the canonical blob SHA-1 over `blob <size>\0<content>`.

Download only the models needed for a local pilot:

```bash
python3 scripts/fetch_hf_snapshot.py \
  --dest /external/model-snapshots \
  --snapshot base_2b opd_r2_2b opd_r3_2b \
  --receipt /external/model-snapshots/receipt.json
```

The downloader pins every URL to the locked commit, resumes partial transfers,
and verifies size plus content identity before publishing a file into the
snapshot directory. Existing files that fail verification are never silently
overwritten. Recheck an already downloaded snapshot without network access:

```bash
python3 scripts/fetch_hf_snapshot.py \
  --dest /external/model-snapshots \
  --snapshot base_2b opd_r2_2b opd_r3_2b \
  --verify-only
```

Regenerating the lock contacts only public Hugging Face metadata endpoints and
does not download weights:

```bash
python3 scripts/build_hf_snapshot_lock.py
```

## Current boundary

This first version seals completed runs and makes verification fail closed. It
does not yet upload bundles or automatically finalize manifests when
`orchestrate.py` exits. Public Hub snapshots are now pinned independently; the
historical private training environment and exact run-time model server remain
separate provenance gaps.
