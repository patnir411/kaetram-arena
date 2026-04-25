#!/usr/bin/env python3
"""
convert_to_qwen.py — Transform extracted OODA turns into Qwen3.5 9B SFT format.

Reads turns.jsonl files produced by extract_turns.py and outputs conversation
records in Qwen3.5 9B messages format suitable for supervised finetuning.

Supports three modes:
  --mode single  : Original single-turn (state→action) records
  --mode multi   : Windowed multi-turn records with state deltas and memory
  --mode mixed   : 70% multi-turn + 30% single-turn (default)

And two output formats:
  --format sft   : Full conversation records for SFT training (default)
  --format grpo  : Prompt-only records with reward context for GRPO training

Usage:
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode mixed --window-size 3
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_grpo/ --format grpo
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS as TOOL_DEFINITIONS

# Warp location IDs and attack style IDs
WARP_IDS = {"Mudwich": 0, "Aynor": 1, "Lakesworld": 2, "Crullfield": 3, "Patsow": 4, "Undersea": 5}
STYLE_IDS = {"Hack": 6, "Chop": 7, "Defensive": 3, "Stab": 1, "Slash": 2}

# System prompt: load the ACTUAL inference prompt (prompts/system.md + game_knowledge.md)
# so that training and inference see the same instructions.
# r8 and earlier used a condensed, divergent prompt here — that mismatch was the
# primary cause of r8-SFT underperforming the base model.
#
# r10: __PERSONALITY_BLOCK__ placeholder is LEFT INTACT so the per-record personality
# block (loaded from prompts/personalities/<name>.md) can be substituted at training
# time at the same textual location eval_harness.resolve_system_prompt puts it.
# This gives byte-exact parity train↔eval, closing the r9 gap where training got a
# 2-sentence paraphrase appended at the end while eval injected the full ~1.5KB file.
def _load_system_prompt() -> str:
    """Load the inference system prompt with game knowledge inlined.

    Leaves __PERSONALITY_BLOCK__ intact — substituted per-record at training time to
    match eval_harness.resolve_system_prompt byte-for-byte.
    """
    script_dir = Path(__file__).resolve().parent
    system_md = script_dir / "prompts" / "system.md"
    game_knowledge_md = script_dir / "prompts" / "game_knowledge.md"

    if not system_md.exists():
        raise FileNotFoundError(f"prompts/system.md not found at {system_md}")

    prompt = system_md.read_text()

    # Inline game knowledge (same substitution as play_qwen.sh)
    if game_knowledge_md.exists():
        gk = game_knowledge_md.read_text()
        prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", gk)
    else:
        prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", "")

    # Fixed substitutions — match eval_harness defaults:
    # eval_harness does .replace("__SERVER_PORT__", "") and .replace("__PROJECT_DIR__", project_dir)
    # Neither placeholder appears in system.md today; these replaces are no-ops that we keep
    # anyway for defense-in-depth.
    prompt = prompt.replace("__USERNAME__", "KaetramAgent")
    prompt = prompt.replace("__SERVER_PORT__", "")

    return prompt


SYSTEM_PROMPT = _load_system_prompt()


def _load_personality_block(name: str) -> str:
    """Load the full personality .md file content as used at eval/data-collection time.

    eval_harness.resolve_system_prompt reads these files verbatim and substitutes the
    full contents into __PERSONALITY_BLOCK__. For byte parity, training must use the
    same source. This replaces the pre-r10 2-sentence hand-paraphrase.
    """
    script_dir = Path(__file__).resolve().parent
    path = script_dir / "prompts" / "personalities" / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text()


# Full personality .md contents, keyed by name. Substituted into __PERSONALITY_BLOCK__
# at training time. Byte-identical to what eval_harness loads.
PERSONALITY_SUFFIXES = {
    "aggressive": _load_personality_block("aggressive"),
    "methodical": _load_personality_block("methodical"),
    "curious":    _load_personality_block("curious"),
}

LOGIN_LOOP_RESULT_MARKERS = (
    "game did not load",
    "browser/context closed",
    "connection closed",
    "login failed",
)

LOGIN_LOOP_REASONING_MARKERS = (
    "login",
    "mcp server",
    "playwright",
    "websocket",
    "register",
    "browser",
    "game did not load",
)


def _ensure_dict(val):
    """Ensure a value is a dict — parse JSON strings, skip non-dicts."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _safe_int(val, default=0):
    """Safely extract an integer from a value that might be a dict, str, or None."""
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, dict):
        return int(val.get("hp", val.get("level", default)))
    return default


def prune_game_state(state: dict) -> dict:
    """Prune game state to essential fields for SFT training."""
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except (json.JSONDecodeError, ValueError):
            return {}
    if not isinstance(state, dict):
        return {}
    pruned = {}

    if "player_position" in state:
        pp = state["player_position"]
        if isinstance(pp, dict):
            pruned["player_position"] = pp

    # Player stats — with fallback to top-level fields
    ps = _ensure_dict(state.get("player_stats"))
    if not ps:
        ps = {}
    hp = _safe_int(ps.get("hp")) or _safe_int(state.get("hp")) or _safe_int(state.get("stats", {}).get("hp") if isinstance(state.get("stats"), dict) else None)
    max_hp = _safe_int(ps.get("max_hp")) or _safe_int(state.get("max_hp")) or _safe_int(state.get("stats", {}).get("max_hp") if isinstance(state.get("stats"), dict) else None)
    level = _safe_int(ps.get("level")) or _safe_int(state.get("level")) or _safe_int(state.get("stats", {}).get("level") if isinstance(state.get("stats"), dict) else None) or 1
    pruned["player_stats"] = {
        "hp": hp,
        "max_hp": max_hp,
        "level": level,
        "experience": _safe_int(ps.get("experience")) or _safe_int(state.get("experience")),
    }

    if "current_target" in state and state["current_target"]:
        ct = _ensure_dict(state["current_target"])
        if ct:
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
        nm = _ensure_dict(state["nearest_mob"])
        if nm:
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
    entities = state.get("nearby_entities", [])
    if not isinstance(entities, list):
        entities = []
    entities = entities[:10]
    pruned_ents = []
    for e in entities:
        if not isinstance(e, dict):
            continue
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
        if "reachable" in e:
            pe["reachable"] = e["reachable"]
        pruned_ents.append(pe)
    if pruned_ents:
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
    if inventory and isinstance(inventory, list):
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

    # UI state (death, indoor, dialogue)
    ui = state.get("ui_state")
    if isinstance(ui, dict):
        ui_pruned = {}
        if ui.get("is_dead"):
            ui_pruned["is_dead"] = True
        if ui.get("is_indoors"):
            ui_pruned["is_indoors"] = True
        if ui.get("quest_panel_visible"):
            ui_pruned["quest_panel_visible"] = True
        if ui.get("npc_dialogue"):
            ui_pruned["npc_dialogue"] = ui["npc_dialogue"][:200]
        if ui_pruned:
            pruned["ui_state"] = ui_pruned

    # Equipment (weapon, armor — key + name only)
    equipment = state.get("equipment")
    if isinstance(equipment, dict):
        eq_pruned = {}
        for slot, item in equipment.items():
            if isinstance(item, dict) and item.get("name"):
                eq_pruned[slot] = {"key": item.get("key", ""), "name": item["name"]}
            elif isinstance(item, str) and item:
                eq_pruned[slot] = {"name": item}
        if eq_pruned:
            pruned["equipment"] = eq_pruned

    # Skills (non-trivial only — level > 1 or xp > 0)
    skills = state.get("skills")
    if isinstance(skills, dict):
        sk_pruned = {}
        for name, data in skills.items():
            if isinstance(data, dict):
                lvl = _safe_int(data.get("level"))
                xp = _safe_int(data.get("experience", data.get("xp")))
                if lvl > 1 or xp > 0:
                    sk_pruned[name] = {"level": lvl}
            elif isinstance(data, (int, float)) and int(data) > 0:
                sk_pruned[name] = {"level": int(data)}
        if sk_pruned:
            pruned["skills"] = sk_pruned

    # Navigation status (include stuck_reason and pathfinding_method for training)
    nav = state.get("navigation")
    if isinstance(nav, dict) and (nav.get("active") or nav.get("status") == "stuck"):
        nav_pruned = {
            "status": nav.get("status"),
            "current_wp": nav.get("current_wp"),
            "total_wps": nav.get("total_wps"),
        }
        if nav.get("stuck_reason"):
            nav_pruned["stuck_reason"] = nav["stuck_reason"]
        if nav.get("pathfinding_method"):
            nav_pruned["pathfinding_method"] = nav["pathfinding_method"]
        pruned["navigation"] = nav_pruned

    return pruned


