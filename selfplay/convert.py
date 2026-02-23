"""
Convert collected trajectories into VLA training format.

Input:  trajectories/{episode_id}/trajectory.jsonl + screenshots
Output: training_data/{split}/samples.jsonl + images/

Training format (Action-of-Thought):
{
    "image": "path/to/screenshot.png",
    "conversations": [
        {"from": "human", "value": "<image>\nYou are playing Kaetram, a 2D MMORPG. What do you see and what should you do next?"},
        {"from": "gpt", "value": "I see a rat at low HP near a building. I should click to attack it and collect the loot.\n<action>click(342, 218)</action>"}
    ]
}

This format is compatible with LLaVA-style VLA fine-tuning.
"""

import argparse
import json
import os
import random
import shutil
from pathlib import Path


# --- Action serialization ---

def action_to_text(action_type: str, params: dict) -> str:
    """Convert an action to a text representation for training."""
    if action_type == "key_hold":
        key = params["key"]
        duration = params.get("duration_ms", 1000)
        direction = {"w": "north", "a": "west", "s": "south", "d": "east"}.get(key, key)
        return f"hold_key({key}, {duration}ms)  # move {direction}"

    elif action_type == "key_press":
        return f"press_key({params['key']})"

    elif action_type == "click":
        return f"click({params['x']}, {params['y']})"

    elif action_type == "type_text":
        return f"type_text(\"{params['text']}\")"

    elif action_type == "key_combo":
        keys = "+".join(params["keys"])
        duration = params.get("duration_ms", 1000)
        return f"hold_keys({keys}, {duration}ms)"

    elif action_type == "screenshot_only":
        return "observe()"

    return f"{action_type}({json.dumps(params)})"


# --- Prompt templates ---

SYSTEM_PROMPTS = [
    "You are playing Kaetram, a 2D pixel MMORPG. Analyze the screenshot and decide your next action.",
    "You are an AI agent playing a 2D MMORPG called Kaetram. Look at the game screen and choose what to do.",
    "Analyze this game screenshot from Kaetram and decide the best next action.",
]

HUMAN_PROMPTS = [
    "<image>\nWhat do you see in the game? What action should you take next?",
    "<image>\nAnalyze the current game state and decide your next move.",
    "<image>\nYou are playing Kaetram. Describe what you observe and choose an action.",
    "<image>\nLook at this game screenshot. What's happening and what should you do?",
]


def convert_step_to_sample(step: dict, image_src: Path, image_dst: Path,
                           use_before: bool = True) -> dict:
    """Convert a single trajectory step to a training sample."""
    # Copy screenshot to output directory
    screenshot_key = "before_screenshot" if use_before else "after_screenshot"
    src_image = image_src / step[screenshot_key]
    if not src_image.exists():
        return None

    shutil.copy2(src_image, image_dst)
    rel_image = str(image_dst.name)

    # Build the response: thought + action
    thought = step.get("thought", "")
    action_text = action_to_text(step["action_type"], step["action_params"])

    if thought:
        response = f"{thought}\n<action>{action_text}</action>"
    else:
        response = f"<action>{action_text}</action>"

    sample = {
        "image": rel_image,
        "conversations": [
            {"from": "human", "value": random.choice(HUMAN_PROMPTS)},
            {"from": "gpt", "value": response},
        ],
    }
    return sample


def convert_step_to_chatml(step: dict, image_src: Path, image_dst: Path) -> dict:
    """Convert step to ChatML format (for Qwen3-VL style training)."""
    src_image = image_src / step["before_screenshot"]
    if not src_image.exists():
        return None

    shutil.copy2(src_image, image_dst)
    rel_image = str(image_dst.name)

    thought = step.get("thought", "")
    action_text = action_to_text(step["action_type"], step["action_params"])

    if thought:
        assistant_msg = f"<think>{thought}</think>\n<action>{action_text}</action>"
    else:
        assistant_msg = f"<action>{action_text}</action>"

    sample = {
        "image": rel_image,
        "messages": [
            {"role": "system", "content": random.choice(SYSTEM_PROMPTS)},
            {"role": "user", "content": [
                {"type": "image", "image": rel_image},
                {"type": "text", "text": "What do you see and what should you do next?"},
            ]},
            {"role": "assistant", "content": assistant_msg},
        ],
    }
    return sample


