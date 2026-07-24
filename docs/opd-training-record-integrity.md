# OPD training-record integrity

The OPD trainer now fails closed unless `--records-path` is paired with
`--records-manifest-path`. Inside the scheduled training job, it verifies the
receipt and every record before model loading or training begins.

The canonical `kaetram-opd-train-record-v2` contract encodes the causal shift
used by the trainer:

- position zero is context and must have label `-100`;
- at least one supervised target must remain after the one-token shift;
- ignored positions have zero advantage and behavior log-probability;
- behavior log-probabilities are finite and non-positive;
- optional `n_action` equals the post-shift supervised-token count.

This prevents a one-token record or a target placed only at position zero from
passing validation even though the trainer would silently discard its signal.

## Receipts

`opd_2b_data.py`, `make_uniform_advantages.py`, and `resample_records.py` emit
create-only receipts. The trainer accepts only those three receipt types and
binds:

- the exact JSONL SHA-256;
- record-schema and validator identities;
- the exact attestor/transformer source identity; and
- transformation-specific parameters and record/token counts; and
- the recursively embedded parent receipt for every derived corpus.

Before session reconstruction, the base corpus builder requires every declared
run to resolve to at least one log and snapshots each log together with its
adjacent session `.meta.json`. Parse failures, empty sessions, missing metadata,
an unbound personality prompt, a tool turn without a reconstructible emission,
or a run with no usable action state aborts the build. Its entrypoint first
copies the builder, bootstrap, parser, evaluator, renderer, prompt assets,
held-out registrations/guard, and OPD sources into an immutable temporary tree,
then starts a clean interpreter from the copied builder. Cached live modules are
rejected; reconstruction imports and reads only from that frozen tree. The
original bytes are re-hashed before sealing, so a change-and-restore race cannot
make the receipt describe inputs different from those consumed.

Both scoring endpoints must expose complete `/health` identity attestations.
Their deployment and checkpoint identities must match the requested artifacts,
their tokenizer hashes must match each other and the local tokenizer snapshot.
The tokenizer is loaded only from an exact temporary copy of that initial
snapshot. The complete original tokenizer directory and both endpoint
attestations are checked again after scoring. The receipt also binds the
held-out bytes and all material parameters.

Every reconstructible action state is accounted for. Endpoint, token-boundary,
prefix, context-length, or mask-alignment failures abort the build rather than
silently shrinking the corpus. Deterministically overlong states are the sole
allowed exclusion; the receipt binds the complete candidate count and digest,
status counts, and the identity/status of every exclusion.

The base builder refuses to resume into any pre-existing output: a partial
build must be retained separately and a fresh sealed build started. Record and
held-out streams acquire their final paths with exclusive-create mode; temporary
transform outputs and receipts are published with atomic create-only links. A
destination created after the initial check is therefore never replaced. Root
receipt emission exists only inside that exclusive fresh-build path; there is
no callable post-hoc builder attestor.

Each transformer requires the source's adjacent receipt, validates its complete
chain before reading records, and embeds that parent plus its canonical digest
in the new receipt. This prevents a uniform or resampled corpus from being
accepted after its builder/model/source/parameter ancestry is removed. Copying
a JSONL file without its matching receipt, editing any link, or training with a
newer validator against an old receipt is an error. Paths are informational so
a byte-identical bundle can be staged at a different mount point; content
hashes are authoritative.

At training time, semantics are replayed through the full transform chain.
Every resample must retain its parent as the exact prefix and append the exact
fixed-seed sample; every uniform-control receipt must match the advantages and
token counts in the preserved records. Repeated uniform transforms are rejected
because a later rewrite would erase the evidence needed to replay the earlier
one.

Historical v1/v2 receipts and post-hoc identity receipts are intentionally not
accepted. A prospective trainable corpus must be freshly built under this
chain.

## Training handoff

Treat `records.jsonl` and its adjacent `records.manifest.json` as one
inseparable bundle. The builder prints both paths and the two mandatory trainer
arguments:

```text
--records-path <bundle>/records.jsonl
--records-manifest-path <bundle>/records.manifest.json
```

Do not upload, copy, or mount the records alone. A handoff is incomplete until
both files arrive byte-for-byte; the trainer then validates the recursive
receipt chain before loading model weights. The builder deliberately does not
print a cloud-volume upload command, so following its default next step cannot
silently omit the authoritative receipt or initiate paid infrastructure.

## Zero-spend probes

`cook_grade_probe.py` and `defect_origin_probe.py` accept loopback endpoints by
default. Each configured hostname must resolve exclusively to loopback
addresses. Remote URLs fail before any HTTP client is used.

The deliberately conspicuous `--allow-metered-remote-endpoints` escape hatch is
reserved for a separately authorized run. The zero-spend protocol never passes
it.
