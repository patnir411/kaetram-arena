"""JSONL session log parsers for Claude Code and Codex CLI session logs."""

import json
import logging
import sys
from collections import OrderedDict
from pathlib import Path
from dashboard.constants import sanitize

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
    if action == "move":
        return f"Move to ({inp.get('x', '?')}, {inp.get('y', '?')})"
    if action == "warp":
        return f"Warp to {inp.get('location', 'mudwich').title()}"
    if action == "interact_npc":
        return f"Talk to {inp.get('npc_name', '?')}"
    if action == "talk_npc":
        return f"Advance dialogue ({inp.get('instance_id', '?')[:12]})"
    if action == "accept_quest":
        return "Accept quest"
    if action == "eat_food":
        return f"Eat food (slot {inp.get('slot', '?')})"
    if action == "equip_item":
        return f"Equip (slot {inp.get('slot', '?')})"
    if action == "set_attack_style":
        return f"Style: {inp.get('style', 'hack')}"
    if action == "click_tile":
        return f"Click tile ({inp.get('x', '?')}, {inp.get('y', '?')})"
    if action == "observe":
        return "Read game state"
    if action == "login":
        return "Login to Kaetram"
    if action == "clear_combat":
        return "Clear combat state"
    if action == "stuck_reset":
        return "Reset stuck detection"
    if action == "cancel_nav":
        return "Cancel navigation"
    if action == "respawn":
        return "Respawn + warp"
    return action


_parse_cache = OrderedDict()  # filepath -> (mtime, parsed_result), LRU
_live_stats_cache = OrderedDict()  # filepath -> (mtime, stats_result), LRU
_PARSE_CACHE_MAX = 25
_STATS_CACHE_MAX = 25

def parse_session_log(filepath):
    """Parse a session log (auto-detecting Claude or Codex format).

    Returns dict with events, turn, cost, tokens, model, duration.
    Cached by (filepath, mtime) — re-parses any time the log changes,
    not just when it grows. LRU eviction keeps the 25 most-recent files.
    """
    try:
        mtime = Path(filepath).stat().st_mtime
    except OSError:
        mtime = -1
    cached = _parse_cache.get(filepath)
    if cached and cached[0] == mtime:
        _parse_cache.move_to_end(filepath)
        return cached[1]

    fmt = detect_log_format(Path(filepath))
    if fmt == "codex":
        result = _parse_codex_session_log(filepath)
    else:
        # Claude, Gemini, OpenCode all use compatible stream-json
        result = _parse_claude_session_log(filepath)

    _parse_cache[filepath] = (mtime, result)
    _parse_cache.move_to_end(filepath)
    while len(_parse_cache) > _PARSE_CACHE_MAX:
        _parse_cache.popitem(last=False)
    return result


