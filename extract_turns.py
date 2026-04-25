#!/usr/bin/env python3
"""
extract_turns.py — Post-process session logs into clean OODA turns.

Reads logs from multiple CLI harnesses and extracts (game_state, reasoning, action)
tuples for SFT training:
- Claude Code: stream-json with thinking blocks
- Codex: --json with item.started/item.completed events
- Qwen Code: stream-json (Gemini CLI fork, same format as Claude)
- Kimi: extended thinking with --thinking flag, raw output + thinking tokens
- OpenCode: --format json with flat type=text/reasoning/tool_use/step_finish events

Usage:
    python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/
    python3 extract_turns.py --log-file logs/session_2_20260319_060749.log
"""

import argparse
import json
import re
import sys
from pathlib import Path

from cli_adapter import detect_log_format


def parse_events(log_path: Path) -> list[dict]:
    """Parse JSONL log into a flat list of typed events (auto-detecting format).

    Supports: Claude stream-json, Codex --json, Qwen Code stream-json, Kimi raw output.
    Qwen Code and Kimi both use Claude-compatible stream-json or similar event structures.
    """
    fmt = detect_log_format(log_path)
    if fmt == "codex":
        # Codex extraction disabled until we validate logs from initial runs.
        # To enable: uncomment the return below and remove the empty return.
        # return _parse_codex_events(log_path)
        print(f"  [skip] {log_path.name}: codex format (extraction disabled)", file=sys.stderr)
        return []
    if fmt == "gemini":
        # Gemini extraction disabled until we validate logs from initial runs.
        # Gemini uses flat stream-json (type=tool_use/tool_result), needs its own parser.
        # To enable: implement _parse_gemini_events() and uncomment below.
        print(f"  [skip] {log_path.name}: gemini format (extraction disabled)", file=sys.stderr)
        return []
    if fmt == "opencode":
        return _parse_opencode_events(log_path)
    # Claude, Qwen Code, and Kimi all use compatible stream-json-like formats
    return _parse_claude_events(log_path)


def _parse_opencode_events(log_path: Path) -> list[dict]:
    """Parse OpenCode --format json log into normalized events.

    OpenCode emits flat JSONL events with:
      - type=text, part.text → assistant prose
      - type=reasoning, part.text → assistant chain-of-thought (thinking models)
      - type=tool_use, part.tool, part.callID, part.state.{input,output} →
        carries BOTH the call and the result in one event (unlike Claude which
        splits them). We emit two normalized events: tool_use and tool_result.
      - type=step_finish, part.tokens.reasoning → token accounting (skipped here;
        the reasoning content already lands in `reasoning` parts when present).

    Tool names are normalized from `kaetram_<name>` to `mcp__kaetram__<name>` so
    they match MCP_ACTION_TOOLS / is_observe downstream.
    """
    events = []
    for i, line in enumerate(open(log_path)):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = ev.get("type")
        timestamp = ev.get("timestamp")
        part = ev.get("part") or {}
        if not isinstance(part, dict):
            continue

        if t == "text":
            text = part.get("text", "")
            if text:
                events.append({
                    "line": i, "type": "text", "role": "assistant",
                    "text": text, "timestamp": timestamp,
                })
            continue

        if t == "reasoning":
            text = part.get("text", "")
            if text:
                events.append({
                    "line": i, "type": "thinking", "role": "assistant",
                    "text": text, "timestamp": timestamp,
                })
            continue

        if t == "tool_use":
            tool_name = part.get("tool", "")
            if tool_name and not tool_name.startswith("mcp__"):
                tool_name = f"mcp__{tool_name}" if tool_name.startswith("kaetram__") else f"mcp__kaetram__{tool_name.removeprefix('kaetram_')}"
            state = part.get("state") or {}
            input_obj = state.get("input", {}) if isinstance(state, dict) else {}
            call_id = part.get("callID", "")

            events.append({
                "line": i, "type": "tool_use", "role": "assistant",
                "name": tool_name,
                "input": input_obj if isinstance(input_obj, dict) else {},
                "id": call_id, "timestamp": timestamp,
            })

            if isinstance(state, dict) and state.get("status") == "completed":
                output = state.get("output", "")
                text_content = output if isinstance(output, str) else json.dumps(output)
                events.append({
                    "line": i, "type": "tool_result", "role": "user",
                    "tool_use_id": call_id,
                    "text": text_content,
                    "timestamp": timestamp,
                })

    return events


