"""
Trajectory collector — wraps Playwright to capture (screenshot, thought, action) tuples.

Every browser action goes through this collector, which:
1. Takes a before-screenshot
2. Records the action + synthetic thought
3. Takes an after-screenshot
4. Saves everything as a training-ready trajectory

Output format per episode:
  trajectories/{episode_id}/
    ├── trajectory.jsonl     # One line per step
    ├── step_000_before.png
    ├── step_000_after.png
    ├── step_001_before.png
    ├── ...
    └── metadata.json        # Episode-level info
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page


@dataclass
class Action:
    """A single discrete game action."""
    type: str           # "key_hold", "key_press", "click", "type_text", "screenshot_only"
    params: dict        # e.g. {"key": "w", "duration_ms": 2000} or {"x": 342, "y": 218}
    thought: str = ""   # Chain-of-thought reasoning for this action
    timestamp: float = 0.0


@dataclass
class Step:
    """One step in a trajectory: before_state -> action -> after_state."""
    step_idx: int
    before_screenshot: str   # relative path to PNG
    after_screenshot: str    # relative path to PNG
    action: Action
    reward: float = 0.0      # can be filled in post-hoc
    game_state: dict = field(default_factory=dict)  # optional parsed state


class TrajectoryCollector:
    """Wraps a Playwright Page to record structured trajectories."""

    def __init__(self, page: Page, output_dir: str, episode_id: str,
                 screenshot_width: int = 800, screenshot_height: int = 600):
        self.page = page
        self.episode_id = episode_id
        self.episode_dir = Path(output_dir) / episode_id
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_width = screenshot_width
        self.screenshot_height = screenshot_height

        self.steps: list[Step] = []
        self.step_idx = 0
        self.start_time = time.time()
        self.metadata = {
            "episode_id": episode_id,
            "start_time": self.start_time,
            "agent_type": "unknown",
            "game_url": "",
        }

    def _screenshot_path(self, label: str) -> str:
        return f"step_{self.step_idx:04d}_{label}.png"

    def _take_screenshot(self, label: str) -> str:
        rel_path = self._screenshot_path(label)
        abs_path = str(self.episode_dir / rel_path)
        self.page.screenshot(path=abs_path)
        return rel_path

    def record_action(self, action: Action, game_state: Optional[dict] = None) -> Step:
        """Execute an action and record the full (before, action, after) step."""
        # 1. Before screenshot
        before_path = self._take_screenshot("before")

        # 2. Execute the action
        action.timestamp = time.time() - self.start_time
        self._execute_action(action)

        # Small delay to let game state update after action
        self.page.wait_for_timeout(200)

        # 3. After screenshot
        after_path = self._take_screenshot("after")

        # 4. Record step
        step = Step(
            step_idx=self.step_idx,
            before_screenshot=before_path,
            after_screenshot=after_path,
            action=action,
            game_state=game_state or {},
        )
        self.steps.append(step)

        # 5. Append to JSONL immediately (crash-safe)
        self._append_step(step)

        self.step_idx += 1
        return step

    def _execute_action(self, action: Action):
        """Execute a game action via Playwright."""
        if action.type == "key_hold":
            key = action.params["key"]
            duration_ms = action.params.get("duration_ms", 1000)
            self.page.keyboard.down(key)
            self.page.wait_for_timeout(duration_ms)
            self.page.keyboard.up(key)

        elif action.type == "key_press":
            key = action.params["key"]
            self.page.keyboard.press(key)

        elif action.type == "click":
            x = action.params["x"]
            y = action.params["y"]
            self.page.mouse.click(x, y)

        elif action.type == "type_text":
            text = action.params["text"]
            self.page.keyboard.type(text)

        elif action.type == "screenshot_only":
            pass  # No action, just observe

        elif action.type == "key_combo":
            # e.g. hold w+a simultaneously
            keys = action.params["keys"]
            duration_ms = action.params.get("duration_ms", 1000)
            for k in keys:
                self.page.keyboard.down(k)
            self.page.wait_for_timeout(duration_ms)
            for k in reversed(keys):
                self.page.keyboard.up(k)

        else:
            raise ValueError(f"Unknown action type: {action.type}")

    def _append_step(self, step: Step):
        """Append a step to the JSONL file (one line per step)."""
        jsonl_path = self.episode_dir / "trajectory.jsonl"
        record = {
            "step": step.step_idx,
            "before_screenshot": step.before_screenshot,
            "after_screenshot": step.after_screenshot,
            "action_type": step.action.type,
            "action_params": step.action.params,
            "thought": step.action.thought,
            "timestamp": step.action.timestamp,
            "reward": step.reward,
            "game_state": step.game_state,
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def save_metadata(self, extra: Optional[dict] = None):
        """Save episode-level metadata."""
        self.metadata["end_time"] = time.time()
        self.metadata["duration_s"] = self.metadata["end_time"] - self.start_time
        self.metadata["total_steps"] = len(self.steps)
        if extra:
            self.metadata.update(extra)
        meta_path = self.episode_dir / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(self.metadata, f, indent=2)

    def close(self, extra_metadata: Optional[dict] = None):
        """Finalize the episode."""
        self.save_metadata(extra_metadata)
        return self.episode_dir
