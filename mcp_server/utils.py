"""Pure Python utilities for the MCP game server.

No Playwright or browser dependencies — safe to unit-test standalone.
"""

import json
import os
import re

# ── Production skill aliases ──────────────────────────────────────────────────

PRODUCTION_SKILL_ALIASES = {
    "cook": "cooking",
    "cooking": "cooking",
    "craft": "crafting",
    "crafting": "crafting",
    "smith": "smithing",
    "smithing": "smithing",
    "smelt": "smelting",
    "smelting": "smelting",
    "brew": "alchemy",
    "alchemy": "alchemy",
    "fletch": "fletching",
    "fletching": "fletching",
    "chisel": "chiseling",
    "chiseling": "chiseling",
}


def normalize_production_skill(skill: str) -> str:
    return PRODUCTION_SKILL_ALIASES.get((skill or "").strip().lower(), "")


# ── NPC → store key mapping ──────────────────────────────────────────────────

NPC_STORE_KEYS = {
    "forester": "forester",
    "miner": "miner",
    "yet another miner": "miner",
    "sorcerer": "sorcerer",
    "fisherman": "fishingstore",
    "babushka": "ingredientsstore",
    "kosmetics vendor": "cosmetics",
    "clerk": "startshop",
}


# ── Quest walkthrough resolution ─────────────────────────────────────────────

_QUEST_WALKTHROUGHS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "quest_walkthroughs.json"
)


def load_quest_walkthroughs() -> dict:
    with open(_QUEST_WALKTHROUGHS_PATH) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Quest walkthrough data must be a JSON object")
    return data


def normalize_quest_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def resolve_quest_name(query: str, data: dict) -> tuple[str | None, dict | None]:
    norm_query = normalize_quest_name(query)
    if not norm_query:
        return None, {"error": "Quest name is empty"}

    canonical = {}
    for key, quest in data.items():
        if not isinstance(quest, dict):
            continue
        names = {key}
        display_name = quest.get("name")
        if isinstance(display_name, str) and display_name.strip():
            names.add(display_name)
        canonical[key] = {normalize_quest_name(name) for name in names if name}

    exact_matches = [
        key for key, normalized_names in canonical.items() if norm_query in normalized_names
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": sorted(exact_matches),
        }

    substring_matches = [
        key
        for key, normalized_names in canonical.items()
        if any(norm_query in normalized_name for normalized_name in normalized_names)
    ]
    if len(substring_matches) == 1:
        return substring_matches[0], None
    if len(substring_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": sorted(substring_matches),
        }

    query_tokens = set(norm_query.split())
    scored: list[tuple[int, str]] = []
    for key, normalized_names in canonical.items():
        best_score = 0
        for normalized_name in normalized_names:
            name_tokens = set(normalized_name.split())
            best_score = max(best_score, len(query_tokens & name_tokens))
        if best_score > 0:
            scored.append((best_score, key))

    if not scored:
        return None, {
            "error": f"No quest matching '{query}'",
            "available": sorted(data.keys()),
        }

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0]
    top_matches = sorted([key for score, key in scored if score == top_score])
    if len(top_matches) > 1:
        return None, {
            "error": f"Ambiguous quest name '{query}'",
            "matches": top_matches,
        }

    return top_matches[0], None


_STAGE_ITEM_RE = re.compile(r"([a-z][a-z0-9]*)\s*x\s*(\d+)", re.IGNORECASE)


def _inventory_counts(inventory) -> dict:
    """Normalize an inventory payload to {item_key_lower: total_count}.

    Accepts either the observe list-of-dicts shape ([{key,name,count,...}]) or a
    flat {key: count} dict. Unknown shapes yield an empty mapping.
    """
    counts: dict[str, int] = {}
    if isinstance(inventory, dict):
        for k, v in inventory.items():
            try:
                counts[str(k).lower()] = counts.get(str(k).lower(), 0) + int(v)
            except (TypeError, ValueError):
                continue
    elif isinstance(inventory, list):
        for it in inventory:
            if not isinstance(it, dict):
                continue
            key = it.get("key") or it.get("name")
            if not key:
                continue
            try:
                cnt = int(it.get("count", 1) or 1)
            except (TypeError, ValueError):
                cnt = 1
            counts[str(key).lower()] = counts.get(str(key).lower(), 0) + cnt
    return counts


def normalize_quest_lists(raw_state) -> tuple[list, list]:
    """Derive (active_quests, finished_quests) from a raw game-state dict.

    `window.__latestGameState` (what `query_quest` reads) carries a FLAT
    `quests` array — `[{name, stage, stageCount, started, finished}]` — while
    the `observe` output exposes the split `active_quests`/`finished_quests`.
    Tools that read the raw cache must derive the split themselves or they see
    every accepted quest as not-accepted. This mirrors the filter in
    `mcp_server/js/observe.js` (started && !finished → active; finished →
    finished) so the two tools agree. If the state is already split (observe
    shape), pass it through unchanged.
    """
    if not isinstance(raw_state, dict):
        return [], []
    quests = raw_state.get("quests")
    if not isinstance(quests, list):
        return (raw_state.get("active_quests") or [], raw_state.get("finished_quests") or [])
    active, finished = [], []
    for q in quests:
        if not isinstance(q, dict):
            continue
        entry = {"name": q.get("name"), "stage": q.get("stage"),
                 "stage_count": q.get("stageCount")}
        if q.get("finished"):
            finished.append({"name": q.get("name")})
        elif q.get("started"):
            active.append(entry)
    return active, finished


