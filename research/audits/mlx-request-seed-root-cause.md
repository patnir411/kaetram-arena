# MLX request-seed root-cause audit

Date: 2026-07-23

## Why this audit exists

The completed `local-trigger-incidence-v1` study sent five distinct OpenAI
request seeds for every checkpoint, interface condition, and historical state.
A post-outcome audit found the same semantic response for all five seeds in all
240 groups. The published v1 artifact correctly collapses those duplicates and
makes no stochastic-sampling claim. This audit explains the failure and defines
a prospective repair; it does not retroactively change v1.

## Isolation result

The request gateway preserved `seed`, `temperature=1.0`, `top_p=0.95`,
`top_k=20`, and `presence_penalty=1.5`. MLX-LM 0.31.3 also parsed the seed and
called `mx.random.seed(args.seed)` before constructing its sampler. Three
checks localized the defect:

1. Direct generation in the process's main thread produced different
   continuations for seeds 730001--730005 with the same public Base checkpoint.
2. The unmodified HTTP server returned the same continuation for those seeds,
   even for a high-entropy prompt and deliberately flattened sampling.
3. A four-category synthetic sampler varied by seed in the main thread but
   returned the same category for every seed when run in a background thread,
   matching MLX-LM's `ResponseGenerator` execution shape.

The pinned MLX 0.32.0 random state used by that background thread is therefore
not changed by the server's `mx.random.seed()` call. The API accepted and
recorded the seed without making it effective.

## Prospective repair

`scripts/mlx_seeded_server.py` replaces only MLX-LM's per-request sampler
factory. For seeded requests it creates an explicit key with
`mx.random.key(seed)`, splits that request-local key once per generated token,
and passes the child key directly to `mx.random.categorical`. Top-p, min-p, and
top-k filtering retain MLX-LM's order and implementation. Greedy requests and
unseeded requests retain native behavior. Seeded XTC is rejected because it is
outside the reviewed explicit-key contract.

The local endpoint:

- runs a background-thread synthetic self-test before loading a model;
- refuses startup unless the five smoke seeds yield at least two distinct
  samples;
- hashes the patched server source and self-test receipt into the render
  contract; and
- publishes the resulting sampling-contract hash in `/health`.

On the real Base checkpoint, five seeds then produced five distinct
continuations under the study's original sampling parameters, while three
repeats of seed 730003 were byte-identical. This establishes both diversity
across seeds and reproducibility within a seed for the repaired serving path.

## Scientific boundary

The repair was written after v1 outcomes were known. It cannot be used to
reinterpret v1's 1,200 requests as independent stochastic draws. Any replacement
study must receive a new identifier, frozen registration, endpoint attestation,
and outcome directory. The v1 result remains a valid 240-output fixed-state
interface diagnostic; a replacement can estimate seed-conditional variation
only after its own preregistration and manipulation check.
