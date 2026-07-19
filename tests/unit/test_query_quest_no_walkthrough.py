from __future__ import annotations

from mcp_server.utils import apply_no_walkthrough_policy


def _full_response():
    return {
        "name": "Desert Quest",
        "matched_name": "Desert Quest",
        "off_limits": False,
        "npc": "Dying Soldier",
        "requirements": {"skills": []},
        "unlocks": {"on_finish": ["lakesworld"]},
        "actual_rewards": [],
        "stage_summary": ["Talk to Dying Soldier", "Deliver CD"],
        "walkthrough": "Talk, deliver, return.",
        "walkthrough_steps": ["Go to the soldier"],
        "items_needed": "CD",
        "item_sources": {"CD": "given"},
        "crafting_chain": {},
        "boss": {},
        "tips": "Use the house door",
        "station_locations": {"cooking": [{"x": 1, "y": 2}]},
        "current_step": {
            "accepted": True,
            "stage": 1,
            "needed": {"cd": 1},
            "have": {"cd": 1},
            "remaining": {"cd": 0},
            "turn_in_npc": "Wife",
            "recommended_action": "Deliver the CD",
            "preconditions": "stand by Wife",
        },
        "live_gate_status": {"gated": False, "blockers": []},
    }


def test_redacts_only_static_and_advisory_fields_for_exact_heldout_quest(monkeypatch):
    monkeypatch.setenv("KAETRAM_NO_WALKTHROUGH", "1")
    monkeypatch.setenv("KAETRAM_HELDOUT_QUEST", "Desert Quest")
    redacted = apply_no_walkthrough_policy(_full_response(), "Desert Quest")
    assert redacted == {
        "name": "Desert Quest",
        "matched_name": "Desert Quest",
        "off_limits": False,
        "no_walkthrough": True,
        "current_step": {"accepted": True, "stage": 1},
        "live_gate_status": {"gated": False, "blockers": []},
    }


def test_policy_does_not_change_other_quests_or_normal_runs(monkeypatch):
    full = _full_response()
    monkeypatch.setenv("KAETRAM_NO_WALKTHROUGH", "1")
    monkeypatch.setenv("KAETRAM_HELDOUT_QUEST", "Desert Quest")
    assert apply_no_walkthrough_policy(full, "Foresting") is full
    monkeypatch.delenv("KAETRAM_NO_WALKTHROUGH")
    assert apply_no_walkthrough_policy(full, "Desert Quest") is full


def test_play_qwen_forwards_eval_policy_to_mcp_subprocess():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "play_qwen.py").read_text()
    assert 'mcp_env["KAETRAM_NO_WALKTHROUGH"]' in source
    assert 'mcp_env["KAETRAM_HELDOUT_QUEST"]' in source
