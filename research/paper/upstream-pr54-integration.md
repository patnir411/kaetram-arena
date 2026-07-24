# Upstream PR #54 integration audit

This fork imports only the reviewable, zero-spend parts of
`patnir411/kaetram-arena#54` (upstream commits
`0d9eb29c0a16a117d1ba524eac27ffa484602de6` and
`7e6e84a55e9b31c8b210f91ac31fd9036ce4a75e`). The import is code provenance,
not independent validation of the maintainer's July result narrative.

## Accepted and hardened

- Rick's-Roll milestone and session-note corrections, including the third
  door-crossing lane.
- Uniform-advantage and fixed-count resampling transformers. Both now validate
  the versioned canonical OPD training-record schema, reject invalid
  numerical/sequence geometry, refuse in-place or accidental overwrite, write
  atomically, and emit byte-level source, output, script, schema, and parameter
  receipts.
- Run-level arm statistics. Verification now takes an explicit artifact root
  and quarantines an entire arm when any declared run/agent artifact is absent;
  the r10 reproduction remains a hard preflight gate.
- Two diagnostic probe implementations are retained as historical utilities.
  They launch nothing and are documented for already-running local endpoints;
  their console output is not paper evidence unless captured in a separately
  registered immutable bundle.

## Deliberately excluded

- The new Modal deployment wrapper. This fork's current work is zero-spend and
  uses local endpoints; no paid-cloud deployment is needed or authorized.
- The ad-hoc tool-definition snapshot. Its normalized digest differs from the
  repository's frozen model-visible schema, so accepting it would reintroduce
  train/serve prompt drift.
- The historical OPD data-builder parity patch. It printed a schema choice but
  had no immutable build receipt, tolerated missing/failed source parses, and
  could resume into an output created under different controls. The fork's
  versioned preparation/render contracts are the supported parity path.
- Two post-hoc paper/program-state memos whose causal interpretations are not
  backed by complete launch attestations. A reviewer-accessible score projection
  is now checked in, but the full raw bundles remain private. The detailed
  historical experiment ledger remains explicitly caveated and is subordinate
  to `claims-evidence-matrix.md` and `submission-readiness.md`.

## Claim boundary

The imported July sessions have now been recovered read-only, copy-verified,
content-bound, and replayed at their record-level six-hour boundaries; the nine
scores are reproduced by a clean-code, exact-input, clock-validated receipt in
`research/audits/july-mechanism-results.json`. Exact checkpoint/configuration
receipts, database/reset snapshots, render contracts, and seeds remain absent.
The anonymous `research/artifacts/july-score-replay-v1/` derivative independently
replays the nine scores and binds its 21,524 projected observations to source
records and logs. It does not expose the full transcripts or independently
verify their extraction.
Nothing in this recovery upgrades E3′, E4, Arm-C, Rick's-Roll, or clean-r1 into
a causal paper result. In particular, the earlier database-lane question is still
resolved by evidence, not recollection: a maintainer states the headline runs
used the separate 9001/9011/9021 orchestrator, but publication requires the
corresponding immutable runtime receipts.

## Local verification

The curated tree was syntax-checked and exercised with the repository's pinned
unit environment. Tests cover missing-evidence failure, deterministic
transformations and hashes, invalid/unaligned advantages, malformed JSONL,
overwrite refusal, and incomplete-arm quarantine. No live endpoint, Modal command, GPU, or paid service
was used during the integration.