def format_reasoning(reasoning: str, max_chars: int = 500) -> str:
    """Clean up and trim reasoning text for the assistant message.

    Keeps reasoning concise to prevent the model from learning to ramble.
    Prioritizes the last few sentences (the decision) over early context.
    """
    # Remove empty lines and excessive whitespace
    lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
    text = " ".join(lines)

    if len(text) <= max_chars:
        return text

    # Split into sentences and keep from the end (decision is usually last)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    # Always keep the last 2-3 sentences (the actual decision)
    kept = []
    char_count = 0
    for s in reversed(sentences):
        if char_count + len(s) > max_chars and kept:
            break
        kept.insert(0, s)
        char_count += len(s) + 1

    return " ".join(kept)


def score_turn(turn: dict) -> float:
    """Score a turn from 0.0-1.0 for training data quality.

    Checks state completeness, action quality, reasoning quality,
    and penalizes known-bad patterns (stuck loops, hallucinations,
    reasoning-action misalignment).
    """
    score = 0.0
    gs = turn.get("game_state", {})
    ps = gs.get("player_stats", {})
    if isinstance(ps, str):
        try:
            ps = json.loads(ps)
        except (json.JSONDecodeError, ValueError):
            ps = {}
    if not isinstance(ps, dict):
        ps = {}

    # State completeness (0.0 - 0.4)
    if _safe_int(ps.get("hp")) > 0:
        score += 0.1
    if _safe_int(ps.get("max_hp")) > 0:
        score += 0.05
    if gs.get("nearby_entities"):
        score += 0.1
    if gs.get("inventory"):
        score += 0.05
    if gs.get("quests"):
        score += 0.05
    if gs.get("equipment"):
        score += 0.05

    # Action quality (0.0 - 0.3)
    action_type = turn.get("action_type", "")
    # r10: observe is high-value — it's the tool the model had 0 training on in r9
    # and now the primary thing we need to teach.
    high_value = ("observe", "attack", "interact_npc", "navigate", "quest_accept",
                  "talk_npc", "gather", "loot", "buy_item", "query_quest")
    medium_value = ("heal", "equip", "warp", "move", "click_entity", "set_style",
                    "wait", "stuck_reset", "nav_cancel", "update_memory",
                    "clear_combat", "drop_item")
    low_value = ("click_tile", "click")
    if action_type in high_value:
        score += 0.2
    elif action_type in medium_value:
        score += 0.15
    elif action_type in low_value:
        score += 0.05  # fallback actions are weak training signal
    elif action_type in ("respawn",):
        score += 0.1  # Recovery is useful training data

    # Reasoning quality (0.0 - 0.3)
    reasoning = turn.get("reasoning", "")
    reasoning_lower = reasoning.lower()
    if 30 < len(reasoning) < 1500:
        score += 0.1  # Good length — not empty, not rambling
    if len(reasoning) > 80:
        score += 0.05
    game_keywords = ["quest", "kill", "heal", "navigate", "explore", "attack",
                     "npc", "equip", "hp", "level", "mob", "warp", "food", "inventory"]
    keyword_hits = sum(1 for kw in game_keywords if kw in reasoning_lower)
    if keyword_hits >= 2:
        score += 0.1  # reasoning references game concepts
    elif keyword_hits >= 1:
        score += 0.05

    # Reasoning-action alignment bonus (0.0 - 0.05)
    action_str = turn.get("action_structured", "").lower()
    alignment_map = {
        "attack": ["attack", "kill", "fight", "mob", "combat", "damage"],
        "heal": ["heal", "food", "hp", "health", "eat", "low hp"],
        "navigate": ["navigate", "walk", "go to", "head to", "move to"],
        "warp": ["warp", "teleport", "fast travel", "mudwich", "aynor", "lakesworld", "crullfield", "patsow", "undersea"],
        "interact_npc": ["npc", "talk", "quest", "interact", "dialogue"],
        "equip": ["equip", "weapon", "armor", "gear", "sword", "axe"],
        "respawn": ["dead", "died", "respawn", "death"],
        "gather": ["gather", "chop", "mine", "forage", "tree", "rock", "resource", "log"],
        "loot": ["loot", "pick up", "drop", "lootbag", "item"],
        "buy_item": ["buy", "purchase", "shop", "store", "gold"],
        "drop_item": ["drop", "discard", "inventory full", "free space"],
        "clear_combat": ["combat", "warp", "cooldown", "stuck in combat"],
        "query_quest": ["quest", "walkthrough", "steps", "objective"],
    }
    if action_type in alignment_map:
        if any(kw in reasoning_lower for kw in alignment_map[action_type]):
            score += 0.05

    # === Penalties ===

    # Login screen (position 0,0)
    pp = turn.get("player_position", {})
    if pp.get("x", 0) == 0 and pp.get("y", 0) == 0:
        score -= 0.5

    # Empty or near-empty reasoning
    if len(reasoning.strip()) < 10:
        score -= 0.15

    # Reasoning-action MISMATCH penalty
    # e.g., reasoning says "heal" but action is "attack"
    mismatch_pairs = [
        (["heal", "eat food", "low hp", "need to heal"], "attack"),
        (["attack", "kill", "fight"], "warp"),
    ]
    for keywords, bad_action in mismatch_pairs:
        if action_type == bad_action and any(kw in reasoning_lower for kw in keywords):
            # Only penalize if the keyword is a STRONG signal (appears multiple times
            # or is the primary intent), not just mentioned in passing
            strong_hits = sum(1 for kw in keywords if kw in reasoning_lower)
            if strong_hits >= 2:
                score -= 0.1

    return max(0.0, min(1.0, score))


