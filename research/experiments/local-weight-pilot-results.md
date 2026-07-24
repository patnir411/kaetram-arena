# Local matched-weights pilot: completed evidence report

Status: completed exploratory feasibility pilot; not confirmatory.

The preregistered `local-render-parity-pilot-v1` ran on July 23, 2026 with no
Modal, cloud GPU, or paid endpoint. It crossed the public Base, OPD R2, and OPD
R3 2B snapshots with three paired inference/environment seeds. Every cell used
the completionist prompt identity, recovery off, the canonical 17-tool schema,
one locked Base tokenizer, one patched render contract, and a five-minute
canonical-start episode.

## Integrity

- Manifest SHA-256: `9333602cd9ed98191d3fa6093645e7fa969e903a83200880acbc06f929519692`
- Source commit: `4209b72566f63167a850b890dd21cef39f0905cc`
- Game commit: `7a3d722e8e200ca44fd959099386b42a5fbe0cb5`
- Game entrypoint SHA-256: `b0f9e42b0da63dc7bb1f9172136cd8a1361f762e683b72011172db286c256916`
- Tokenizer SHA-256: `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`
- Render-contract SHA-256: `11908184ad9ba71352740d4d429790ea427446b11146be202bf873bd7163979f`
- Bundle-index SHA-256: `cf62c370b0e5d133f3049b6aab3c114d4795e5a9ed97dc4f68807ff4f32f1c26`
- Completed cells: 9 valid, 0 invalid.
- Artifact audit: 261 files in sealed per-cell inventories rehashed, 0
  mismatches.
- Each cell contains an exact canonical-start projection and a seed-bound
  `mulberry32-sha256-v1` environment RNG receipt.

The retained local bundle is
`kaetram-local-experiments/local-render-parity-pilot-v1-20260723b`. Run:

```bash
python3 scripts/opd/analyze_local_weight_pilot.py <bundle> \
  --allow-legacy-v1 \
  --expected-bundle-index-sha256 cf62c370b0e5d133f3049b6aab3c114d4795e5a9ed97dc4f68807ff4f32f1c26
```

The explicit legacy opt-in is required because this retained bundle predates the
v2 database and full-snapshot attestations; its report is therefore labeled
`legacy_v1_unattested`. The analyzer still binds the analysis to the recorded
sealed-ledger root, rehashes every inventoried file, validates the v1
endpoint/game/seed identities and exact canonical projections, inspects terminal
chains and raw emissions, recomputes game metrics, and regenerates the
descriptive summary. The three top-level preflight logs are retained but are not
members of the sealed per-cell inventories.

## Preregistered diagnostics

| Weights | Structured calls by cell | Mean calls/min | Raw generations total | No-call generations total | Logged `API error:` lines | Mean budget overrun |
|---|---:|---:|---:|---:|---:|---:|
| Base | 5, 6, 5 | 0.968 | 37 | 21 | 0 | 30.2 s |
| OPD R2 | 1, 5, 6 | 0.736 | 34 | 22 | 0 | 27.9 s |
| OPD R3 | 5, 4, 5 | 0.872 | 35 | 21 | 0 | 20.7 s |

All nine cells produced at least one structured action. Across the 106 retained
raw endpoint generations, 42 emitted one structured call and 64 emitted no
call. Every one of the 42 calls had a canonical tool name and JSON-object
arguments. The analyzer found no `API error:` line in retained stderr; this is
not a claim about failures that were never logged. The overrun is the
time needed to finish the final in-flight generation after the fixed 300-second
budget expired; it was retained rather than truncated.

## Exploratory game metrics

No cell advanced any quest or Core-3 stage. Unique-position counts were
Base `[2, 3, 2]`, R2 `[1, 2, 2]`, and R3 `[2, 2, 2]`. The observed action
totals were 31 `observe`, 8 `warp`, 2 `interact_npc`, and 1 `navigate`.
Observed XP deltas (a preregistered diagnostic) were Base `[55, 35, 40]`, R2
`[60, 50, 45]`, and R3 `[40, 50, 55]`.

## Claim boundary

This pilot supports one narrow observation: the local pipeline—common matched
renderer, hash-identified endpoint weights, seed-attested game-RNG lane,
canonical reset, and artifact retention—executed all nine registered
prospective cells with internally hash-checked evidence and no paid endpoint.

It does **not** show that any weight snapshot is better, that OPD improves
quest performance, that recovery helps, or that the method generalizes. Three
five-minute cells per snapshot are neither powered nor long enough for the
paper's task-level outcome. The pilot cannot be pooled into the confirmatory
estimate.
