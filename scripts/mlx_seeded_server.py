#!/usr/bin/env python3
"""Launch MLX-LM with request-local, explicit-key seeded sampling.

MLX-LM 0.31.3 serves generation from a background thread. On the pinned
MLX 0.32.0 runtime, ``mx.random.seed()`` does not update the random stream used
by that thread, so the server's OpenAI-compatible ``seed`` field is accepted
but has no observable sampling effect. This launcher replaces only the
request-sampler factory: seeded requests use an explicit PRNG key that is split
once per generated token. Unseeded requests retain MLX-LM's native behavior.
"""
from __future__ import annotations

import json
import sys
import threading
from importlib.metadata import version
from types import SimpleNamespace
from typing import Any, Callable

import mlx.core as mx
from mlx_lm import server
from mlx_lm.sample_utils import apply_min_p, apply_top_k, apply_top_p


SAMPLING_CONTRACT_SCHEMA = "kaetram.mlx-explicit-key-sampling.v1"
PINNED_MLX_LM_VERSION = "0.31.3"
PINNED_MLX_VERSION = "0.32.0"
_NATIVE_MAKE_SAMPLER = server._make_sampler


class SeededSamplerError(RuntimeError):
    """Raised when the explicit-key sampling contract cannot be enforced."""


def make_request_sampler(args: Any, tokenizer: Any) -> Callable[[mx.array], mx.array]:
    """Return the native sampler or a request-local explicit-key sampler."""
    if args.seed is None:
        return _NATIVE_MAKE_SAMPLER(args, tokenizer)

    sampling = args.sampling
    temperature = float(sampling.temperature)
    if temperature == 0:
        return lambda logits: mx.argmax(logits, axis=-1)
    if temperature < 0:
        raise SeededSamplerError("temperature must be non-negative")
    if float(sampling.xtc_probability) != 0:
        raise SeededSamplerError(
            "seeded XTC sampling is outside the reviewed explicit-key contract"
        )

    key = mx.random.key(int(args.seed))

    def sampler(logprobs: mx.array) -> mx.array:
        nonlocal key
        if 0 < float(sampling.top_p) < 1:
            logprobs = apply_top_p(logprobs, float(sampling.top_p))
        if float(sampling.min_p) != 0:
            logprobs = apply_min_p(logprobs, float(sampling.min_p))
        if int(sampling.top_k) > 0:
            logprobs = apply_top_k(logprobs, int(sampling.top_k))
        split_keys = mx.random.split(key, 2)
        key = split_keys[0]
        return mx.random.categorical(
            logprobs * (1 / temperature),
            key=split_keys[1],
        )

    return sampler


def install_patch() -> None:
    if version("mlx-lm") != PINNED_MLX_LM_VERSION:
        raise SeededSamplerError("mlx-lm version differs from the reviewed runtime")
    if mx.__version__ != PINNED_MLX_VERSION:
        raise SeededSamplerError("mlx version differs from the reviewed runtime")
    server._make_sampler = make_request_sampler


def self_test() -> dict[str, Any]:
    """Exercise explicit-key categorical sampling on MLX's server thread shape."""
    outputs: list[int] = []
    native_outputs: list[int] = []
    failures: list[str] = []

    def exercise() -> None:
        try:
            tokenizer = SimpleNamespace(eos_token_id=0, encode=lambda _text: [])
            sampling = SimpleNamespace(
                temperature=1.0,
                top_p=0.95,
                top_k=3,
                min_p=0.0,
                xtc_probability=0.0,
                xtc_threshold=0.0,
            )
            uniform_logits = mx.array([[0.0, 0.0, 0.0, 0.0]])
            for seed in (730001, 730002, 730003, 730004, 730005):
                args = SimpleNamespace(seed=seed, sampling=sampling)
                mx.random.seed(seed)
                native_sampled = _NATIVE_MAKE_SAMPLER(
                    args, tokenizer
                )(uniform_logits)
                mx.eval(native_sampled)
                native_outputs.append(int(native_sampled.item()))
                sampled = make_request_sampler(args, tokenizer)(uniform_logits)
                mx.eval(sampled)
                outputs.append(int(sampled.item()))
        except Exception as exc:  # pragma: no cover - emitted in runtime receipt
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=exercise, name="mlx-seeded-sampler-self-test")
    worker.start()
    worker.join()
    if failures:
        raise SeededSamplerError(f"background-thread self-test failed: {failures}")
    distinct = len(set(outputs))
    if len(outputs) != 5 or distinct < 2:
        raise SeededSamplerError(
            "explicit-key sampler did not distinguish the registered smoke seeds"
        )
    return {
        "schema_version": SAMPLING_CONTRACT_SCHEMA,
        "mlx_lm_version": version("mlx-lm"),
        "mlx_version": mx.__version__,
        "request_seeds": [730001, 730002, 730003, 730004, 730005],
        "native_sampled_token_ids": native_outputs,
        "native_distinct_seed_outputs": len(set(native_outputs)),
        "sampled_token_ids": outputs,
        "distinct_seed_outputs": distinct,
        "execution_thread": "background",
        "prng": "mx.random.key + per-token mx.random.split + categorical(key=...)",
    }


def main() -> None:
    install_patch()
    if sys.argv[1:] == ["--self-test"]:
        print(json.dumps(self_test(), sort_keys=True))
        return
    server.main()


if __name__ == "__main__":
    main()
