#!/usr/bin/env python3
"""
test_logger.py — Simulates a Claude gameplay session to verify logger.py works.

Run:  python3 test_logger.py
Then: python3 logger.py   (in another terminal, or check dataset/ after)

What it does:
  1. Creates fake logs/, state/ files that look exactly like real gameplay
  2. Simulates 4 turns (screenshot updates + log appends)
  3. You can run logger.py alongside to watch it react in real time,
     or run this first and check dataset/ afterwards
"""

import json
import os
import shutil
import time
from pathlib import Path

BASE = Path(__file__).parent

# --- helpers -----------------------------------------------------------

def make_screenshot(path: Path):
    """Write a minimal valid 1x1 PNG — just needs to exist and change."""
    # Smallest valid PNG bytes (1x1 white pixel)
    png = bytes([
        0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a,  # PNG signature
        0x00,0x00,0x00,0x0d,0x49,0x48,0x44,0x52,  # IHDR chunk
        0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
        0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,
        0xde,0x00,0x00,0x00,0x0c,0x49,0x44,0x41,  # IDAT chunk
        0x54,0x08,0xd7,0x63,0xf8,0xff,0xff,0x3f,
        0x00,0x05,0xfe,0x02,0xfe,0xdc,0xcc,0x59,
        0xe7,0x00,0x00,0x00,0x00,0x49,0x45,0x4e,  # IEND chunk
        0x44,0xae,0x42,0x60,0x82,
    ])
    path.write_bytes(png)


def log_event(log_path: Path, event: dict):
    with open(log_path, "a") as f:
        f.write(json.dumps(event) + "\n")


def action_event(code: str) -> dict:
    """Mimics the stream-json format Claude Code outputs for a tool call."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "mcp__playwright__browser_run_code",
                    "input": {"code": code},
                }
            ]
        },
    }


def screenshot_event() -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "mcp__playwright__browser_take_screenshot",
                    "input": {},
                }
            ]
        },
    }


# --- test turns --------------------------------------------------------

TURNS = [
    {
        "label": "walk south out of spawn",
        "code": "await page.keyboard.down('ArrowDown'); await page.waitForTimeout(800); await page.keyboard.up('ArrowDown');",
        "state": {
            "sessions": 1,
            "level": 1,
            "xp_estimate": "0",
            "locations_visited": ["mudwich"],
            "kills_this_session": 0,
            "last_action": "walked south",
            "notes": "",
        },
    },
    {
        "label": "attack rat",
        "code": "await page.mouse.click(640, 360);",
        "state": {
            "sessions": 1,
            "level": 1,
            "xp_estimate": "40",
            "locations_visited": ["mudwich"],
            "kills_this_session": 1,
            "last_action": "killed rat, gained 40 xp",
            "notes": "",
        },
    },
    {
        "label": "level up + new area",
        "code": "await page.keyboard.down('ArrowRight'); await page.waitForTimeout(1200); await page.keyboard.up('ArrowRight');",
        "state": {
            "sessions": 1,
            "level": 2,
            "xp_estimate": "130",
            "locations_visited": ["mudwich", "coastline_with_dock"],
            "kills_this_session": 2,
            "last_action": "explored east, found coastline",
            "notes": "",
        },
    },
    {
        "label": "loot drop",
        "code": "await page.mouse.click(655, 370);",
        "state": {
            "sessions": 1,
            "level": 2,
            "xp_estimate": "170",
            "locations_visited": ["mudwich", "coastline_with_dock"],
            "kills_this_session": 3,
            "last_action": "looted item drop",
            "notes": "",
        },
    },
]


# --- main --------------------------------------------------------------

def run():
    # Setup dirs
    (BASE / "state").mkdir(exist_ok=True)
    (BASE / "logs").mkdir(exist_ok=True)

    log_path = BASE / "logs/session_1_20260317_120000.log"
    log_path.unlink(missing_ok=True)  # fresh log each run

    print("=== test: simulating 4 turns ===\n")
    print("If logger.py is running in another terminal, you'll see it react.\n")

    for i, turn in enumerate(TURNS, 1):
        print(f"turn {i}: {turn['label']}")

        # 1. Append action to log  (what Claude did)
        log_event(log_path, action_event(turn["code"]))

        # 2. Append screenshot event to log
        log_event(log_path, screenshot_event())

        # 3. Update progress.json
        (BASE / "state/progress.json").write_text(json.dumps(turn["state"], indent=2))

        # 4. Write new screenshot (triggers logger)
        make_screenshot(BASE / "state/screenshot.png")

        time.sleep(0.8)  # logger polls every 0.5s — give it time to catch each turn

    print("\n=== done ===")
    print("check dataset/session_1/steps.jsonl")


def verify():
    """Print what the logger produced so you can eyeball it."""
    jsonl = BASE / "dataset/session_1/steps.jsonl"
    if not jsonl.exists():
        print("\nno dataset yet — run logger.py first, then this script")
        return

    print("\n=== dataset/session_1/steps.jsonl ===\n")
    for line in jsonl.read_text().splitlines():
        rec = json.loads(line)
        print(
            f"  step {rec['step']:04d}"
            f"  reward={rec['reward']:+.3f}"
            f"  action={rec['action']['tool'].split('__')[-1] if rec['action'] else '?'}"
            f"  level={rec['state'].get('level','?')}"
            f"  xp={rec['state'].get('xp_estimate','?')}"
        )

    frames = list((BASE / "dataset/session_1/frames").glob("*.png"))
    print(f"\n  {len(frames)} frame(s) saved to dataset/session_1/frames/")


if __name__ == "__main__":
    run()
    verify()