def _parse_claude_events(log_path: Path) -> list[dict]:
    """Parse Claude Code stream-json log into normalized events."""
    events = []
    for i, line in enumerate(open(log_path)):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = ev.get("type")
        if t not in ("assistant", "user"):
            continue

        msg = ev.get("message", {})
        content = msg.get("content", [])
        timestamp = ev.get("timestamp")

        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")

            if bt == "thinking":
                events.append(
                    {
                        "line": i,
                        "type": "thinking",
                        "role": t,
                        "text": block.get("thinking", ""),
                        "timestamp": timestamp,
                    }
                )
            elif bt == "text":
                events.append(
                    {
                        "line": i,
                        "type": "text",
                        "role": t,
                        "text": block.get("text", ""),
                        "timestamp": timestamp,
                    }
                )
            elif bt == "tool_use":
                events.append(
                    {
                        "line": i,
                        "type": "tool_use",
                        "role": t,
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                        "id": block.get("id", ""),
                        "timestamp": timestamp,
                    }
                )
            elif bt == "tool_result":
                text_content = ""
                c = block.get("content", [])
                if isinstance(c, str):
                    text_content = c
                elif isinstance(c, list):
                    for item in c:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_content += item.get("text", "")
                events.append(
                    {
                        "line": i,
                        "type": "tool_result",
                        "role": t,
                        "tool_use_id": block.get("tool_use_id", ""),
                        "text": text_content,
                        "timestamp": timestamp,
                    }
                )

    return events


def _parse_codex_events(log_path: Path) -> list[dict]:
    """Parse Codex --json log into normalized events.

    Codex emits item.started/item.completed events with mcp_tool_call items.
    We normalize to the same event dicts as Claude:
    {line, type, role, text/name/input/id, timestamp}
    """
    events = []

    for i, line in enumerate(open(log_path)):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = ev.get("type", "")
        item = ev.get("item", {})
        item_type = item.get("type", "")
        timestamp = ev.get("timestamp", ev.get("created_at"))

        # item.completed with agent_message → text event (agent reasoning/planning)
        if t == "item.completed" and item_type == "agent_message":
            text = item.get("text", "")
            if text:
                events.append({
                    "line": i, "type": "text", "role": "assistant",
                    "text": text,
                    "timestamp": timestamp,
                })
            continue

        # item.completed with reasoning → thinking event
        if t == "item.completed" and item_type == "reasoning":
            text = item.get("text", "")
            if text:
                events.append({
                    "line": i, "type": "thinking", "role": "assistant",
                    "text": text,
                    "timestamp": timestamp,
                })
            continue

        # item.started with mcp_tool_call → tool_use event
        if t == "item.started" and item_type == "mcp_tool_call":
            tool_name = item.get("tool", "unknown")
            if "__" in tool_name and not tool_name.startswith("mcp__"):
                tool_name = f"mcp__{tool_name}"
            args = item.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {"raw": args}
            events.append({
                "line": i, "type": "tool_use", "role": "assistant",
                "name": tool_name,
                "input": args if isinstance(args, dict) else {},
                "id": item.get("id", ""),
                "timestamp": timestamp,
            })

        # item.completed with mcp_tool_call → tool_result event
        elif t == "item.completed" and item_type == "mcp_tool_call":
            result = item.get("result", {})
            text_content = ""
            if isinstance(result, dict):
                for block in result.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_content += block.get("text", "")
            elif isinstance(result, str):
                text_content = result
            events.append({
                "line": i, "type": "tool_result", "role": "user",
                "tool_use_id": item.get("id", ""),
                "text": text_content,
                "timestamp": timestamp,
            })

    return events



