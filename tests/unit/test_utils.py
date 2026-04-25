"""Unit tests for mcp_server.utils — pure Python functions, no browser needed."""

import pytest

from mcp_server.utils import (
    NPC_STORE_KEYS,
    PRODUCTION_SKILL_ALIASES,
    build_quest_query_response,
    check_shop_visibly_open,
    compact_shop_ui,
    normalize_production_skill,
    normalize_quest_name,
    resolve_quest_name,
)


# ── Production skill normalization ───────────────────────────────────────────

class TestNormalizeProductionSkill:
    def test_canonical_names(self):
        assert normalize_production_skill("cooking") == "cooking"
        assert normalize_production_skill("smithing") == "smithing"
        assert normalize_production_skill("smelting") == "smelting"

    def test_aliases(self):
        assert normalize_production_skill("cook") == "cooking"
        assert normalize_production_skill("smith") == "smithing"
        assert normalize_production_skill("smelt") == "smelting"
        assert normalize_production_skill("brew") == "alchemy"
        assert normalize_production_skill("fletch") == "fletching"
        assert normalize_production_skill("chisel") == "chiseling"

    def test_case_insensitive(self):
        assert normalize_production_skill("COOK") == "cooking"
        assert normalize_production_skill("Smithing") == "smithing"

    def test_whitespace(self):
        assert normalize_production_skill("  cook  ") == "cooking"

    def test_unknown(self):
        assert normalize_production_skill("woodcutting") == ""
        assert normalize_production_skill("") == ""
        assert normalize_production_skill(None) == ""

    def test_all_aliases_resolve(self):
        for alias, canonical in PRODUCTION_SKILL_ALIASES.items():
            assert normalize_production_skill(alias) == canonical


# ── Quest name resolution ────────────────────────────────────────────────────

MOCK_QUEST_DATA = {
    "sorcery": {"name": "Sorcery and Stuff"},
    "scavenger": {"name": "Scavenger Quest"},
    "royaldrama": {"name": "Royal Drama"},
    "minersquest1": {"name": "Miner's Quest"},
    "minersquest2": {"name": "Miner's Quest 2"},
}


class TestNormalizeQuestName:
    def test_basic(self):
        assert normalize_quest_name("Sorcery and Stuff") == "sorcery and stuff"

    def test_special_chars(self):
        assert normalize_quest_name("Miner's Quest") == "miner s quest"

    def test_empty(self):
        assert normalize_quest_name("") == ""
        assert normalize_quest_name(None) == ""


class TestResolveQuestName:
    def test_exact_key(self):
        matched, err = resolve_quest_name("sorcery", MOCK_QUEST_DATA)
        assert matched == "sorcery"
        assert err is None

    def test_exact_display_name(self):
        matched, err = resolve_quest_name("Royal Drama", MOCK_QUEST_DATA)
        assert matched == "royaldrama"
        assert err is None

    def test_substring_match(self):
        matched, err = resolve_quest_name("scavenger", MOCK_QUEST_DATA)
        assert matched == "scavenger"
        assert err is None

    def test_ambiguous_returns_error(self):
        matched, err = resolve_quest_name("miner", MOCK_QUEST_DATA)
        assert matched is None
        assert err is not None
        assert "Ambiguous" in err.get("error", "")
        assert "minersquest1" in err.get("matches", [])
        assert "minersquest2" in err.get("matches", [])

    def test_no_match(self):
        matched, err = resolve_quest_name("nonexistent", MOCK_QUEST_DATA)
        assert matched is None
        assert err is not None
        assert "No quest matching" in err.get("error", "")

    def test_empty_query(self):
        matched, err = resolve_quest_name("", MOCK_QUEST_DATA)
        assert matched is None
        assert "empty" in err.get("error", "").lower()


# ── Quest response builder ───────────────────────────────────────────────────

class TestBuildQuestQueryResponse:
    def test_basic_structure(self):
        quest = {"name": "Test Quest", "status": "ready", "npc": "TestNPC"}
        result = build_quest_query_response("testquest", quest)
        assert result["name"] == "Test Quest"
        assert result["matched_name"] == "testquest"
        assert result["status"] == "ready"
        assert result["npc"] == "TestNPC"

    def test_blocked_adds_skip(self):
        quest = {"name": "Blocked Quest", "status": "blocked"}
        result = build_quest_query_response("blocked", quest)
        assert result.get("skip_recommended") is True

    def test_defaults(self):
        result = build_quest_query_response("empty", {})
        assert result["status"] == "unknown"
        assert result["requirements"] == {}


# ── Shop UI helpers ──────────────────────────────────────────────────────────

class TestCompactShopUi:
    def test_none_input(self):
        result = compact_shop_ui(None)
        assert result["type"] is None
        assert result["shop_ready"] is None

    def test_empty_dict(self):
        result = compact_shop_ui({})
        assert result["type"] is None

    def test_valid_shop(self):
        ui = {
            "type": "shop",
            "shop": {"ready": True, "visible": True, "store_key": "forester",
                     "has_store": True, "selectedBuyIndex": 0, "item_entries": [1, 2, 3, 4, 5],
                     "debug": {"any_visible_dom_storeish": True, "dom_store_text": "items"}},
        }
        result = compact_shop_ui(ui)
        assert result["type"] == "shop"
        assert result["shop_ready"] is True
        assert result["store_key"] == "forester"
        assert len(result["item_entries"]) == 4  # truncated to 4


class TestCheckShopVisiblyOpen:
    def test_none(self):
        assert check_shop_visibly_open(None) is False

    def test_empty(self):
        assert check_shop_visibly_open({}) is False

    def test_ready(self):
        assert check_shop_visibly_open({"shop": {"ready": True}}) is True

    def test_visible(self):
        assert check_shop_visibly_open({"shop": {"visible": True}}) is True

    def test_container_visible(self):
        assert check_shop_visibly_open({"shop": {"containerVisible": True}}) is True

    def test_dom_visible(self):
        assert check_shop_visibly_open({"shop": {"debug": {"any_visible_dom_storeish": True}}}) is True


# ── NPC store keys ───────────────────────────────────────────────────────────

class TestNpcStoreKeys:
    def test_known_npcs_have_keys(self):
        assert "forester" in NPC_STORE_KEYS
        assert "miner" in NPC_STORE_KEYS
        assert "babushka" in NPC_STORE_KEYS
        assert "clerk" in NPC_STORE_KEYS

    def test_values_are_strings(self):
        for npc, key in NPC_STORE_KEYS.items():
            assert isinstance(key, str), f"{npc} has non-string store key: {key}"
