from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FINETUNE_DIR = REPO_ROOT / "finetune"
for path in (REPO_ROOT, FINETUNE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from render import (  # noqa: E402
    LEGACY_MARKDOWN_R10,
    NATIVE_TOOLS_V1,
    render_messages,
    render_record,
    resolve_checkpoint_render_contract,
    resolve_render_contract,
    validate_request_tools,
)
from tool_surface import (  # noqa: E402
    MODEL_VISIBLE_TOOL_DEFINITIONS,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
    TOOL_SCHEMA_VERSION,
)


class RecordingTokenizer:
    def __init__(self):
        self.calls = []

    def apply_chat_template(self, messages, **kwargs):
        call = {"messages": messages, "kwargs": kwargs}
        self.calls.append(call)
        return json.dumps(call, sort_keys=True)


def _native_metadata():
    return {
        "version": "native-tools-v1",
        "tool_render_mode": NATIVE_TOOLS_V1,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "tool_schema_sha256": MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
        "tools": MODEL_VISIBLE_TOOL_DEFINITIONS,
    }


def test_native_tools_v1_passes_full_schema_and_adapts_openai_arguments():
    tokenizer = RecordingTokenizer()
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "reasoning<tool_call>duplicate</tool_call>",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "warp", "arguments": '{"location":"aynor"}'},
                }
            ],
        },
    ]

    render_messages(
        tokenizer,
        messages,
        render_mode=NATIVE_TOOLS_V1,
        tools=MODEL_VISIBLE_TOOL_DEFINITIONS,
        add_generation_prompt=True,
    )

    call = tokenizer.calls[-1]
    assert call["kwargs"]["tools"] is MODEL_VISIBLE_TOOL_DEFINITIONS
    assert call["kwargs"]["add_generation_prompt"] is True
    assert call["messages"][1]["tool_calls"][0]["function"]["arguments"] == {
        "location": "aynor"
    }
    assert call["messages"][1]["content"] == "reasoning"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == '{"location":"aynor"}'


def test_legacy_markdown_r10_omits_tools_keyword_entirely():
    tokenizer = RecordingTokenizer()
    messages = [
        {
            "role": "assistant",
            "content": "historical<tool_call>bytes</tool_call>",
            "tool_calls": [
                {"function": {"name": "observe", "arguments": "{}"}}
            ],
        }
    ]
    render_messages(
        tokenizer,
        messages,
        render_mode=LEGACY_MARKDOWN_R10,
        tools=None,
        add_generation_prompt=True,
    )
    assert "tools" not in tokenizer.calls[-1]["kwargs"]
    assert tokenizer.calls[-1]["messages"] is messages


def test_only_named_r10_artifacts_receive_legacy_fallback():
    assert resolve_render_contract({"version": "r10"})["tool_render_mode"] == LEGACY_MARKDOWN_R10
    with pytest.raises(ValueError, match="missing required tool_render_mode"):
        resolve_render_contract({"version": "r11"})

    assert resolve_checkpoint_render_contract(
        "kaetram-qwen3.5-9b-r10", None
    )["tool_render_mode"] == LEGACY_MARKDOWN_R10
    for experiment in ("r10", "kaetram-qwen3.5-9b-r11", "kaetram-qwen3.5-9b-r10-copy"):
        with pytest.raises(ValueError, match="missing kaetram_render_contract.json"):
            resolve_checkpoint_render_contract(experiment, None)


def test_native_contract_rejects_any_full_schema_drift():
    metadata = _native_metadata()
    drifted = copy.deepcopy(metadata)
    drifted["tools"][0]["function"]["description"] += " drift"
    with pytest.raises(ValueError, match="schema drift"):
        resolve_render_contract(drifted)


def test_serving_rejects_request_schema_that_differs_from_checkpoint_contract():
    contract = resolve_render_contract(_native_metadata())
    drifted_request = copy.deepcopy(MODEL_VISIBLE_TOOL_DEFINITIONS)
    drifted_request[3]["function"]["parameters"]["properties"]["location"][
        "default"
    ] = "aynor"
    with pytest.raises(ValueError, match="schema drift"):
        validate_request_tools(contract, drifted_request)

    validate_request_tools(contract, MODEL_VISIBLE_TOOL_DEFINITIONS)
    validate_request_tools(contract, None)


def test_legacy_serving_ignores_request_schema_for_historical_compatibility():
    contract = resolve_render_contract({"version": "r10"})
    validate_request_tools(contract, [{"historical": "caller-owned schema"}])


def test_train_and_serve_use_byte_identical_native_rendering():
    record = {"messages": [{"role": "user", "content": "observe"}]}
    system_prompt = "system"
    train_tokenizer = RecordingTokenizer()
    serve_tokenizer = RecordingTokenizer()

    train_text = render_record(
        record,
        system_prompt,
        {},
        train_tokenizer,
        render_mode=NATIVE_TOOLS_V1,
        tools=MODEL_VISIBLE_TOOL_DEFINITIONS,
    )
    serve_text = render_messages(
        serve_tokenizer,
        [{"role": "system", "content": system_prompt}, *record["messages"]],
        render_mode=NATIVE_TOOLS_V1,
        tools=MODEL_VISIBLE_TOOL_DEFINITIONS,
        add_generation_prompt=False,
    )
    assert train_text == serve_text
