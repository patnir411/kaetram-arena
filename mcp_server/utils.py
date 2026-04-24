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


def build_quest_query_response(matched_name: str, quest: dict) -> dict:
    ordered = {
        "name": quest.get("name", matched_name),
        "matched_name": matched_name,
        "status": quest.get("status", "unknown"),
        "phase": quest.get("phase"),
        "order": quest.get("order"),
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
        "items_needed",
        "item_sources",
        "crafting_chain",
        "boss",
        "tips",
    ):
        if key in quest:
            ordered[key] = quest[key]
    if ordered["status"] == "blocked":
        ordered["skip_recommended"] = True
    return ordered


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
