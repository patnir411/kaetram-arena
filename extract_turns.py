#!/usr/bin/env python3
"""
extract_turns.py — Post-process Claude JSONL session logs into clean OODA turns.

Reads the stream-json output from `claude -p` sessions and extracts
(screenshot, game_state, reasoning, action) tuples for SFT training.

Usage:
    python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/
    python3 extract_turns.py --log-file logs/session_2_20260319_060749.log
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


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


def is_observe(event: dict) -> bool:
    """Check if a tool_use event is an observe step (reads __latestGameState)."""
    if event["type"] != "tool_use":
        return False
    if "browser_run_code" not in event.get("name", ""):
        return False
    code = event.get("input", {}).get("code", "")
    return "__latestGameState" in code or "__extractGameState" in code


def is_browser_action(event: dict) -> bool:
    """Check if a tool_use event is a browser action (not an observe)."""
    if event["type"] != "tool_use":
        return False
    if "browser_run_code" not in event.get("name", ""):
        return False
    code = event.get("input", {}).get("code", "")
    return "__latestGameState" not in code and "__extractGameState" not in code


def extract_screenshot_path(code: str) -> str | None:
    """Extract screenshot file path from browser_run_code JS."""
    m = re.search(r"path:\s*'([^']+\.png)'", code)
    if m:
        return m.group(1)
    m = re.search(r'path:\s*"([^"]+\.png)"', code)
    if m:
        return m.group(1)
    return None


def parse_game_state(text: str) -> dict | None:
    """Parse game state JSON from tool_result text. Handles double-encoding."""
    text = text.strip()
    if not text:
        return None

    # The tool result format is multi-line:
    #   ### Result
    #   "{\"timestamp\":...}"    <-- double-encoded JSON string
    #   ### Ran Playwright code
    #   ...
    # We need to extract and parse the JSON line.
    lines = text.split("\n")

    # Try each line for JSON content
    for line in lines:
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
                return obj
        except (json.JSONDecodeError, TypeError):
            continue

    # Fallback: find first { and try to parse to end of that line
    for line in lines:
        idx = line.find("{")
        if idx >= 0:
            try:
                obj = json.loads(line[idx:])
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, TypeError):
                continue

    return None


def classify_action(code: str) -> str:
    """Classify browser action JS code into a named action type."""
    code_lower = code.lower()

    if "quest-button" in code or "quest_button" in code:
        return "quest_accept"
    if "selectEdible" in code or "selectedible" in code_lower:
        return "heal"
    if "action-equip" in code:
        return "equip"
    if "warp" in code_lower and ("show()" in code or "warp0" in code or "warp1" in code):
        return "warp"
    if "setattackstyle" in code_lower:
        return "set_style"
    if "mouseevent" in code_lower or ".click(" in code_lower or "dispatchevent" in code_lower:
        return "click"
    if "waitfortimeout" in code_lower:
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


def structured_action(action_type: str, action_code: str) -> str:
    """Convert raw JS action code into a structured action string for SFT."""
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
        m = re.search(r"warp(\d+)", action_code)
        idx = m.group(1) if m else "0"
        locations = {"0": "Mudwich", "1": "Crossroads", "2": "Lakesworld"}
        return f"warp({locations.get(idx, idx)})"

    if action_type == "quest_accept":
        return "quest_accept()"

    if action_type == "set_style":
        m = re.search(r"setAttackStyle\((\d+)\)", action_code)
        styles = {"0": "Stab", "1": "Hack", "2": "Chop"}
        idx = m.group(1) if m else "?"
        return f"set_style({styles.get(idx, idx)})"

    if action_type == "wait":
        m = re.search(r"waitForTimeout\((\d+)\)", action_code)
        ms = int(m.group(1)) if m else 0
        return f"wait({ms / 1000:.1f}s)"

    return f"other({action_type})"


def normalize_game_state(gs: dict) -> dict | None:
    """Normalize variant game state formats to a standard schema.

    The agent sometimes returns custom subsets (e.g., {pos: {x,y}, player: {...}})
    instead of the full __latestGameState format. This normalizes them.
    """
    if not gs or not isinstance(gs, dict):
        return None
    if gs.get("error"):
        return None

    # Already standard format
    if "player_position" in gs and "player_stats" in gs:
        return gs

    # Custom format: {pos: {x, y}, player: {hp, ...}, ...}
    if "pos" in gs:
        normalized = dict(gs)
        normalized["player_position"] = gs["pos"]
        if "player" in gs:
            p = gs["player"]
            normalized["player_stats"] = {
                "hp": p.get("hp", 0),
                "max_hp": p.get("max_hp", p.get("maxHp", 0)),
                "level": p.get("level", 1),
                "experience": p.get("experience", p.get("xp", 0)),
            }
        return normalized

    # Custom format: {x: N, y: N, ...} (bare position)
    if "x" in gs and "y" in gs and isinstance(gs.get("x"), (int, float)):
        normalized = dict(gs)
        normalized["player_position"] = {"x": gs["x"], "y": gs["y"]}
        return normalized

    # Custom format with just player stats: {level: N, hp: N, ...}
    if "level" in gs and "hp" in gs:
        normalized = dict(gs)
        normalized["player_stats"] = {
            "hp": gs.get("hp", 0),
            "max_hp": gs.get("max_hp", gs.get("maxHp", 0)),
            "level": gs.get("level", 1),
        }
        # No position — can't use as primary state, but keep for reference
        return None

    return None


def has_action_code(code: str) -> bool:
    """Check if browser_run_code contains actual game actions (not just state reading)."""
    code_lower = code.lower()
    return any(
        k in code_lower
        for k in [
            "mouseevent",
            "dispatchevent",
            ".click(",
            "warp",
            "selectEdible",
            "selectedible",
            "action-equip",
            "quest-button",
            "setattackstyle",
        ]
    )


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
        screenshot_path = extract_screenshot_path(obs_code)
        obs_tool_id = obs_event.get("id", "")

        # Find the tool_result for this call
        game_state = None
        for j in range(obs_idx + 1, min(obs_idx + 15, len(events))):
            e = events[j]
            if e["type"] == "tool_result" and e.get("tool_use_id") == obs_tool_id:
                raw_gs = parse_game_state(e.get("text", ""))
                game_state = normalize_game_state(raw_gs) if raw_gs else None
                break

        if game_state is None:
            continue

        pp = game_state.get("player_position")
        if not pp:
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
        if has_action_code(obs_code):
            # Combined observe+action call
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
                    action_code = ev.get("input", {}).get("code", "")
                    break

            if not action_code:
                continue  # observe-only turn, skip

        action_type = classify_action(action_code)
        action_target = extract_action_target(action_code)
        reasoning = "\n".join(reasoning_parts)

        ps = game_state.get("player_stats", {})

        turn = {
            "turn_id": f"{log_path.stem}_t{len(turns):03d}",
            "timestamp": game_state.get("timestamp", 0),
            "game_state": game_state,
            "screenshot_path": screenshot_path or "",
            "reasoning": reasoning,
            "action_code": action_code,
            "action_type": action_type,
            "action_structured": structured_action(action_type, action_code),
            "action_target": extract_action_target(action_code),
            "player_stats": {
                "hp": ps.get("hp", 0),
                "max_hp": ps.get("max_hp", 0),
                "level": ps.get("level", 1),
            },
            "player_position": {"x": pp.get("x", 0), "y": pp.get("y", 0)},
        }
        turns.append(turn)

    # Deduplicate: skip consecutive turns with same position + same action_structured
    deduped = []
    for t in turns:
        if deduped:
            prev = deduped[-1]
            if (
                prev["player_position"] == t["player_position"]
                and prev["action_structured"] == t["action_structured"]
            ):
                continue
        deduped.append(t)

    return deduped


def process_log(log_path: Path, output_dir: Path, copy_frames: bool = True) -> int:
    """Process a single log file. Returns number of turns extracted."""
    turns = extract_turns(log_path)
    if not turns:
        return 0

    session_dir = output_dir / log_path.stem
    session_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = session_dir / "frames"

    if copy_frames:
        frames_dir.mkdir(exist_ok=True)

    # Copy screenshots and update paths
    for turn in turns:
        src = turn["screenshot_path"]
        if copy_frames and src and Path(src).exists():
            dst = frames_dir / f"{turn['turn_id']}.png"
            shutil.copy2(src, dst)
            turn["screenshot_path"] = str(dst)

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
    parser.add_argument(
        "--no-frames",
        action="store_true",
        help="Skip copying screenshot frames (just extract turns)",
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
        n = process_log(log_path, args.output_dir, copy_frames=not args.no_frames)
        if n > 0:
            print(f"  {log_path.name}: {n} turns")
        total_turns += n

    print(f"\nTotal: {total_turns} turns from {len(logs)} logs → {args.output_dir}")


if __name__ == "__main__":
    main()
