"""JSONL session log parsers for Claude Code and Codex CLI session logs."""

import json
import sys
from pathlib import Path
from dashboard.constants import sanitize

# Import shared format detection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cli_adapter import detect_log_format


def parse_session_log(filepath):
    """Parse a session log (auto-detecting Claude or Codex format).

    Returns dict with events, turn, cost, tokens, model, duration.
    """
    fmt = detect_log_format(Path(filepath))
    if fmt == "codex":
        return _parse_codex_session_log(filepath)
    return _parse_claude_session_log(filepath)


def _parse_claude_session_log(filepath):
    """Parse a Claude Code JSONL session log."""
    events = []
    turn = 0
    cost_usd = 0
    model = ""
    tokens = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    last_context = 0
    seen_msg_ids = set()
    duration_ms = 0
    num_turns = 0

    try:
        with open(filepath) as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                t = obj.get("type", "")

                if t == "assistant":
                    msg = obj.get("message", {})
                    if not model:
                        model = msg.get("model", "")
                    msg_id = msg.get("id", "")
                    if msg_id and msg_id not in seen_msg_ids:
                        seen_msg_ids.add(msg_id)
                        usage = msg.get("usage", {})
                        tokens["output"] += usage.get("output_tokens", 0)
                        tokens["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                        tokens["cache_read"] += usage.get("cache_read_input_tokens", 0)
                        tokens["input"] += usage.get("input_tokens", 0)
                        last_context = (usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0))
                    contents = msg.get("content", [])
                    for c in contents:
                        ct = c.get("type", "")
                        if ct == "tool_use":
                            tool = c.get("name", "unknown")
                            tool_display = tool.replace("mcp__playwright__", "pw:")
                            inp = c.get("input", {})
                            summary = ""
                            detail = ""
                            if "code" in inp:
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
                            turn += 1
                            events.append({
                                "turn": turn, "type": "tool",
                                "tool": tool_display,
                                "tool_full": tool,
                                "summary": sanitize(summary),
                                "detail": sanitize(detail),
                                "id": c.get("id", ""),
                            })
                        elif ct == "text":
                            text = c.get("text", "")
                            if text.strip():
                                events.append({"turn": turn, "type": "text", "text": sanitize(text)})
                        elif ct == "thinking":
                            thinking = c.get("thinking", "")
                            if thinking.strip():
                                events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

                elif t == "result":
                    cost_usd = obj.get("total_cost_usd", 0)
                    duration_ms = obj.get("duration_ms", 0)
                    num_turns = obj.get("num_turns", 0)

    except Exception:
        pass

    return {
        "events": events,
        "turn": turn,
        "cost_usd": round(cost_usd, 4),
        "model": model,
        "tokens": {
            "input": tokens["input"],
            "output": tokens["output"],
            "cache_create": tokens["cache_create"],
            "cache_read": tokens["cache_read"],
            "context": last_context,
            "total": last_context + tokens["output"],
        },
        "duration_ms": duration_ms,
        "num_turns": num_turns,
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


def _parse_codex_session_log(filepath):
    """Parse a Codex CLI --json session log.

    Tries multiple event structures since Codex format may vary.
    Returns same dict shape as _parse_claude_session_log.
    """
    events = []
    turn = 0
    cost_usd = 0
    model = ""
    tokens = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    last_context = 0
    duration_ms = 0
    num_turns = 0
    seen_ids = set()

    try:
        with open(filepath) as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                # Extract model from various locations
                if not model:
                    model = (obj.get("model", "")
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
                        last_context = ctx

                # Summary/result event (end of session)
                t = obj.get("type", "")
                if t == "result":
                    cost_usd = obj.get("total_cost_usd", cost_usd)
                    duration_ms = obj.get("duration_ms", duration_ms)
                    num_turns = obj.get("num_turns", num_turns)
                    continue
                if t == "summary":
                    cost_usd = obj.get("cost_usd", obj.get("total_cost_usd", cost_usd))
                    duration_ms = obj.get("duration_ms", duration_ms)
                    continue

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
                            turn += 1
                            events.append({
                                "turn": turn, "type": "tool",
                                "tool": tool_display,
                                "tool_full": tool,
                                "summary": sanitize(summary),
                                "detail": sanitize(detail),
                                "id": call_id,
                            })

                        elif ct == "text":
                            text = c.get("text", "")
                            if text.strip():
                                events.append({"turn": turn, "type": "text", "text": sanitize(text)})

                        elif ct == "thinking":
                            thinking = c.get("thinking", c.get("text", ""))
                            if thinking.strip():
                                events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

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
                                # Only show first 200 chars of tool results in activity
                                snippet = result_text[:200]
                                events.append({"turn": turn, "type": "text", "text": sanitize(f"[result] {snippet}")})

    except Exception:
        pass

    return {
        "events": events,
        "turn": turn,
        "cost_usd": round(cost_usd, 4),
        "model": model or "codex",
        "tokens": {
            "input": tokens["input"],
            "output": tokens["output"],
            "cache_create": tokens["cache_create"],
            "cache_read": tokens["cache_read"],
            "context": last_context,
            "total": last_context + tokens["output"],
        },
        "duration_ms": duration_ms,
        "num_turns": num_turns or turn,
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
                        # Claude format
                        if obj.get("type") == "result":
                            cost = obj.get("total_cost_usd", 0)
                            turns = obj.get("num_turns", 0)
                            duration_ms = obj.get("duration_ms", 0)
                            for m in (obj.get("modelUsage") or {}):
                                model = m
                                break
                        elif obj.get("type") == "assistant" and not model:
                            model = obj.get("message", {}).get("model", "")
                except Exception:
                    pass
    except Exception:
        pass
    return {"cost_usd": round(cost, 4), "turns": turns, "model": model or (fmt if fmt != "unknown" else ""), "duration_ms": duration_ms}


def live_session_stats(filepath):
    """Read turn count, context tokens, cost, and model from a session log.

    Single-pass scan of the last ~1MB. Returns metadata only —
    game state is handled separately by game_state module.
    """
    fmt = detect_log_format(Path(filepath))

    turns = 0
    context_tokens = 0
    output_tokens_total = 0
    model = ""
    cost = 0
    duration_ms = 0
    seen_msg_ids = set()
    try:
        with open(filepath) as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 1048576))
            if size > 1048576:
                fh.readline()
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type", "")

                if fmt == "codex":
                    # Codex format
                    if not model:
                        model = (obj.get("model", "")
                                 or obj.get("message", {}).get("model", "")
                                 or obj.get("response", {}).get("model", ""))
                    # Extract usage
                    usage = (obj.get("usage", {})
                             or obj.get("message", {}).get("usage", {})
                             or obj.get("response", {}).get("usage", {}))
                    if usage:
                        inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
                        out = usage.get("output_tokens", usage.get("completion_tokens", 0))
                        ctx = inp + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                        if ctx > 0:
                            context_tokens = ctx
                        output_tokens_total += out
                    # Count tool calls as turns
                    for _role, blocks in _extract_codex_content_blocks(obj):
                        if not isinstance(blocks, list):
                            continue
                        for c in blocks:
                            if isinstance(c, dict) and c.get("type") in ("tool_use", "function_call"):
                                turns += 1
                    if t in ("result", "summary"):
                        turns = obj.get("num_turns", turns)
                        cost = obj.get("total_cost_usd", obj.get("cost_usd", 0))
                        duration_ms = obj.get("duration_ms", 0)
                else:
                    # Claude format
                    if t == "assistant":
                        msg = obj.get("message", {})
                        if not model:
                            model = msg.get("model", "")
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
                                context_tokens = ctx
                            output_tokens_total += out
                        for c in msg.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                turns += 1
                    elif t == "result":
                        turns = obj.get("num_turns", turns)
                        cost = obj.get("total_cost_usd", 0)
                        duration_ms = obj.get("duration_ms", 0)
                        for m in (obj.get("modelUsage") or {}):
                            model = m
                            break
    except Exception:
        pass
    return {
        "turns": turns,
        "context_tokens": context_tokens,
        "output_tokens": output_tokens_total,
        "model": model or (fmt if fmt != "unknown" else ""),
        "cost_usd": round(cost, 4),
        "duration_ms": duration_ms,
    }
