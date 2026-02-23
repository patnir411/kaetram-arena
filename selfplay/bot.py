"""
Scripted heuristic bot — plays Kaetram with zero API cost.

Generates bulk trajectory data by following simple rules:
1. Login and teleport to overworld
2. Explore randomly with WASD
3. Click on entities near screen center (monsters, NPCs, items)
4. Manage HP (retreat when low)
5. Generate synthetic "thoughts" for each action

Each thought is a template like:
  "I see open terrain to the north, I should explore that direction"
  "There's a monster nearby, I'll click to attack it"

These synthetic thoughts bootstrap the Action-of-Thought training format.
When fine-tuning, you can later replace these with Claude-generated thoughts.
"""

import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

from collector import TrajectoryCollector, Action

# --- Configuration ---

GAME_URL = "http://localhost:9000"
PLAYER_NAME = "TrainBot"
TELEPORT_COORDS = (188, 157)  # Mudwich village center
VIEWPORT_W = 800
VIEWPORT_H = 600

# Screen regions for entity detection (approximate)
# The player character is roughly centered
CENTER_X = VIEWPORT_W // 2
CENTER_Y = VIEWPORT_H // 2

# WASD movement with durations
DIRECTIONS = {
    "w": {"thought_tpl": "I should move north to explore", "key": "w"},
    "a": {"thought_tpl": "I should move west to explore", "key": "a"},
    "s": {"thought_tpl": "I should move south to explore", "key": "s"},
    "d": {"thought_tpl": "I should move east to explore", "key": "d"},
}

DIAGONAL_COMBOS = [
    {"keys": ["w", "a"], "thought_tpl": "Moving northwest to explore diagonally"},
    {"keys": ["w", "d"], "thought_tpl": "Moving northeast to explore diagonally"},
    {"keys": ["s", "a"], "thought_tpl": "Moving southwest to explore diagonally"},
    {"keys": ["s", "d"], "thought_tpl": "Moving southeast to explore diagonally"},
]

# Click targets relative to center — simulate clicking on entities
# These are offsets from center in various directions
CLICK_OFFSETS = [
    (0, -60, "I see something above me, clicking to interact"),
    (0, 60, "I see something below me, clicking to interact"),
    (-60, 0, "I see something to my left, clicking to interact"),
    (60, 0, "I see something to my right, clicking to interact"),
    (40, -40, "I see an entity nearby, clicking to engage"),
    (-40, 40, "I see an entity nearby, clicking to engage"),
    (80, -20, "There's something in the distance, clicking to check it out"),
    (-80, 20, "There's something in the distance, clicking to check it out"),
    (0, 0, "Clicking on my current position to pick up any items"),
]

# Exploration strategies
STRATEGIES = [
    "random_walk",      # Pure random WASD
    "spiral_out",       # Expand in spiral pattern
    "line_explore",     # Pick direction, walk far
    "combat_seek",      # Click around looking for monsters
    "mixed",            # Random mix of all above
]


def login_sequence(page: Page, collector: TrajectoryCollector):
    """Handle the login screen."""
    # Wait for page to load
    page.wait_for_timeout(3000)

    collector.record_action(Action(
        type="screenshot_only",
        params={},
        thought="Taking initial screenshot to see the login screen",
    ))

    # Check the guest checkbox
    try:
        guest_checkbox = page.locator('#guest')
        if guest_checkbox.count() > 0:
            guest_checkbox.check()
            page.wait_for_timeout(500)
    except Exception:
        pass

    # Type player name
    try:
        name_input = page.locator('#login-name-input')
        if name_input.count() > 0:
            name_input.fill(PLAYER_NAME + str(random.randint(0, 9999)))
            page.wait_for_timeout(300)
    except Exception:
        pass

    # Click login button
    collector.record_action(Action(
        type="click",
        params={"x": CENTER_X, "y": CENTER_Y + 100},
        thought="Clicking the LOGIN button to enter the game",
    ))

    # Try clicking the actual login button
    try:
        login_btn = page.locator('#login')
        if login_btn.count() > 0:
            login_btn.click()
    except Exception:
        pass

    page.wait_for_timeout(5000)

    # Press Escape to close welcome dialog
    collector.record_action(Action(
        type="key_press",
        params={"key": "Escape"},
        thought="Pressing Escape to close the welcome dialog",
    ))
    page.wait_for_timeout(1000)