def detect_personality(session_name: str, input_dir: Path) -> str | None:
    """Try to detect personality from the agent's metadata.json."""
    # Session might be under agent_N/ directory
    session_path = None
    for p in input_dir.rglob(session_name):
        session_path = p
        break
    if not session_path:
        return None

    # Walk up to find metadata.json
    for parent in [session_path.parent, session_path.parent.parent]:
        meta_path = parent / "state" / "metadata.json"
        if not meta_path.exists():
            # Check raw data dir equivalent
            raw_equiv = str(parent).replace("/extracted/", "/raw/")
            meta_path = Path(raw_equiv) / "state" / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                return meta.get("personality")
            except (json.JSONDecodeError, ValueError):
                pass

    # Fallback: infer from agent_N directory name
    AGENT_PERSONALITY_MAP = {
        "agent_0": "aggressive",
        "agent_1": "methodical",
        "agent_2": "curious",
    }
    for part in session_path.parts:
        if part in AGENT_PERSONALITY_MAP:
            return AGENT_PERSONALITY_MAP[part]

    return None


def is_desert_quest_waste(turn: dict) -> bool:
    """Detect turns where the agent is stuck trying to reach the Wife NPC.

    These turns teach the model to bang into walls and should be excluded.
    """
    reasoning = turn.get("reasoning", "").lower()
    gs = turn.get("game_state", {})
    pp = gs.get("player_position", {})
    x = pp.get("x", 0)

    # Agent stuck in Wife room area (x=770-781, interior zone)
    if 770 <= x <= 790 and any(k in reasoning for k in ["wife", "735", "desert quest", "old lady"]):
        return True

    # Agent navigating to Wife and getting stuck
    if "wife" in reasoning and "stuck" in reasoning:
        return True
    if "735, 101" in reasoning or "735,101" in reasoning:
        if "unreachable" not in reasoning and "skip" not in reasoning:
            return True

    return False


def compute_state_delta(prev_state: dict, curr_state: dict) -> dict:
    """Compute observable changes between consecutive game states."""
    delta = {}
    pp = prev_state.get("player_stats", {})
    cp = curr_state.get("player_stats", {})
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except: pp = {}
    if isinstance(cp, str):
        try: cp = json.loads(cp)
        except: cp = {}

    hp_delta = _safe_int(cp.get("hp")) - _safe_int(pp.get("hp"))
    xp_delta = _safe_int(cp.get("experience")) - _safe_int(pp.get("experience"))
    level_delta = _safe_int(cp.get("level")) - _safe_int(pp.get("level"))

    if hp_delta != 0:
        delta["hp_delta"] = hp_delta
    if xp_delta != 0:
        delta["xp_delta"] = xp_delta
    if level_delta != 0:
        delta["level_delta"] = level_delta

    prev_pos = prev_state.get("player_position", {})
    curr_pos = curr_state.get("player_position", {})
    if isinstance(prev_pos, dict) and isinstance(curr_pos, dict):
        if prev_pos.get("x") != curr_pos.get("x") or prev_pos.get("y") != curr_pos.get("y"):
            delta["moved_from"] = prev_pos

    # Quest progression
    prev_quests = {q["key"]: q for q in (prev_state.get("quests") or []) if isinstance(q, dict) and "key" in q}
    curr_quests = {q["key"]: q for q in (curr_state.get("quests") or []) if isinstance(q, dict) and "key" in q}
    new_quests = [k for k in curr_quests if k not in prev_quests]
    stage_advances = 0
    quest_completions = 0
    for key, cq in curr_quests.items():
        pq = prev_quests.get(key)
        if pq:
            if cq.get("stage", 0) > pq.get("stage", 0):
                stage_advances += 1
            if cq.get("finished") and not pq.get("finished"):
                quest_completions += 1
    if new_quests:
        delta["new_quests"] = new_quests
    if stage_advances:
        delta["quest_stage_advances"] = stage_advances
    if quest_completions:
        delta["quest_completions"] = quest_completions

    ui = curr_state.get("ui_state", {})
    if isinstance(ui, dict) and ui.get("is_dead"):
        delta["died"] = True

    return delta


def find_latest_memory(session_turns: list[dict], before_index: int) -> dict | None:
    """Find the most recent update_memory turn before the given index."""
    for i in range(before_index - 1, -1, -1):
        if session_turns[i].get("action_type") == "update_memory":
            return session_turns[i].get("memory_content")
    return None


DEFAULT_MEMORY = {
    "sessions": 0,
    "level": 1,
    "active_quests": [],
    "completed_quests": [],
    "inventory_summary": [],
    "kills_this_session": 0,
    "next_objective": "accept quests from NPCs",
    "notes": "fresh start",
}


def structured_action_to_js(action: str) -> str:
    """Convert structured action string to JavaScript code for browser_run_code tool call."""
    m = re.match(r"(\w+)\((.*)\)", action, re.DOTALL)
    if not m:
        return f"return '{action}: unknown action'"
    name = m.group(1)
    args_str = m.group(2).strip()
    args = [a.strip().strip("'\"") for a in re.split(r",\s*", args_str)] if args_str else []

    if name == "attack" and args:
        return f"return window.__attackMob('{args[0]}')"
    if name == "interact_npc" and args:
        return f"return window.__interactNPC('{args[0]}')"
    if name == "talk_npc" and args:
        return f"return window.__talkToNPC('{args[0]}')"
    if name == "navigate" and len(args) >= 2:
        return f"return window.__navigateTo({args[0]}, {args[1]})"
    if name == "move" and len(args) >= 2:
        return f"return window.__moveTo({args[0]}, {args[1]})"
    if name == "click_entity" and args:
        return f"return window.__clickEntity('{args[0]}')"
    if name == "click_tile" and len(args) >= 2:
        return f"return window.__clickTile({args[0]}, {args[1]})"
    if name == "click" and len(args) >= 2:
        return f"const c=document.getElementById('canvas');['mousedown','mouseup','click'].forEach(t=>c.dispatchEvent(new MouseEvent(t,{{clientX:{args[0]},clientY:{args[1]},bubbles:true}})));return 'clicked({args[0]},{args[1]})'"
    if name == "warp" and args:
        wid = WARP_IDS.get(args[0], 0)
        return f"return window.__safeWarp({wid})"
    if name == "heal" and args:
        slot = re.search(r"(\d+)", args[0])
        s = slot.group(1) if slot else "0"
        return f"return window.__eatFood({s})"
    if name == "equip" and args:
        slot = re.search(r"(\d+)", args[0])
        s = slot.group(1) if slot else "0"
        return f"const sl=document.querySelectorAll('#inventory-container .slot');if(sl[{s}]){{sl[{s}].click();const e=document.querySelector('[data-action=\"action-equip\"]');if(e)e.click()}};return 'equipped({s})'"
    if name == "quest_accept":
        return "const b=document.querySelector('#quest-button');if(b)b.click();return 'quest_accepted'"
    if name == "set_style" and args:
        sid = STYLE_IDS.get(args[0], 6)
        return f"if(window.game&&window.game.player)window.game.player.setAttackStyle({sid});return 'style_set({sid})'"
    if name == "wait" and args:
        ms_m = re.search(r"([\d.]+)", args[0])
        ms = min(int(float(ms_m.group(1)) * 1000), 8000) if ms_m else 5000
        return f"await new Promise(r=>setTimeout(r,{ms}));return 'waited({ms}ms)'"
    if name == "respawn":
        return "const b=document.querySelector('#respawn');if(b)b.click();return 'respawned'"
    if name == "stuck_reset":
        return "return window.__stuckReset()"
    if name == "nav_cancel":
        return "return window.__navCancel()"

    return f"return '{name}: unknown'"


