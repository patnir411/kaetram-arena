#!/usr/bin/env python3
"""
convert_to_qwen.py — Transform extracted OODA turns into Qwen3 VL SFT format.

Reads turns.jsonl files produced by extract_turns.py and outputs conversation
records in Qwen3 VL messages format suitable for supervised finetuning.

Usage:
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
"""

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

# Condensed game rules for the system message (~500 tokens)
SYSTEM_PROMPT = """\
You are an AI agent playing Kaetram, a 2D pixel MMORPG. You observe the game via screenshots and structured game state, then decide and execute actions.

## Entity Types
- type 0: other player
- type 1: NPC (quest_npc=true means active quest target, has_achievement=true means achievement available)
- type 3: mob (attackable — has HP)
- type 4: item drop (lootable)

## Actions
- click(x, y): Click canvas at pixel coordinates. Used to attack mobs, walk, interact with NPCs, loot items.
- equip(slot=N): Open inventory and equip item at slot N.
- heal(slot=N): Consume edible item at slot N to restore HP.
- warp(location): Fast travel (Mudwich, Crossroads, Lakesworld).
- quest_accept(): Click the quest button to accept/progress a quest.
- set_style(style): Change attack style (Stab, Hack for Strength XP, Chop).
- wait(Ns): Wait N seconds for combat/regen.

## Priority System
1. HEAL — HP below 50%? Eat food.
2. LOOT — Item drop nearby (type=4)? Click it.
3. EQUIP — Better gear available (equippable=true)? Equip it.
4. QUEST NPC — NPC with quest_npc=true? Walk close, click, accept quest.
5. QUEST — Active quest? Work on objective.
6. GRIND — Kill nearest mob for XP.
7. EXPLORE — Walk in a new direction.

## Combat
Click mob using click_x/click_y. Character auto-walks and auto-attacks. Wait 5-6s per kill.
All clicks use canvas MouseEvent dispatch on #canvas element (9 canvases in DOM — always use getElementById).

## Key Info
- Canvas center ≈ player position. Entities have click_x/click_y when on_screen=true.
- Distance ≤ 3: can click directly. Distance > 3: walk toward them first.
- After death: warp to Mudwich."""


def prune_game_state(state: dict) -> dict:
    """Prune game state to essential fields for SFT training."""
    pruned = {}

    if "player_position" in state:
        pruned["player_position"] = state["player_position"]

    if "player_stats" in state:
        ps = state["player_stats"]
        pruned["player_stats"] = {
            "hp": ps.get("hp", 0),
            "max_hp": ps.get("max_hp", 0),
            "level": ps.get("level", 1),
            "experience": ps.get("experience", 0),
        }

    if "current_target" in state and state["current_target"]:
        ct = state["current_target"]
        pruned["current_target"] = {
            "name": ct.get("name", ""),
            "type": ct.get("type"),
            "hp": ct.get("hp", 0),
            "max_hp": ct.get("max_hp", 0),
            "distance": ct.get("distance"),
            "click_x": ct.get("click_x"),
            "click_y": ct.get("click_y"),
        }

    if "nearest_mob" in state and state["nearest_mob"]:
        nm = state["nearest_mob"]
        pruned["nearest_mob"] = {
            "name": nm.get("name", ""),
            "distance": nm.get("distance"),
            "hp": nm.get("hp", 0),
            "max_hp": nm.get("max_hp", 0),
            "click_x": nm.get("click_x"),
            "click_y": nm.get("click_y"),
            "on_screen": nm.get("on_screen"),
        }

    # Top 10 nearby entities, stripped of noise fields
    entities = state.get("nearby_entities", [])[:10]
    pruned_ents = []
    for e in entities:
        pe = {
            "name": e.get("name", ""),
            "type": e.get("type"),
            "distance": e.get("distance"),
            "hp": e.get("hp", 0),
            "max_hp": e.get("max_hp", 0),
            "on_screen": e.get("on_screen"),
        }
        if e.get("on_screen"):
            pe["click_x"] = e.get("click_x")
            pe["click_y"] = e.get("click_y")
        if e.get("quest_npc"):
            pe["quest_npc"] = True
        if e.get("has_achievement"):
            pe["has_achievement"] = True
        pruned_ents.append(pe)
    pruned["nearby_entities"] = pruned_ents

    # Quests (active only)
    quests = state.get("quests", [])
    if isinstance(quests, list):
        active = []
        for q in quests:
            if isinstance(q, dict) and q.get("started") and not q.get("finished"):
                active.append(q)
        if active:
            pruned["quests"] = [
                {
                    "name": q.get("name", ""),
                    "description": q.get("description", ""),
                    "stage": q.get("stage"),
                    "stageCount": q.get("stageCount"),
                }
                for q in active[:5]
            ]

    # Inventory (non-empty)
    inventory = state.get("inventory", [])
    if inventory:
        pruned_inv = []
        for it in inventory[:15]:
            if not isinstance(it, dict):
                continue
            pruned_inv.append(
                {
                    "slot": it.get("slot"),
                    "name": it.get("name", ""),
                    "count": it.get("count", 1),
                    "edible": it.get("edible", False),
                    "equippable": it.get("equippable", False),
                }
            )
        if pruned_inv:
            pruned["inventory"] = pruned_inv

    return pruned


