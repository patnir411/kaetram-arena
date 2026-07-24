# Seeded trigger-incidence replication

Status: registered before outcomes. No study state has been sent to an endpoint
under this registration.

The first local trigger-incidence study found a 15--30 point increase in
recoverable content-only calls when the native tool schema was present.
However, all five nominal seeds within every state-condition group produced
one semantic response. That made v1 a valid deterministic interface diagnostic,
not a stochastic replication.

V2 repairs that limitation without rewriting v1. It uses the reviewed
request-local MLX sampling contract, requires a model-level seed gate before
any study outcome directory can be created, and selects 20 different source
logs from the same historical Base run. All 20 v1 source paths are explicitly
excluded. Selection is based only on registered paths, metadata, and
parseability.

The seed gate uses an unrelated plain-English prompt with no tool schema. Five
different seeds must yield at least three semantic responses, and an exact
repeat of the third seed must reproduce exactly. The gate is run and retained
separately for Base, R2, and R3. A failed gate prohibits that checkpoint's
outcome launch.

After a passed gate, the design remains the same 3 checkpoints by 4 interface
conditions by 20 states by 5 paired seeds: 1,200 local requests. The primary
outcome and three finite-grid contrasts are unchanged. The directional
replication criterion is fixed in advance: the native-tools contrast must be
strictly positive at all three checkpoints. Documentation and interaction
contrasts have no directional success criterion.

The analysis also reports semantic-response and primary-outcome heterogeneity
across seeds. It reports no p-values or confidence intervals. The states are
outcome-unseen but still come from one historical run, so this is not an
independent population sample and cannot establish training superiority,
recovery benefit, quest improvement, or broad generalization.

Machine-readable registration:
[`local-trigger-incidence-v2.json`](local-trigger-incidence-v2.json).

The versioned runner is
[`scripts/opd/trigger_incidence_probe_v2.py`](../../scripts/opd/trigger_incidence_probe_v2.py).
Its commands are deliberately ordered: `prepare`, then `seed-gate` for a
checkpoint, then `run` with that exact gate directory, and finally `analyze`
with all three run and gate directories. Every command refuses dirty source;
the run accepts loopback endpoints only.
