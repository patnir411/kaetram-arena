"""Game state extraction from session logs with format normalization.

Extracts composite game state by scanning tool_result events in session logs.
Handles quest format normalization for reliable display.
"""

import json
import os
import re
import glob
import sys
import time
from datetime import datetime
from pathlib import Path

from dashboard.constants import DATASET_DIR, LOG_DIR

# Import shared format detection
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cli_adapter import detect_log_format


def extract_game_state_from_db(username: str) -> dict | None:
    """Extract game state directly from MongoDB for a given player username.

    Returns normalized state dict or None if DB is unavailable or player not found.
    """
    try:
        from dashboard.db import get_reader
        reader = get_reader()
        return reader.get_player_state(username)
    except Exception:
        return None


# Keys that indicate a tool result contains game state data
GAME_STATE_KEYS = {
    "player_stats", "playerStats", "stats",
    "player_position", "playerPosition", "pos", "player_pos",
    "inventory", "quests", "achievements", "equipment", "skills",
    "nearby_entities", "nearest_mob", "current_target",
}

# Keys to merge as top-level replacements (newer overwrites older)
MERGE_KEYS = {
    "player_stats", "player_position", "inventory", "quests",
    "achievements", "equipment", "skills", "nearby_entities",
    "nearest_mob", "current_target", "player_count_nearby",
    "ui_state", "navigation", "last_combat", "last_xp_event",
}

# Regex for parsing flat string quests like "foresting stage:3 done:true"
_QUEST_STRING_RE = re.compile(
    r'^(?P<name>\S+)'
    r'(?:\s+stage:(?P<stage>\d+))?'
    r'(?:\s+(?:done|finished):(?P<done>\w+))?',
    re.IGNORECASE,
)


def _normalize_quest_entry(q):
    """Normalize a single quest entry (string or dict) to canonical format.

    Returns: {key, name, description, stage, stageCount, started, finished}
    or None if unrecognizable.
    """
    if isinstance(q, str):
        m = _QUEST_STRING_RE.match(q.strip())
        if not m:
            return {"key": q.strip(), "name": q.strip(), "description": "",
                    "stage": 0, "stageCount": 1, "started": True, "finished": False}
        name = m.group("name")
        stage = int(m.group("stage")) if m.group("stage") else 0
        done = m.group("done")
        finished = done and done.lower() == "true"
        return {
            "key": name, "name": name, "description": "",
            "stage": stage, "stageCount": max(stage, 1),
            "started": stage > 0 or finished, "finished": finished,
        }
    elif isinstance(q, dict):
        key = q.get("key", q.get("name", "unknown"))
        name = q.get("name", q.get("key", key))
        desc = q.get("description", "")
        if isinstance(desc, str) and "|" in desc:
            desc = desc.split("|")[0]
        stage = q.get("stage", q.get("progress", 0)) or 0
        stage_count = q.get("stageCount", q.get("total_stages", q.get("stages", 1))) or 1
        started = q.get("started", stage > 0)
        finished = q.get("finished", q.get("done", False))
        if isinstance(finished, str):
            finished = finished.lower() == "true"
        if isinstance(started, str):
            started = started.lower() == "true"
        # Infer finished from stage >= stageCount
        if not finished and stage >= stage_count and stage > 0:
            finished = True
        return {
            "key": key, "name": name, "description": desc,
            "stage": stage, "stageCount": stage_count,
            "started": started or finished, "finished": finished,
        }
    return None


def normalize_quests(quests_list):
    """Normalize a list of quest entries to canonical format, deduplicating by key.

    Later entries take precedence (since composite merge processes chronologically).
    """
    if not isinstance(quests_list, list):
        return []
    by_key = {}
    for q in quests_list:
        normalized = _normalize_quest_entry(q)
        if normalized:
            by_key[normalized["key"]] = normalized
    return list(by_key.values())


