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
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode mixed --window-size 5
    python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_grpo/ --format grpo
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# Tool definitions for browser_run_code (Playwright MCP) and Bash
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "browser_run_code",
            "description": "Execute JavaScript code in the game browser. Use helper functions: __attackMob(name), __interactNPC(name), __talkToNPC(id), __navigateTo(x,y), __moveTo(x,y), __clickEntity(label), __clickTile(x,y), __safeWarp(id), __eatFood(slot), __stuckReset(), __navCancel(). Return values provide action results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "JavaScript code to execute in the browser page context",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Execute a shell command. Use ONLY for writing progress.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
]

# Warp location IDs and attack style IDs
WARP_IDS = {"Mudwich": 0, "Crossroads": 1, "Lakesworld": 2, "Patsow": 3, "Crullfield": 4, "Undersea": 5}
STYLE_IDS = {"Hack": 6, "Chop": 7, "Defensive": 3, "Stab": 1, "Slash": 2}

# Condensed game rules for the system message
SYSTEM_PROMPT = """\
You are an AI agent playing Kaetram, a 2D pixel MMORPG. You observe the game via structured game state and an ASCII map, then decide and execute actions.

## Entity Types
- type 0: other player
- type 1: NPC (quest_npc=true means active quest target, has_achievement=true means achievement available)
- type 3: mob (attackable — has HP)
- type 4: item drop (lootable)
- type 12: harvestable (tree, rock, fish spot)

## Actions
- attack(mob_name): Attack nearest mob matching name. Auto-walks and auto-attacks.
- interact_npc(npc_name): Walk to NPC and initiate dialogue.
- talk_npc(instance_id): Advance NPC dialogue by one line.
- navigate(x, y): Long-distance pathfinding to grid coordinates.
- move(x, y): Short-distance movement to nearby grid coordinates.
- click(x, y): Click canvas at pixel coordinates. For mobs, NPCs, loot, or walking.
- click_entity(label): Click entity by ASCII map label (E0, E1...).
- click_tile(x, y): Click specific grid tile.
- equip(slot=N): Equip item from inventory slot N.
- heal(slot=N): Consume edible item at slot N to restore HP.
- warp(location): Fast travel (Mudwich, Crossroads, Lakesworld, Patsow, Crullfield, Undersea).
- quest_accept(): Accept or progress a quest.
- set_style(style): Change attack style (Hack, Chop, Defensive).
- wait(Ns): Wait N seconds for combat/regen.
- respawn(): Click respawn button after death.

## Priority System
1. RESPAWN — Dead? Click respawn, then warp to Mudwich.
2. HEAL — HP below 50%? Eat food (edible=true in inventory).
3. LOOT — Item drop nearby (type=4)? Click it.
4. EQUIP — Better gear available (equippable=true)? Equip it.
5. QUEST NPC — NPC with quest_npc=true? Walk close, interact, accept quest.
6. QUEST — Active quest? Work on objective (kill, gather, deliver).
7. GRIND — Kill nearest mob for XP using attack(mob_name).
8. EXPLORE — Navigate to a new area.

## Combat
Use attack(mob_name) to auto-walk and auto-attack. Wait 5-6s per kill cycle.
After kill, observe state before next action. Fight mobs near your level.

## Navigation
Use navigate(x, y) for distances > 15 tiles. Use move(x, y) for < 15 tiles.
Check navigation.status: 'arrived' = done, 'stuck' = warp to Mudwich instead.

## Key Info
- Canvas center ≈ player position. Entities have click_x/click_y when on_screen=true.
- Distance ≤ 3: can click directly. Distance > 3: walk toward them first.
- After death: respawn(), then warp(Mudwich), re-equip weapon, set_style(Hack).
- Mudwich village center: approximately (188, 157)."""

# Condensed personality suffixes (2-3 sentences each)
PERSONALITY_SUFFIXES = {
    "aggressive": "\n\n## Playstyle: AGGRESSIVE\nPrioritize combat above all. Push into harder zones and fight mobs at the edge of your capability. Accept death as part of progression — re-engage immediately after respawn.",
    "methodical": "\n\n## Playstyle: METHODICAL\nPrepare thoroughly before advancing. Complete quests in order, gather resources, build skills. Keep HP above 60% and always carry food before entering dangerous areas.",
    "curious": "\n\n## Playstyle: CURIOUS\nExplore the world broadly. Talk to every NPC, enter every building, warp to new locations. Discovery matters more than efficiency — find quests and areas others miss.",
    "efficient": "\n\n## Playstyle: EFFICIENT\nOptimize quest completion. Accept multiple quests, batch objectives, minimize travel. No wasted turns — every action should progress toward a quest or level goal.",
}


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


