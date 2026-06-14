"""Regression tests for the log_analysis decode kernel — qwen-harness quirks
that have bitten analysis: the [format] recovery-note prefix and plain-string
validation errors."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "log_analysis"))

from parse import decode_tool_result_content, decode_kaetram_tool_output  # noqa: E402


def test_format_note_stripped_observe():
    raw = ("[format] Your previous call used non-canonical syntax and was "
           "auto-recovered. Emit ...\n\n"
           '{"pos": {"x": 220, "y": 108}}\n\nASCII_MAP:###')
    payload, ascii_map = decode_tool_result_content(raw)
    assert isinstance(payload, dict) and payload["pos"]["x"] == 220
    assert ascii_map == "###"


def test_format_note_stripped_compact():
    raw = '[format] note here\n\n{"items_gained": {"tomato": 1}}'
    payload, _ = decode_tool_result_content(raw)
    assert payload == {"items_gained": {"tomato": 1}}


def test_format_note_error_string_preserved():
    raw = "[format] note\n\nError executing tool gather: 1 validation error"
    payload, _ = decode_tool_result_content(raw)
    assert isinstance(payload, str) and payload.startswith("Error executing tool")


def test_no_format_note_unaffected():
    raw = '{"items_gained": "none"}'
    assert decode_tool_result_content(raw)[0] == {"items_gained": "none"}


def test_kaetram_output_strips_format_note():
    raw = '[format] x\n\n{"a": 1}\n\nASCII_MAP:grid'
    payload, am = decode_kaetram_tool_output(raw)
    assert payload == {"a": 1} and am == "grid"
