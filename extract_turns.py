#!/usr/bin/env python3
"""
extract_turns.py — Post-process session logs into clean OODA turns.

Reads logs from multiple CLI harnesses and extracts (game_state, reasoning, action)
tuples for SFT training:
- Claude Code: stream-json with thinking blocks
- Codex: --json with item.started/item.completed events
- Qwen Code: stream-json (Gemini CLI fork, same format as Claude)
- Kimi: extended thinking with --thinking flag, raw output + thinking tokens

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
        return _parse_codex_events(log_path)
    # Claude, Qwen Code, and Kimi all use compatible stream-json-like formats
    # or compatible message structures
    return _parse_claude_events(log_path)


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


def is_memory_write(event: dict) -> bool:
    """Check if a tool_use event is a Bash call that writes progress.json."""
    if event["type"] != "tool_use":
        return False
    name = event.get("name", "").lower()
    if "bash" not in name:
        return False
    command = event.get("input", {}).get("command", "")
    return "progress.json" in command


def extract_memory_content(event: dict) -> dict | None:
    """Parse progress.json content from a Bash heredoc command.

    Handles patterns like:
        cat > .../progress.json << 'PROGRESS'
        { "sessions": 1, ... }
        PROGRESS
    """
    command = event.get("input", {}).get("command", "")
    if not command:
        return None

    # Try heredoc pattern: cat > ... << 'DELIM' ... DELIM
    m = re.search(
        r"<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1",
        command,
        re.DOTALL,
    )
    if m:
        body = m.group(2).strip()
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try echo/printf pattern with inline JSON
    m = re.search(r"echo\s+['\"](\{.*?\})['\"]", command, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Last resort: find any JSON object in the command
    idx = command.find("{")
    if idx >= 0:
        # Find matching closing brace
        depth = 0
        for i in range(idx, len(command)):
            if command[i] == "{":
                depth += 1
            elif command[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(command[idx : i + 1])
                    except (json.JSONDecodeError, ValueError):
                        break

    return None


def is_observe(event: dict) -> bool:
    """Check if a tool_use event is an observe step (reads game state)."""
    if event["type"] != "tool_use":
        return False
    name = event.get("name", "")
    # MCP server observe tool
    if name == "mcp__kaetram__observe":
        return True
    # Legacy browser_run_code with game state read
    if "browser_run_code" in name:
        code = event.get("input", {}).get("code", "")
        return "__latestGameState" in code or "__extractGameState" in code
    return False


# MCP action tool names → action types
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
    "mcp__kaetram__quest_action": "quest_accept",
}


def is_browser_action(event: dict) -> bool:
    """Check if a tool_use event is a game action (not an observe)."""
    if event["type"] != "tool_use":
        return False
    name = event.get("name", "")
    # MCP server action tools
    if name in MCP_ACTION_TOOLS:
        return True
    # Legacy browser_run_code action
    if "browser_run_code" in name:
        code = event.get("input", {}).get("code", "")
        return "__latestGameState" not in code and "__extractGameState" not in code
    return False


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
    """Classify action into a named action type.

    Supports both MCP tool names and legacy browser_run_code JS patterns.
    Helper functions are checked FIRST because they internally contain
    .click() calls that would otherwise match the generic 'click' pattern.
    """
    # MCP tool name → action type (fast path)
    if tool_name in MCP_ACTION_TOOLS:
        return MCP_ACTION_TOOLS[tool_name]

    code_lower = code.lower()

    # --- Helper function patterns (highest priority) ---
    if "__attackmob" in code_lower:
        return "attack"
    if "__interactnpc" in code_lower:
        return "interact_npc"
    if "__navigateto" in code_lower:
        return "navigate"
    if "__moveto" in code_lower:
        return "move"
    if "__clickentity" in code_lower:
        return "click_entity"
    if "__clicktile" in code_lower:
        return "click_tile"
    if "__talktonpc" in code_lower:
        return "talk_npc"
    if "__safewarp" in code_lower:
        return "warp"
    if "__stuckreset" in code_lower:
        return "stuck_reset"
    if "__clearcombatstate" in code_lower:
        return "clear_combat"
    if "__navcancel" in code_lower:
        return "nav_cancel"

    # --- Infrastructure actions ---
    if "page.goto" in code:
        return "reconnect"
    if "#respawn" in code or "'respawn'" in code or '"respawn"' in code:
        return "respawn"
    if ("#login" in code or "#play" in code) and ("fill(" in code_lower or ".click()" in code_lower):
        return "login"

    # --- Original action patterns ---
    if "quest-button" in code or "quest_button" in code:
        return "quest_accept"
    if "selectEdible" in code or "selectedible" in code_lower:
        return "heal"
    if "action-equip" in code:
        return "equip"
    if "warp" in code_lower and ("show()" in code or "warp0" in code or "warp1" in code or "warp2" in code):
        return "warp"
    if "setattackstyle" in code_lower:
        return "set_style"
    if "mouseevent" in code_lower or "dispatchevent" in code_lower:
        return "click"
    if "waitfortimeout" in code_lower and "mouseevent" not in code_lower:
        return "wait"

    return "other"


def extract_action_target(code: str) -> dict | None:
    """Extract click coordinates and description from action JS code."""
    target = {}

    # clientX/clientY from MouseEvent
    mx = re.search(r"clientX:\s*(\d+)", code)
    my = re.search(r"clientY:\s*(\d+)", code)
    if mx and my:
        target["x"] = int(mx.group(1))
        target["y"] = int(my.group(1))

    # Fallback: { x: N, y: N } variable pattern
    if "x" not in target:
        m = re.search(r"\{\s*x:\s*(\d+),\s*y:\s*(\d+)\s*\}", code)
        if m:
            target["x"] = int(m.group(1))
            target["y"] = int(m.group(2))

    # Description from return string
    m = re.search(r"return\s+['\"]([^'\"]+)['\"]", code)
    if m:
        target["description"] = m.group(1)

    return target if target else None


def structured_action(action_type: str, action_code: str, tool_input: dict | None = None) -> str:
    """Convert action into a structured action string for SFT.

    Supports both MCP tool inputs (structured JSON) and legacy JS code (regex).
    """
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
        return f"{action_type}({json.dumps(tool_input)})"

    # --- Legacy JS code path (regex parsing) ---
    if action_type == "attack":
        m = re.search(r"__attackMob\(['\"]([^'\"]+)['\"]", action_code)
        name = m.group(1) if m else "?"
        return f"attack({name})"

    if action_type == "interact_npc":
        m = re.search(r"__interactNPC\(['\"]([^'\"]+)['\"]", action_code)
        name = m.group(1) if m else "?"
        return f"interact_npc({name})"

    if action_type == "navigate":
        # Try {x: N, y: N} object pattern
        m = re.search(r"\{\s*x:\s*(\d+),\s*y:\s*(\d+)\s*\}", action_code)
        if m:
            return f"navigate({m.group(1)}, {m.group(2)})"
        # Try __navigateTo(x, y) direct args
        m = re.search(r"__navigateTo\((\d+),\s*(\d+)\)", action_code)
        if m:
            return f"navigate({m.group(1)}, {m.group(2)})"
        return "navigate(?, ?)"

    if action_type == "move":
        m = re.search(r"\{\s*x:\s*(\d+),\s*y:\s*(\d+)\s*\}", action_code)
        if m:
            return f"move({m.group(1)}, {m.group(2)})"
        m = re.search(r"__moveTo\((\d+),\s*(\d+)\)", action_code)
        if m:
            return f"move({m.group(1)}, {m.group(2)})"
        return "move(?, ?)"

    if action_type == "click_entity":
        m = re.search(r"__clickEntity\(['\"]([^'\"]+)['\"]", action_code)
        label = m.group(1) if m else "?"
        return f"click_entity({label})"

    if action_type == "click_tile":
        m = re.search(r"__clickTile\((\d+),\s*(\d+)\)", action_code)
        if m:
            return f"click_tile({m.group(1)}, {m.group(2)})"
        return "click_tile(?, ?)"

    if action_type == "talk_npc":
        m = re.search(r"__talkToNPC\(['\"]?([^'\")\s]+)", action_code)
        npc_id = m.group(1) if m else "?"
        return f"talk_npc({npc_id})"

    if action_type == "respawn":
        return "respawn()"

    if action_type == "reconnect":
        return "reconnect()"

    if action_type == "login":
        return "login()"

    if action_type == "stuck_reset":
        return "stuck_reset()"

    if action_type == "clear_combat":
        return "clear_combat()"

    if action_type == "nav_cancel":
        return "nav_cancel()"

    # --- Original action types ---
    if action_type == "click":
        mx = re.search(r"clientX:\s*(\d+)", action_code)
        my = re.search(r"clientY:\s*(\d+)", action_code)
        if mx and my:
            return f"click({mx.group(1)}, {my.group(1)})"
        # Chain clicks from helper function
        pairs = re.findall(r"click\((\d+),\s*(\d+)\)", action_code)
        if pairs:
            return "; ".join(f"click({x}, {y})" for x, y in pairs)
        # Variable pattern: { x: N, y: N }
        m = re.search(r"\{\s*x:\s*(\d+),\s*y:\s*(\d+)\s*\}", action_code)
        if m:
            return f"click({m.group(1)}, {m.group(2)})"
        return "click(?, ?)"

    if action_type == "equip":
        m = re.search(r"slots\[(\d+)\]", action_code)
        slot = m.group(1) if m else "?"
        return f"equip(slot={slot})"

    if action_type == "heal":
        m = re.search(r"selectEdible\((\d+)\)", action_code)
        slot = m.group(1) if m else "?"
        return f"heal(slot={slot})"

    if action_type == "warp":
        # __safeWarp(id) pattern
        m = re.search(r"__safeWarp\((\d+)\)", action_code)
        if not m:
            m = re.search(r"warp(\d+)", action_code)
        idx = m.group(1) if m else "0"
        locations = {
            "0": "Mudwich", "1": "Crossroads", "2": "Lakesworld",
            "3": "Patsow", "4": "Crullfield", "5": "Undersea",
        }
        return f"warp({locations.get(idx, idx)})"

    if action_type == "quest_accept":
        return "quest_accept()"

    if action_type == "set_style":
        m = re.search(r"setAttackStyle\((\d+)\)", action_code)
        styles = {"1": "Stab", "2": "Slash", "3": "Defensive", "6": "Hack", "7": "Chop"}
        idx = m.group(1) if m else "?"
        return f"set_style({styles.get(idx, idx)})"

    if action_type == "wait":
        m = re.search(r"waitForTimeout\((\d+)\)", action_code)
        ms = int(m.group(1)) if m else 0
        return f"wait({ms / 1000:.1f}s)"

    return f"other({action_type})"


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


def has_action_code(code: str, tool_name: str = "") -> bool:
    """Check if event contains actual game actions (not just state reading)."""
    # MCP tool actions are always actions (never combined with observe)
    if tool_name in MCP_ACTION_TOOLS:
        return True
    code_lower = code.lower()
    return any(
        k in code_lower
        for k in [
            # Helper functions
            "__attackmob",
            "__interactnpc",
            "__navigateto",
            "__moveto",
            "__clickentity",
            "__clicktile",
            "__safewarp",
            "__stuckreset",
            "__talktonpc",
            "__clearcombatstate",
            "__navcancel",
            # Original patterns
            "mouseevent",
            "dispatchevent",
            "warp",
            "selectEdible",
            "selectedible",
            "action-equip",
            "quest-button",
            "setattackstyle",
            # Infrastructure
            "page.goto",
            "'respawn'",
            '"respawn"',
            "#respawn",
        ]
    )


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

    The agent typically combines observe + action in a single browser_run_code call.
    We detect these combined calls, extract game state from the result, and classify
    the action from the code. Reasoning is collected from thinking/text blocks that
    precede each call.
    """
    events = parse_events(log_path)
    turns = []

    # Index all browser_run_code calls that read game state
    observe_indices = [i for i, e in enumerate(events) if is_observe(e)]
    if not observe_indices:
        return []

    # Also track standalone actions (browser_run_code without __latestGameState)
    action_indices = [i for i, e in enumerate(events) if is_browser_action(e)]

    for oi_pos, obs_idx in enumerate(observe_indices):
        obs_event = events[obs_idx]
        obs_code = obs_event.get("input", {}).get("code", "")
        obs_tool_id = obs_event.get("id", "")

        # Find the tool_result for this call
        game_state = None
        ascii_map = ""
        for j in range(obs_idx + 1, min(obs_idx + 15, len(events))):
            e = events[j]
            if e["type"] == "tool_result" and e.get("tool_use_id") == obs_tool_id:
                result_text = e.get("text", "")
                raw_gs = parse_game_state(result_text)
                game_state = normalize_game_state(raw_gs) if raw_gs else None
                ascii_map = extract_ascii_map(result_text)
                break

        if game_state is None:
            continue

        pp = game_state.get("player_position")
        if not pp or not isinstance(pp, dict):
            continue

        # Collect reasoning from thinking/text blocks BEFORE this observe call
        # (look back to the previous observe or start)
        prev_obs = observe_indices[oi_pos - 1] if oi_pos > 0 else -1
        reasoning_parts = []
        for j in range(prev_obs + 1, obs_idx):
            ev = events[j]
            if ev["type"] in ("thinking", "text") and ev["role"] == "assistant":
                t = ev.get("text", "").strip()
                if t:
                    reasoning_parts.append(t)

        # Also collect reasoning from blocks AFTER the tool_result but before the
        # next observe (for pure-observe → separate-action pattern)
        next_obs = observe_indices[oi_pos + 1] if oi_pos + 1 < len(observe_indices) else len(events)

        # Determine the action: either embedded in the observe code or a separate call
        action_tool_name = ""
        action_tool_input = None
        if has_action_code(obs_code):
            # Combined observe+action call (legacy browser_run_code)
            action_code = obs_code
        else:
            # Pure observe — look for a standalone action before the next observe
            action_code = None
            for j in range(obs_idx + 1, next_obs):
                ev = events[j]
                if ev["type"] in ("thinking", "text") and ev["role"] == "assistant":
                    t = ev.get("text", "").strip()
                    if t:
                        reasoning_parts.append(t)
                elif is_browser_action(ev):
                    action_tool_name = ev.get("name", "")
                    action_tool_input = ev.get("input", {})
                    action_code = ev.get("input", {}).get("code", "")
                    break

            if not action_code and not action_tool_name:
                continue  # observe-only turn, skip

        action_type = classify_action(action_code or "", action_tool_name)
        action_target = extract_action_target(action_code or "")
        reasoning = "\n".join(reasoning_parts)

        ps = game_state.get("player_stats", {})
        if isinstance(ps, str):
            try:
                ps = json.loads(ps)
            except (json.JSONDecodeError, ValueError):
                ps = {}
        if not isinstance(ps, dict):
            ps = {}

        turn = {
            "turn_id": f"{log_path.stem}_t{len(turns):03d}",
            "timestamp": game_state.get("timestamp", 0),
            "game_state": game_state,
            "ascii_map": ascii_map,
            "reasoning": reasoning,
            "action_code": action_code or json.dumps(action_tool_input or {}),
            "action_type": action_type,
            "action_structured": structured_action(action_type, action_code or "", action_tool_input),
            "action_target": action_target,
            "player_stats": {
                "hp": ps.get("hp", 0),
                "max_hp": ps.get("max_hp", 0),
                "level": ps.get("level", 1),
            },
            "player_position": {"x": pp.get("x", 0), "y": pp.get("y", 0)},
        }

        if is_valid_turn(turn):
            turns.append(turn)

        # Scan for Bash progress.json writes between this observe and the next
        for j in range(obs_idx + 1, next_obs):
            ev = events[j]
            if is_memory_write(ev):
                mem_content = extract_memory_content(ev)
                if mem_content is None:
                    continue
                # Collect reasoning before the Bash call
                mem_reasoning_parts = []
                for k in range(max(obs_idx + 1, j - 10), j):
                    ek = events[k]
                    if ek["type"] in ("thinking", "text") and ek["role"] == "assistant":
                        txt = ek.get("text", "").strip()
                        if txt:
                            mem_reasoning_parts.append(txt)
                mem_summary = json.dumps(mem_content, separators=(",", ":"))
                if len(mem_summary) > 300:
                    # Truncate but keep parseable
                    mem_summary = mem_summary[:297] + "..."
                mem_turn = {
                    "turn_id": f"{log_path.stem}_t{len(turns):03d}",
                    "timestamp": game_state.get("timestamp", 0),
                    "game_state": game_state,
                    "ascii_map": "",
                    "reasoning": "\n".join(mem_reasoning_parts) if mem_reasoning_parts else "Saving progress.",
                    "action_code": ev.get("input", {}).get("command", ""),
                    "action_type": "update_memory",
                    "action_structured": f"update_memory({mem_summary})",
                    "action_target": None,
                    "player_stats": {
                        "hp": ps.get("hp", 0),
                        "max_hp": ps.get("max_hp", 0),
                        "level": ps.get("level", 1),
                    },
                    "player_position": {"x": pp.get("x", 0), "y": pp.get("y", 0)},
                    "memory_content": mem_content,
                }
                turns.append(mem_turn)

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


def process_log(log_path: Path, output_dir: Path) -> int:
    """Process a single log file. Returns number of turns extracted."""
    turns = extract_turns(log_path)
    if not turns:
        return 0

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
