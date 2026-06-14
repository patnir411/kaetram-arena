"""JSONL session log parsers for Claude Code and Codex CLI session logs.

All parsers are incremental: per-filepath offset + accumulator are persisted
across calls via dashboard._log_tail.IncrementalCache. The first call on a
freshly-rotated log is O(file_size); every subsequent call is O(new_bytes).
On a 200 MB log with 1 KB of new bytes, this is the difference between
~800 ms and <1 ms per parse. See _log_tail.py for the tail mechanics.
"""

import json
import logging
import re
import sys
from collections import OrderedDict
from pathlib import Path
from dashboard.constants import sanitize
from dashboard._log_tail import IncrementalCache, tail_new_lines

logger = logging.getLogger(__name__)

# Import shared format detection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cli_adapter import detect_log_format


def _kaetram_tool_summary(tool: str, inp: dict) -> str:
    """Generate a readable summary for a Kaetram MCP tool call."""
    action = tool.split("__")[-1]
    if action == "attack":
        return f"Attack {inp.get('mob_name', '?')}"
    if action == "navigate":
        return f"Navigate to ({inp.get('x', '?')}, {inp.get('y', '?')})"
    if action == "warp":
        return f"Warp to {inp.get('location', 'mudwich').title()}"
    if action == "interact_npc":
        return f"Talk to {inp.get('npc_name', '?')}"
    if action == "eat_food":
        return f"Eat food (slot {inp.get('slot', '?')})"
    if action == "equip_item":
        return f"Equip (slot {inp.get('slot', '?')})"
    if action == "set_attack_style":
        return f"Style: {inp.get('style', 'hack')}"
    if action == "observe":
        return "Read game state"
    if action == "stuck_reset":
        return "Reset stuck detection"
    if action == "cancel_nav":
        return "Cancel navigation"
    if action == "respawn":
        return "Respawn + warp"
    return action


_parse_cache_claude = IncrementalCache(max_entries=25)
_parse_cache_codex = IncrementalCache(max_entries=25)
_live_stats_cache_claude = IncrementalCache(max_entries=25)
_live_stats_cache_codex = IncrementalCache(max_entries=25)


def parse_session_log(filepath):
    """Parse a session log (auto-detecting Claude or Codex format).

    Incremental: only new bytes since the last call are parsed; the prior
    accumulator (events list, turn counter, tokens, etc.) is preserved across
    calls. On rotation/truncation we reset and re-parse from offset 0.
    Returns a {events, turn, cost_usd, tokens, model, duration_ms, num_turns} dict.
    """
    fmt = detect_log_format(Path(filepath))
    if fmt == "codex":
        return _incremental_parse(
            filepath, _parse_cache_codex,
            _init_codex_acc, _consume_codex_obj, _finalize_codex_acc,
        )
    # Claude, Gemini, OpenCode all use compatible stream-json
    return _incremental_parse(
        filepath, _parse_cache_claude,
        _init_claude_acc, _consume_claude_obj, _finalize_claude_acc,
    )


def _incremental_parse(filepath, cache, init_fn, consume_fn, finalize_fn):
    """Generic driver: tail new lines, feed each into consume_fn, finalize.

    Rotation/truncation is detected here before the tail generator runs so the
    first call after a fresh log gets a clean accumulator from offset 0.
    """
    slot = cache.get_slot(filepath, init_fn)
    try:
        size = Path(filepath).stat().st_size
    except OSError:
        return slot.get("result") or finalize_fn(slot["acc"])
    if size < slot.get("offset", 0):
        cache.reset_slot(slot, init_fn)
    advanced = False
    for obj in tail_new_lines(filepath, slot):
        consume_fn(slot["acc"], obj)
        advanced = True
    if advanced or slot.get("result") is None:
        slot["result"] = finalize_fn(slot["acc"])
    return slot["result"]


def _init_claude_acc() -> dict:
    """Fresh accumulator for the Claude/Gemini/OpenCode parser.

    All cross-line state lives here so the incremental driver can persist it
    across calls. Loop-local-only state (single-line scratch vars) stays inline
    in _consume_claude_obj.
    """
    return {
        "events": [],
        "turn": 0,
        "cost_usd": 0,
        "model": "",
        "tokens": {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0},
        "last_context": 0,
        "seen_msg_ids": set(),
        "duration_ms": 0,
        "num_turns": 0,
        # tool_use_id → event index. A tool_result in a later line attaches
        # to the tool event from an earlier line, possibly across an
        # incremental call boundary, so this must persist across calls.
        "tool_idx_by_id": {},
        # Step-boundary tracking for the "(silent) deliberated for Ns" row.
        "last_step_started_at": 0,
        "last_step_had_text": True,
    }