def _parse_claude_session_log(filepath):
    """Parse a Claude Code or Gemini CLI JSONL session log.

    Claude uses nested events: type=assistant → message.content[{type: tool_use}]
    Gemini uses flat events: type=tool_use (top-level), type=tool_result, type=init
    Both are handled here.
    """
    events = []
    turn = 0
    cost_usd = 0
    model = ""
    tokens = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    last_context = 0
    seen_msg_ids = set()
    duration_ms = 0
    num_turns = 0
    # tool_use_id → event index, so when the matching tool_result comes
    # later (Claude emits it in a separate user-role message) we can attach
    # the result text to the original tool event for click-to-expand.
    tool_idx_by_id: dict[str, int] = {}
    # Step-boundary tracking for the "(silent) deliberated for Ns" annotation.
    # Initialized here (not inside the step_start branch) so a stray
    # step_finish arriving first doesn't fall through to a NameError.
    last_step_started_at: int = 0
    last_step_had_text: bool = True

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

                # Gemini: flat init event with model
                if t == "init":
                    if not model:
                        model = obj.get("model", "")
                    continue

                # OpenCode emits a "text" event for every model utterance —
                # natural-language reasoning between tool calls. Surface as a
                # `thinking` event (matches the existing 🧠 frontend renderer)
                # so the activity feed shows in-context model thoughts.
                if t == "text":
                    part = obj.get("part") or {}
                    txt = (part.get("text") or obj.get("text") or "").strip()
                    if txt:
                        events.append({
                            "turn": turn, "type": "thinking",
                            "text": sanitize(txt[:8000]),
                        })
                    # Track that this step had narration so we don't synthesize
                    # a "(silent thinking)" placeholder for it later.
                    last_step_had_text = True
                    continue

                # Step boundaries — surface silent thinking time. Some models
                # (esp. Qwen3-Coder under the grinder personality) emit zero
                # `text` events between tool calls. The activity feed would
                # otherwise look like pure action with no cognition. Show a
                # "(silent) Ns" thinking row when we see a step finish that
                # had no narration, so the user can see the model paused to
                # decide even when it didn't verbalize.
                if t in ("step_start", "step-start"):
                    last_step_started_at = obj.get("timestamp") or 0
                    last_step_had_text = False
                    continue
                if t in ("step_finish", "step-finish"):
                    end_ts = obj.get("timestamp") or 0
                    start_ts = last_step_started_at
                    had_text = last_step_had_text
                    if not had_text and start_ts and end_ts > start_ts:
                        secs = round((end_ts - start_ts) / 1000.0, 1)
                        events.append({
                            "turn": turn, "type": "thinking",
                            "text": f"(silent) deliberated for {secs}s",
                        })
                    last_step_had_text = True   # reset
                    # Fall through to step_finish token-accounting block below.

                # Gemini: flat tool_use event / OpenCode: flat tool_use with
                # part.tool. OpenCode wraps tool name + input + output inside
                # `part.state` rather than putting them at the top level.
                if t == "tool_use":
                    part = obj.get("part") or {}
                    state = part.get("state") or {}
                    tool = obj.get("tool_name") or part.get("tool") or "unknown"
                    # OpenCode prefixes MCP tools as kaetram_<name> (no double
                    # underscore). Normalize both spellings to a common form.
                    tool_norm = tool.replace("mcp_kaetram_", "").replace("kaetram_", "")
                    tool_display = tool_norm if tool != tool_norm else tool.replace("mcp_kaetram_", "")
                    inp = obj.get("parameters") or state.get("input") or {}
                    summary = ""
                    detail = ""
                    if "kaetram" in tool:
                        # Feed a canonical mcp__kaetram__<name> handle to the
                        # existing summary helper so Claude/Gemini/OpenCode
                        # render identically in Live Activity.
                        canonical = "mcp__kaetram__" + tool_norm
                        summary = _kaetram_tool_summary(canonical, inp)
                        detail = json.dumps(inp)[:500] if inp else ""
                    elif inp:
                        parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
                        summary = " ".join(parts)
                    turn += 1
                    # OpenCode embeds the tool OUTPUT inside the same tool_use
                    # event. Attach it to the tool event as `result` so the
                    # frontend can expand the row to show the full output
                    # (e.g. game state JSON for `observe`, NPC dialogue for
                    # `interact_npc`, etc.).
                    oc_output = state.get("output")
                    result_text = ""
                    if isinstance(oc_output, str) and oc_output.strip():
                        result_text = sanitize(oc_output[:8000])
                    events.append({
                        "turn": turn, "type": "tool",
                        "tool": tool_display,
                        "tool_full": tool,
                        "summary": sanitize(summary),
                        "detail": sanitize(detail),
                        "result": result_text,
                        "id": obj.get("tool_id") or part.get("callID", ""),
                    })
                    continue

                # Synthetic harness-level errors written by play.sh when a
                # session ends abnormally (e.g. opencode hits NIM 429 and
                # produces zero step_finish events). Surface in activity feed.
                if t == "harness_error":
                    err = obj.get("error", "harness error")
                    backoff = obj.get("backoff_secs")
                    label = f"{err}" if backoff is None else f"{err} (sleep {backoff}s)"
                    turn += 1
                    events.append({
                        "turn": turn, "type": "error",
                        "summary": sanitize(str(label)[:240]),
                    })
                    continue

                # Gemini: flat tool_result event
                if t == "tool_result":
                    output = obj.get("output", "")
                    if isinstance(output, str) and output.strip():
                        events.append({"turn": turn, "type": "result", "text": sanitize(output[:8000])})
                    continue

                # OpenCode token accounting lives in step_finish events.
                if t in ("step_finish", "step-finish"):
                    tkn = (obj.get("part") or {}).get("tokens") or {}
                    if tkn:
                        tokens["input"]        += tkn.get("input", 0)
                        tokens["output"]       += tkn.get("output", 0)
                        cache = tkn.get("cache") or {}
                        tokens["cache_create"] += cache.get("write", 0)
                        tokens["cache_read"]   += cache.get("read", 0)
                        last_context = (tkn.get("input", 0)
                            + cache.get("write", 0) + cache.get("read", 0))
                    num_turns += 1
                    continue

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
                            turn += 1
                            tool_use_id = c.get("id", "")
                            events.append({
                                "turn": turn, "type": "tool",
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
                                events.append({"turn": turn, "type": "text", "text": sanitize(text)})
                        elif ct == "thinking":
                            thinking = c.get("thinking", "")
                            if thinking.strip():
                                events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

                # Claude tool_result lives in user-role messages; attach to
                # the matching tool event so the activity feed expand panel
                # can show the full output (e.g. game_state JSON + ASCII map
                # for `observe`).
                elif t == "user":
                    msg = obj.get("message", {})
                    contents = msg.get("content", []) if isinstance(msg, dict) else []
                    if not isinstance(contents, list):
                        continue
                    for c in contents:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") != "tool_result":
                            continue
                        tuid = c.get("tool_use_id", "")
                        raw = c.get("content", "")
                        # Claude content can be str or [{type:text,text:...}, ...]
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
                        # MCP tool results are typically wrapped as
                        # `{"result": "..."}` by the FastMCP layer. Unwrap so
                        # the frontend sees clean text it can split on the
                        # ASCII_MAP marker and pretty-print the JSON.
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


def live_session_stats(filepath):
    """Read turn count, context tokens, cost, and model from a session log.

    Single-pass scan of the last ~1MB. Cached by (filepath, mtime) —
    only re-parses when the log file is modified.
    """
    try:
        mtime = Path(filepath).stat().st_mtime
    except OSError:
        mtime = -1
    cached = _live_stats_cache.get(filepath)
    if cached and cached[0] == mtime:
        _live_stats_cache.move_to_end(filepath)
        return cached[1]

    fmt = detect_log_format(Path(filepath))

    turns = 0
    context_tokens = 0
    output_tokens_total = 0
    model = ""
    cost = 0
    duration_ms = 0
    rate_limit = None
    is_overage = False
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
                    # Claude / Gemini format
                    if t == "init" and not model:
                        model = obj.get("model", "")
                    elif t == "tool_use":
                        # Gemini flat tool_use event
                        turns += 1
                    elif t == "assistant":
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
                    elif t == "rate_limit_event":
                        info = obj.get("rate_limit_info", {})
                        rate_limit = {
                            "status": info.get("overageStatus"),
                            "type": info.get("rateLimitType"),
                            "resets_at": info.get("resetsAt"),
                            "using_overage": info.get("isUsingOverage", False),
                        }
                        if info.get("isUsingOverage"):
                            is_overage = True
                    elif t == "result":
                        turns = obj.get("num_turns", turns)
                        cost = obj.get("total_cost_usd", 0)
                        duration_ms = obj.get("duration_ms", 0)
                        for m in (obj.get("modelUsage") or {}):
                            model = m
                            break
    except Exception:
        pass
    result = {
        "turns": turns,
        "context_tokens": context_tokens,
        "output_tokens": output_tokens_total,
        "model": model or (fmt if fmt != "unknown" else ""),
        "cost_usd": round(cost, 4),
        "rate_limit": rate_limit,
        "is_overage": is_overage,
        "duration_ms": duration_ms,
    }
    _live_stats_cache[filepath] = (mtime, result)
    _live_stats_cache.move_to_end(filepath)
    while len(_live_stats_cache) > _STATS_CACHE_MAX:
        _live_stats_cache.popitem(last=False)
    return result