def synthesize_tool_result(action: str, turn: dict | None = None) -> str:
    """Generate a JSON tool result matching the real MCP server output format.

    Uses actual game state from the turn when available to produce realistic
    results, reducing train/inference mismatch.

    NOTE: This is a FALLBACK path. Preferred behavior is to surface the raw
    tool result from the log via `turn["action_result_raw"]` — see
    build_tool_result_message(). This synthesizer only fires when the raw
    result is missing (e.g. legacy turns.jsonl files from before
    extract_turns.py started emitting action_result_raw).

    Critical: the interact_npc branch intentionally omits quest_opened.
    Previously it hardcoded `quest_opened: false`, which erased every real
    quest acceptance (574/2,082 interact_npc results had quest_opened=true)
    and trained the model on a world where quests never actually open.
    """
    m = re.match(r"(\w+)\((.*)\)", action, re.DOTALL)
    name = m.group(1) if m else "unknown"
    args_str = m.group(2).strip() if m else ""
    args = [a.strip().strip("'\"") for a in re.split(r",\s*", args_str)] if args_str else []

    gs = (turn or {}).get("game_state", {})
    pp = gs.get("player_position", {})
    ps = gs.get("player_stats", {})

    if name == "attack" and args:
        mob_name = args[0]
        return json.dumps({"attacking": mob_name, "distance": 2,
                          "player_pos": pp, "status": "engaging"})
    if name == "interact_npc" and args:
        # Do NOT hardcode quest_opened — the real value depends on whether the
        # NPC actually had a quest to offer and was accepted. Omitting it is
        # safer than lying to the model. Prefer action_result_raw upstream.
        return json.dumps({"arrived": True, "npc": args[0],
                          "dialogue_lines": 1})
    if name == "talk_npc":
        return json.dumps({"dialogue": ["..."], "has_more": False})
    if name == "navigate" and len(args) >= 2:
        return json.dumps({"status": "walking", "target": {"x": int(args[0]), "y": int(args[1])},
                          "from": pp})
    if name == "move" and len(args) >= 2:
        return json.dumps({"status": "arrived", "position": {"x": int(args[0]), "y": int(args[1])}})
    if name == "warp" and args:
        return json.dumps({"warped_to": args[0], "status": "arrived"})
    if name == "heal" and args:
        hp = _safe_int(ps.get("hp"))
        return json.dumps({"healed": True, "hp_before": hp, "hp_after": min(hp + 20, _safe_int(ps.get("max_hp", 100)))})
    if name == "equip":
        return json.dumps({"equipped": True})
    if name == "click_tile" and len(args) >= 2:
        return json.dumps({"clicked": {"x": int(args[0]), "y": int(args[1])}})
    if name == "quest_accept":
        return json.dumps({"result": "Quest accept clicked"})
    if name == "set_style" and args:
        return json.dumps({"style": args[0], "applied": True})
    if name == "respawn":
        return json.dumps({"respawned": True, "position": {"x": 188, "y": 157}})
    if name == "stuck_reset":
        return json.dumps({"reset": True, "warping": True})
    if name == "nav_cancel":
        return json.dumps({"cancelled": True})
    if name == "wait":
        return json.dumps({"waited": True})
    # New tools (fallback shapes — prefer action_result_raw when available)
    if name == "gather":
        resource = args[0] if args else ""
        return json.dumps({"resource": resource, "items_gained": "unknown"})
    if name == "loot":
        return json.dumps({"looted": True, "items_gained": "unknown"})
    if name == "buy_item" and args:
        return json.dumps({"purchased": True, "npc": args[0]})
    if name == "drop_item" and args:
        slot = re.search(r"(\d+)", args[0])
        return json.dumps({"dropped": True, "slot": int(slot.group(1)) if slot else 0})
    if name == "clear_combat":
        return json.dumps({"cleared": True})
    if name == "query_quest" and args:
        return json.dumps({"quest": args[0], "info": "walkthrough"})

    return json.dumps({"result": "ok"})


def build_user_message(turn: dict, prev_turn: dict | None = None, memory: dict | None = None) -> str:
    """Build the user message posed to the model between assistant turns.

    r10: does NOT inject <game_state> anymore. State is delivered to the model via
    the tool_result of a preceding observe tool_call (see build_tool_result_message
    for observe turns). Pre-r10 this function handed game_state to the model for
    free in every user message — the model never learned to call observe, and at
    inference (where state is NOT injected) it was undertrained on observe.
    Verified: r9 train.json had 0 observe tool_calls in 21,976 assistant turns.

    A <state_delta> momentum signal is still included when a prior turn exists —
    that's a cheap aggregate of "what changed since last turn" and does not
    substitute for current state, which only observe provides.

    r9 fix retained: no <memory> block (play_qwen.py never injects one at inference).
    """
    parts = []

    # State delta (momentum signal — keeps the window compact).
    if prev_turn is not None:
        delta = compute_state_delta(
            prev_turn.get("game_state", {}),
            turn.get("game_state", {}),
        )
        if delta:
            delta_json = json.dumps(delta, separators=(",", ":"))
            parts.append(f"<state_delta>\n{delta_json}\n</state_delta>")

    parts.append("What should you do?")
    return "\n\n".join(parts)


