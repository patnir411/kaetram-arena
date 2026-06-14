from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GAME_KNOWLEDGE = REPO_ROOT / "prompts" / "game_knowledge.md"
QUEST_WALKTHROUGHS = REPO_ROOT / "prompts" / "quest_walkthroughs.json"
SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.md"

# Quests that game_knowledge.md catalogs as completable (3 CORE + 5 EXTRA + 5
# bonus = 13) plus the auto-completed Tutorial. Source: `prompts/game_knowledge.md`
# QUEST CATALOG + bonus rows.
COMPLETABLE_QUESTS = (
    "Tutorial",
    "Foresting",
    "Desert Quest",
    "Anvil's Echoes",
    "Royal Drama",
    "Royal Pet",
    "Sorcery and Stuff",
    "Rick's Roll",
    "Scientist's Potion",
    "Herbalist's Desperation",
    "Scavenger",
    "Clam Chowder",
    "Ancient Lands",
    "Evil Santa",
)

# Quests game_knowledge.md flags off-limits (must NOT be accepted).
BLOCKED_QUESTS = (
    "Miner's Quest",
    "Miner's Quest II",
    "The Coder's Glitch",
    "The Coder's Glitch II",
    "Coder's Fallacy",
)


def _load_walkthroughs() -> dict:
    return json.loads(QUEST_WALKTHROUGHS.read_text())


def test_game_knowledge_lists_core3_and_blocked_quests():
    # Post-shrink (r11 teacher prompt): game_knowledge.md carries only the
    # Core-3 quick-reference + the off-limits list. The full completable
    # catalog now lives in quest_walkthroughs.json (served on demand via
    # query_quest) and is covered by
    # test_quest_walkthroughs_use_canonical_names_and_cover_all_quests.
    text = GAME_KNOWLEDGE.read_text()
    for quest in ("Foresting", "Rick's Roll", "Herbalist's Desperation"):
        assert quest in text, f"{quest} (Core 3) missing from game_knowledge.md"
    # game_knowledge.md abbreviates the Coder chain on a single line
    # ("The Coder's Glitch / Glitch II / Coder's Fallacy"), so check only
    # the leading prefixes that are guaranteed unique.
    coder_aliases = {
        "The Coder's Glitch II": "Glitch II",
        "Coder's Fallacy": "Coder's Fallacy",
    }
    for quest in BLOCKED_QUESTS:
        needle = coder_aliases.get(quest, quest)
        assert needle in text, f"{quest!r} (needle={needle!r}) missing from off-limits section"


def test_game_knowledge_includes_key_runtime_truths():
    # Post-shrink: per-quest runtime truths (Scientist's→Alchemy, Ice Knight
    # coords, Fletching knife, etc.) moved into quest_walkthroughs.json and are
    # covered by test_high_impact_quest_fields_are_grounded. game_knowledge.md
    # now keeps only the always-true, quest-agnostic facts the agent needs in
    # the always-on window.
    text = GAME_KNOWLEDGE.read_text()
    required_snippets = (
        "Starter kit",            # starter inventory enumerated
        "Reward strings lie",     # liar-quest caveat → trust query_quest
        "Foraging 1→5",           # the single grind that unlocks Herbalist ingredients
        "Inventory = 25 slots",   # inventory cap
        "OFF-LIMITS",             # broken-quest section present
    )
    for snippet in required_snippets:
        assert snippet in text, f"Missing runtime truth: {snippet}"


def test_quest_walkthroughs_use_canonical_names_and_cover_all_quests():
    data = _load_walkthroughs()
    assert set(data.keys()) == set(COMPLETABLE_QUESTS + BLOCKED_QUESTS)
    assert data["Tutorial"]["name"] == "Tutorial"
    assert "Welcome to Kaetram" not in QUEST_WALKTHROUGHS.read_text()


def test_quest_walkthrough_statuses_match_current_tree_truth():
    data = _load_walkthroughs()
    assert data["Tutorial"]["status"] == "auto_completed"
    # All BLOCKED_QUESTS must be marked off-limits with a blocked_reason.
    for quest in BLOCKED_QUESTS:
        assert data[quest]["status"] == "off-limits", (
            f"{quest}: expected status='off-limits', got {data[quest]['status']!r}"
        )
        assert data[quest].get("blocked_reason"), (
            f"{quest}: blocked_reason missing or empty"
        )
    # Quests that game_knowledge lists as completable must NOT be off-limits.
    for quest in COMPLETABLE_QUESTS:
        if quest == "Tutorial":
            continue
        assert data[quest]["status"] != "off-limits", (
            f"{quest} marked off-limits but game_knowledge lists it as completable"
        )


def test_high_impact_quest_fields_are_grounded():
    data = _load_walkthroughs()

    # On-start unlocks (still load-bearing for the agent's quest-acceptance heuristics).
    assert data["Scientist's Potion"]["unlocks"]["on_start"] == ["Alchemy interface"]

    # Anvil's Echoes — actual_rewards drift: caveat says `smithingboots`
    # (live patched reward) but the field still reports `bronzeboots`. Pin
    # the current field value here so any future flip surfaces; bump this
    # assertion to `smithingboots` once the field is corrected.
    assert data["Anvil's Echoes"]["actual_rewards"] == ["bronzeboots"]
    assert "smithingboots" in " ".join(data["Anvil's Echoes"]["reward_caveats"]), (
        "Anvil's Echoes caveat must mention smithingboots (live reward fix)"
    )

    assert data["Clam Chowder"]["crafting_chain"]["clamchowder"] == (
        "clamobject x1 + potato x1 + bowlsmall x1"
    )
    assert "Ice Knight at (808,813)" in " ".join(data["Ancient Lands"]["stage_summary"])

    # Herbalist NPC name parity — must be the literal `interact_npc(npc_name=...)`
    # string, not the friendlier "Herbalist" label.
    herbalist_npc = data["Herbalist's Desperation"]["npc"]
    assert herbalist_npc.startswith("Herby Mc. Herb"), (
        "Herbalist npc field must be 'Herby Mc. Herb' (matches "
        "`interact_npc(npc_name=...)` string the agent passes); "
        f"got {herbalist_npc!r}"
    )

    # Rick's Roll reward must be the actual gold payout, not the in-game
    # flavor text (the Cooking-XP popup is a known typo per the walkthrough).
    assert "1987" in " ".join(
        str(v) for v in data["Rick's Roll"]["actual_rewards"]
    ), "Rick's Roll actual_rewards must include the 1987 gold payout"


def test_system_prompt_routes_off_catalog_and_uses_query_quest_proactively():
    text = SYSTEM_PROMPT.read_text()
    # system.md routes via `game_knowledge` → PRIMARY OBJECTIVE: the 3-quest
    # benchmark first, other non-off-limits quests after, off-limits ones never.
    assert "3-quest Kaetram benchmark" in text
    assert "non-off-limits" in text
    assert "OFF-LIMITS" in text
    assert "accept_quest_offer=True" in text
    # query_quest is the canonical pre-acceptance gate check.
    assert "query_quest" in text
    assert "live_gate_status" in text
    # Tool-surface anchors present in the compact tool notes. (buy_item's full
    # schema arrives via the native tools= spec rather than the prompt table, so
    # it is not asserted here — it is not on the Core-3 critical path.)
    assert "chop" in text            # set_attack_style options
    assert "craft_item" in text
    assert "interact_npc" in text
