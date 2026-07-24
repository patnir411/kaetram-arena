# Historical evidence audit

The May/June r10 and OPD claims are derived from raw per-agent session logs.
An absent run must never be interpreted as a zero-score run.

Inventory the expected paper bundles:

```bash
python3 scripts/audit_historical_artifacts.py \
  --out artifacts/historical-artifact-inventory.json
```

The command exits nonzero until every expected agent/run directory contains at
least one `session_*.log`. Use `--report-only` only when producing a diagnostic
inventory; it does not make an incomplete bundle suitable for analysis.

Once the logs and `dataset/qwen_sft/train.json` are restored, rerun:

```bash
python3 scripts/r10_stats.py
python3 scripts/r10_credit_diag.py
```

Both analyses now fail with explicit missing paths before computing any
statistics. A complete path inventory proves availability only. Use immutable
run manifests and cryptographic hashes to establish provenance and integrity.