def format_reasoning(reasoning: str) -> str:
    """Clean up reasoning text for the assistant message."""
    # Remove empty lines and excessive whitespace
    lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
    return " ".join(lines)


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
    high_value = ("attack", "interact_npc", "navigate", "quest_accept", "talk_npc")
    medium_value = ("heal", "equip", "warp", "move", "click_entity", "set_style")
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
        "warp": ["warp", "teleport", "fast travel", "mudwich", "crossroads", "lakesworld"],
        "interact_npc": ["npc", "talk", "quest", "interact", "dialogue"],
        "equip": ["equip", "weapon", "armor", "gear", "sword", "axe"],
        "respawn": ["dead", "died", "respawn", "death"],
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


def synthesize_tool_result(action: str) -> str:
    """Generate a brief tool result string for the action (training data only)."""
    m = re.match(r"(\w+)\(", action)
    name = m.group(1) if m else "unknown"
    results = {
        "attack": "Targeting mob, auto-attacking",
        "interact_npc": "Walking to NPC, initiating dialogue",
        "talk_npc": "Advancing dialogue",
        "navigate": "Pathfinding started",
        "move": "Moving to target",
        "click": "Clicked canvas",
        "click_entity": "Clicked entity",
        "click_tile": "Clicked tile",
        "warp": "Warping...",
        "heal": "Consumed food, healing",
        "equip": "Item equipped",
        "quest_accept": "Quest accepted",
        "set_style": "Attack style changed",
        "wait": "Waited",
        "respawn": "Respawning...",
        "stuck_reset": "Reset, warping to safety",
        "nav_cancel": "Navigation cancelled",
    }
    return results.get(name, "OK")


def build_user_message(turn: dict, prev_turn: dict | None = None, memory: dict | None = None) -> str:
    """Build the user message with optional memory and state delta."""
    pruned = prune_game_state(turn.get("game_state", {}))
    state_json = json.dumps(pruned, separators=(",", ":"))
    ascii_map = turn.get("ascii_map", "").strip()

    parts = []

    # Memory block (only on first turn of a window)
    if memory is not None:
        mem_json = json.dumps(memory, separators=(",", ":"))
        parts.append(f"<memory>\n{mem_json}\n</memory>")

    parts.append(f"<game_state>\n{state_json}\n</game_state>")

    # State delta (turns 2+ in a window)
    if prev_turn is not None:
        delta = compute_state_delta(
            prev_turn.get("game_state", {}),
            turn.get("game_state", {}),
        )
        if delta:
            delta_json = json.dumps(delta, separators=(",", ":"))
            parts.append(f"<state_delta>\n{delta_json}\n</state_delta>")

    if ascii_map:
        parts.append(f"<ascii_map>\n{ascii_map}\n</ascii_map>")

    parts.append("What should you do?")
    return "\n\n".join(parts)


