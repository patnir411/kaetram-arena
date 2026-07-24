# Local weights × recovery exploratory result

This directory contains the anonymous public subset of the create-only export
from the preregistered 18-cell, 30-minute local factorial:

- Base, OPD round-2, and OPD round-3 public 2B checkpoints;
- recovery off and on;
- three paired inference/environment seeds per arm.

All 18 registered cells were launcher-valid. The analyzer rehashed 1,266 sealed
files and bound them to bundle index
`c6c69bbf3416cf8405c211032b5c1e3fdc250c264325faed69bf618e941aab0e`.
Across 958 raw generations, 294 contained canonical structured calls and 664
contained no structured call. There were no logged API errors, malformed
emissions, recovery opportunities, or recovered calls. Four cells advanced one
stage in the registered completionist lane.

The zero recovery-opportunity count is a failed manipulation check: the
recovery-on arm never executed its intervention. Recovery-on/off differences
therefore cannot be interpreted as recovery effects. The three fixed public
checkpoints also do not represent independently trained method replicates, and
three seed blocks per arm are descriptive only.

The bundle is explicitly `legacy_v1_unattested`: the run preceded the
prospective effective-database and full-model-snapshot attestations. A
post-launch filesystem audit found database alignment, but that cannot
retroactively upgrade the evidence tier. The raw 33 MB bundle remains local and
is not yet a reviewer-accessible artifact, so this subset supports internal
hash-checked analysis rather than independent reproduction.

`cells.csv` and `paired-differences.csv` are byte-exact analyzer outputs.
`public-summary.json` and the two public paper tables are outcome-preserving
derivatives with clearer labels for the single-lane stage metric, pooled
structured-call rate, and zero recovery opportunities.

The complete original report, artifact index, and one-time unblinding receipt
remain preserved locally. They are intentionally not in this anonymous subset:
the original code-provenance record included the repository remote. Future
analyzer outputs omit that identity-bearing field. `public-artifact-index.json`
hashes every public data/table file in this directory.