def _safe_int(s, default=0):
    """Parse int from string, return default on failure."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def _structured_action_to_tool_call(action: str, action_type: str) -> tuple[str, dict] | None:
    """Convert structured action string to (tool_name, arguments) for native MCP tools.

    Returns None for actions that can't be mapped (skip these turns).
    """
    m = re.match(r"(\w+)\((.*)\)", action, re.DOTALL)
    if not m:
        return None
    name = m.group(1)
    args_str = m.group(2).strip()
    args = [a.strip().strip("'\"") for a in re.split(r",\s*", args_str)] if args_str else []

    # r10: observe is a first-class tool call. Emitted by extract_turns for every
    # observe the Sonnet teacher made (~28% of tool calls). Teaches the model to
    # request state before acting — pre-r10 training had 0 observe calls because
    # extraction silently consumed them to populate game_state.
    if name == "observe":
        return "observe", {}
    if name == "attack" and args and args[0] != "?":
        return "attack", {"mob_name": args[0]}
    if name == "interact_npc" and args and args[0] != "?":
        return "interact_npc", {"npc_name": args[0]}
    if name == "talk_npc" and args and args[0] != "?":
        return "talk_npc", {"instance_id": args[0]}
    if name == "navigate" and len(args) >= 2 and "?" not in args[:2]:
        return "navigate", {"x": _safe_int(args[0]), "y": _safe_int(args[1])}
    if name == "move" and len(args) >= 2 and "?" not in args[:2]:
        return "move", {"x": _safe_int(args[0]), "y": _safe_int(args[1])}
    if name == "click_tile" and len(args) >= 2 and "?" not in args[:2]:
        return "click_tile", {"x": _safe_int(args[0]), "y": _safe_int(args[1])}
    if name == "click" and len(args) >= 2 and "?" not in args[:2]:
        return "click_tile", {"x": _safe_int(args[0]), "y": _safe_int(args[1])}
    if name == "warp" and args:
        return "warp", {"location": args[0].lower()}
    if name == "heal" and args:
        slot = re.search(r"(\d+)", args[0])
        return "eat_food", {"slot": int(slot.group(1)) if slot else 0}
    if name == "equip" and args:
        slot = re.search(r"(\d+)", args[0])
        return "equip_item", {"slot": int(slot.group(1)) if slot else 0}
    if name in ("quest_accept", "accept_quest"):
        return "accept_quest", {}
    if name == "set_style" and args:
        return "set_attack_style", {"style": args[0].lower()}
    if name == "respawn":
        return "respawn", {}
    if name == "stuck_reset":
        return "stuck_reset", {}
    if name == "nav_cancel":
        return "cancel_nav", {}
    if name == "gather":
        return ("gather", {"resource_name": args[0] if args else ""})
    if name == "loot":
        return ("loot", {})
    if name == "buy_item":
        # Expected forms: buy_item(<npc>, <item_index>, [count])
        if len(args) >= 2:
            call_args = {"npc_name": args[0], "item_index": _safe_int(args[1])}
            if len(args) >= 3:
                # args[2] is the raw "count=N" string (extract_turns.structured_action
                # emits this form). Strip the key= prefix before parsing.
                count_str = args[2].split("=", 1)[1] if "=" in args[2] else args[2]
                call_args["count"] = _safe_int(count_str, 1)
            return ("buy_item", call_args)
        return None
    if name == "drop_item" and args:
        slot = re.search(r"(\d+)", args[0])
        return ("drop_item", {"slot": int(slot.group(1)) if slot else 0})
    if name == "clear_combat":
        return ("clear_combat", {})
    if name == "query_quest":
        return ("query_quest", {"quest_name": args[0] if args else ""})
    # Skip unmappable actions (wait, click_entity with ?, other)
    return None


def build_assistant_message(turn: dict, include_thinking: bool = True) -> dict:
    """Build assistant message dict with native MCP tool_calls.

    Returns a full message dict with typed tool calls matching MCP server tools.

    r10 note: reasoning stays inline in `content` as `<think>...</think>`. The
    runtime chat-template patch in `finetune/train_modal.py:_patch_qwen_chat_template`
    extracts it from content into `reasoning_content` and emits `<think>` on
    every assistant turn (bypassing the stock Qwen3 `last_query_index` gate
    that would otherwise strip intermediate turns — QwenLM/Qwen3 #1831).
    `tests/test_think_roundtrip.py` applies the same patch and asserts the
    end-to-end round-trip works.
    """
    reasoning = turn.get("reasoning", "").strip()
    action = turn.get("action_structured", "")
    action_type = turn.get("action_type", "")
    turn_id = turn.get("turn_id", "t000")

    # Build thinking text
    if include_thinking:
        clean = format_reasoning(reasoning) if reasoning else "Assessing situation."
        content = f"<think>\n{clean}\n</think>"
    else:
        content = ""

    call_id = f"call_{turn_id[-3:]}"

    # Skip update_memory turns — no Bash tool in native MCP format
    if action_type == "update_memory":
        return None  # Signal to caller to skip this turn

    # Convert structured action to native tool call
    result = _structured_action_to_tool_call(action, action_type)
    if result is None:
        return None  # Skip unmappable actions
    tool_name, tool_args = result
    tool_calls = [{
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": tool_args,
        },
    }]

    msg = {"role": "assistant", "tool_calls": tool_calls}
    if content:
        msg["content"] = content
    return msg


def _prefer_real_tool_result(raw: str | None) -> str | None:
    """Return the raw tool result, unwrapping the `{"result": "..."}` envelope
    if present, so the training data exposes the same JSON shape that the
    MCP server actually returns at inference time.

    Returns None if raw is None/empty/whitespace so the caller can fall back
    to the synthesizer.

    Unwrap policy: if the outer value is exactly `{"result": <str>}`, return
    the inner string verbatim. Otherwise return `raw` unchanged.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return None
    try:
        outer = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — return the raw text as-is (still real signal).
        return raw
    if isinstance(outer, dict) and set(outer.keys()) == {"result"} and isinstance(outer["result"], str):
        return outer["result"]
    return raw


def _cap_observe_entities(result: str) -> str:
    """Cap nearby_entities noise in an observe tool_result to keep it under the
    silent-truncation threshold.

    r10 / KAE-42 patch 1: observe tool_results are the dominant contributor to the
    3.5% of records exceeding MAX_SEQ_LEN=16384. The main culprit is fishspot
    clusters (type=13) — docks can spawn 88 in a single observe — and off-screen
    NPC/player entities (type=0/1) that are never actionable at >15 tiles.

    Inner format: `"<inner JSON>\\n\\nASCII_MAP:..."` (post _prefer_real_tool_result
    unwrap). We parse the JSON, trim `nearby_entities` in-place, and restitch.
    If parsing fails, return the string unchanged — safer than crashing the build.
    """
    if not result or not isinstance(result, str):
        return result
    # Split off the ASCII_MAP suffix — it must be preserved verbatim.
    if "\n\nASCII_MAP:" in result:
        json_part, _, ascii_part = result.partition("\n\nASCII_MAP:")
        ascii_suffix = "\n\nASCII_MAP:" + ascii_part
    else:
        json_part = result
        ascii_suffix = ""
    try:
        gs = json.loads(json_part)
    except (json.JSONDecodeError, ValueError):
        return result
    if not isinstance(gs, dict):
        return result
    ents = gs.get("nearby_entities")
    if not isinstance(ents, list):
        return result

    # Cap fishspot (type 13) at 5 and drop off-screen NPCs (type 1) / players
    # (type 0) > distance 15. Matches spec in KAE-42 patch 1.
    fish = 0
    trimmed = []
    for e in ents:
        if not isinstance(e, dict):
            trimmed.append(e)
            continue
        if e.get("type") == 13:
            if fish >= 5:
                continue
            fish += 1
        elif e.get("type") in (0, 1):
            if not e.get("on_screen") and e.get("distance", 0) > 15:
                continue
        trimmed.append(e)
    gs["nearby_entities"] = trimmed
    # Re-serialize with the MCP server's compact shape (no indent).
    return json.dumps(gs, separators=(",", ":")) + ascii_suffix


def build_tool_result_message(turn: dict) -> dict:
    """Build a tool result message for the action taken in this turn.

    Prefers the real tool result captured by extract_turns.py in the
    `action_result_raw` field over the synthetic fallback. This is load-bearing:
    synthesize_tool_result() used to hardcode interact_npc → quest_opened=false,
    which erased every real quest acceptance (574/2,082 interact_npc calls)
    and trained the model on a world where quests never actually opened.
    Real results must take precedence whenever they are available.
    """
    action = turn.get("action_structured", "")
    action_type = turn.get("action_type", "")
    turn_id = turn.get("turn_id", "t000")
    call_id = f"call_{turn_id[-3:]}"

    if action_type == "update_memory":
        return None  # Skip — no Bash tool in native MCP format

    tc_result = _structured_action_to_tool_call(action, action_type)
    if tc_result is None:
        return None
    tool_name, _ = tc_result

    # Prefer real tool result from logs; fall back to synthesizer for legacy
    # turns.jsonl files that predate the action_result_raw contract.
    real = _prefer_real_tool_result(turn.get("action_result_raw"))
    if real is not None:
        result = real
    else:
        result = synthesize_tool_result(action, turn=turn)

    # r10 / KAE-42 patch 1: cap entity noise on observe results only.
    if tool_name == "observe":
        result = _cap_observe_entities(result)

    return {"role": "tool", "content": result, "tool_call_id": call_id, "name": tool_name}


def build_multi_turn_records(
    session_turns: list[dict],
    personality: str | None,
    min_score: float,
    window_size: int = 3,
    stride: int | None = None,
) -> list[dict]:
    """Build sliding-window multi-turn training records from a session's turns.

    r10 / KAE-42 patch 3: default window_size reduced 5→3. Shorter windows cut
    truncation risk (records >16K tokens) and tighten supervision density per
    record (less filler context per assistant turn).
    """
    if stride is None:
        stride = max(1, window_size // 2)

    records = []
    n = len(session_turns)
    if n == 0:
        return []

    # Generate windows
    starts = list(range(0, n, stride))
    # Ensure we don't miss the tail
    if starts and starts[-1] + window_size < n:
        starts.append(max(0, n - window_size))

    # Note: SFT records do not embed the system message — train_modal._build_system_prompt
    # builds it per-record from metadata["system_prompt"] + personality_suffixes at
    # training time. We only tag each record with its personality label here.

    for start in starts:
        end = min(start + window_size, n)
        window = session_turns[start:end]

        # Filter out bad turns
        # r10 / KAE-42 patch 5: removed the old "skip click_tile in non-last
        # positions" filter. Its rationale — only the last turn got <think> —
        # stopped being true at r9 when reasoning was added to every turn. The
        # filter was also creating a training bias where click_tile only ever
        # appeared as the final action of a window, which is not a pattern we
        # want the model to internalize. The degenerate-record filter below
        # (click_tile > 50% of actions) still guards against click_tile spam.
        valid_window: list[dict] = []
        for t in window:
            if is_desert_quest_waste(t):
                continue
            if min_score > 0 and score_turn(t) < min_score:
                continue
            valid_window.append(t)

        if len(valid_window) < 2:
            continue

        # Skip repetitive windows — same action 3+ times in a row teaches spam
        actions = [t.get("action_type", "") for t in valid_window]
        is_repetitive = False
        for i_a in range(len(actions) - 2):
            if actions[i_a] and actions[i_a] == actions[i_a + 1] == actions[i_a + 2]:
                is_repetitive = True
                break
        # r10 / KAE-42 patch 2: also reject any window with observe→observe —
        # violates rule #1 in system.md ("don't double-observe"). Data-side
        # enforcement so the model never sees this pattern during SFT.
        if not is_repetitive:
            for i_a in range(len(actions) - 1):
                if actions[i_a] == "observe" == actions[i_a + 1]:
                    is_repetitive = True
                    break
        if is_repetitive:
            continue

        # Find memory context for the first turn
        memory = find_latest_memory(session_turns, start)
        if memory is None:
            memory = DEFAULT_MEMORY

        messages = []

        for i, turn in enumerate(valid_window):
            prev = valid_window[i - 1] if i > 0 else None
            mem = memory if i == 0 else None

            user_text = build_user_message(turn, prev_turn=prev, memory=mem)
            messages.append({"role": "user", "content": user_text})

            # r9 fix: include <think> on ALL turns, not just the last.
            # r8 and earlier only included reasoning on the final turn (69% of
            # training had no reasoning), teaching the model to act without thinking.
            asst_msg = build_assistant_message(turn, include_thinking=True)
            if asst_msg is None:
                continue  # Skip update_memory turns

            messages.append(asst_msg)

            # Tool result message (provides action feedback before next turn)
            tool_result = build_tool_result_message(turn)
            if tool_result is not None:
                messages.append(tool_result)

        records.append({"messages": messages, "personality": personality})

    return records


def build_grpo_prompts(
    session_turns: list[dict],
    personality: str | None,
    min_score: float,
) -> list[dict]:
    """Build prompt-only records with reward context for GRPO training.

    Each record contains the prompt (system + user) that the model will complete,
    plus reward_context with current/next state for scoring completions.
    """
    # GRPO embeds the system prompt per-record. Substitute personality into the
    # __PERSONALITY_BLOCK__ placeholder so byte-parity matches eval_harness.
    personality_block = PERSONALITY_SUFFIXES.get(personality or "", "")
    sys_prompt = SYSTEM_PROMPT.replace("__PERSONALITY_BLOCK__", personality_block)

    records = []
    for i, turn in enumerate(session_turns):
        if is_desert_quest_waste(turn):
            continue
        if min_score > 0 and score_turn(turn) < min_score:
            continue

        gs = turn.get("game_state", {})
        if not gs or not gs.get("player_position"):
            continue
        action = turn.get("action_structured", "")
        if not action:
            continue

        # Build user message (single-turn, no memory for GRPO prompts)
        pruned = prune_game_state(gs)
        state_json = json.dumps(pruned, separators=(",", ":"))
        ascii_map = turn.get("ascii_map", "").strip()

        parts = [f"<game_state>\n{state_json}\n</game_state>"]
        if ascii_map:
            parts.append(f"<ascii_map>\n{ascii_map}\n</ascii_map>")

        # Add reward context (next state for scoring)
        reward_ctx = {}
        if i + 1 < len(session_turns):
            next_gs = session_turns[i + 1].get("game_state", {})
            delta = compute_state_delta(gs, next_gs)
            reward_ctx = delta
        reward_ctx_json = json.dumps(reward_ctx, separators=(",", ":"))
        parts.append(f"<reward_context>\n{reward_ctx_json}\n</reward_context>")

        parts.append("What should you do?")
        user_text = "\n\n".join(parts)

        records.append({
            "prompt": [
                {"role": "system", "content": [{"type": "text", "text": sys_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_text}]},
            ],
            "reward_context": reward_ctx,
            "expected_action": action,
        })

    return records


def turn_to_conversation(turn: dict, personality: str | None = None, min_score: float = 0.0) -> dict | None:
    """Convert a single turn into a Qwen3.5 conversation record with tool calls."""
    game_state = turn.get("game_state")
    if not game_state or not game_state.get("player_position"):
        return None

    action_structured = turn.get("action_structured", "")
    if not action_structured:
        return None

    if is_desert_quest_waste(turn):
        return None

    if min_score > 0 and score_turn(turn) < min_score:
        return None

    # Skip click_tile with weak reasoning in single-turn mode too
    action_type = turn.get("action_type", "")
    reasoning = turn.get("reasoning", "")
    if action_type == "click_tile" and len(reasoning.strip()) < 30:
        return None

    user_text = build_user_message(turn)

    asst_msg = build_assistant_message(turn, include_thinking=True)
    if asst_msg is None:
        return None  # Skip update_memory turns

    tool_result = build_tool_result_message(turn)
    msgs = [
        {"role": "user", "content": user_text},
        asst_msg,
    ]
    if tool_result is not None:
        msgs.append(tool_result)

    return {"messages": msgs, "personality": personality}


# Agents excluded from training. Uses path segments (not substrings) to avoid
# false matches like "agent_40". agent_4/agent_5 are Qwen finetuned/base
# harness logs, not teacher Sonnet data.
EXCLUDED_AGENTS = {"agent_3", "agent_4", "agent_5"}

# Only include turns from these harnesses in training data.
# Codex/Gemini/OpenCode integrations are experimental (smoke-test only) and
# their turns are excluded until validated.
INCLUDED_HARNESSES = {"claude", "unknown"}


def _is_excluded_agent(path: Path) -> bool:
    """Check if a path belongs to an excluded agent using path segments, not substrings."""
    parts = path.parts
    return any(agent in parts for agent in EXCLUDED_AGENTS)


def _is_reasoningless_tool_turn(turn: dict) -> bool:
    """Drop turns where a non-observe tool action was emitted without any reasoning.

    r10: observe turns are exempt — session-start observes and some repeated
    observes legitimately carry no preceding reasoning (the agent's motion is
    "first look at the world"), and the model must still learn that pattern.
    Observe turns are still filtered elsewhere (degenerate-session checks).
    """
    if turn.get("action_type") == "observe":
        return False
    return bool(turn.get("action_structured")) and not str(turn.get("reasoning", "")).strip()


def _is_login_loop_session(turns: list[dict]) -> bool:
    """Detect sessions dominated by login/browser recovery debugging instead of gameplay."""
    if not turns:
        return False

    first_reasoning = str(turns[0].get("reasoning", "")).lower()
    first_meta_hits = sum(marker in first_reasoning for marker in LOGIN_LOOP_REASONING_MARKERS)
    if first_meta_hits >= 5:
        return True

    flagged = 0
    for turn in turns[:5]:
        result_text = str(turn.get("action_result_raw", "")).lower()
        reasoning = str(turn.get("reasoning", "")).lower()
        meta_hits = sum(marker in reasoning for marker in LOGIN_LOOP_REASONING_MARKERS)
        if any(marker in result_text for marker in LOGIN_LOOP_RESULT_MARKERS) or meta_hits >= 3:
            flagged += 1

    return flagged >= 2 or (len(turns) <= 2 and flagged >= 1)


def _filter_session_turns(turns: list[dict]) -> tuple[list[dict], bool, int]:
    """Filter a session without mutating on-disk extracted turns."""
    if _is_login_loop_session(turns):
        return [], True, 0

    filtered = [turn for turn in turns if not _is_reasoningless_tool_turn(turn)]
    dropped = len(turns) - len(filtered)
    return filtered, False, dropped


def load_turns(input_dir: Path) -> list[tuple[str, dict]]:
    """Load all turns from extracted dataset directory. Returns (session_name, turn) pairs."""
    all_turns = []
    skipped_harnesses: dict[str, int] = {}
    dropped_login_sessions = 0
    dropped_reasoningless_turns = 0
    for jsonl in sorted(input_dir.rglob("turns.jsonl")):
        # Skip excluded agents (deprecated agent_3, non-Claude agent_4)
        if _is_excluded_agent(jsonl):
            continue
        session = jsonl.parent.name
        session_turns = []
        for line in open(jsonl):
            try:
                turn = json.loads(line)
                # Filter by harness — only include validated harnesses
                harness = turn.get("harness", "unknown")
                if harness not in INCLUDED_HARNESSES:
                    skipped_harnesses[harness] = skipped_harnesses.get(harness, 0) + 1
                    continue
                session_turns.append(turn)
            except json.JSONDecodeError:
                continue
        session_turns, dropped_session, dropped_turns = _filter_session_turns(session_turns)
        if dropped_session:
            dropped_login_sessions += 1
            continue
        dropped_reasoningless_turns += dropped_turns
        all_turns.extend((session, turn) for turn in session_turns)
    if skipped_harnesses:
        for h, count in skipped_harnesses.items():
            print(f"  [filter] Skipped {count} turns from harness '{h}'")
    if dropped_login_sessions:
        print(f"  [filter] Dropped {dropped_login_sessions} login-loop sessions")
    if dropped_reasoningless_turns:
        print(f"  [filter] Dropped {dropped_reasoningless_turns} tool turns with no reasoning")
    return all_turns


def load_turns_by_session(input_dir: Path) -> dict[str, list[dict]]:
    """Load turns grouped by session, preserving chronological order."""
    sessions = {}
    dropped_login_sessions = 0
    dropped_reasoningless_turns = 0
    for jsonl in sorted(input_dir.rglob("turns.jsonl")):
        if _is_excluded_agent(jsonl):
            continue
        session = jsonl.parent.name
        turns = []
        for line in open(jsonl):
            try:
                turn = json.loads(line)
                # Filter by harness — only include validated harnesses
                harness = turn.get("harness", "unknown")
                if harness not in INCLUDED_HARNESSES:
                    continue
                turns.append(turn)
            except json.JSONDecodeError:
                continue
        turns, dropped_session, dropped_turns = _filter_session_turns(turns)
        if dropped_session:
            dropped_login_sessions += 1
            continue
        dropped_reasoningless_turns += dropped_turns
        if turns:
            sessions[session] = turns
    if dropped_login_sessions:
        print(f"  [filter] Dropped {dropped_login_sessions} login-loop sessions")
    if dropped_reasoningless_turns:
        print(f"  [filter] Dropped {dropped_reasoningless_turns} tool turns with no reasoning")
    return sessions


def main():
    parser = argparse.ArgumentParser(description="Convert extracted turns to Qwen3.5 9B SFT format")
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
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum quality score to include (default: 0.0, range 0.0-1.0)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for train/val split")
    parser.add_argument(
        "--mode",
        choices=["single", "multi", "mixed"],
        default="mixed",
        help="Training mode: single (original), multi (windowed), mixed (default)",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=3,
        help="Turns per multi-turn window (default: 3; reduced 5→3 in r10/KAE-42)",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=None,
        help="Window step size (default: window_size // 2)",
    )
    parser.add_argument(
        "--format",
        choices=["sft", "grpo"],
        default="sft",
        help="Output format: sft (conversations) or grpo (prompts with reward context)",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # Detect personality per session (cache)
    personality_cache = {}

    def get_personality(session: str) -> str | None:
        if session not in personality_cache:
            personality_cache[session] = detect_personality(session, args.input)
        return personality_cache[session]

    # GRPO format: prompt-only with reward context
    if args.format == "grpo":
        sessions_data = load_turns_by_session(args.input)
        if not sessions_data:
            print("No turns found in input directory.", file=sys.stderr)
            sys.exit(1)

        all_prompts = []
        for session, turns in sessions_data.items():
            personality = get_personality(session)
            prompts = build_grpo_prompts(turns, personality=personality, min_score=args.min_score)
            all_prompts.extend(prompts)

        if not all_prompts:
            print("No valid prompts produced.", file=sys.stderr)
            sys.exit(1)

        prompt_path = args.output / "prompts.json"
        with open(prompt_path, "w") as f:
            json.dump(all_prompts, f, indent=2)

        print(f"\nGRPO prompts: {len(all_prompts)} → {prompt_path}")
        return

    conversations = []
    skipped = 0

    if args.mode in ("multi", "mixed"):
        # Load turns grouped by session for windowed multi-turn
        sessions_data = load_turns_by_session(args.input)
        if not sessions_data:
            print("No turns found in input directory.", file=sys.stderr)
            sys.exit(1)

        multi_records = []
        for session, turns in sessions_data.items():
            personality = get_personality(session)
            records = build_multi_turn_records(
                turns,
                personality=personality,
                min_score=args.min_score,
                window_size=args.window_size,
                stride=args.stride,
            )
            for r in records:
                r["_session"] = session
            multi_records.extend(records)

        if args.mode == "multi":
            conversations = multi_records
        else:
            # Mixed mode: 70% multi-turn + 30% single-turn
            # Build single-turn records
            single_records = []
            all_turns = load_turns(args.input)
            for session, turn in all_turns:
                personality = get_personality(session)
                conv = turn_to_conversation(turn, personality=personality, min_score=args.min_score)
                if conv:
                    conv["_session"] = session
                    single_records.append(conv)
                else:
                    skipped += 1

            # Sample 30% single-turn records
            random.seed(args.seed + 1)
            n_single = max(1, int(len(multi_records) * 0.43))  # 30% of total ≈ 43% of multi count
            if len(single_records) > n_single:
                single_sample = random.sample(single_records, n_single)
            else:
                single_sample = single_records

            conversations = multi_records + single_sample
            print(f"  Mixed mode: {len(multi_records)} multi-turn + {len(single_sample)} single-turn")
    else:
        # Single mode (original behavior)
        all_turns = load_turns(args.input)
        if not all_turns:
            print("No turns found in input directory.", file=sys.stderr)
            sys.exit(1)

        for session, turn in all_turns:
            personality = get_personality(session)
            conv = turn_to_conversation(turn, personality=personality, min_score=args.min_score)
            if conv:
                conv["_session"] = session
                conversations.append(conv)
            else:
                skipped += 1

    if not conversations:
        print("No valid conversations produced.", file=sys.stderr)
        sys.exit(1)

    # r9 fix: filter degenerate records (F9 click_tile spam, F10 stuck loops)
    pre_filter = len(conversations)
    filtered_conversations = []
    for c in conversations:
        actions = []
        has_assistant = False
        for m in c["messages"]:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                has_assistant = True
                for tc in m["tool_calls"]:
                    actions.append(tc["function"]["name"])
        if not has_assistant:
            continue  # F13: drop records with no assistant turns
        if actions:
            click_pct = actions.count("click_tile") / len(actions)
            recovery_pct = (actions.count("cancel_nav") + actions.count("stuck_reset")) / len(actions)
            if click_pct > 0.5:
                continue  # F9: >50% click_tile spam
            if recovery_pct > 0.75:
                continue  # F10: >75% stuck recovery loops
        filtered_conversations.append(c)
    degenerate_removed = pre_filter - len(filtered_conversations)
    print(f"  Degenerate filter: removed {degenerate_removed} records ({100*degenerate_removed/pre_filter:.1f}%)")
    conversations = filtered_conversations

    # r10 / KAE-42 patch 6: pre-tokenize truncation gate. Reject records that
    # would silently truncate at MAX_SEQ_LEN=16384 in training. Pre-r10, ~3.5%
    # of records exceeded this; the truncation was silent because TRL/Unsloth
    # drop tokens past max_seq_length without raising. Gating at convert time
    # guarantees zero silent truncation downstream. Leaves a 256-token safety
    # margin for the assistant generation prefix.
    MAX_SEQ_LEN = 16384
    GATE = MAX_SEQ_LEN - 256
    pre_trunc = len(conversations)
    try:
        from transformers import AutoTokenizer
        # Qwen3.5-9B — match training tokenizer exactly.
        tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-9B")
    except Exception as e:
        print(f"  [trunc-gate] Skipping pre-tokenize gate — tokenizer load failed: {e}")
        tok = None

    n_truncation_rejects = 0
    if tok is not None:
        kept = []
        for c in conversations:
            # Resolve system prompt identically to how train_modal does it:
            # SYSTEM_PROMPT with __PERSONALITY_BLOCK__ → full personality .md text.
            personality = c.get("personality") or ""
            personality_block = PERSONALITY_SUFFIXES.get(personality, "")
            sys_prompt = SYSTEM_PROMPT.replace("__PERSONALITY_BLOCK__", personality_block)
            full_messages = [{"role": "system", "content": sys_prompt}] + c["messages"]
            try:
                tok_ids = tok.apply_chat_template(
                    full_messages,
                    tools=TOOL_DEFINITIONS,
                    tokenize=True,
                    add_generation_prompt=False,
                )
            except Exception:
                # If template rendering fails for a record, conservatively keep it
                # rather than silently dropping (the real training pipeline will
                # surface the error).
                kept.append(c)
                continue
            if len(tok_ids) > GATE:
                n_truncation_rejects += 1
                continue
            kept.append(c)
        conversations = kept
        pct = (100 * n_truncation_rejects / pre_trunc) if pre_trunc else 0.0
        print(f"  Truncation gate: rejected {n_truncation_rejects}/{pre_trunc} records ({pct:.2f}%) >{GATE} tokens")

    if not conversations:
        print("No valid conversations after truncation gate.", file=sys.stderr)
        sys.exit(1)

    # Post-build observe->observe safety net. The window-level filter in
    # build_multi_turn_records catches observe->observe within the action
    # sequence, but sub-session boundaries occasionally produce records where
    # two adjacent assistant turns each emit observe tool_calls despite not
    # being consecutive in the original action list. Drop those records.
    pre_obs_dedup = len(conversations)
    cleaned = []
    for c in conversations:
        tool_seq = []
        for m in c.get("messages", []):
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function", {}) or {}
                name = fn.get("name") or tc.get("name")
                tool_seq.append(name)
        has_observe_pair = any(
            a == "observe" == b for a, b in zip(tool_seq, tool_seq[1:])
        )
        if not has_observe_pair:
            cleaned.append(c)
    n_obs_drops = pre_obs_dedup - len(cleaned)
    if n_obs_drops:
        pct = 100 * n_obs_drops / pre_obs_dedup
        print(f"  Observe-pair dedup: dropped {n_obs_drops}/{pre_obs_dedup} records ({pct:.3f}%)")
    conversations = cleaned

    if not conversations:
        print("No valid conversations after observe-pair dedup.", file=sys.stderr)
        sys.exit(1)

    # Stratified split by session, with fallback to record-level split
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

    # Fix: if session-level split produced a bad ratio, fall back to record-level
    total = len(train) + len(val)
    actual_val_ratio = len(val) / total if total > 0 else 0
    if actual_val_ratio < args.val_ratio * 0.5 or actual_val_ratio > args.val_ratio * 2:
        print(f"  Session split produced bad ratio ({actual_val_ratio:.2%}), using record-level split")
        all_records = train + val
        random.shuffle(all_records)
        n_val = max(1, int(len(all_records) * args.val_ratio))
        val = all_records[:n_val]
        train = all_records[n_val:]

    # Write metadata (system prompt + tools, injected at training time)
    metadata_path = args.output / "metadata.json"
    metadata = {
        "system_prompt": SYSTEM_PROMPT,
        "tools": TOOL_DEFINITIONS,
        "personality_suffixes": PERSONALITY_SUFFIXES,
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Write output
    train_path = args.output / "train.json"
    val_path = args.output / "val.json"

    with open(train_path, "w") as f:
        json.dump(train, f, indent=2)
    with open(val_path, "w") as f:
        json.dump(val, f, indent=2)

    # Count messages per record for stats
    msg_counts = [len(c["messages"]) for c in train + val]
    avg_msgs = sum(msg_counts) / len(msg_counts) if msg_counts else 0
    max_msgs = max(msg_counts) if msg_counts else 0

    print(f"\nConverted {len(conversations)} records ({skipped} skipped)")
    print(f"  Mode: {args.mode} (window_size={args.window_size})")
    print(f"  Messages/record: avg={avg_msgs:.1f}, max={max_msgs}")
    print(f"  Train: {len(train)} → {train_path}")
    print(f"  Val:   {len(val)} → {val_path}")

    # Print tool call distribution
    type_counts = Counter()
    for c in train + val:
        for msg in c["messages"]:
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    type_counts[func.get("name", "unknown")] += 1
    print("\nTool call distribution:")
    for action, count in type_counts.most_common():
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
