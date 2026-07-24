# Recovery and copy-prior diagnostics

These tools separate three questions that are easy to conflate:

1. Did the model emit malformed tool syntax?
2. Did harness recovery produce an executable tool call?
3. Does malformed syntax become more likely when it appears in the visible
   history or tool documentation?

## Offline recovery audit

```bash
python3 scripts/opd/recovery_audit.py \
  --run-id run_YYYYMMDD_HHMMSS \
  --out artifacts/recovery-audit.json
```

You can pass explicit logs with repeatable `--log` arguments instead. The
report counts malformed emissions, recovered calls, recovered execution
errors, and same-tool repeat recoveries within a configurable window. The
repeat count is deliberately labeled a **relapse proxy**: it is useful for
triage but does not establish that recovery caused the later emission.

## Paired copy-prior diagnostic

First materialize and inspect the design without contacting a model endpoint:

```bash
python3 scripts/opd/copy_prior_diag.py \
  --run-id run_YYYYMMDD_HHMMSS \
  --dry-run --include-text \
  --out artifacts/copy-prior-design.jsonl
```

Then score the matched candidates:

```bash
python3 scripts/opd/copy_prior_diag.py \
  --run-id run_YYYYMMDD_HHMMSS \
  --endpoint teacher-4b=https://ENDPOINT.example \
  --endpoint student-2b=https://ENDPOINT.example \
  --tool-schema-source canonical \
  --out artifacts/copy-prior-scores.jsonl
```

For every recoverable malformed state, the tool crosses four model-visible
contexts (`real`, repaired history, repaired docs, and both repaired) with two
semantically matched completions (recorded malformed syntax and canonical
syntax). The JSONL retains state IDs, source locations, semantic calls, hashes,
and token-level aggregate scores. `--include-text` additionally records full
messages and completions for review; omit it when prompts may contain sensitive
run data. Endpoint URLs are never written.

`--tool-schema-source none` is the default and reproduces the historical OPD
teacher-grading renderer, which omitted `tools=`. Use `canonical` to include the
frozen full schema and test the model-visible native context introduced by the
versioned render-contract PR. Record and report this choice: the two conditions
are different experimental interfaces and must not be pooled.

The sibling `*.summary.json` reports within-state paired effects. Positive
`canonical_minus_malformed` means the canonical rendering received higher
mean token log-probability. Negative `malformed_minus_real` under a repaired
condition is evidence consistent with a copy prior. It is not by itself proof
of a causal training effect; report the sample count and paired distribution,
and keep failed scores in the raw JSONL.
