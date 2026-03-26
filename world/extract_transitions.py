#!/usr/bin/env python3
"""
extract_transitions.py — Extract (state, action, next_state) transition triads
from raw Claude JSONL session logs for world model training.

Reuses parsing logic from the existing extract_turns.py pipeline.

Usage:
    python -m world.extract_transitions --log-dir dataset/raw/ --output dataset/world_model/transitions.jsonl
    python -m world.extract_transitions --log-dir dataset/raw/ --output dataset/world_model/transitions.jsonl --limit 500
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── Reuse the same parsing helpers as extract_turns.py ────────────────────────

def parse_events(log_path: Path) -> list[dict]:
    """Parse JSONL log into a flat list of typed events."""
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

            if bt == "tool_use":
                events.append({
                    "line": i, "type": "tool_use", "role": t,
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                    "id": block.get("id", ""),
                    "timestamp": timestamp,
                })
            elif bt == "tool_result":
                text_content = ""
                c = block.get("content", [])
                if isinstance(c, str):
                    text_content = c
                elif isinstance(c, list):
                    for item in c:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_content += item.get("text", "")
                events.append({
                    "line": i, "type": "tool_result", "role": t,
                    "tool_use_id": block.get("tool_use_id", ""),
                    "text": text_content,
                    "timestamp": timestamp,
                })

    return events


def is_observe(event: dict) -> bool:
    """Check if a tool_use event reads game state."""
    if event["type"] != "tool_use":
        return False
    if "browser_run_code" not in event.get("name", ""):
        return False
    code = event.get("input", {}).get("code", "")
    return "__latestGameState" in code or "__extractGameState" in code


def is_browser_action(event: dict) -> bool:
    """Check if a tool_use event is a game action (not observe)."""
    if event["type"] != "tool_use":
        return False
    if "browser_run_code" not in event.get("name", ""):
        return False
    code = event.get("input", {}).get("code", "")
    return "__latestGameState" not in code and "__extractGameState" not in code


def parse_game_state(text: str) -> dict | None:
    """Parse game state JSON from tool_result text."""
    text = text.strip()
    if not text:
        return None

    lines = text.split("\n")
    result_lines = []
    for line in lines:
        if line.strip().startswith("### Ran Playwright code"):
            break
        result_lines.append(line)

    for line in result_lines:
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, str):
                inner = json.loads(obj)
                if isinstance(inner, dict):
                    return inner
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def classify_action(code: str) -> str:
    """Classify JS code into a canonical action type."""
    code_lower = code.lower()

    if "__attackmob" in code_lower: return "attack"
    if "__interactnpc" in code_lower: return "interact_npc"
    if "__navigateto" in code_lower: return "navigate"
    if "__moveto" in code_lower: return "move"
    if "__clickentity" in code_lower: return "click_entity"
    if "__clicktile" in code_lower: return "click_tile"
    if "__talktonpc" in code_lower: return "talk_npc"
    if "__safewarp" in code_lower: return "warp"
    if "__stuckreset" in code_lower: return "stuck_reset"
    if "__eatfood" in code_lower or "selectedible" in code_lower: return "eat"
    if "page.goto" in code: return "reconnect"
    if "#respawn" in code or "'respawn'" in code: return "respawn"
    if "quest-button" in code or "quest_button" in code: return "quest_accept"
    if "selectEdible" in code: return "heal"
    if "action-equip" in code: return "equip"
    if "mouseevent" in code_lower or "dispatchevent" in code_lower: return "click"
    if "waitfortimeout" in code_lower: return "wait"

    return "other"


def extract_action_args(code: str, action_type: str) -> dict:
    """Extract numeric args from action code."""
    args = {}

    if action_type == "attack":
        m = re.search(r"__attackMob\(['\"]([^'\"]+)['\"]", code)
        if m:
            args["target"] = m.group(1)

    if action_type in ("navigate", "move", "click_tile"):
        m = re.search(r"\{\s*x:\s*(\d+),\s*y:\s*(\d+)\s*\}", code)
        if m:
            args["x"] = int(m.group(1))
            args["y"] = int(m.group(2))
        else:
            m = re.search(r"__\w+To\((\d+),\s*(\d+)\)", code)
            if m:
                args["x"] = int(m.group(1))
                args["y"] = int(m.group(2))

    if action_type == "click":
        mx = re.search(r"clientX:\s*(\d+)", code)
        my = re.search(r"clientY:\s*(\d+)", code)
        if mx and my:
            args["x"] = int(mx.group(1))
            args["y"] = int(my.group(1))

    if action_type in ("equip", "heal"):
        m = re.search(r"\((\d+)\)", code)
        if m:
            args["slot"] = int(m.group(1))

    return args


def _safe_num(val, default=0):
    """Coerce to int/float safely, handling strings, dicts, etc."""
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return default
    return default


def compute_delta(state: dict, next_state: dict) -> dict:
    """Compute meaningful differences between consecutive states."""
    delta = {}

    # Player stats deltas
    ps = state.get("player_stats", state)
    nps = next_state.get("player_stats", next_state)

    try:
        hp_now = _safe_num(ps.get("hp", ps.get("hitpoints", 0)))
        hp_next = _safe_num(nps.get("hp", nps.get("hitpoints", 0)))
        if hp_now != hp_next:
            delta["hp_delta"] = hp_next - hp_now
    except Exception:
        pass

    try:
        xp_now = _safe_num(ps.get("experience", ps.get("exp", 0)))
        xp_next = _safe_num(nps.get("experience", nps.get("exp", 0)))
        if xp_now != xp_next:
            delta["xp_delta"] = xp_next - xp_now
    except Exception:
        pass

    try:
        level_now = _safe_num(ps.get("level", 1), 1)
        level_next = _safe_num(nps.get("level", 1), 1)
        if level_now != level_next:
            delta["level_delta"] = level_next - level_now
    except Exception:
        pass

    # Position change
    try:
        pos = state.get("player_position", state.get("position", {}))
        npos = next_state.get("player_position", next_state.get("position", {}))
        if isinstance(pos, dict) and isinstance(npos, dict):
            px, py = _safe_num(pos.get("x", 0)), _safe_num(pos.get("y", 0))
            nx, ny = _safe_num(npos.get("x", 0)), _safe_num(npos.get("y", 0))
            if px != nx or py != ny:
                delta["moved"] = True
                delta["dx"] = nx - px
                delta["dy"] = ny - py
    except Exception:
        pass

    # Death detection
    try:
        ui_next = next_state.get("ui_state", {})
        if isinstance(ui_next, dict) and ui_next.get("is_dead", next_state.get("is_dead", False)):
            delta["died"] = True
    except Exception:
        pass

    # Entity count changes
    try:
        ents_now = state.get("nearby_entities", state.get("entities", []))
        ents_next = next_state.get("nearby_entities", next_state.get("entities", []))
        if isinstance(ents_now, list) and isinstance(ents_next, list):
            if len(ents_now) != len(ents_next):
                delta["entity_count_delta"] = len(ents_next) - len(ents_now)
    except Exception:
        pass

    return delta


def extract_transitions_from_log(log_path: Path) -> list[dict]:
    """Extract (state, action, next_state) triads from one session log."""
    events = parse_events(log_path)
    transitions = []

    # Build index: tool_use_id → tool_result text
    result_map = {}
    for ev in events:
        if ev["type"] == "tool_result":
            result_map[ev["tool_use_id"]] = ev["text"]

    # Collect observe events with their parsed game states
    observations = []
    for ev in events:
        if is_observe(ev):
            result_text = result_map.get(ev["id"], "")
            game_state = parse_game_state(result_text)
            if game_state:
                observations.append({
                    "line": ev["line"],
                    "state": game_state,
                    "timestamp": ev.get("timestamp"),
                })

    if len(observations) < 2:
        return []

    # For each pair of consecutive observations, find the action between them
    for i in range(len(observations) - 1):
        obs_now = observations[i]
        obs_next = observations[i + 1]

        # Find browser actions between these two observations
        actions_between = []
        for ev in events:
            if ev["line"] > obs_now["line"] and ev["line"] < obs_next["line"]:
                if is_browser_action(ev):
                    actions_between.append(ev)

        if not actions_between:
            continue

        # Use the LAST action before the next observation as the transition action
        last_action = actions_between[-1]
        action_code = last_action.get("input", {}).get("code", "")
        action_type = classify_action(action_code)
        action_args = extract_action_args(action_code, action_type)

        # Skip infrastructure actions
        if action_type in ("reconnect", "stuck_reset", "clear_combat", "nav_cancel"):
            continue

        delta = compute_delta(obs_now["state"], obs_next["state"])

        transitions.append({
            "state": obs_now["state"],
            "action": action_type,
            "action_args": action_args,
            "next_state": obs_next["state"],
            "delta": delta,
            "source": str(log_path.name),
            "timestamp": obs_now.get("timestamp"),
        })

    return transitions


def extract_all(log_dir: Path, output_path: Path, limit: int = 0):
    """Extract transitions from all session logs under log_dir."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log_files = sorted(log_dir.rglob("*.log"))
    if not log_files:
        print(f"No .log files found under {log_dir}")
        sys.exit(1)

    total = 0
    skipped_files = 0

    with open(output_path, "w") as f:
        for log_path in log_files:
            try:
                transitions = extract_transitions_from_log(log_path)
            except Exception as e:
                print(f"  SKIP {log_path.name}: {e}")
                skipped_files += 1
                continue

            for t in transitions:
                f.write(json.dumps(t) + "\n")
                total += 1

                if limit and total >= limit:
                    print(f"\nReached limit of {limit} transitions.")
                    print(f"Total: {total} transitions from {len(log_files) - skipped_files} files")
                    return total

            if transitions:
                print(f"  {log_path.name}: {len(transitions)} transitions")

    print(f"\nTotal: {total} transitions from {len(log_files) - skipped_files} files")
    print(f"Output: {output_path}")
    return total


def main():
    parser = argparse.ArgumentParser(description="Extract world model transitions from raw logs")
    parser.add_argument("--log-dir", type=str, default="dataset/raw/",
                        help="Directory containing raw agent logs")
    parser.add_argument("--output", type=str, default="dataset/world_model/transitions.jsonl",
                        help="Output JSONL file path")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max transitions to extract (0 = all)")
    args = parser.parse_args()

    extract_all(Path(args.log_dir), Path(args.output), args.limit)


if __name__ == "__main__":
    main()