def teleport_to_overworld(page: Page, collector: TrajectoryCollector):
    """Use admin teleport to get to the overworld."""
    # Open chat
    collector.record_action(Action(
        type="key_press",
        params={"key": "Enter"},
        thought="Opening the chat window to type a teleport command",
    ))
    page.wait_for_timeout(500)

    # Type teleport command
    x, y = TELEPORT_COORDS
    collector.record_action(Action(
        type="type_text",
        params={"text": f"/teleport {x} {y}"},
        thought=f"Typing teleport command to reach Mudwich village at ({x}, {y})",
    ))
    page.wait_for_timeout(300)

    # Send command
    collector.record_action(Action(
        type="key_press",
        params={"key": "Enter"},
        thought="Sending the teleport command",
    ))
    page.wait_for_timeout(2000)


def random_walk_step(collector: TrajectoryCollector):
    """Take a random WASD movement step."""
    direction = random.choice(list(DIRECTIONS.values()))
    duration = random.randint(500, 3000)
    collector.record_action(Action(
        type="key_hold",
        params={"key": direction["key"], "duration_ms": duration},
        thought=direction["thought_tpl"],
    ))


def diagonal_walk_step(collector: TrajectoryCollector):
    """Take a diagonal WASD movement step."""
    combo = random.choice(DIAGONAL_COMBOS)
    duration = random.randint(500, 2000)
    collector.record_action(Action(
        type="key_combo",
        params={"keys": combo["keys"], "duration_ms": duration},
        thought=combo["thought_tpl"],
    ))


def click_nearby(collector: TrajectoryCollector):
    """Click somewhere near the player to interact with entities."""
    dx, dy, thought = random.choice(CLICK_OFFSETS)
    # Add some jitter
    jx = dx + random.randint(-15, 15)
    jy = dy + random.randint(-15, 15)
    x = max(10, min(VIEWPORT_W - 10, CENTER_X + jx))
    y = max(10, min(VIEWPORT_H - 10, CENTER_Y + jy))
    collector.record_action(Action(
        type="click",
        params={"x": x, "y": y},
        thought=thought,
    ))


def open_inventory(collector: TrajectoryCollector):
    """Toggle inventory to see items."""
    collector.record_action(Action(
        type="key_press",
        params={"key": "i"},
        thought="Opening inventory to check my items and equipment",
    ))
    collector.page.wait_for_timeout(1000)

    collector.record_action(Action(
        type="screenshot_only",
        params={},
        thought="Examining my inventory contents",
    ))

    collector.record_action(Action(
        type="key_press",
        params={"key": "i"},
        thought="Closing inventory to return to gameplay",
    ))


def line_explore(collector: TrajectoryCollector, steps: int = 5):
    """Pick a random direction and walk far in it."""
    direction = random.choice(list(DIRECTIONS.values()))
    thought = f"Exploring far to the {'north' if direction['key'] == 'w' else 'south' if direction['key'] == 's' else 'west' if direction['key'] == 'a' else 'east'}"

    for i in range(steps):
        duration = random.randint(1500, 3000)
        collector.record_action(Action(
            type="key_hold",
            params={"key": direction["key"], "duration_ms": duration},
            thought=f"{thought} (step {i+1}/{steps})",
        ))
        # Occasional click to pick up items or fight monsters
        if random.random() < 0.3:
            click_nearby(collector)


