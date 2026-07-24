from __future__ import annotations

from pathlib import Path

from eval_harness import resolve_system_prompt


REPO = Path(__file__).resolve().parents[2]


def test_isolated_db_usernames_render_one_matched_prompt_identity() -> None:
    first = resolve_system_prompt(
        str(REPO),
        "opdfxr01basec0",
        "completionist",
        prompt_agent_name="EvalCompletionist",
    )
    second = resolve_system_prompt(
        str(REPO),
        "opdfxr01r3c1",
        "completionist",
        prompt_agent_name="EvalCompletionist",
    )
    assert first == second
    assert "EvalCompletionist" in first
    assert "opdfxr01basec0" not in first
    assert "opdfxr01r3c1" not in second


def test_legacy_prompt_identity_defaults_to_db_username() -> None:
    prompt = resolve_system_prompt(str(REPO), "LegacyEval", "completionist")
    assert "LegacyEval" in prompt