def build_assistant_message(turn: dict, include_thinking: bool = True) -> dict:
    """Build assistant message dict with tool_calls for browser_run_code or Bash.

    Returns a full message dict (not just text) because tool_calls need special structure.
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

    # Build tool call
    call_id = f"call_{turn_id[-3:]}"

    if action_type == "update_memory":
        # Memory writes use Bash tool
        mem_content = turn.get("memory_content", {})
        mem_json = json.dumps(mem_content, indent=2)
        command = f"cat > state/progress.json << 'PROGRESS'\n{mem_json}\nPROGRESS"
        tool_calls = [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": {"command": command},
            },
        }]
    else:
        # Game actions use browser_run_code
        js_code = structured_action_to_js(action)
        tool_calls = [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "browser_run_code",
                "arguments": {"code": js_code},
            },
        }]

    msg = {"role": "assistant", "tool_calls": tool_calls}
    if content:
        msg["content"] = content
    return msg


def build_tool_result_message(turn: dict) -> dict:
    """Build a tool result message for the action taken in this turn."""
    action = turn.get("action_structured", "")
    action_type = turn.get("action_type", "")
    turn_id = turn.get("turn_id", "t000")
    call_id = f"call_{turn_id[-3:]}"

    if action_type == "update_memory":
        return {"role": "tool", "content": "", "tool_call_id": call_id, "name": "Bash"}
    else:
        result = synthesize_tool_result(action)
        return {"role": "tool", "content": result, "tool_call_id": call_id, "name": "browser_run_code"}


def build_multi_turn_records(
    session_turns: list[dict],
    personality: str | None,
    min_score: float,
    window_size: int = 5,
    stride: int | None = None,
) -> list[dict]:
    """Build sliding-window multi-turn training records from a session's turns."""
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

    sys_prompt = SYSTEM_PROMPT
    if personality and personality in PERSONALITY_SUFFIXES:
        sys_prompt += PERSONALITY_SUFFIXES[personality]

    for start in starts:
        end = min(start + window_size, n)
        window = session_turns[start:end]

        # Filter out bad turns
        valid_window = []
        for t in window:
            if is_desert_quest_waste(t):
                continue
            if min_score > 0 and score_turn(t) < min_score:
                continue
            valid_window.append(t)

        if len(valid_window) < 2:
            continue

        # Find memory context for the first turn
        memory = find_latest_memory(session_turns, start)
        if memory is None:
            memory = DEFAULT_MEMORY

        messages = [
            {"role": "system", "content": sys_prompt},
        ]

        for i, turn in enumerate(valid_window):
            prev = valid_window[i - 1] if i > 0 else None
            mem = memory if i == 0 else None

            user_text = build_user_message(turn, prev_turn=prev, memory=mem)
            messages.append({"role": "user", "content": user_text})

            # Qwen3.5 guidance: only include <think> on the LAST assistant turn
            is_last = i == len(valid_window) - 1
            asst_msg = build_assistant_message(turn, include_thinking=is_last)
            messages.append(asst_msg)

            # Tool result message (provides action feedback before next turn)
            tool_result = build_tool_result_message(turn)
            messages.append(tool_result)

        records.append({"messages": messages, "tools": TOOL_DEFINITIONS})

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
    sys_prompt = SYSTEM_PROMPT
    if personality and personality in PERSONALITY_SUFFIXES:
        sys_prompt += PERSONALITY_SUFFIXES[personality]

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

    user_text = build_user_message(turn)

    sys_prompt = SYSTEM_PROMPT
    if personality and personality in PERSONALITY_SUFFIXES:
        sys_prompt += PERSONALITY_SUFFIXES[personality]

    asst_msg = build_assistant_message(turn, include_thinking=True)
    tool_result = build_tool_result_message(turn)

    return {
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text},
            asst_msg,
            tool_result,
        ],
        "tools": TOOL_DEFINITIONS,
    }


def load_turns(input_dir: Path) -> list[tuple[str, dict]]:
    """Load all turns from extracted dataset directory. Returns (session_name, turn) pairs."""
    all_turns = []
    for jsonl in sorted(input_dir.rglob("turns.jsonl")):
        session = jsonl.parent.name
        for line in open(jsonl):
            try:
                turn = json.loads(line)
                all_turns.append((session, turn))
            except json.JSONDecodeError:
                continue
    return all_turns


def load_turns_by_session(input_dir: Path) -> dict[str, list[dict]]:
    """Load turns grouped by session, preserving chronological order."""
    sessions = {}
    for jsonl in sorted(input_dir.rglob("turns.jsonl")):
        session = jsonl.parent.name
        turns = []
        for line in open(jsonl):
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if turns:
            sessions[session] = turns
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
        default=5,
        help="Turns per multi-turn window (default: 5)",
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

    # Print action type distribution from tool_calls
    type_counts = Counter()
    for c in train + val:
        for msg in c["messages"]:
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "unknown")
                    if tool_name == "browser_run_code":
                        raw_args = func.get("arguments", {})
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        code = args.get("code", "")
                        # Extract helper function name from JS
                        m = re.search(r"window\.(__\w+)\(", code)
                        if m:
                            type_counts[m.group(1)] += 1
                        elif "MouseEvent" in code or "dispatchEvent" in code:
                            type_counts["click"] += 1
                        elif "quest-button" in code:
                            type_counts["quest_accept"] += 1
                        else:
                            type_counts["other_js"] += 1
                    elif tool_name == "Bash":
                        type_counts["Bash(progress.json)"] += 1
    print("\nTool call distribution:")
    for action, count in type_counts.most_common():
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
