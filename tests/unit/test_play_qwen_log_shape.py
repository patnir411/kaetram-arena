"""play_qwen.py emits Claude-shaped stream-json into per-session log files.

Locks the on-disk log shape so dashboard heartbeat, log_analysis/parse.py,
extract_turns.py, and dashboard/parsers.py all read Qwen runs through the
existing Claude-branch parsers (no Qwen-specific code paths).

The emitters now take a SessionLogger and write to file (not stdout). Tests
build a logger pointed at a tmp_path and read records back.

If this test changes, you've changed the log contract and downstream tooling
will need a matching update.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import play_qwen
from cli_adapter import detect_log_format
from scripts.log_analysis.parse import parse_session


def _make_logger(tmp_path: Path) -> play_qwen.SessionLogger:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    return play_qwen.SessionLogger(
        run_dir=run_dir,
        sandbox_dir=sandbox,
        harness_meta={
            "agent_id": 0, "personality": "grinder", "harness": "qwen",
            "model": "r10-sft", "username": "QwenGrinder",
            "auth_mode": "subscription", "max_budget_usd": None,
        },
    )


def _read_records(logger: play_qwen.SessionLogger) -> list[dict]:
    """Read JSONL records from the logger's current session log file."""
    path = logger.session_log_path
    assert path is not None and path.is_file()
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def test_log_system_init_is_claude_shaped(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_system_init(
        logger,
        personality="grinder",
        model="r10-sft",
        endpoint="https://example.modal.run/v1",
        tools=["observe", "attack"],
    )
    logger.close()
    recs = _read_records(logger)
    assert len(recs) == 1
    r = recs[0]
    assert r["type"] == "system" and r["subtype"] == "init"
    assert r["model"] == "r10-sft"
    assert r["harness"] == "qwen"
    assert r["tools"] == ["observe", "attack"]
    assert r["session_n"] == 1


def test_log_assistant_splits_thinking_text_and_tool_use(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    parsed_calls = [{"id": "tu_1", "name": "observe", "args": {"radius": 5}}]
    play_qwen.log_assistant(
        logger,
        turn=3,
        content="<think>plan to observe</think>I will look around now.",
        parsed_calls=parsed_calls,
        usage={"prompt_tokens": 1200, "completion_tokens": 80},
    )
    logger.close()
    recs = _read_records(logger)
    # One record per content block (thinking, text, tool_use), Claude-shape.
    assert [r["type"] for r in recs] == ["assistant", "assistant", "assistant"]
    blocks = [r["message"]["content"][0] for r in recs]
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "plan to observe"
    assert blocks[1]["type"] == "text"
    assert blocks[1]["text"] == "I will look around now."
    assert blocks[2]["type"] == "tool_use"
    assert blocks[2]["id"] == "tu_1"
    assert blocks[2]["name"] == "observe"
    assert blocks[2]["input"] == {"radius": 5}
    # Usage stamped on the LAST record only, mapped to Anthropic keys.
    assert "usage" not in recs[0]["message"]
    assert "usage" not in recs[1]["message"]
    assert recs[2]["message"]["usage"] == {
        "input_tokens": 1200, "output_tokens": 80,
    }


def test_log_assistant_text_only_emits_one_record(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_assistant(
        logger, turn=1, content="hello world", parsed_calls=None, usage=None,
    )
    logger.close()
    recs = _read_records(logger)
    assert len(recs) == 1
    assert recs[0]["type"] == "assistant"
    assert recs[0]["message"]["content"] == [
        {"type": "text", "text": "hello world"},
    ]


def test_log_assistant_skips_when_nothing_to_emit(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_assistant(
        logger, turn=1, content="", parsed_calls=None, usage=None,
    )
    logger.close()
    assert _read_records(logger) == []


def test_raw_model_emission_preserves_pre_rewrite_content_and_arguments(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    call = SimpleNamespace(
        id="raw_1",
        function=SimpleNamespace(
            name="gather",
            arguments='<parameter=item="Oak">',
        ),
    )
    play_qwen.log_raw_model_emission(
        logger,
        turn=4,
        content='<function=gather("Oak")>',
        tool_calls=[call],
        usage={"prompt_tokens": 10, "completion_tokens": 3},
    )
    logger.close()
    record = _read_records(logger)[0]
    assert record["type"] == "raw_model_emission"
    assert record["content"] == '<function=gather("Oak")>'
    assert record["tool_calls"] == [{
        "id": "raw_1",
        "name": "gather",
        "arguments": '<parameter=item="Oak">',
    }]
    assert record["usage"] == {"input_tokens": 10, "output_tokens": 3}


def test_log_tool_result_pairs_via_tool_use_id(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_tool_result(
        logger, turn=3, tool_use_id="tu_1", name="observe", result="HP=42 LV=5",
    )
    logger.close()
    recs = _read_records(logger)
    assert len(recs) == 1
    r = recs[0]
    assert r["type"] == "user"
    blk = r["message"]["content"][0]
    assert blk["type"] == "tool_result"
    assert blk["tool_use_id"] == "tu_1"
    assert blk["content"] == "HP=42 LV=5"


def test_log_session_end_is_claude_result_record(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_session_end(logger, turn=42, reason="context_overflow")
    logger.close()
    recs = _read_records(logger)
    assert len(recs) == 1
    r = recs[0]
    assert r["type"] == "result"
    assert r["num_turns"] == 42
    assert r["terminal_reason"] == "context_overflow"
    assert r["is_error"] is False


def test_full_log_round_trips_through_claude_parser(tmp_path: Path) -> None:
    """End-to-end: a synthetic Qwen session log writes via the new emitters,
    is detected as 'claude' by cli_adapter.detect_log_format, and parses
    cleanly via scripts/log_analysis/parse.parse_session — including
    tool_use → tool_result pairing."""
    logger = _make_logger(tmp_path)
    logger.open_next_session()
    play_qwen.log_system_init(
        logger,
        personality="grinder",
        model="r10-sft",
        endpoint="https://example/v1",
        tools=["observe", "attack"],
    )
    play_qwen.log_assistant(
        logger,
        turn=1,
        content="<think>scan the area</think>Calling observe.",
        parsed_calls=[{"id": "tu_1", "name": "observe", "args": {}}],
        usage={"prompt_tokens": 100, "completion_tokens": 10},
    )
    play_qwen.log_tool_result(
        logger, turn=1, tool_use_id="tu_1", name="observe",
        result="player at (10,10)",
    )
    play_qwen.log_session_end(logger, turn=1, reason="context_overflow")
    logger.close()

    log_path = logger.session_log_path
    assert detect_log_format(log_path) == "claude"

    sv = parse_session(log_path)
    assert sv.init_info.get("model") == "r10-sft"
    assert sv.n_thinking == 1
    assert sv.n_text == 1
    assert len(sv.tool_calls) == 1
    tc = sv.tool_calls[0]
    assert tc.name == "observe"
    assert tc.thinking == "scan the area"
    assert tc.text == "Calling observe."
    # tool_result should have been paired into the same ToolCall.
    assert tc.result_raw == "player at (10,10)"
    # Result record populated.
    assert sv.result_summary.get("terminal_reason") == "context_overflow"
