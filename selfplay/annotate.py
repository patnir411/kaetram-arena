"""
Post-hoc annotation — use a vision model to replace synthetic thoughts with real reasoning.

Takes raw trajectories (with template thoughts from the heuristic bot) and re-annotates
each screenshot with high-quality chain-of-thought reasoning from a vision model.

This is MUCH cheaper than running Claude in-the-loop because:
1. No browser automation overhead
2. Batch processing (can use Batch API at 50% cost)
3. Simple image + prompt, no multi-turn conversation
4. Can run offline on already-collected screenshots

Usage:
    python annotate.py --input trajectories/ --output trajectories_annotated/
    python annotate.py --input trajectories/ --provider local --model qwen3-vl-8b
"""

import argparse
import base64
import json
import os
import shutil
from pathlib import Path


def load_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


ANNOTATION_PROMPT = """You are analyzing a screenshot from Kaetram, a 2D pixel MMORPG.

The player just took this screenshot before performing an action.

Action taken: {action_text}

Describe what you see in the screenshot in 1-2 sentences, then explain WHY this action makes sense.
Focus on:
- What entities are visible (monsters, NPCs, items, other players)
- The player's apparent HP/status
- The terrain and surroundings
- Why the chosen action is reasonable given the game state

Keep your response concise (2-4 sentences max). Write as if you are the player thinking out loud.
Example: "I see a level 1 rat near the building to my east. My HP looks full. I should click on it to start combat and farm some experience."
"""


def annotate_with_anthropic(image_path: str, action_text: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Use Anthropic API to generate thought annotation."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("pip install anthropic")

    client = anthropic.Anthropic()
    image_data = load_image_base64(image_path)

    message = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": ANNOTATION_PROMPT.format(action_text=action_text),
                },
            ],
        }],
    )
    return message.content[0].text


def annotate_with_openai(image_path: str, action_text: str, model: str = "gpt-4o-mini") -> str:
    """Use OpenAI API to generate thought annotation."""
    try:
        import openai
    except ImportError:
        raise ImportError("pip install openai")

    client = openai.OpenAI()
    image_data = load_image_base64(image_path)

    response = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_data}"},
                },
                {
                    "type": "text",
                    "text": ANNOTATION_PROMPT.format(action_text=action_text),
                },
            ],
        }],
    )
    return response.choices[0].message.content


def annotate_with_local(image_path: str, action_text: str, model: str = "qwen3-vl-8b",
                        api_base: str = "http://localhost:8000/v1") -> str:
    """Use a local vLLM/Ollama server with OpenAI-compatible API."""
    try:
        import openai
    except ImportError:
        raise ImportError("pip install openai")

    client = openai.OpenAI(base_url=api_base, api_key="dummy")
    image_data = load_image_base64(image_path)

    response = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_data}"},
                },
                {
                    "type": "text",
                    "text": ANNOTATION_PROMPT.format(action_text=action_text),
                },
            ],
        }],
    )
    return response.choices[0].message.content


def annotate_episode(episode_dir: Path, output_dir: Path, provider: str, model: str,
                     api_base: str = "http://localhost:8000/v1") -> int:
    """Re-annotate one episode's trajectory with vision model thoughts."""
    traj_file = episode_dir / "trajectory.jsonl"
    if not traj_file.exists():
        return 0

    # Copy episode directory structure
    out_ep = output_dir / episode_dir.name
    if out_ep.exists():
        shutil.rmtree(out_ep)
    shutil.copytree(episode_dir, out_ep)

    # Re-annotate each step
    steps = []
    with open(traj_file) as f:
        for line in f:
            steps.append(json.loads(line.strip()))

    annotated = 0
    from convert import action_to_text

    for step in steps:
        if step["action_type"] == "screenshot_only":
            continue

        image_path = str(episode_dir / step["before_screenshot"])
        if not os.path.exists(image_path):
            continue

        action_text = action_to_text(step["action_type"], step["action_params"])

        try:
            if provider == "anthropic":
                thought = annotate_with_anthropic(image_path, action_text, model)
            elif provider == "openai":
                thought = annotate_with_openai(image_path, action_text, model)
            elif provider == "local":
                thought = annotate_with_local(image_path, action_text, model, api_base)
            else:
                raise ValueError(f"Unknown provider: {provider}")

            step["thought"] = thought
            step["thought_source"] = f"{provider}/{model}"
            annotated += 1

        except Exception as e:
            print(f"    Annotation failed for step {step['step']}: {e}")

    # Write annotated trajectory
    with open(out_ep / "trajectory.jsonl", "w") as f:
        for step in steps:
            f.write(json.dumps(step) + "\n")

    # Update metadata
    meta_path = out_ep / "metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        meta["annotation_provider"] = provider
        meta["annotation_model"] = model
        meta["annotated_steps"] = annotated
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    return annotated


def main():
    parser = argparse.ArgumentParser(description="Annotate trajectories with vision model thoughts")
    parser.add_argument("--input", type=str, required=True, help="Input trajectories directory")
    parser.add_argument("--output", type=str, required=True, help="Output annotated directory")
    parser.add_argument("--provider", type=str, default="anthropic",
                        choices=["anthropic", "openai", "local"],
                        help="Vision model provider")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name (default: provider-specific)")
    parser.add_argument("--api-base", type=str, default="http://localhost:8000/v1",
                        help="API base URL for local provider")
    parser.add_argument("--max-episodes", type=int, default=None,
                        help="Max episodes to annotate")
    args = parser.parse_args()

    # Default models per provider
    if args.model is None:
        args.model = {
            "anthropic": "claude-haiku-4-5-20251001",
            "openai": "gpt-4o-mini",
            "local": "qwen3-vl-8b",
        }[args.provider]

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    episodes = sorted([d for d in input_dir.iterdir() if d.is_dir()])
    if args.max_episodes:
        episodes = episodes[:args.max_episodes]

    print(f"Annotating {len(episodes)} episodes with {args.provider}/{args.model}")

    total_annotated = 0
    for i, ep_dir in enumerate(episodes):
        print(f"  [{i+1}/{len(episodes)}] {ep_dir.name}...", end=" ")
        n = annotate_episode(ep_dir, output_dir, args.provider, args.model, args.api_base)
        print(f"{n} steps annotated")
        total_annotated += n

    print(f"\nDone! Annotated {total_annotated} steps across {len(episodes)} episodes")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