def _consume_claude_obj(state: dict, obj: dict) -> None:
    """Apply one parsed JSON event to the Claude/Gemini/OpenCode accumulator."""
    events = state["events"]
    tokens = state["tokens"]
    tool_idx_by_id = state["tool_idx_by_id"]
    seen_msg_ids = state["seen_msg_ids"]

    t = obj.get("type", "")

    # Gemini: flat init event with model
    if t == "init":
        if not state["model"]:
            state["model"] = obj.get("model", "")
        return

    # OpenCode emits a "text" event for the model's visible content. Two cases:
    #   - Plain prose: terse OODA narration between tool calls — surface as
    #     `talk` (🗣️).
    #   - <think>...</think> wrapped content: chain-of-thought from the
    #     DeepSeek SSE-rewriting proxy (scripts/nim_proxy.py merges
    #     reasoning_content into delta.content, since opencode's
    #     openai-compatible provider doesn't read reasoning_content directly).
    #     Split out as `thinking` (🧠) so the dashboard renders it correctly.
    if t == "text":
        part = obj.get("part") or {}
        txt = part.get("text") or obj.get("text") or ""
        if txt:
            think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL)
            seen_cot: set[str] = state.setdefault("seen_cot_per_turn", {}).setdefault(
                state["turn"], set()
            )
            def _emit_thinking(cot_text: str):
                cot_text = cot_text.strip()
                if not cot_text:
                    return
                # Dedupe identical thinking within the same turn — DeepSeek can
                # echo prior reasoning when our proxy round-trips <think> tags
                # in the assistant history (a known artifact pending an
                # opencode-side fix that omits reasoning_content from
                # request history).
                key = cot_text[:200]
                if key in seen_cot:
                    return
                seen_cot.add(key)
                events.append({
                    "turn": state["turn"], "type": "thinking",
                    "text": sanitize(cot_text[:8000]),
                })
            last_end = 0
            for m in think_pattern.finditer(txt):
                pre = txt[last_end:m.start()].strip()
                if pre:
                    events.append({
                        "turn": state["turn"], "type": "text",
                        "text": sanitize(pre[:8000]),
                    })
                _emit_thinking(m.group(1))
                last_end = m.end()
            tail = txt[last_end:].strip()
            # Handle unclosed <think> from a stream that ended mid-thinking
            # (proxy's _finalize_think emits </think> on stream end, so this
            # path is rare — defensive only).
            if tail.startswith("<think>"):
                _emit_thinking(tail[len("<think>"):])
            elif tail:
                events.append({
                    "turn": state["turn"], "type": "text",
                    "text": sanitize(tail[:8000]),
                })
        # Either talk or reasoning counts as "the step said something" — both
        # suppress the (silent) deliberated placeholder.
        state["last_step_had_text"] = True
        return

    # OpenCode reasoning part — chain-of-thought stream from providers that
    # emit it (e.g. DeepSeek V4 with thinking mode + interleaved config). Maps
    # to the existing 🧠 thinking renderer.
    if t == "reasoning":
        part = obj.get("part") or {}
        txt = (part.get("text") or obj.get("text") or "").strip()
        if txt:
            events.append({
                "turn": state["turn"], "type": "thinking",
                "text": sanitize(txt[:8000]),
            })
        state["last_step_had_text"] = True
        return

    # Step boundaries — surface silent thinking time. Some models emit zero
    # `text` events between tool calls; show a "(silent) Ns" thinking row so
    # the user can see the model paused to decide even when it didn't verbalize.
    if t in ("step_start", "step-start"):
        state["last_step_started_at"] = obj.get("timestamp") or 0
        state["last_step_had_text"] = False
        return
    if t in ("step_finish", "step-finish"):
        end_ts = obj.get("timestamp") or 0
        start_ts = state["last_step_started_at"]
        had_text = state["last_step_had_text"]
        if not had_text and start_ts and end_ts > start_ts:
            secs = round((end_ts - start_ts) / 1000.0, 1)
            events.append({
                "turn": state["turn"], "type": "thinking",
                "text": f"(silent) deliberated for {secs}s",
            })
        state["last_step_had_text"] = True   # reset
        # Fall through to step_finish token-accounting block below.

    # Gemini: flat tool_use event / OpenCode: flat tool_use with part.tool.
    # OpenCode wraps tool name + input + output inside `part.state`.
    if t == "tool_use":
        part = obj.get("part") or {}
        oc_state = part.get("state") or {}  # not the parser state — OpenCode's
        tool = obj.get("tool_name") or part.get("tool") or "unknown"
        # OpenCode prefixes MCP tools as kaetram_<name> (no double underscore).
        tool_norm = tool.replace("mcp_kaetram_", "").replace("kaetram_", "")
        tool_display = tool_norm if tool != tool_norm else tool.replace("mcp_kaetram_", "")
        inp = obj.get("parameters") or oc_state.get("input") or {}
        summary = ""
        detail = ""
        if "kaetram" in tool:
            canonical = "mcp__kaetram__" + tool_norm
            summary = _kaetram_tool_summary(canonical, inp)
            detail = json.dumps(inp)[:500] if inp else ""
        elif inp:
            parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
            summary = " ".join(parts)
        state["turn"] += 1
        # OpenCode embeds the tool OUTPUT inside the same tool_use event.
        oc_output = oc_state.get("output")
        result_text = ""
        if isinstance(oc_output, str) and oc_output.strip():
            result_text = sanitize(oc_output[:8000])
        events.append({
            "turn": state["turn"], "type": "tool",
            "tool": tool_display,
            "tool_full": tool,
            "summary": sanitize(summary),
            "detail": sanitize(detail),
            "result": result_text,
            "id": obj.get("tool_id") or part.get("callID", ""),
        })
        return

    # Synthetic harness-level errors written by play.sh when a session ends
    # abnormally (e.g. opencode hits NIM 429 and produces zero step_finish events).
    if t == "harness_error":
        err = obj.get("error", "harness error")
        backoff = obj.get("backoff_secs")
        label = f"{err}" if backoff is None else f"{err} (sleep {backoff}s)"
        state["turn"] += 1
        events.append({
            "turn": state["turn"], "type": "error",
            "summary": sanitize(str(label)[:240]),
        })
        return

    # Gemini: flat tool_result event
    if t == "tool_result":
        output = obj.get("output", "")
        if isinstance(output, str) and output.strip():
            events.append({"turn": state["turn"], "type": "result", "text": sanitize(output[:8000])})
        return

    # OpenCode token accounting lives in step_finish events.
    if t in ("step_finish", "step-finish"):
        tkn = (obj.get("part") or {}).get("tokens") or {}
        if tkn:
            tokens["input"]        += tkn.get("input", 0)
            tokens["output"]       += tkn.get("output", 0)
            cache = tkn.get("cache") or {}
            tokens["cache_create"] += cache.get("write", 0)
            tokens["cache_read"]   += cache.get("read", 0)
            state["last_context"] = (tkn.get("input", 0)
                + cache.get("write", 0) + cache.get("read", 0))
        state["num_turns"] += 1
        return

    if t == "assistant":
        msg = obj.get("message", {})
        if not state["model"]:
            state["model"] = msg.get("model", "")
        msg_id = msg.get("id", "")
        if msg_id and msg_id not in seen_msg_ids:
            seen_msg_ids.add(msg_id)
            usage = msg.get("usage", {})
            tokens["output"] += usage.get("output_tokens", 0)
            tokens["cache_create"] += usage.get("cache_creation_input_tokens", 0)
            tokens["cache_read"] += usage.get("cache_read_input_tokens", 0)
            tokens["input"] += usage.get("input_tokens", 0)
            state["last_context"] = (usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0))
        contents = msg.get("content", [])
        for c in contents:
            ct = c.get("type", "")
            if ct == "tool_use":
                tool = c.get("name", "unknown")
                tool_display = tool.replace("mcp__playwright__", "pw:").replace("mcp__kaetram__", "")
                inp = c.get("input", {})
                summary = ""
                detail = ""
                # Kaetram MCP tools — generate readable summaries
                if tool.startswith("mcp__kaetram__"):
                    summary = _kaetram_tool_summary(tool, inp)
                    detail = json.dumps(inp)[:500] if inp else ""
                elif "code" in inp:
                    detail = inp["code"][:500]
                    code = inp["code"][:120]
                    summary = code.split("return ")[1].split("'")[1] if "return '" in code else code[:80]
                elif "command" in inp:
                    summary = inp["command"][:80]
                    detail = inp["command"]
                elif "url" in inp:
                    summary = inp["url"][:80]
                    detail = inp["url"]
                elif "file_path" in inp:
                    summary = inp["file_path"].split("/")[-1]
                    detail = inp["file_path"]
                elif "query" in inp:
                    summary = inp["query"][:80]
                    detail = inp.get("query", "")
                elif "path" in inp:
                    summary = str(inp["path"])[:80]
                elif "pattern" in inp:
                    summary = inp["pattern"][:80]
                    detail = json.dumps(inp, indent=2)[:500]
                elif "text" in inp:
                    summary = inp["text"][:80]
                elif inp:
                    parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
                    summary = " ".join(parts)
                state["turn"] += 1
                tool_use_id = c.get("id", "")
                events.append({
                    "turn": state["turn"], "type": "tool",
                    "tool": tool_display,
                    "tool_full": tool,
                    "summary": sanitize(summary),
                    "detail": sanitize(detail),
                    "result": "",   # populated by matching tool_result
                    "id": tool_use_id,
                })
                if tool_use_id:
                    tool_idx_by_id[tool_use_id] = len(events) - 1
            elif ct == "text":
                text = c.get("text", "")
                if text.strip():
                    events.append({"turn": state["turn"], "type": "text", "text": sanitize(text)})
            elif ct == "thinking":
                thinking = c.get("thinking", "")
                if thinking.strip():
                    events.append({"turn": state["turn"], "type": "thinking", "text": sanitize(thinking)})
        return

    # Claude tool_result lives in user-role messages; attach to the matching
    # tool event so the activity feed expand panel can show the full output.
    if t == "user":
        msg = obj.get("message", {})
        contents = msg.get("content", []) if isinstance(msg, dict) else []
        if not isinstance(contents, list):
            return
        for c in contents:
            if not isinstance(c, dict):
                continue
            if c.get("type") != "tool_result":
                continue
            tuid = c.get("tool_use_id", "")
            raw = c.get("content", "")
            if isinstance(raw, list):
                parts = []
                for blk in raw:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        parts.append(blk.get("text", ""))
                    elif isinstance(blk, str):
                        parts.append(blk)
                text = "\n".join(p for p in parts if p)
            else:
                text = raw if isinstance(raw, str) else ""
            if not text:
                continue
            stripped = text.strip()
            if stripped.startswith("{") and '"result"' in stripped[:20]:
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict) and "result" in parsed:
                        inner = parsed["result"]
                        if isinstance(inner, str):
                            text = inner
                        elif isinstance(inner, (dict, list)):
                            text = json.dumps(inner, indent=2)
                except (json.JSONDecodeError, ValueError):
                    pass
            idx = tool_idx_by_id.get(tuid)
            if idx is not None and 0 <= idx < len(events):
                events[idx]["result"] = sanitize(text[:8000])
        return

    if t == "result":
        state["cost_usd"] = obj.get("total_cost_usd", 0)
        state["duration_ms"] = obj.get("duration_ms", 0)
        state["num_turns"] = obj.get("num_turns", 0)
        return


