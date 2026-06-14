#!/usr/bin/env python3
"""Offline reconstruction utilities for Qwen agent session logs.

Rebuilds a logged session into OpenAI-format chat messages that are byte-identical
to what play_qwen.py rendered at inference time (system prompt + bootstrap via the
canonical helpers; assistant turns paired with their tool_use_id-matched results).
This lets offline analysis re-query a model on the agent's *own* states — e.g. the
on-policy DAgger probe (`opd_onpolicy_probe.py`), which rolls out the student and
asks the teacher what it would do on the student's states.

Validated by `tests/unit/test_opd_probe_replay.py`.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "log_analysis"))

from bootstrap import build_orchestrate_bootstrap  # noqa: E402
from eval_harness import resolve_system_prompt  # noqa: E402
from parse import iter_lines, decode_tool_result_content, session_meta  # noqa: E402

# Deployed Modal endpoints (scale-to-zero). Resolve from env like cli_adapter /
# eval_harness — set MODAL_WORKSPACE (or the explicit endpoint vars) on a live
# machine; the placeholder workspace is anonymized for publication.
_MODAL_WORKSPACE = os.environ.get("MODAL_WORKSPACE", "workspace")
STUDENT = os.environ.get("KAETRAM_QWEN_SFT_ENDPOINT") or (
    f"https://{_MODAL_WORKSPACE}--kaetram-qwen-serve-inference-serve.modal.run/v1"
)
TEACHER = os.environ.get("KAETRAM_QWEN_BASE_ENDPOINT") or (
    f"https://{_MODAL_WORKSPACE}--kaetram-qwen-base-inference-serve.modal.run/v1"
)

# Keep system+bootstrap + the last N history turn-pairs so deep-quest states fit
# under the 32K serving limit and contexts are length-comparable.
MAX_HIST_TURNS = 14


# ── Message reconstruction ────────────────────────────────────────────────────


@dataclass
class AssistantTurn:
    """One logical assistant turn assembled from consecutive log lines (a turn
    may span multiple JSONL records: thinking / text / one per tool_use)."""
    line_no: int
    thinking: str = ""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    short_tool_names: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    tool_use_id: str
    name: str
    full_name: str
    result_str: str
    payload: Any = None
    error: str | None = None


def _short_name(full: str) -> str:
    return full.split("__")[-1] if "__" in full else full


def reconstruct_session(log_path: Path) -> tuple[list[dict], list[tuple[AssistantTurn, list[ToolResult]]]]:
    """Walk a session log and return (system_bootstrap_messages, turns).

    System + bootstrap are rebuilt via the canonical helpers so the prefix is
    byte-identical to what play_qwen.py rendered. Turns pair each assistant turn
    with its tool_use_id-matched results, in chronological order.
    """
    meta = session_meta(log_path)
    if not meta:
        return [], []
    personality = meta.get("personality") or "completionist"
    username = meta.get("username") or "evalbotR10"
    session_n = int(meta.get("session", 1))

    system_prompt = resolve_system_prompt(str(REPO), username=username, personality=personality)
    bootstrap_text = build_orchestrate_bootstrap(personality, session_n)
    base_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": bootstrap_text},
    ]

    turns: list[tuple[AssistantTurn, list[ToolResult]]] = []
    current: AssistantTurn | None = None
    pending_ids: list[str] = []
    results_by_id: dict[str, ToolResult] = {}
    saw_user_since_assistant = False

    for line_no, rec in iter_lines(log_path):
        rtype = rec.get("type")
        if rtype == "assistant":
            content = rec.get("message", {}).get("content", []) or []
            if current is not None and saw_user_since_assistant:
                results = [results_by_id[tid] for tid in pending_ids if tid in results_by_id]
                turns.append((current, results))
                current = None
                pending_ids = []
                results_by_id = {}
                saw_user_since_assistant = False
            if current is None:
                current = AssistantTurn(line_no=line_no)
            for blk in content:
                btype = blk.get("type")
                if btype == "thinking":
                    current.thinking += blk.get("thinking", "")
                elif btype == "text":
                    current.text += blk.get("text", "")
                elif btype == "tool_use":
                    tool_id = blk.get("id", "")
                    full_name = blk.get("name", "")
                    short = _short_name(full_name)
                    args = blk.get("input", {}) or {}
                    current.tool_calls.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": short, "arguments": args},
                    })
                    current.short_tool_names.append(short)
                    pending_ids.append(tool_id)
            continue
        if rtype == "user":
            saw_user_since_assistant = True
            content = rec.get("message", {}).get("content", []) or []
            for blk in content:
                if blk.get("type") != "tool_result":
                    continue
                tid = blk.get("tool_use_id", "")
                raw = blk.get("content")
                if isinstance(raw, list):
                    raw = "".join(x.get("text", "") for x in raw if isinstance(x, dict))
                if not isinstance(raw, str):
                    raw = json.dumps(raw)
                payload, _ascii = decode_tool_result_content(raw)
                err = None
                if isinstance(payload, dict) and payload.get("error"):
                    err = str(payload["error"])[:200]
                short = ""
                if current and tid:
                    for tc in current.tool_calls:
                        if tc["id"] == tid:
                            short = tc["function"]["name"]
                            break
                results_by_id[tid] = ToolResult(
                    tool_use_id=tid, name=short, full_name="",
                    result_str=raw, payload=payload, error=err,
                )
            continue
        if rtype == "result":
            break

    if current is not None:
        results = [results_by_id[tid] for tid in pending_ids if tid in results_by_id]
        turns.append((current, results))

    return base_messages, turns


def turn_to_openai_assistant(turn: AssistantTurn) -> dict:
    """Render an AssistantTurn as an OpenAI-compatible assistant message
    (thinking in <think>...</think>, text, then inline <tool_call> XML + the
    structured tool_calls field — matching what play_qwen.py emits)."""
    parts = []
    if turn.thinking:
        parts.append(f"<think>\n{turn.thinking}\n</think>\n\n")
    if turn.text:
        parts.append(turn.text)
    for tc in turn.tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments", {}) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        xml = f"\n<tool_call>\n<function={name}>\n"
        for k, v in args.items():
            xml += f"<parameter={k}>{v}</parameter>\n"
        xml += "</function>\n</tool_call>"
        parts.append(xml)
    return {"role": "assistant", "content": "".join(parts), "tool_calls": turn.tool_calls}


def result_to_openai(tr: ToolResult) -> dict:
    return {"role": "tool", "content": tr.result_str,
            "tool_call_id": tr.tool_use_id, "name": tr.name}


def est_tokens(messages) -> int:
    tot = 0
    for m in messages:
        c = m.get("content", "")
        if not isinstance(c, str):
            c = json.dumps(c)
        tot += len(c)
        for tc in m.get("tool_calls", []) or []:
            tot += len(json.dumps(tc))
    return tot // 4  # ~4 chars/token
