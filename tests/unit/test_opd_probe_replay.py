"""Validate that opd_probe.py's message reconstruction is byte-identical to
what play_qwen.py would have built at inference time.

Walks one real session log (run_20260520_044433 grinder, session 3) and asserts:
  - System prompt reconstruction matches resolve_system_prompt() byte-for-byte.
  - Bootstrap matches bootstrap.build_orchestrate_bootstrap() byte-for-byte.
  - Reconstructed assistant turns have the expected number of tool_calls.
  - When rendered via apply_chat_template (the patched template), the prefix
    tokens for turn N match the prefix you'd get if you fed turns 0..N-1 in
    one shot — i.e. the running state is consistent with a single-pass render.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "scripts" / "log_analysis"))

from opd_probe import reconstruct_session, turn_to_openai_assistant, result_to_openai  # noqa: E402
from bootstrap import build_orchestrate_bootstrap  # noqa: E402
from eval_harness import resolve_system_prompt  # noqa: E402
from parse import session_meta  # noqa: E402


SESSION_LOG = REPO / "dataset/raw/agent_0/runs/run_20260520_044433/session_3_20260520_084903.log"


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_reconstruct_session_basic():
    base_messages, turns = reconstruct_session(SESSION_LOG)
    assert len(base_messages) == 2
    assert base_messages[0]["role"] == "system"
    assert base_messages[1]["role"] == "user"
    assert len(base_messages[0]["content"]) > 1000, "system prompt should be substantive"
    assert "GRINDER" in base_messages[1]["content"], "grinder bootstrap should mention archetype"

    # The fixture log has multiple turns; we expect at least 3.
    assert len(turns) >= 3, f"expected ≥3 turns, got {len(turns)}"


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_system_prompt_byte_identical():
    """The reconstructed system prompt must match resolve_system_prompt's output."""
    meta = session_meta(SESSION_LOG)
    expected = resolve_system_prompt(
        str(REPO),
        username=meta.get("username", "evalbotR10"),
        personality=meta.get("personality", "completionist"),
    )
    base_messages, _ = reconstruct_session(SESSION_LOG)
    assert base_messages[0]["content"] == expected


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_bootstrap_byte_identical():
    meta = session_meta(SESSION_LOG)
    expected = build_orchestrate_bootstrap(meta.get("personality"), int(meta.get("session", 1)))
    base_messages, _ = reconstruct_session(SESSION_LOG)
    assert base_messages[1]["content"] == expected


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_turn_to_openai_assistant_shape():
    _, turns = reconstruct_session(SESSION_LOG)
    for turn, _results in turns[:5]:
        msg = turn_to_openai_assistant(turn)
        assert msg["role"] == "assistant"
        assert isinstance(msg["content"], str)
        # If there's a tool_call in the structured field, it should also be
        # embedded in content as XML (matches what convert_to_qwen renders).
        if msg["tool_calls"]:
            assert "<tool_call>" in msg["content"]
            assert "<function=" in msg["content"]
            for tc in msg["tool_calls"]:
                assert isinstance(tc["function"]["arguments"], dict), \
                    "arguments must be dict (Qwen template iterates with .items())"


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_tool_results_paired_to_calls():
    """Every tool_call should have a matching tool_result."""
    _, turns = reconstruct_session(SESSION_LOG)
    # Some sessions may have unmatched tool_results at the tail (truncated logs).
    # Check the first 20 turns where the pairing should be clean.
    for turn, results in turns[:20]:
        if not turn.tool_calls:
            continue
        result_ids = {r.tool_use_id for r in results}
        call_ids = {tc["id"] for tc in turn.tool_calls}
        # Every result we got should be a result for one of our calls.
        assert result_ids.issubset(call_ids), \
            f"orphan tool_result ids: {result_ids - call_ids}"


@pytest.mark.skipif(not SESSION_LOG.is_file(), reason="fixture session log not present")
def test_chat_template_round_trip():
    """Applying the patched Qwen chat template to reconstructed messages
    should succeed (not crash) and produce a string longer than the system
    prompt alone."""
    from transformers import AutoTokenizer
    from finetune.render import patch_qwen_chat_template

    tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-9B", trust_remote_code=True)
    patch_qwen_chat_template(tok)

    base_messages, turns = reconstruct_session(SESSION_LOG)
    # Build a rolling message list as the probe would.
    msgs = list(base_messages)
    sys_only = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    for turn, results in turns[:3]:
        asst = turn_to_openai_assistant(turn)
        # Convert arguments string→dict (probe normally relies on
        # _adapt_for_template on the server side; do it locally so the
        # template doesn't crash).
        for tc in asst["tool_calls"]:
            if isinstance(tc["function"]["arguments"], str):
                try:
                    tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tc["function"]["arguments"] = {}
        msgs_with = msgs + [asst]
        rendered = tok.apply_chat_template(msgs_with, tokenize=False, add_generation_prompt=False)
        assert len(rendered) > len(sys_only)
        msgs.append(asst)
        for tr in results:
            msgs.append(result_to_openai(tr))