def _finalize_claude_acc(state: dict) -> dict:
    tokens = state["tokens"]
    return {
        "events": state["events"],
        "turn": state["turn"],
        "cost_usd": round(state["cost_usd"], 4),
        "model": state["model"],
        "tokens": {
            "input": tokens["input"],
            "output": tokens["output"],
            "cache_create": tokens["cache_create"],
            "cache_read": tokens["cache_read"],
            "context": state["last_context"],
            "total": state["last_context"] + tokens["output"],
        },
        "duration_ms": state["duration_ms"],
        "num_turns": state["num_turns"],
    }


def _extract_codex_content_blocks(obj):
    """Extract content blocks from a Codex JSON event.

    Codex --json emits item.started/item.completed events with mcp_tool_call items.
    We normalize these to content block lists for the parsers.
    """
    t = obj.get("type", "")
    item = obj.get("item", {})
    item_type = item.get("type", "")

    # Primary format: item.started with mcp_tool_call → tool_use block
    if t == "item.started" and item_type == "mcp_tool_call":
        tool_name = item.get("tool", "unknown")
        # Normalize MCP tool names: "server__tool" → "mcp__server__tool"
        if "__" in tool_name and not tool_name.startswith("mcp__"):
            tool_name = f"mcp__{tool_name}"
        args = item.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {"raw": args}
        yield "assistant", [{"type": "tool_use", "name": tool_name,
                             "input": args, "id": item.get("id", "")}]
        return

    # Primary format: item.completed with mcp_tool_call → tool_result block
    if t == "item.completed" and item_type == "mcp_tool_call":
        result = item.get("result", {})
        if isinstance(result, dict):
            content = result.get("content", [])
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            if text_parts:
                yield "user", [{"type": "tool_result", "content": "\n".join(text_parts),
                                "tool_use_id": item.get("id", "")}]
        elif isinstance(result, str):
            yield "user", [{"type": "tool_result", "content": result,
                            "tool_use_id": item.get("id", "")}]
        return

    # Fallback: message.content[] (other Codex formats)
    msg = obj.get("message", {})
    if isinstance(msg, dict) and "content" in msg:
        role = obj.get("type", msg.get("role", "assistant"))
        yield role, msg.get("content", [])
        return

    # Fallback: top-level content array
    if "content" in obj and isinstance(obj.get("content"), list):
        yield obj.get("role", obj.get("type", "assistant")), obj["content"]
        return