def convert_episode(episode_dir: Path, output_dir: Path, fmt: str = "llava",
                    skip_observe: bool = True) -> list[dict]:
    """Convert one episode's trajectory into training samples."""
    trajectory_file = episode_dir / "trajectory.jsonl"
    if not trajectory_file.exists():
        return []

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    with open(trajectory_file) as f:
        for line in f:
            step = json.loads(line.strip())

            # Skip observe-only steps (no action to learn)
            if skip_observe and step["action_type"] == "screenshot_only":
                continue

            # Generate unique image filename
            ep_id = episode_dir.name
            step_idx = step["step"]
            image_name = f"{ep_id}_step{step_idx:04d}.png"
            image_dst = images_dir / image_name

            if fmt == "llava":
                sample = convert_step_to_sample(step, episode_dir, image_dst)
            elif fmt == "chatml":
                sample = convert_step_to_chatml(step, episode_dir, image_dst)
            else:
                raise ValueError(f"Unknown format: {fmt}")

            if sample:
                samples.append(sample)

    return samples


def convert_all(trajectories_dir: str, output_dir: str, fmt: str = "llava",
                train_split: float = 0.9, skip_observe: bool = True):
    """Convert all episodes to training data with train/val split."""
    traj_path = Path(trajectories_dir)
    out_path = Path(output_dir)

    # Find all episode directories
    episodes = sorted([d for d in traj_path.iterdir() if d.is_dir()])
    if not episodes:
        print(f"No episodes found in {trajectories_dir}")
        return

    print(f"Found {len(episodes)} episodes")

    # Shuffle for random split
    random.shuffle(episodes)
    split_idx = int(len(episodes) * train_split)
    train_episodes = episodes[:split_idx]
    val_episodes = episodes[split_idx:]

    for split_name, split_episodes in [("train", train_episodes), ("val", val_episodes)]:
        split_dir = out_path / split_name
        split_dir.mkdir(parents=True, exist_ok=True)

        all_samples = []
        for ep_dir in split_episodes:
            samples = convert_episode(ep_dir, split_dir, fmt=fmt, skip_observe=skip_observe)
            all_samples.extend(samples)
            print(f"  {ep_dir.name}: {len(samples)} samples")

        # Write samples JSONL
        samples_file = split_dir / "samples.jsonl"
        with open(samples_file, "w") as f:
            for sample in all_samples:
                f.write(json.dumps(sample) + "\n")

        print(f"{split_name}: {len(all_samples)} samples from {len(split_episodes)} episodes")

    # Write dataset info
    info = {
        "total_episodes": len(episodes),
        "train_episodes": len(train_episodes),
        "val_episodes": len(val_episodes),
        "format": fmt,
        "skip_observe": skip_observe,
    }
    with open(out_path / "dataset_info.json", "w") as f:
        json.dump(info, f, indent=2)

    print(f"\nDataset saved to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Convert trajectories to VLA training format")
    parser.add_argument("--input", type=str, default="trajectories",
                        help="Input trajectories directory")
    parser.add_argument("--output", type=str, default="training_data",
                        help="Output training data directory")
    parser.add_argument("--format", type=str, default="chatml",
                        choices=["llava", "chatml"],
                        help="Output format (llava for LLaVA-style, chatml for Qwen3-VL)")
    parser.add_argument("--train-split", type=float, default=0.9,
                        help="Train/val split ratio")
    parser.add_argument("--include-observe", action="store_true",
                        help="Include observe-only steps")
    args = parser.parse_args()

    base = os.path.dirname(os.path.dirname(__file__))
    input_dir = os.path.join(base, args.input)
    output_dir = os.path.join(base, args.output)

    convert_all(
        trajectories_dir=input_dir,
        output_dir=output_dir,
        fmt=args.format,
        train_split=args.train_split,
        skip_observe=not args.include_observe,
    )


if __name__ == "__main__":
    main()
