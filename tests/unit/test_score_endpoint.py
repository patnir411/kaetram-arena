"""Sanity tests for the deployed /v1/score endpoint.

These tests hit live Modal endpoints when STUDENT_ENDPOINT and TEACHER_ENDPOINT
env vars are set. They're skipped by default in CI.

Use:
    STUDENT_ENDPOINT=https://...modal.run/v1 \\
    TEACHER_ENDPOINT=https://...modal.run/v1 \\
    pytest tests/unit/test_score_endpoint.py -v
"""
from __future__ import annotations

import asyncio
import os

import pytest

STUDENT = os.environ.get("STUDENT_ENDPOINT")
TEACHER = os.environ.get("TEACHER_ENDPOINT")

needs_endpoints = pytest.mark.skipif(
    not (STUDENT and TEACHER),
    reason="set STUDENT_ENDPOINT and TEACHER_ENDPOINT to run live tests",
)


SIMPLE_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is 2+2?"},
    {"role": "assistant", "content": "<think>Simple arithmetic.</think>\n\n2+2 equals 4."},
]


async def _score(endpoint: str, body: dict, timeout: float = 180.0) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{endpoint}/score", json=body)
        r.raise_for_status()
        return r.json()


@needs_endpoints
async def test_score_shape_student():
    resp = await _score(STUDENT, {"messages": SIMPLE_MESSAGES})
    assert "target_token_ids" in resp
    assert "target_logprobs" in resp
    assert "n_context_tokens" in resp
    assert "n_target_tokens" in resp
    assert len(resp["target_token_ids"]) == resp["n_target_tokens"]
    assert len(resp["target_logprobs"]) == resp["n_target_tokens"]
    # SGLang convention: first entry is None (no preceding token).
    assert resp["target_logprobs"][0] is None
    # Subsequent should be finite negative floats.
    for v in resp["target_logprobs"][1:]:
        assert v is None or (isinstance(v, float) and v <= 0)


@needs_endpoints
async def test_score_shape_teacher():
    resp = await _score(TEACHER, {"messages": SIMPLE_MESSAGES})
    assert len(resp["target_token_ids"]) == resp["n_target_tokens"]
    assert resp["target_logprobs"][0] is None


@needs_endpoints
async def test_score_system_prefix_teacher():
    """Adding system_prefix should shift the boundary (more context tokens)."""
    base = await _score(TEACHER, {"messages": SIMPLE_MESSAGES})
    with_prefix = await _score(
        TEACHER,
        {"messages": SIMPLE_MESSAGES, "system_prefix": "You always think carefully before answering."},
    )
    assert with_prefix["n_context_tokens"] > base["n_context_tokens"], \
        "system_prefix should add context tokens"
    # Target token count should be identical (same assistant message).
    assert with_prefix["n_target_tokens"] == base["n_target_tokens"]


@needs_endpoints
async def test_score_rejects_non_assistant_last_message():
    """Last message must be assistant."""
    import httpx
    body = {"messages": [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "y"},
    ]}
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f"{STUDENT}/score", json=body)
        assert r.status_code == 400


@needs_endpoints
async def test_score_logp_sums_are_finite():
    """sum(target_logprobs[1:]) should be a finite negative float."""
    resp = await _score(STUDENT, {"messages": SIMPLE_MESSAGES})
    finite = [v for v in resp["target_logprobs"] if v is not None]
    assert finite, "expected at least one finite logprob"
    s = sum(finite)
    assert s < 0
    assert s > -1000  # sanity: not pathologically negative for 10-token target