def _init_codex_acc() -> dict:
    return {
        "events": [],
        "turn": 0,
        "cost_usd": 0,
        "model": "",
        "tokens": {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0},
        "last_context": 0,
        "duration_ms": 0,
        "num_turns": 0,
        "seen_ids": set(),
    }


def _consume_codex_obj(state: dict, obj: dict) -> None:
    events = state["events"]
    tokens = state["tokens"]
    seen_ids = state["seen_ids"]

    # Extract model from various locations
    if not state["model"]:
        state["model"] = (obj.get("model", "")
                 or obj.get("message", {}).get("model", "")
                 or obj.get("response", {}).get("model", ""))

    # Extract usage/tokens from various locations
    usage = (obj.get("usage", {})
             or obj.get("message", {}).get("usage", {})
             or obj.get("response", {}).get("usage", {}))
    if usage:
        inp_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0))
        tokens["input"] += inp_tok
        tokens["output"] += out_tok
        ctx = inp_tok + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        if ctx > 0:
            state["last_context"] = ctx

    # Summary/result event (end of session)
    t = obj.get("type", "")
    if t == "result":
        state["cost_usd"] = obj.get("total_cost_usd", state["cost_usd"])
        state["duration_ms"] = obj.get("duration_ms", state["duration_ms"])
        state["num_turns"] = obj.get("num_turns", state["num_turns"])
        return
    if t == "summary":
        state["cost_usd"] = obj.get("cost_usd", obj.get("total_cost_usd", state["cost_usd"]))
        state["duration_ms"] = obj.get("duration_ms", state["duration_ms"])
        return

    # Process content blocks from any recognized structure
    for role, content_list in _extract_codex_content_blocks(obj):
        if not isinstance(content_list, list):
            continue
        for c in content_list:
            if not isinstance(c, dict):
                continue
            ct = c.get("type", "")

            if ct in ("tool_use", "function_call"):
                tool = c.get("name", c.get("function", "unknown"))
                tool_display = tool.replace("mcp__playwright__", "pw:")
                inp = c.get("input", c.get("arguments", {}))
                if isinstance(inp, str):
                    try:
                        inp = json.loads(inp)
                    except (json.JSONDecodeError, ValueError):
                        inp = {"raw": inp}
                summary = ""
                detail = ""
                if isinstance(inp, dict):
                    if "code" in inp:
                        detail = inp["code"][:500]
                        code = inp["code"][:120]
                        summary = code.split("return ")[1].split("'")[1] if "return '" in code else code[:80]
                    elif "command" in inp:
                        summary = inp["command"][:80]
                        detail = inp["command"]
                    elif inp:
                        parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
                        summary = " ".join(parts)
                call_id = c.get("id", c.get("call_id", ""))
                if call_id and call_id in seen_ids:
                    continue
                if call_id:
                    seen_ids.add(call_id)
                state["turn"] += 1
                events.append({
                    "turn": state["turn"], "type": "tool",
                    "tool": tool_display,
                    "tool_full": tool,
                    "summary": sanitize(summary),
                    "detail": sanitize(detail),
                    "id": call_id,
                })

            elif ct == "text":
                text = c.get("text", "")
                if text.strip():
                    events.append({"turn": state["turn"], "type": "text", "text": sanitize(text)})

            elif ct == "thinking":
                thinking = c.get("thinking", c.get("text", ""))
                if thinking.strip():
                    events.append({"turn": state["turn"], "type": "thinking", "text": sanitize(thinking)})

            elif ct in ("tool_result", "function_call_output"):
                # Tool results may contain game state — record as text for activity feed
                result_text = c.get("content", c.get("output", c.get("text", "")))
                if isinstance(result_text, list):
                    for item in result_text:
                        if isinstance(item, dict):
                            result_text = item.get("text", "")
                            break
                    else:
                        result_text = ""
                if isinstance(result_text, str) and result_text.strip():
                    snippet = result_text[:200]
                    events.append({"turn": state["turn"], "type": "text", "text": sanitize(f"[result] {snippet}")})


