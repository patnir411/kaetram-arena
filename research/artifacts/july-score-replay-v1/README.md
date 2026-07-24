# July mechanism score-replay artifact

This anonymous artifact contains only the fields needed to replay the historical
Core-3 quest scores: offset-aware run starts and successful `observe` results
projected to quest names and stages. It omits prompts, model actions, maps,
inventory, endpoint addresses, and unrelated player state.

Verify it from the repository root:

```bash
python3.12 scripts/score_july_public_artifact.py \
  --artifact-dir research/artifacts/july-score-replay-v1
```

The artifact proves that the nine documented descriptive scores are present in
the content-bound recovered logs. It does not prove treatment identity or a
causal training effect; see `registry.json` for the exact claim boundary.
