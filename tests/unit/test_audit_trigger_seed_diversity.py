import copy

import pytest

from scripts.opd.audit_trigger_incidence_artifact import AuditError
from scripts.opd.audit_trigger_seed_diversity import (
    semantic_response_sha256,
    summarize_seed_diversity,
)


def _registration():
    return {
        "snapshots": {"base": {}},
        "conditions": [{"condition_id": "condition"}],
        "state_pool": {"state_count": 2},
        "sampling": {"samples_per_state_condition": 3, "base_seed": 100},
    }


def _rows():
    rows = []
    for state_index in range(2):
        for sample_index in range(3):
            rows.append(
                {
                    "snapshot": "base",
                    "condition_id": "condition",
                    "state_id": f"state-{state_index + 1:02d}",
                    "sample_index": sample_index,
                    "seed": 100 + 100 * state_index + sample_index,
                    "status": "ok",
                    "response_message": {
                        "role": "assistant",
                        "content": f"state {state_index}",
                    },
                    "recovery_opportunity": state_index == 0,
                }
            )
    return rows


def test_call_ids_do_not_create_false_semantic_diversity():
    first = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call-one",
                "type": "function",
                "function": {"name": "observe", "arguments": "{}"},
            }
        ],
    }
    second = copy.deepcopy(first)
    second["tool_calls"][0]["id"] = "call-two"

    assert semantic_response_sha256(first) == semantic_response_sha256(second)


def test_content_changes_are_semantic_diversity():
    first = {"role": "assistant", "content": "first"}
    second = {"role": "assistant", "content": "second"}

    assert semantic_response_sha256(first) != semantic_response_sha256(second)


def test_identical_seed_replays_collapse_to_state_outputs():
    result = summarize_seed_diversity(_registration(), _rows())

    assert result["state_condition_groups"] == 2
    assert result["groups_with_identical_semantic_responses"] == 2
    assert result["groups_with_multiple_semantic_responses"] == 0
    assert result["semantic_response_count_after_within_group_deduplication"] == 2
    assert result["collapsed_cells"] == [
        {
            "snapshot": "base",
            "condition_id": "condition",
            "state_outputs": 2,
            "outcome_stable_states": 2,
            "recovery_opportunity_states": 1,
            "opportunity_rate": 0.5,
        }
    ]


def test_seed_schedule_mismatch_is_rejected():
    rows = _rows()
    rows[0]["seed"] = 999

    with pytest.raises(AuditError, match="request seeds"):
        summarize_seed_diversity(_registration(), rows)