def _finalize_codex_acc(state: dict) -> dict:
    tokens = state["tokens"]
    return {
        "events": state["events"],
        "turn": state["turn"],
        "cost_usd": round(state["cost_usd"], 4),
        "model": state["model"] or "codex",
        "tokens": {
            "input": tokens["input"],
            "output": tokens["output"],
            "cache_create": tokens["cache_create"],
            "cache_read": tokens["cache_read"],
            "context": state["last_context"],
            "total": state["last_context"] + tokens["output"],
        },
        "duration_ms": state["duration_ms"],
        "num_turns": state["num_turns"] or state["turn"],
    }


def quick_session_summary(filepath):
    """Read cost/turns/model from the result event at end of session log (fast — reads last 10KB only)."""
    fmt = detect_log_format(Path(filepath))

    cost = 0
    turns = 0
    model = ""
    duration_ms = 0
    try:
        with open(filepath) as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 10240))
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)

                    if fmt == "codex":
                        # Codex: try multiple summary structures
                        t = obj.get("type", "")
                        if t in ("result", "summary"):
                            cost = obj.get("total_cost_usd", obj.get("cost_usd", cost))
                            turns = obj.get("num_turns", turns)
                            duration_ms = obj.get("duration_ms", duration_ms)
                        if not model:
                            model = (obj.get("model", "")
                                     or obj.get("message", {}).get("model", "")
                                     or obj.get("response", {}).get("model", ""))
                        # Count function_call events as turns
                        if t in ("function_call",) or obj.get("type") == "response":
                            resp = obj.get("response", {})
                            for item in resp.get("output", []):
                                if isinstance(item, dict) and item.get("type") in ("function_call", "tool_use"):
                                    turns += 1
                    else:
                        # Claude / Gemini format
                        if obj.get("type") == "result":
                            cost = obj.get("total_cost_usd", 0)
                            turns = obj.get("num_turns", 0)
                            duration_ms = obj.get("duration_ms", 0)
                            for m in (obj.get("modelUsage") or {}):
                                model = m
                                break
                        elif obj.get("type") == "init" and not model:
                            model = obj.get("model", "")
                        elif obj.get("type") == "assistant" and not model:
                            model = obj.get("message", {}).get("model", "")
                except Exception:
                    pass
    except Exception:
        pass
    return {"cost_usd": round(cost, 4), "turns": turns, "model": model or (fmt if fmt != "unknown" else ""), "duration_ms": duration_ms}