def format_reasoning(reasoning: str) -> str:
    """Clean up reasoning text for the assistant message."""
    # Remove empty lines and excessive whitespace
    lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
    return " ".join(lines)


def turn_to_conversation(turn: dict, image_dir: Path | None = None) -> dict | None:
    """Convert a single turn into a Qwen3 VL conversation record."""
    game_state = turn.get("game_state")
    if not game_state or not game_state.get("player_position"):
        return None

    reasoning = turn.get("reasoning", "").strip()
    action_structured = turn.get("action_structured", "")
    if not action_structured:
        return None

    # Build pruned state JSON
    pruned = prune_game_state(game_state)
    state_json = json.dumps(pruned, separators=(",", ":"))

    # Determine screenshot path
    screenshot = turn.get("screenshot_path", "")
    if image_dir and screenshot:
        src = Path(screenshot)
        if src.exists():
            dst = image_dir / f"{turn['turn_id']}.png"
            if not dst.exists():
                shutil.copy2(src, dst)
            screenshot = str(dst)

    # User message content
    user_content = []
    if screenshot and Path(screenshot).exists():
        user_content.append({"type": "image", "image": f"file://{screenshot}"})
    user_content.append(
        {
            "type": "text",
            "text": f"<game_state>\n{state_json}\n</game_state>\n\nWhat should you do?",
        }
    )

    # Assistant message: <think>reasoning</think>\n<action>structured_action</action>
    clean_reasoning = format_reasoning(reasoning) if reasoning else "Assessing situation."
    assistant_text = f"<think>\n{clean_reasoning}\n</think>\n<action>\n{action_structured}\n</action>"

    return {
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
        ]
    }


def load_turns(input_dir: Path) -> list[tuple[str, dict]]:
    """Load all turns from extracted dataset directory. Returns (session_name, turn) pairs."""
    all_turns = []
    for jsonl in sorted(input_dir.glob("*/turns.jsonl")):
        session = jsonl.parent.name
        for line in open(jsonl):
            try:
                turn = json.loads(line)
                all_turns.append((session, turn))
            except json.JSONDecodeError:
                continue
    return all_turns


def main():
    parser = argparse.ArgumentParser(description="Convert extracted turns to Qwen3 VL SFT format")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("dataset/extracted"),
        help="Input directory with extracted turns (default: dataset/extracted/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dataset/qwen_sft"),
        help="Output directory (default: dataset/qwen_sft/)",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Validation set ratio (default: 0.1)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for train/val split")
    args = parser.parse_args()

    all_turns = load_turns(args.input)
    if not all_turns:
        print("No turns found in input directory.", file=sys.stderr)
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)
    image_dir = args.output / "images"
    image_dir.mkdir(exist_ok=True)

    # Convert turns to conversations
    conversations = []
    skipped = 0
    for session, turn in all_turns:
        conv = turn_to_conversation(turn, image_dir)
        if conv:
            conv["_session"] = session
            conversations.append(conv)
        else:
            skipped += 1

    if not conversations:
        print("No valid conversations produced.", file=sys.stderr)
        sys.exit(1)

    # Stratified split by session
    sessions = sorted(set(c["_session"] for c in conversations))
    random.seed(args.seed)
    random.shuffle(sessions)
    n_val_sessions = max(1, int(len(sessions) * args.val_ratio))
    val_sessions = set(sessions[:n_val_sessions])

    train = []
    val = []
    for c in conversations:
        session = c.pop("_session")
        if session in val_sessions:
            val.append(c)
        else:
            train.append(c)

    # If val ended up empty, move some from train
    if not val and len(train) > 1:
        random.shuffle(train)
        n_val = max(1, int(len(train) * args.val_ratio))
        val = train[:n_val]
        train = train[n_val:]

    # Write output
    train_path = args.output / "train.json"
    val_path = args.output / "val.json"

    with open(train_path, "w") as f:
        json.dump(train, f, indent=2)
    with open(val_path, "w") as f:
        json.dump(val, f, indent=2)

    print(f"Converted {len(conversations)} turns ({skipped} skipped)")
    print(f"  Train: {len(train)} → {train_path}")
    print(f"  Val:   {len(val)} → {val_path}")
    print(f"  Images: {image_dir}")

    # Print action type distribution
    from collections import Counter

    type_counts = Counter()
    for session, turn in all_turns:
        type_counts[turn.get("action_type", "unknown")] += 1
    print("\nAction distribution:")
    for action, count in type_counts.most_common():
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
