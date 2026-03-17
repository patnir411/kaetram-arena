#!/usr/bin/env python3
"""
logger.py — Watches Claude's gameplay and builds a training dataset.

Run alongside play.sh:
    python3 logger.py

Output:
    dataset/
      session_1/
        frames/     step_0001.png  step_0002.png  ...
        steps.jsonl
"""

import json
import os
import shutil
import time
from pathlib import Path

BASE           = Path(__file__).parent
SCREENSHOT     = BASE / "state/screenshot.png"
STATE_FILE     = BASE / "state/progress.json"
WS_STATE_FILE  = BASE / "state/game_state.json"
LOGS_DIR       = BASE / "logs"
DATASET_DIR    = BASE / "dataset"


def latest_log():
    logs = sorted(LOGS_DIR.glob("session_*.log"), key=os.path.getmtime)
    return logs[-1] if logs else None


def session_id(log: Path) -> str:
    # session_3_20240315_123456  →  session_3
    parts = log.stem.split("_")
    return f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else log.stem


def last_game_action(log: Path):
    """Scan the log and return the most recent browser_run_code call."""
    result = None
    try:
        with open(log) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "assistant":
                    continue
                for block in ev.get("message", {}).get("content", []):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and "browser_run_code" in block.get("name", "")
                    ):
                        result = {
                            "tool": block["name"],
                            "code": block.get("input", {}).get("code", "")[:300],
                        }
    except OSError:
        pass
    return result


def read_state():
    state = {}
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    try:
        ws_state = json.loads(WS_STATE_FILE.read_text())
        state = {**state, **ws_state}
    except (OSError, json.JSONDecodeError):
        pass
    return state


def compute_reward(prev, curr):
    r = 0.0

    # XP gained
    try:
        curr_xp  = float(str(curr.get("xp_estimate", 0)).replace(",", "") or 0)
        prev_xp  = float(str(prev.get("xp_estimate", 0)).replace(",", "") or 0)
        xp_delta = curr_xp - prev_xp
        r += max(0.0, xp_delta) * 0.01
    except (ValueError, TypeError):
        pass

    # Level up
    if curr.get("level", 1) > prev.get("level", 1):
        r += 5.0

    # New area explored
    new_locs = set(curr.get("locations_visited", [])) - set(prev.get("locations_visited", []))
    r += len(new_locs) * 1.0

    return round(r, 3)


def main():
    DATASET_DIR.mkdir(exist_ok=True)

    sid       = None
    step      = 0
    frames    = None
    jsonl     = None
    prev_st   = {}
    prev_mtime = 0.0

    print("logger: watching for gameplay… (ctrl-c to stop)")

    while True:
        try:
            if not SCREENSHOT.exists():
                time.sleep(1)
                continue

            mtime = SCREENSHOT.stat().st_mtime
            if mtime <= prev_mtime:
                time.sleep(0.5)
                continue
            prev_mtime = mtime

            log = latest_log()
            if not log:
                time.sleep(1)
                continue

            # New session?
            current_sid = session_id(log)
            if current_sid != sid:
                sid    = current_sid
                step   = 0
                sdir   = DATASET_DIR / sid
                frames = sdir / "frames"
                frames.mkdir(parents=True, exist_ok=True)
                jsonl  = sdir / "steps.jsonl"
                prev_st = read_state()
                print(f"logger: session → {sid}")

            step += 1
            frame = frames / f"step_{step:04d}.png"
            shutil.copy2(SCREENSHOT, frame)

            curr_st = read_state()
            action  = last_game_action(log)
            r       = compute_reward(prev_st, curr_st)

            record = {
                "session":    sid,
                "step":       step,
                "timestamp":  mtime,
                "screenshot": str(frame),
                "state":      curr_st,
                "action":     action,
                "done":       False,
                "reward":     r,
            }

            with open(jsonl, "a") as f:
                f.write(json.dumps(record) + "\n")

            prev_st = curr_st
            label   = action["tool"].split("__")[-1] if action else "?"
            print(f"  [{step:04d}]  reward={r:+.3f}  {label}")

        except KeyboardInterrupt:
            print("\nlogger: stopped.")
            break
        except Exception as e:
            print(f"logger: error — {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