def _init_stats_acc() -> dict:
    return {
        "turns": 0,
        "context_tokens": 0,
        "output_tokens": 0,
        "model": "",
        "cost_usd": 0,
        "duration_ms": 0,
        "rate_limit": None,
        "is_overage": False,
        "seen_msg_ids": set(),
        "fmt": "",  # set lazily on first call
    }


def _consume_stats_claude_obj(state: dict, obj: dict) -> None:
    seen_msg_ids = state["seen_msg_ids"]
    t = obj.get("type", "")

    if t == "init" and not state["model"]:
        state["model"] = obj.get("model", "")
    elif t == "tool_use":
        state["turns"] += 1
    elif t == "assistant":
        msg = obj.get("message", {})
        if not state["model"]:
            state["model"] = msg.get("model", "")
        msg_id = msg.get("id", "")
        if msg_id and msg_id not in seen_msg_ids:
            seen_msg_ids.add(msg_id)
            usage = msg.get("usage", {})
            inp = usage.get("input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            out = usage.get("output_tokens", 0)
            ctx = inp + cache_create + cache_read
            if ctx > 0:
                state["context_tokens"] = ctx
            state["output_tokens"] += out
        for c in msg.get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_use":
                state["turns"] += 1
    elif t == "rate_limit_event":
        info = obj.get("rate_limit_info", {})
        state["rate_limit"] = {
            "status": info.get("overageStatus"),
            "type": info.get("rateLimitType"),
            "resets_at": info.get("resetsAt"),
            "using_overage": info.get("isUsingOverage", False),
        }
        if info.get("isUsingOverage"):
            state["is_overage"] = True
    elif t == "result":
        state["turns"] = obj.get("num_turns", state["turns"])
        state["cost_usd"] = obj.get("total_cost_usd", 0)
        state["duration_ms"] = obj.get("duration_ms", 0)
        for m in (obj.get("modelUsage") or {}):
            state["model"] = m
            break


def _consume_stats_codex_obj(state: dict, obj: dict) -> None:
    if not state["model"]:
        state["model"] = (obj.get("model", "")
                 or obj.get("message", {}).get("model", "")
                 or obj.get("response", {}).get("model", ""))
    usage = (obj.get("usage", {})
             or obj.get("message", {}).get("usage", {})
             or obj.get("response", {}).get("usage", {}))
    if usage:
        inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        out = usage.get("output_tokens", usage.get("completion_tokens", 0))
        ctx = inp + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        if ctx > 0:
            state["context_tokens"] = ctx
        state["output_tokens"] += out
    for _role, blocks in _extract_codex_content_blocks(obj):
        if not isinstance(blocks, list):
            continue
        for c in blocks:
            if isinstance(c, dict) and c.get("type") in ("tool_use", "function_call"):
                state["turns"] += 1
    t = obj.get("type", "")
    if t in ("result", "summary"):
        state["turns"] = obj.get("num_turns", state["turns"])
        state["cost_usd"] = obj.get("total_cost_usd", obj.get("cost_usd", 0))
        state["duration_ms"] = obj.get("duration_ms", 0)


def _finalize_stats_acc(state: dict) -> dict:
    return {
        "turns": state["turns"],
        "context_tokens": state["context_tokens"],
        "output_tokens": state["output_tokens"],
        "model": state["model"] or (state["fmt"] if state["fmt"] not in ("", "unknown") else ""),
        "cost_usd": round(state["cost_usd"], 4),
        "rate_limit": state["rate_limit"],
        "is_overage": state["is_overage"],
        "duration_ms": state["duration_ms"],
    }


def live_session_stats(filepath):
    """Turn count, context tokens, cost, model — incremental.

    The per-line accumulator persists across calls so re-parsing during play is
    O(new_bytes) instead of O(1 MB): the first call scans from offset 0 to seed
    the accumulator; subsequent calls advance by exactly the bytes the harness wrote.
    """
    fmt = detect_log_format(Path(filepath))
    cache = _live_stats_cache_codex if fmt == "codex" else _live_stats_cache_claude
    consume_fn = _consume_stats_codex_obj if fmt == "codex" else _consume_stats_claude_obj

    slot = cache.get_slot(filepath, _init_stats_acc)
    try:
        size = Path(filepath).stat().st_size
    except OSError:
        return slot.get("result") or _finalize_stats_acc(slot["acc"])
    if size < slot.get("offset", 0):
        cache.reset_slot(slot, _init_stats_acc)
    slot["acc"]["fmt"] = fmt
    advanced = False
    for obj in tail_new_lines(filepath, slot):
        consume_fn(slot["acc"], obj)
        advanced = True
    if advanced or slot.get("result") is None:
        slot["result"] = _finalize_stats_acc(slot["acc"])
    return slot["result"]