def is_observe(event: dict) -> bool:
    """Check if a tool_use event is an MCP observe call."""
    return event.get("type") == "tool_use" and event.get("name", "") == "mcp__kaetram__observe"


# MCP action tool names → action types
# Must stay in sync with the @mcp.tool() decorators in mcp_game_server.py.
# Every action-producing MCP tool (i.e. everything except observe and login)
# should be listed here so is_browser_action() picks it up and the extraction
# loop does not silently drop its turns.
MCP_ACTION_TOOLS = {
    "mcp__kaetram__attack": "attack",
    "mcp__kaetram__navigate": "navigate",
    "mcp__kaetram__move": "move",
    "mcp__kaetram__interact_npc": "interact_npc",
    "mcp__kaetram__talk_npc": "talk_npc",
    "mcp__kaetram__warp": "warp",
    "mcp__kaetram__click_tile": "click_tile",
    "mcp__kaetram__click_entity": "click_entity",
    "mcp__kaetram__equip_item": "equip",
    "mcp__kaetram__eat_food": "heal",
    "mcp__kaetram__set_attack_style": "set_style",
    "mcp__kaetram__stuck_reset": "stuck_reset",
    "mcp__kaetram__cancel_nav": "nav_cancel",
    "mcp__kaetram__respawn": "respawn",
    "mcp__kaetram__accept_quest": "quest_accept",
    "mcp__kaetram__gather": "gather",
    "mcp__kaetram__loot": "loot",
    "mcp__kaetram__buy_item": "buy_item",
    "mcp__kaetram__drop_item": "drop_item",
    "mcp__kaetram__clear_combat": "clear_combat",
    "mcp__kaetram__query_quest": "query_quest",
}


def is_browser_action(event: dict) -> bool:
    """Check if a tool_use event is a game action (not an observe)."""
    return event.get("type") == "tool_use" and event.get("name", "") in MCP_ACTION_TOOLS