def parse_tool_result_text(text):
    """Parse game state JSON from a tool_result text string.

    Accepts full game state dumps AND partial action results that contain
    game-relevant keys (inventory, pos, stats, level, etc.).
    Returns parsed dict or None.
    """
    if not isinstance(text, str):
        return None
    # Quick reject: must contain at least one game-relevant keyword
    if not any(k in text for k in ("player_stats", "playerStats", "stats",
                                    "inventory", "pos", "player_pos",
                                    "nearby_entities", "level", "quests",
                                    "equipment", "skills")):
        return None
    # Strip markdown wrapper
    if text.startswith("### Result"):
        lines = text.split("\n")
        json_line = lines[1].strip() if len(lines) > 1 else ""
        if json_line.startswith('"') and json_line.endswith('"'):
            try:
                text = json.loads(json_line)
            except Exception:
                pass
    # Strip ASCII map / symbols appendix
    if isinstance(text, str):
        for sep in ("\n\nASCII_MAP:", "\n\nASCII:", "\n\nSYMBOLS:",
                    "\n\nSTUCK_CHECK:"):
            idx = text.find(sep)
            if idx != -1:
                text = text[:idx]
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    # If result wraps game state under a "state" sub-key, merge it up
    if "state" in parsed and isinstance(parsed["state"], dict):
        sub = parsed.pop("state")
        for k, v in sub.items():
            if k not in parsed:
                parsed[k] = v
    # Also handle "final_state" wrapper
    if "final_state" in parsed and isinstance(parsed["final_state"], dict):
        sub = parsed.pop("final_state")
        for k, v in sub.items():
            if k not in parsed:
                parsed[k] = v
    # Normalize aliases
    if "playerStats" in parsed and "player_stats" not in parsed:
        parsed["player_stats"] = parsed.pop("playerStats")
    if "playerPosition" in parsed and "player_position" not in parsed:
        parsed["player_position"] = parsed.pop("playerPosition")
    if "stats" in parsed and "player_stats" not in parsed:
        parsed["player_stats"] = parsed.pop("stats")
    if "pos" in parsed and "player_position" not in parsed:
        parsed["player_position"] = parsed.pop("pos")
    if "player_pos" in parsed and "player_position" not in parsed:
        parsed["player_position"] = parsed.pop("player_pos")
    # Handle "hp": "68/69" string format → synthesize player_stats
    if "hp" in parsed and isinstance(parsed["hp"], str) and "/" in parsed["hp"]:
        try:
            hp, max_hp = parsed["hp"].split("/")
            ps = parsed.setdefault("player_stats", {})
            ps["hp"] = int(hp)
            ps["max_hp"] = int(max_hp)
        except (ValueError, TypeError):
            pass
    # Handle "hp": {"hp":369,"max_hp":369,"level":41,...} dict format
    if "hp" in parsed and isinstance(parsed["hp"], dict) and "player_stats" not in parsed:
        hp_dict = parsed["hp"]
        if "hp" in hp_dict or "max_hp" in hp_dict or "level" in hp_dict:
            parsed["player_stats"] = hp_dict
    # Merge top-level "level" into player_stats
    if "level" in parsed and isinstance(parsed["level"], int):
        ps = parsed.setdefault("player_stats", {})
        if "level" not in ps:
            ps["level"] = parsed["level"]
    # Normalize inventory: convert bare strings to objects
    if "inventory" in parsed and isinstance(parsed["inventory"], list):
        normalized = []
        for item in parsed["inventory"]:
            if isinstance(item, str):
                normalized.append({"name": item, "count": 1})
            elif isinstance(item, dict):
                normalized.append(item)
        parsed["inventory"] = normalized
    # Normalize quests to canonical format
    if "quests" in parsed and isinstance(parsed["quests"], list):
        parsed["quests"] = normalize_quests(parsed["quests"])
    # Accept if any game state key is present
    if GAME_STATE_KEYS & parsed.keys():
        return parsed
    return None


