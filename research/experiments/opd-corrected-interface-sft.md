# Corrected-interface pretokenized SFT adapter

`scripts/opd/corrected_interface_sft.py` closes the data-handoff boundary for
the registered corrected-interface SFT baseline. It consumes the
`normalized-records.jsonl` and `backend-plan.json` produced by the matched
training backend. It does not consume raw conversations, call a tokenizer or
renderer, invoke Modal, update weights, or claim a checkpoint.

## Frozen input contract

The adapter accepts only a backend plan for the `corrected_interface_sft` arm
whose execution status is `not_run`. Before producing output it:

- re-hashes the cell config and every frozen interface file;
- recomputes the registered render/interface digest and matches it against the
  backend plan, every record identity, and every corrected-interface evidence
  record;
- re-hashes the normalized JSONL bundle and checks its declared record count;
- requires aligned integer `input_ids` and `labels`, at least one supervised
  token, labels equal to their causal input token wherever they are not `-100`,
  and null OPD-only arrays; and
- recomputes action-token, teacher-scoring-token, and environment-interaction
  totals and requires exact equality with both the backend plan and frozen cell
  contract; and
- verifies the shared parameterization hash: a fresh bf16 LoRA per cell, rank
  64/alpha 64, zero dropout, no bias, frozen base weights, and the registered
  seven projection modules.

The emitted JSONL contains only pretokenized arrays, weights, identities,
budget usage, and a hash of the normalized source record. State and history
content are deliberately not copied or rendered.

## Safe preparation

```bash
python3 scripts/opd/corrected_interface_sft.py \
  --backend-plan <cell-directory>/backend-plan.json \
  --dry-run
```

Dry-run writes nothing. A non-dry invocation uses exclusive-create semantics
to create `pretokenized-sft-records.jsonl`,
`corrected-interface-sft-plan.json`, and
`corrected-interface-sft-result.json`. The result remains
`prepared_not_trained` with `trainer_execution_status: not_run`.

## Remaining execution boundary

The historical `finetune/train_modal.py` accepts conversation records and
renders them during loading, so it is intentionally not an entry point for this
artifact. `finetune/train_corrected_interface_sft.py` is the reviewed
direct-token entry point. Its collator only pads frozen arrays, its shifted
causal loss preserves `-100` masks and registered record weights, and its PEFT
setup refuses any trainable non-LoRA parameter. It requires `--execute`, an
exact experiment and cell confirmation, and
`KAETRAM_ENABLE_ACCELERATOR_TRAINING=1`; preflight remains read-only and reports
`executable_pending_compute`. No accelerator compute was performed while adding
this adapter.