def run_episode(page: Page, output_dir: str, episode_id: str,
                max_steps: int = 100, strategy: str = "mixed") -> Path:
    """Run one full episode of self-play and collect trajectory data."""

    collector = TrajectoryCollector(
        page=page,
        output_dir=output_dir,
        episode_id=episode_id,
        screenshot_width=VIEWPORT_W,
        screenshot_height=VIEWPORT_H,
    )
    collector.metadata["agent_type"] = f"heuristic_{strategy}"
    collector.metadata["game_url"] = GAME_URL

    # Phase 1: Login
    login_sequence(page, collector)

    # Phase 2: Teleport to overworld
    teleport_to_overworld(page, collector)

    # Phase 3: Gameplay loop
    steps_taken = 0
    while steps_taken < max_steps:
        try:
            # Choose action based on strategy
            roll = random.random()

            if strategy == "random_walk" or (strategy == "mixed" and roll < 0.4):
                random_walk_step(collector)

            elif strategy == "combat_seek" or (strategy == "mixed" and roll < 0.65):
                click_nearby(collector)

            elif strategy == "line_explore" or (strategy == "mixed" and roll < 0.8):
                remaining = min(5, max_steps - steps_taken)
                line_explore(collector, steps=remaining)
                steps_taken += remaining - 1  # account for line_explore's internal steps

            elif strategy == "spiral_out" or (strategy == "mixed" and roll < 0.9):
                diagonal_walk_step(collector)

            else:
                # Occasionally check inventory
                open_inventory(collector)

            steps_taken += 1

            # Brief pause between actions
            page.wait_for_timeout(random.randint(200, 800))

        except Exception as e:
            print(f"  Step {steps_taken} error: {e}")
            # Try to recover by waiting
            page.wait_for_timeout(2000)
            steps_taken += 1

    # Finalize
    episode_dir = collector.close(extra_metadata={
        "strategy": strategy,
        "max_steps": max_steps,
        "steps_completed": steps_taken,
    })
    return episode_dir


def main():
    """Run self-play episodes in a loop."""
    import argparse

    parser = argparse.ArgumentParser(description="Kaetram self-play data collector")
    parser.add_argument("--episodes", type=int, default=10, help="Number of episodes to run")
    parser.add_argument("--steps", type=int, default=100, help="Max steps per episode")
    parser.add_argument("--output", type=str, default="trajectories", help="Output directory")
    parser.add_argument("--strategy", type=str, default="mixed",
                        choices=STRATEGIES, help="Bot strategy")
    parser.add_argument("--headless", action="store_true", help="Run browser headlessly")
    parser.add_argument("--game-url", type=str, default=GAME_URL, help="Game server URL")
    args = parser.parse_args()

    global GAME_URL
    GAME_URL = args.game_url

    output_dir = os.path.join(os.path.dirname(__file__), "..", args.output)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Starting self-play: {args.episodes} episodes, {args.steps} steps each")
    print(f"Strategy: {args.strategy}, Output: {output_dir}")
    print(f"Game URL: {GAME_URL}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)

        for ep in range(args.episodes):
            episode_id = f"ep_{ep:05d}_{int(time.time())}"
            print(f"Episode {ep+1}/{args.episodes} [{episode_id}]")

            context = browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            )
            page = context.new_page()

            try:
                page.goto(GAME_URL, wait_until="networkidle", timeout=30000)
                episode_dir = run_episode(
                    page=page,
                    output_dir=output_dir,
                    episode_id=episode_id,
                    max_steps=args.steps,
                    strategy=args.strategy,
                )
                print(f"  Saved to {episode_dir}")
            except Exception as e:
                print(f"  Episode failed: {e}")
            finally:
                context.close()

            # Brief pause between episodes
            time.sleep(2)

        browser.close()

    print(f"\nDone! Collected {args.episodes} episodes in {output_dir}")


if __name__ == "__main__":
    main()