def _extract_tool_result_texts_from_line(obj, fmt):
    """Extract tool result text strings from a single log line.

    Yields text strings that may contain game state JSON.
    Handles both Claude and Codex event structures.
    """
    if fmt == "claude" or fmt == "unknown":
        # Claude: {"type": "user", "message": {"content": [{"type": "tool_result", ...}]}}
        if obj.get("type") != "user":
            return
        msg = obj.get("message", {})
        content = msg.get("content", msg)
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            c = block.get("content", "")
            if isinstance(c, list):
                for item in c:
                    if isinstance(item, dict):
                        c = item.get("text", "")
                        break
                else:
                    continue
            if isinstance(c, str):
                yield c

    if fmt == "codex" or fmt == "unknown":
        # Primary Codex format: item.completed with mcp_tool_call
        if obj.get("type") == "item.completed":
            item = obj.get("item", {})
            if item.get("type") == "mcp_tool_call":
                result = item.get("result", {})
                if isinstance(result, dict):
                    for block in result.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if isinstance(text, str):
                                yield text
                elif isinstance(result, str):
                    yield result

        # Fallback: top-level output/result string
        for key in ("output", "result"):
            val = obj.get(key)
            if isinstance(val, str) and "player_position" in val:
                yield val


def _merge_parsed_state(composite, quest_by_key, parsed):
    """Merge a parsed game state dict into the composite state."""
    for k in MERGE_KEYS:
        if k not in parsed:
            continue
        new_val = parsed[k]
        old_val = composite.get(k)

        # Special handling: quests merge by key
        if k == "quests" and isinstance(new_val, list):
            for q in new_val:
                if isinstance(q, dict) and "key" in q:
                    quest_by_key[q["key"]] = q
            continue

        # For lists (inventory): don't replace objects with strings
        if (isinstance(new_val, list) and isinstance(old_val, list)
                and old_val and isinstance(old_val[0], dict)
                and new_val and isinstance(new_val[0], str)):
            continue  # keep richer old data
        # For dicts (player_stats): merge keys, don't replace
        if isinstance(new_val, dict) and isinstance(old_val, dict):
            old_val.update(new_val)
            continue
        composite[k] = new_val


def extract_game_state_from_log(qs=None):
    """Extract composite game state from session log tool results.

    Scans the last 1MB of the latest session log. Builds a composite
    state by merging all parsed tool results — full OBSERVE dumps and
    partial action results alike. Newer values overwrite older ones.

    Supports both Claude and Codex log formats via auto-detection.
    Quest lists are merged by key (deduplication) instead of full replacement.
    """
    agent_id = qs.get("agent", [None])[0] if qs else None
    if agent_id is not None:
        log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{agent_id}", "logs")
    else:
        log_dir = LOG_DIR
    logs = sorted(glob.glob(os.path.join(log_dir, "session_*.log")),
                   key=os.path.getmtime)
    if not logs:
        return None
    latest = logs[-1]

    # Detect log format for appropriate extraction
    fmt = detect_log_format(Path(latest))

    composite = {}
    quest_by_key = {}  # Key-based quest dedup
    last_timestamp = None
    tail_size = 1048576  # 1MB
    try:
        with open(latest) as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_size))
            if size > tail_size:
                fh.readline()  # skip partial first line
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                line_ts = obj.get("timestamp")

                # Extract tool result texts using format-aware extraction
                for text in _extract_tool_result_texts_from_line(obj, fmt):
                    parsed = parse_tool_result_text(text)
                    if not parsed:
                        continue
                    _merge_parsed_state(composite, quest_by_key, parsed)
                    last_timestamp = line_ts
    except Exception:
        pass

    # Finalize quests from the key-based map
    if quest_by_key:
        composite["quests"] = list(quest_by_key.values())

    if not composite:
        return None
    freshness = -1
    if last_timestamp:
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            freshness = round(time.time() - dt.timestamp(), 1)
        except Exception:
            freshness = round(time.time() - os.path.getmtime(latest), 1)
    else:
        freshness = round(time.time() - os.path.getmtime(latest), 1)
    composite["_freshness"] = freshness
    return composite