def quest_stage_item_progress(quest_data: dict, stage, inventory) -> dict | None:
    """Compare the current quest stage's item requirement against inventory.

    The live `stage` (from observe's `active_quests[].stage`) indexes directly
    into `stage_summary` — e.g. Herbalist's stage 1 is `stage_summary[1]` =
    "Turn in `bluelily x3`", the current objective. Item requirements are parsed
    from that summary string (`key xN` tokens), cross-referenced against the
    player's inventory, and returned as needed/have/remaining so the agent can
    continue from where it is instead of re-planning from stage 0.

    Returns None when the current stage names no items (e.g. a talk-only step) or
    the data is unusable, so callers omit the block rather than fail.
    """
    if not isinstance(quest_data, dict):
        return None
    summaries = quest_data.get("stage_summary")
    if not isinstance(summaries, list) or not summaries:
        return None
    try:
        stage = int(stage)
    except (TypeError, ValueError):
        return None
    if stage < 0:
        return None
    if stage >= len(summaries):
        return {
            "stage": stage,
            "stage_label": "finished",
            "finished": True,
            "needed": {},
            "have": {},
            "remaining": {},
            "all_satisfied": True,
        }
    label = str(summaries[stage])
    needed: dict[str, int] = {}
    for m in _STAGE_ITEM_RE.finditer(label):
        key = m.group(1).lower()
        try:
            needed[key] = needed.get(key, 0) + int(m.group(2))
        except ValueError:
            continue
    if not needed:
        return None
    held = _inventory_counts(inventory)
    have = {k: held.get(k, 0) for k in needed}
    remaining = {k: max(0, needed[k] - have[k]) for k in needed}
    return {
        "stage": stage,
        "stage_label": label,
        "needed": needed,
        "have": have,
        "remaining": remaining,
        "all_satisfied": all(v == 0 for v in remaining.values()),
    }


def build_quest_query_response(matched_name: str, quest: dict) -> dict:
    ordered = {
        "name": quest.get("name", matched_name),
        "matched_name": matched_name,
        "order": quest.get("order"),
        "off_limits": quest.get("status") == "off-limits",
        "blocked_reason": quest.get("blocked_reason"),
        "requirements": quest.get("requirements", {}),
        "unlocks": quest.get("unlocks", {}),
        "actual_rewards": quest.get("actual_rewards", []),
        "reward_caveats": quest.get("reward_caveats", []),
        "known_mismatches": quest.get("known_mismatches", []),
    }
    for key in (
        "npc",
        "stages",
        "prereqs",
        "stage_summary",
        "walkthrough",
        "walkthrough_steps",
        "items_needed",
        "item_sources",
        "crafting_chain",
        "boss",
        "tips",
    ):
        if key in quest:
            ordered[key] = quest[key]
    return ordered


def apply_no_walkthrough_policy(response: dict, matched_name: str) -> dict:
    """Redact static guidance for the eval-only held-out quest condition.

    This pure helper is deliberately scoped to one exact quest named by the
    eval harness. Other quests and normal runs retain the full response. Live
    accepted/stage/finished state and gate status remain visible; walkthrough,
    next-action, NPC, item, recipe, boss, reward, and station hints do not.
    """
    enabled = os.environ.get("KAETRAM_NO_WALKTHROUGH", "").lower() in {"1", "true", "yes"}
    target = os.environ.get("KAETRAM_HELDOUT_QUEST", "")
    if not enabled or not target:
        return response
    if normalize_quest_name(matched_name) != normalize_quest_name(target):
        return response

    redacted = {
        "name": response.get("name", matched_name),
        "matched_name": response.get("matched_name", matched_name),
        "off_limits": response.get("off_limits", False),
        "no_walkthrough": True,
    }
    current = response.get("current_step")
    if isinstance(current, dict):
        redacted["current_step"] = {
            key: current[key]
            for key in ("accepted", "stage", "finished")
            if key in current
        }
    if "live_gate_status" in response:
        redacted["live_gate_status"] = response["live_gate_status"]
    return redacted


# ── Shop UI helpers ──────────────────────────────────────────────────────────

def compact_shop_ui(ui_state: dict | None) -> dict:
    ui_state = ui_state or {}
    shop = ui_state.get("shop") if isinstance(ui_state, dict) else {}
    debug = shop.get("debug") if isinstance(shop, dict) else {}
    return {
        "type": ui_state.get("type"),
        "shop_ready": shop.get("ready") if isinstance(shop, dict) else None,
        "shop_visible": shop.get("visible") if isinstance(shop, dict) else None,
        "store_key": shop.get("store_key") if isinstance(shop, dict) else None,
        "has_store": shop.get("has_store") if isinstance(shop, dict) else None,
        "selected_buy_index": shop.get("selectedBuyIndex") if isinstance(shop, dict) else None,
        "item_entries": (shop.get("item_entries") or [])[:4] if isinstance(shop, dict) else [],
        "dom_visible": debug.get("any_visible_dom_storeish") if isinstance(debug, dict) else None,
        "dom_text": debug.get("dom_store_text") if isinstance(debug, dict) else None,
    }


def check_shop_visibly_open(ui_state: dict | None) -> bool:
    """Check if the shop UI is visibly open based on all known visibility flags."""
    if not isinstance(ui_state, dict):
        return False
    shop = ui_state.get("shop")
    if not isinstance(shop, dict):
        return False
    shop_debug = shop.get("debug") if isinstance(shop, dict) else {}
    return bool(
        shop.get("ready")
        or shop.get("visible")
        or shop.get("containerVisible")
        or shop.get("storeContainerVisible")
        or (isinstance(shop_debug, dict) and shop_debug.get("any_visible_dom_storeish"))
    )
