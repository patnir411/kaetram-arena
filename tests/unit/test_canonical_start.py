from __future__ import annotations

import json
import sys
from types import ModuleType
from pathlib import Path

import pytest

import canonical_start
from canonical_start import CANONICAL_INITIAL_STATE
from eval_harness import (
    parse_log,
    validate_canonical_first_observation,
    validate_eval_session_terminals,
)


def _observe_payload() -> dict:
    return {
        **CANONICAL_INITIAL_STATE,
        "finished_quests": [{"name": "Miner's Quest"}],
    }


def test_seed_uses_exact_historical_canonical_start(monkeypatch) -> None:
    captured = {}

    def fake_seed(username: str, **kwargs):
        captured["username"] = username
        captured.update(kwargs)
        return {
            "username": username.lower(),
            "player_info": {"password": "never-persist-this"},
            "inventory_slots": [],
            "bank_slots": [],
            "equipment": [],
            "quests": [],
            "achievements": [],
            "skills": [],
            "statistics": {},
        }

    fake_seed_module = ModuleType("bench.seed")
    fake_seed_module.STARTER_KIT = [
        {"index": index, "key": item["key"], "count": item["count"]}
        for index, item in enumerate(canonical_start.STARTER_INVENTORY)
    ]
    fake_seed_module.seed_player = fake_seed
    monkeypatch.setitem(sys.modules, "bench.seed", fake_seed_module)
    receipt = canonical_start.seed_canonical_player("EvalBot", db_name="eval_db")

    assert captured["position"] == (328, 892)
    assert captured["hit_points"] == 69
    assert captured["quests"] == canonical_start.CANONICAL_DB_QUESTS
    assert captured["db_name"] == "eval_db"
    assert receipt["expected_first_observation"] == CANONICAL_INITIAL_STATE
    assert "password" not in json.dumps(receipt)


def test_first_observation_must_match_every_canonical_field() -> None:
    entries = [{"role": "tool", "content": "observe: " + json.dumps(_observe_payload())}]
    assert validate_canonical_first_observation(entries) == CANONICAL_INITIAL_STATE

    payload = _observe_payload()
    payload["pos"] = {"x": 188, "y": 157}
    with pytest.raises(RuntimeError, match="canonical start"):
        validate_canonical_first_observation([
            {"role": "tool", "content": "observe: " + json.dumps(payload)}
        ])


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [{"role": "tool", "content": "query_quest: {}"}],
        [{"role": "tool", "content": "observe: not-json"}],
        [{
            "role": "tool",
            "content": "observe: Error executing tool observe: Login FAILED",
        }],
    ],
)
def test_missing_or_failed_first_observation_is_invalid(entries: list[dict]) -> None:
    with pytest.raises(RuntimeError):
        validate_canonical_first_observation(entries)


def test_parse_log_does_not_count_thinking_as_a_second_turn(tmp_path: Path) -> None:
    path = tmp_path / "session.log"
    records = [
        {
            "type": "assistant",
            "turn": 1,
            "message": {
                "content": [{"type": "thinking", "thinking": "I should observe"}]
            },
        },
        {
            "type": "assistant",
            "turn": 1,
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "observe",
                    "input": {},
                }]
            },
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert parse_log(path) == [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{"name": "observe", "args": {}, "id": "call-1"}],
    }]


def _write_terminal(path: Path, reason: str, *, is_error: bool = False) -> Path:
    path.write_text(json.dumps({
        "type": "result",
        "subtype": "session_end",
        "result": reason,
        "terminal_reason": reason,
        "is_error": is_error,
    }) + "\n")
    return path


def test_session_terminals_require_rollovers_then_duration_exhaustion(
    tmp_path: Path,
) -> None:
    first = _write_terminal(tmp_path / "session_1.log", "context_overflow")
    final = _write_terminal(tmp_path / "session_2.log", "duration_exhausted")
    terminals = validate_eval_session_terminals([first, final])
    assert [record["terminal_reason"] for record in terminals] == [
        "context_overflow",
        "duration_exhausted",
    ]


@pytest.mark.parametrize(
    ("records", "match"),
    [
        ([], "no session logs"),
        ([("api_errors", True)], "ended in error"),
        ([("interrupted", False)], "expected 'duration_exhausted'"),
        (
            [("duration_exhausted", False), ("duration_exhausted", False)],
            "intermediate session",
        ),
    ],
)
def test_incomplete_or_failed_session_chain_is_invalid(
    tmp_path: Path,
    records: list[tuple[str, bool]],
    match: str,
) -> None:
    logs = [
        _write_terminal(
            tmp_path / f"session_{index}.log",
            reason,
            is_error=is_error,
        )
        for index, (reason, is_error) in enumerate(records, start=1)
    ]
    with pytest.raises(RuntimeError, match=match):
        validate_eval_session_terminals(logs)


def test_session_log_requires_exactly_one_terminal(tmp_path: Path) -> None:
    path = tmp_path / "session_1.log"
    terminal = {
        "type": "result",
        "subtype": "session_end",
        "result": "duration_exhausted",
        "terminal_reason": "duration_exhausted",
        "is_error": False,
    }
    path.write_text(json.dumps(terminal) + "\n" + json.dumps(terminal) + "\n")
    with pytest.raises(RuntimeError, match="exactly one session_end"):
        validate_eval_session_terminals([path])