def parse_game_state(text: str) -> dict | None:
    """Parse game state JSON from tool_result text. Handles double-encoding.

    The tool result format is multi-line:
      ### Result
      "{\"timestamp\":...}"    <-- double-encoded JSON string
      ### Ran Playwright code
      ...
    Stop parsing at "### Ran Playwright code" to avoid picking up JSON from
    the echoed source code.
    """
    text = text.strip()
    if not text:
        return None

    lines = text.split("\n")

    # Stop at the code echo section
    result_lines = []
    for line in lines:
        if line.strip().startswith("### Ran Playwright code"):
            break
        result_lines.append(line)

    # Try each line for JSON content
    for line in result_lines:
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```") or line.startswith("-"):
            continue

        try:
            obj = json.loads(line)
            if isinstance(obj, str):
                # Double-encoded: parse the inner JSON
                inner = json.loads(obj)
                if isinstance(inner, dict):
                    return inner
            if isinstance(obj, dict):
                # MCP tool result wrapper: {"result": "{...JSON...}\n\nASCII_MAP:..."}
                if "result" in obj and isinstance(obj["result"], str):
                    result_str = obj["result"]
                    # Strip ASCII_MAP suffix before parsing JSON
                    if "\n\nASCII_MAP:" in result_str:
                        result_str = result_str.split("\n\nASCII_MAP:")[0]
                    try:
                        inner = json.loads(result_str)
                        if isinstance(inner, dict):
                            return inner
                    except (json.JSONDecodeError, TypeError):
                        pass
                return obj
        except (json.JSONDecodeError, TypeError):
            continue

    # Fallback: find first { and try to parse to end of that line
    for line in result_lines:
        idx = line.find("{")
        if idx >= 0:
            try:
                obj = json.loads(line[idx:])
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, TypeError):
                continue

    return None


def extract_ascii_map(text: str) -> str:
    """Extract the ASCII map section from tool_result text."""
    # For MCP results, ASCII_MAP may be inside {"result": "...\\n\\nASCII_MAP:..."}
    search_text = text
    if "ASCII_MAP:" not in search_text:
        # Try parsing as JSON wrapper and checking the result string
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "result" in obj and isinstance(obj["result"], str):
                search_text = obj["result"]
        except (json.JSONDecodeError, TypeError):
            pass
    if "ASCII_MAP:" not in search_text:
        return ""
    idx = search_text.find("ASCII_MAP:")
    if idx < 0:
        return ""
    ascii_section = search_text[idx + len("ASCII_MAP:"):].strip()
    # Trim at STUCK_CHECK if present
    stuck_idx = ascii_section.find("STUCK_CHECK:")
    if stuck_idx >= 0:
        ascii_section = ascii_section[:stuck_idx].strip()
    return ascii_section


def classify_action(code: str, tool_name: str = "") -> str:
    """Classify an action tool_use into a named action type via its MCP tool name."""
    return MCP_ACTION_TOOLS.get(tool_name, "other")


def structured_action(action_type: str, action_code: str, tool_input: dict | None = None) -> str:
    """Convert an MCP tool input into a structured action string for SFT."""
    # --- MCP tool input path (structured JSON) ---
    if tool_input is not None:
        if action_type == "attack":
            return f"attack({tool_input.get('mob_name', tool_input.get('target', '?'))})"
        if action_type == "interact_npc":
            return f"interact_npc({tool_input.get('npc_name', '?')})"
        if action_type == "navigate":
            return f"navigate({tool_input.get('x', '?')}, {tool_input.get('y', '?')})"
        if action_type == "move":
            return f"move({tool_input.get('x', '?')}, {tool_input.get('y', '?')})"
        if action_type == "click_entity":
            return f"click_entity({tool_input.get('label', tool_input.get('entity', '?'))})"
        if action_type == "click_tile":
            return f"click_tile({tool_input.get('x', '?')}, {tool_input.get('y', '?')})"
        if action_type == "talk_npc":
            return f"talk_npc({tool_input.get('instance_id', tool_input.get('npc_id', '?'))})"
        if action_type == "warp":
            loc = tool_input.get("location", "?")
            return f"warp({loc.capitalize() if isinstance(loc, str) else loc})"
        if action_type == "equip":
            return f"equip(slot={tool_input.get('slot', '?')})"
        if action_type == "heal":
            return f"heal(slot={tool_input.get('slot', '?')})"
        if action_type == "set_style":
            style = tool_input.get("style", "?")
            return f"set_style({style.capitalize() if isinstance(style, str) else style})"
        if action_type == "quest_accept":
            return "quest_accept()"
        if action_type == "respawn":
            return "respawn()"
        if action_type == "stuck_reset":
            return "stuck_reset()"
        if action_type == "nav_cancel":
            return "nav_cancel()"
        if action_type == "gather":
            # mcp_game_server.gather(ctx, resource_name)
            return f"gather({tool_input.get('resource_name', tool_input.get('target', '?'))})"
        if action_type == "loot":
            # mcp_game_server.loot(ctx) — no args
            return "loot()"
        if action_type == "clear_combat":
            # mcp_game_server.clear_combat(ctx) — no args
            return "clear_combat()"
        if action_type == "buy_item":
            # mcp_game_server.buy_item(ctx, npc_name, item_index, count=1)
            npc = tool_input.get("npc_name", "?")
            item = tool_input.get("item_index", "?")
            count = tool_input.get("count", 1)
            return f"buy_item({npc}, {item}, count={count})"
        if action_type == "drop_item":
            # mcp_game_server.drop_item(ctx, slot)
            return f"drop_item(slot={tool_input.get('slot', '?')})"
        if action_type == "query_quest":
            # mcp_game_server.query_quest(ctx, quest_name)
            return f"query_quest({tool_input.get('quest_name', '?')})"
        return f"{action_type}({json.dumps(tool_input)})"

    # No tool_input — we only get here for MCP tool calls that had missing input.
    # Emit a generic arg-less form so downstream can still classify the action.
    return f"{action_type}()"


def _safe_int(val, default=0):
    """Safely extract an integer from a value that might be a dict, str, or None."""
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, dict):
        # Agent sometimes nests the full stats dict under 'hp' key
        return int(val.get("hp", val.get("level", default)))
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            pass
    return default


def _build_player_stats(gs: dict) -> dict:
    """Build a player_stats dict from whatever fields are available in gs."""
    # Try 'stats' sub-dict first
    stats = gs.get("stats", {})
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except (json.JSONDecodeError, ValueError):
            stats = {}
    if not isinstance(stats, dict):
        stats = {}

    # If gs["hp"] is itself a dict (agent put full stats under "hp" key), use it as stats
    hp_val = gs.get("hp")
    if isinstance(hp_val, dict):
        stats = hp_val
        hp_val = stats.get("hp", 0)

    player = gs.get("player", {})
    if not isinstance(player, dict):
        player = {}

    hp = _safe_int(hp_val) or _safe_int(stats.get("hp")) or _safe_int(player.get("hp"))
    max_hp = (
        _safe_int(gs.get("max_hp")) or _safe_int(gs.get("maxHp"))
        or _safe_int(stats.get("max_hp")) or _safe_int(stats.get("maxHp"))
        or _safe_int(player.get("max_hp")) or _safe_int(player.get("maxHp"))
    )
    level = (
        _safe_int(gs.get("level")) or _safe_int(stats.get("level"))
        or _safe_int(player.get("level"))
        or 1
    )
    experience = (
        _safe_int(gs.get("experience")) or _safe_int(gs.get("xp"))
        or _safe_int(stats.get("experience")) or _safe_int(stats.get("xp"))
    )
    return {
        "hp": hp,
        "max_hp": max_hp,
        "level": level,
        "experience": experience,
    }


def normalize_game_state(gs: dict) -> dict | None:
    """Normalize variant game state formats to a standard schema.

    The agent sometimes returns custom subsets instead of the full
    __latestGameState format. This handles all observed variants.
    """
    if not gs or not isinstance(gs, dict):
        return None
    if gs.get("error"):
        return None

    normalized = dict(gs)

    # --- Normalize field aliases ---
    for alias, canonical in [
        ("nearby_mobs", "nearby_entities"),
        ("nearby", "nearby_entities"),
        ("entities", "nearby_entities"),
        ("inv", "inventory"),
    ]:
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized.pop(alias)

    # --- Ensure player_position ---
    if "player_position" not in normalized:
        if "pos" in gs:
            pos = gs["pos"]
            if isinstance(pos, dict) and "x" in pos:
                normalized["player_position"] = pos
            elif isinstance(pos, str):
                m = re.match(r'\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', pos)
                if m:
                    normalized["player_position"] = {"x": int(m.group(1)), "y": int(m.group(2))}
        elif "x" in gs and "y" in gs and isinstance(gs.get("x"), (int, float)):
            normalized["player_position"] = {"x": int(gs["x"]), "y": int(gs["y"])}

    if "player_position" not in normalized:
        return None

    pp = normalized["player_position"]
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
            normalized["player_position"] = pp
        except (json.JSONDecodeError, ValueError):
            m = re.match(r'\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', pp)
            if m:
                pp = {"x": int(m.group(1)), "y": int(m.group(2))}
                normalized["player_position"] = pp
            else:
                return None
    if not isinstance(pp, dict) or "x" not in pp:
        return None

    # --- Ensure player_stats ---
    existing_ps = normalized.get("player_stats")
    if isinstance(existing_ps, str):
        try:
            existing_ps = json.loads(existing_ps)
        except (json.JSONDecodeError, ValueError):
            existing_ps = None

    # Check if existing player_stats is valid (has non-zero hp or max_hp)
    ps_valid = (
        isinstance(existing_ps, dict)
        and (existing_ps.get("hp", 0) > 0 or existing_ps.get("max_hp", 0) > 0)
    )

    if not ps_valid:
        # Build player_stats from top-level fields, stats dict, or player dict
        built_ps = _build_player_stats(gs)
        if built_ps["hp"] > 0 or built_ps["max_hp"] > 0:
            normalized["player_stats"] = built_ps
        elif isinstance(existing_ps, dict):
            # Keep existing even if zero — it's the standard format with actual zeros
            normalized["player_stats"] = existing_ps
        else:
            normalized["player_stats"] = built_ps

    return normalized


def is_valid_turn(turn: dict) -> bool:
    """Filter out garbage turns that would pollute training data."""
    pp = turn.get("player_position", {})
    ps = turn.get("player_stats", {})
    action_type = turn.get("action_type", "")

    # Position (0, 0) = login screen / game not loaded
    if pp.get("x", 0) == 0 and pp.get("y", 0) == 0:
        return False

    # Infrastructure actions aren't gameplay
    if action_type in ("login", "reconnect"):
        return False

    return True


def extract_turns(log_path: Path) -> list[dict]:
    """Extract OODA turns from a single session log file.

    Each observe tool_use becomes its own turn, carrying the reasoning that
    preceded it. Any standalone action tool_use that follows becomes a second
    turn, carrying the reasoning between the observe tool_result and the action.
    """
    events = parse_events(log_path)
    turns = []

    # Build a tool_use_id → raw result text map so we can persist the real
    # tool_result content for each matched action (contract with convert_to_qwen.py:
    # action_result_raw is the authoritative tool message content, fallback to
    # the synthesizer only when this field is absent or empty).
    #
    # The raw text is stored AS-IS. Observe results are double-wrapped
    # ({"result": "<inner JSON>\n\nASCII_MAP:..."}) and we deliberately do not
    # unwrap here — downstream consumers can decide how to handle it.
    tool_result_by_id: dict[str, str] = {}
    for e in events:
        if e.get("type") == "tool_result":
            tid = e.get("tool_use_id", "")
            if tid:
                tool_result_by_id[tid] = e.get("text", "")

    observe_indices = [i for i, e in enumerate(events) if is_observe(e)]
    if not observe_indices:
        return []

    for oi_pos, obs_idx in enumerate(observe_indices):
        obs_event = events[obs_idx]
        obs_tool_id = obs_event.get("id", "")

        # Find the tool_result for this observe call.
        game_state = None
        ascii_map = ""
        observe_result_raw = None
        obs_result_idx = None
        for j in range(obs_idx + 1, min(obs_idx + 15, len(events))):
            e = events[j]
            if e["type"] == "tool_result" and e.get("tool_use_id") == obs_tool_id:
                result_text = e.get("text", "")
                observe_result_raw = result_text
                obs_result_idx = j
                raw_gs = parse_game_state(result_text)
                game_state = normalize_game_state(raw_gs) if raw_gs else None
                ascii_map = extract_ascii_map(result_text)
                break

        if game_state is None:
            continue

        pp = game_state.get("player_position")
        if not pp or not isinstance(pp, dict):
            continue

        ps = game_state.get("player_stats", {})
        if isinstance(ps, str):
            try:
                ps = json.loads(ps)
            except (json.JSONDecodeError, ValueError):
                ps = {}
        if not isinstance(ps, dict):
            ps = {}

        player_stats = {
            "hp": ps.get("hp", 0),
            "max_hp": ps.get("max_hp", 0),
            "level": ps.get("level", 1),
        }
        player_position = {"x": pp.get("x", 0), "y": pp.get("y", 0)}

        # Reasoning BEFORE this observe (since previous observe / start of log).
        prev_obs = observe_indices[oi_pos - 1] if oi_pos > 0 else -1
        reasoning_before_parts = []
        for j in range(prev_obs + 1, obs_idx):
            ev = events[j]
            if ev["type"] in ("thinking", "text") and ev["role"] == "assistant":
                t = ev.get("text", "").strip()
                if t:
                    reasoning_before_parts.append(t)

        # Search for a standalone action between this observe's result and the next
        # observe, collecting post-observe reasoning as we go.
        next_obs = observe_indices[oi_pos + 1] if oi_pos + 1 < len(observe_indices) else len(events)
        action_tool_name = ""
        action_tool_input: dict | None = None
        action_tool_id = ""
        action_code: str = ""
        reasoning_after_parts: list[str] = []
        search_from = (obs_result_idx + 1) if obs_result_idx is not None else (obs_idx + 1)
        for j in range(search_from, next_obs):
            ev = events[j]
            if ev["type"] in ("thinking", "text") and ev["role"] == "assistant":
                t = ev.get("text", "").strip()
                if t:
                    reasoning_after_parts.append(t)
            elif is_browser_action(ev):
                action_tool_name = ev.get("name", "")
                action_tool_input = ev.get("input", {})
                action_tool_id = ev.get("id", "")
                break

        # Emit observe as its own first-class turn. Teaches the model to call
        # observe before acting, closing the r9 bug where training data had 0
        # observe tool_calls because extraction silently consumed them to populate
        # game_state.
        obs_turn = {
            "turn_id": f"{log_path.stem}_t{len(turns):03d}",
            "timestamp": game_state.get("timestamp", 0),
            "game_state": game_state,
            "ascii_map": ascii_map,
            "reasoning": "\n".join(reasoning_before_parts),
            "action_code": "",
            "action_type": "observe",
            "action_structured": "observe()",
            "action_target": "",
            # For observe turns, the tool_result text is the raw observe result
            # (game_state JSON + ASCII map, exactly as the MCP server returned it).
            "action_result_raw": observe_result_raw,
            "player_stats": player_stats,
            "player_position": player_position,
        }
        if is_valid_turn(obs_turn):
            turns.append(obs_turn)

        # Observe-only tail (no following action) — nothing further to do.
        if not action_tool_name:
            continue

        # Emit the action turn.
        action_result_raw = tool_result_by_id.get(action_tool_id) if action_tool_id else None
        action_type = classify_action(action_code, action_tool_name)

        action_turn = {
            "turn_id": f"{log_path.stem}_t{len(turns):03d}",
            "timestamp": game_state.get("timestamp", 0),
            "game_state": game_state,
            "ascii_map": "",
            "reasoning": "\n".join(reasoning_after_parts),
            "action_code": json.dumps(action_tool_input or {}),
            "action_type": action_type,
            "action_structured": structured_action(action_type, action_code, action_tool_input),
            "action_target": None,
            "action_result_raw": action_result_raw,
            "player_stats": player_stats,
            "player_position": player_position,
        }

        if is_valid_turn(action_turn):
            turns.append(action_turn)


    # Deduplicate: skip consecutive turns with same position + same action,
    # allowing at most 3 repeats before filtering
    deduped = []
    repeat_count = 0
    for t in turns:
        if deduped:
            prev = deduped[-1]
            same_pos = prev["player_position"] == t["player_position"]
            same_action = prev["action_structured"] == t["action_structured"]
            same_reasoning = (
                prev.get("reasoning", "")[:100] == t.get("reasoning", "")[:100]
                and len(prev.get("reasoning", "")) > 0
            )
            if same_pos and (same_action or same_reasoning):
                repeat_count += 1
                if repeat_count >= 3:
                    continue  # Skip after 3 consecutive repeats
            else:
                repeat_count = 0
        deduped.append(t)

    # Navigation-aware filtering: use stuck_reason and reachable fields to
    # keep informative failures and discard unproductive thrashing
    filtered = []
    timeout_nav_count = 0
    for t in deduped:
        gs = t.get("game_state", {})
        nav = gs.get("navigation") or {}
        stuck_reason = nav.get("stuck_reason")

        # Keep first 'wall' stuck turn (teaches bail-out), discard repeats
        if stuck_reason == "wall":
            wall_turns = sum(
                1 for prev in filtered
                if (prev.get("game_state", {}).get("navigation") or {}).get("stuck_reason") == "wall"
            )
            if wall_turns >= 2:
                continue

        # Discard 'timeout' stuck turns (just slow, not informative)
        if stuck_reason == "timeout":
            timeout_nav_count += 1
            if timeout_nav_count > 1:
                continue

        # Filter turns where agent navigated to an unreachable entity target
        action_code = t.get("action_code", "")
        if "navigateTo" in action_code or "moveTo" in action_code:
            entities = gs.get("nearby_entities", [])
            # Check if the navigation target matches an unreachable entity
            for ent in entities:
                if isinstance(ent, dict) and ent.get("reachable") is False:
                    ent_name = ent.get("name", "").lower()
                    if ent_name and ent_name in action_code.lower():
                        break
            else:
                filtered.append(t)
                continue
            # Entity was unreachable — only keep if agent recognized it (bail/skip in reasoning)
            reasoning = t.get("reasoning", "").lower()
            if any(kw in reasoning for kw in ["unreachable", "skip", "bail", "can't reach", "blocked"]):
                filtered.append(t)
            continue

        filtered.append(t)

    return filtered


def _read_harness_from_sidecar(log_path: Path) -> str:
    """Read harness type from the .meta.json sidecar file next to a session log."""
    meta_path = log_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            return meta.get("harness", "unknown")
        except (json.JSONDecodeError, OSError):
            pass
    return "unknown"


def process_log(log_path: Path, output_dir: Path) -> int:
    """Process a single log file. Returns number of turns extracted."""
    harness = _read_harness_from_sidecar(log_path)

    turns = extract_turns(log_path)
    if not turns:
        return 0

    # Tag each turn with harness source for downstream filtering
    for turn in turns:
        turn["harness"] = harness

    session_dir = output_dir / log_path.stem
    session_dir.mkdir(parents=True, exist_ok=True)

    # Write turns JSONL
    jsonl_path = session_dir / "turns.jsonl"
    with open(jsonl_path, "w") as f:
        for turn in turns:
            f.write(json.dumps(turn, separators=(",", ":")) + "\n")

    return len(turns)


def main():
    parser = argparse.ArgumentParser(description="Extract OODA turns from Claude session logs")
    parser.add_argument("--log-dir", type=Path, help="Directory containing session .log files")
    parser.add_argument("--log-file", type=Path, help="Single log file to process")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dataset/extracted"),
        help="Output directory (default: dataset/extracted/)",
    )
    args = parser.parse_args()

    if not args.log_dir and not args.log_file:
        parser.error("Provide --log-dir or --log-file")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    total_turns = 0

    if args.log_file:
        logs = [args.log_file]
    else:
        logs = sorted(args.log_dir.glob("session_*.log"))

    if not logs:
        print("No log files found.", file=sys.stderr)
        sys.exit(1)

    for log_path in logs:
        n = process_log(log_path, args.output_dir)
        if n > 0:
            print(f"  {log_path.name}: {n} turns")
        total_turns += n

    print(f"\nTotal: {total_turns} turns from {len(logs)} logs → {args.output_dir}")


if __name__ == "__main__":
    main()
