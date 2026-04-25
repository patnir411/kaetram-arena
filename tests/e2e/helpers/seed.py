"""Direct-pymongo player seeding for KaetramBench scenarios.

Writes into the already-running `kaetram-mongo` container (port 27017, database
`kaetram_devlopment`). This avoids needing the arena REST helper or a second
Kaetram server on the e2emcp port range.

Collection schemas mirror what Kaetram-Open's server expects on login —
derived from `packages/common/database/mongodb/creator.ts` and the arena
helper at kaetram-arena/tests/mcp_e2e/helpers/seed.py.

Key design point: we ALWAYS insert a finished tutorial quest (stage 16) so the
server's `applyTutorialBypass` doesn't override our seeded spawn coordinates.
Without this, seeded players snap back to the Programmer's house every time.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

from pymongo import MongoClient

DEFAULT_MONGO_URI = os.environ.get("KAETRAM_MONGO_URI", "mongodb://127.0.0.1:27017")
DEFAULT_DB_NAME = os.environ.get("KAETRAM_MONGO_DB", "kaetram_devlopment")

# bcrypt hash for plaintext "test" — Kaetram's login compares via bcryptjs.
# Verified against packages/common/database/mongodb/mongodb.ts:92.
FIXED_BCRYPT_HASH = "$2a$10$C78OFhflOeBZOXhGo7XHQ.8d9FF5xAjRBrVjxDm.b6.WmgGLgghJG"
DEFAULT_PASSWORD = "test"

ALL_COLLECTIONS: tuple[str, ...] = (
    "player_info",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
)

# Items that are NOT stackable in Kaetram (type: "object" in items.json).
# Seeding `{key: ..., count: N}` in a single slot for these items is a lie —
# the server reads the slot as a SINGLE item and silently ignores the count.
# Quest turn-ins that check `hasAllItems(count>=N)` will never fire until
# the count is represented as N *separate slots*.
#
# This list is explicit rather than dynamically loaded to avoid coupling the
# test suite to Kaetram-Open's filesystem. Add entries as new tests need them.
NON_STACKABLE_KEYS: frozenset[str] = frozenset({
    "logs",           # Foresting, Scavenger chain
    "bluelily",       # Herbalist's Desperation
    "tomato",         # Scavenger, Herbalist's
    "strawberry",     # Scavenger
    "paprika",        # Herbalist's
    "mushroom1",      # Arts and Crafts
    "bowlsmall",      # Arts and Crafts, Clam Chowder
    "bowlmedium",     # Arts and Crafts
    "stew",           # Arts and Crafts
    "clamobject",     # Clam Chowder
    "clamchowder",    # Clam Chowder
    "beryl",          # Arts and Crafts
    "berylpendant",   # Arts and Crafts
    "string",         # Scavenger, Arts and Crafts
    "cd",             # Desert Quest
    "seaweedroll",    # Rick's Roll
    "rawshrimp",      # Rick's Roll
    "cookedshrimp",   # Rick's Roll
    "nisocore",       # Miner's Quest
    "coal",           # Mining / smelting ingredients
    "tinore",         # Miner's Quest II / smelting ingredients
    "copperore",      # Miner's Quest II / smelting ingredients
    "bronzeore",      # Mining / smelting ingredients
    "tinbar",         # Miner's Quest II
    "copperbar",      # Miner's Quest II
    "bronzebar",      # Miner's Quest II / smithing outputs
    "bead",           # Sorcery and Stuff
    "icesword",       # Ancient Lands
    "snowpotion",     # Ancient Lands
    "apple",          # edible, sometimes wanted stackable for eat_food seeds
    "stick",          # Fletching
})

TUTORIAL_FINISHED_QUEST = {
    "key": "tutorial",
    "stage": 16,
    "subStage": 0,
    "completedSubStages": [],
}

# The five weapons the tutorial bypass grants. Mirrors
# Kaetram-Open/packages/server/src/game/entity/character/player/quests.ts:213.
# Every preset + test seed should include these so the agent's starter
# inventory matches what a real post-tutorial player has.
STARTER_KIT: tuple[dict[str, Any], ...] = (
    {"index": 0, "key": "bronzeaxe",   "count": 1},
    {"index": 1, "key": "knife",       "count": 1},
    {"index": 2, "key": "fishingpole", "count": 1},
    {"index": 3, "key": "coppersword", "count": 1},
    {"index": 4, "key": "woodenbow",   "count": 1},
)

# Modules.Skills enum from packages/common/network/modules.ts:215-235.
# Accepting names in seed payloads keeps presets human-readable while
# still writing the {type:int, experience:int} shape that
# skills.ts:load() actually reads. Wrong-name seeds used to silently
# produce a Lv-1 character regardless of what the preset claimed.
_SKILL_NAME_TO_TYPE: dict[str, int] = {
    "lumberjacking": 0,
    "accuracy":      1,
    "archery":       2,
    "health":        3,
    "magic":         4,
    "mining":        5,
    "strength":      6,
    "defense":       7,
    "fishing":       8,
    "cooking":       9,
    "fletching":    10,
    "crafting":     11,
    "foraging":     12,
    "eating":       13,
    "loitering":    14,
    "alchemy":      15,
    "smithing":     16,
    # 17 Chiseling and 18 Smelting are routing markers per GAME_SYSTEMS §2 —
    # XP actually lands in crafting / smithing respectively, so omit them.
}


def _normalize_skills(skills: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert preset-friendly {name: 'accuracy', experience: N} rows into
    the {type: 1, experience: N} shape Kaetram's skills.ts:load() expects.

    Accepts mixed input — rows that already specify `type` pass through
    untouched. Unknown names log a warning and are dropped so typos don't
    silently waste a seed.
    """
    if not skills:
        return []
    normalized: list[dict[str, Any]] = []
    for row in skills:
        if not isinstance(row, dict):
            continue
        out = dict(row)
        if "type" not in out:
            name = (out.pop("name", "") or "").strip().lower()
            skill_type = _SKILL_NAME_TO_TYPE.get(name)
            if skill_type is None:
                print(
                    f"[seed] warning: dropping skill with unknown name {name!r} "
                    f"(expected one of {sorted(_SKILL_NAME_TO_TYPE)})",
                    flush=True,
                )
                continue
            out["type"] = skill_type
        # `level` is derived from experience by Kaetram, ignore if passed
        out.pop("level", None)
        out.pop("name", None)  # defensive — don't leak unknown keys into Mongo
        out["experience"] = int(out.get("experience", 0) or 0)
        normalized.append(out)
    return normalized


def _client(uri: str = DEFAULT_MONGO_URI) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=3000)


def _upsert(db, collection: str, username: str, body: dict[str, Any]) -> None:
    """Upsert a document keyed by lowercase username.

    Kaetram stores usernames lowercase; we match that convention so the server
    finds our row on login.
    """
    body = dict(body)
    body["username"] = username.lower()
    db[collection].update_one(
        {"username": body["username"]},
        {"$set": body},
        upsert=True,
    )


def _default_player_info(username: str, x: int, y: int, **overrides: Any) -> dict[str, Any]:
    """Minimum viable player_info doc. Fields mirror arena helper so Kaetram's
    loader doesn't default to tutorial spawn."""
    return {
        "username": username.lower(),
        "password": FIXED_BCRYPT_HASH,
        "email": f"{username.lower()}@kaetrambench.test",
        "x": x,
        "y": y,
        "userAgent": "kaetrambench",
        "rank": 0,
        "poison": {"type": -1, "remaining": -1},
        "effects": {},
        "hitPoints": 100,
        "mana": 20,
        "orientation": 1,
        "ban": 0,
        "jail": 0,
        "mute": 0,
        "lastWarp": 0,
        "mapVersion": -1,
        "regionsLoaded": [],
        "friends": [],
        "lastServerId": 1,
        "lastAddress": "127.0.0.1",
        "lastGlobalChat": 0,
        "guild": "",
        "pet": "",
        **overrides,
    }


def _inventory_slots(items: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Build a 25-slot inventory array. Missing slots filled with empty placeholders.

    Non-stackable items with `count > 1` are auto-expanded into consecutive
    slots starting at the requested index — the game ignores `count` on
    non-stackables, so compact seeds like `{key: "logs", count: 10}` would
    otherwise produce a single log in-game. Quest turn-ins that require
    `count >= N` would silently never fire.

    Stackable items (default: anything not in NON_STACKABLE_KEYS) occupy a
    single slot with their count intact.
    """
    slots: list[dict[str, Any]] = [
        {"index": i, "key": "", "count": 0, "enchantments": {}} for i in range(25)
    ]

    # Expand input items into a list of {index, key, count, enchantments} — one
    # entry per occupied slot. Non-stackable items get split across slots.
    pending: list[tuple[int, str, int, dict]] = []  # (index, key, count, enchant)
    for raw in items or []:
        idx = int(raw.get("index", 0))
        key = raw.get("key", "")
        count = int(raw.get("count", 0) or 0)
        enchant = raw.get("enchantments", {})
        if not key:
            continue
        if key in NON_STACKABLE_KEYS and count > 1:
            # Expand across `count` consecutive slots, each with count=1.
            for offset in range(count):
                pending.append((idx + offset, key, 1, enchant))
        else:
            pending.append((idx, key, count, enchant))

    # Auto-displace collisions forward to the next empty slot so expansions
    # don't silently overwrite previously-placed items.
    used: set[int] = set()
    for entry in pending:
        target, key, count, enchant = entry
        while target < 25 and target in used:
            target += 1
        if target >= 25:
            continue  # inventory full — drop silently (tests should not overfill)
        used.add(target)
        slots[target] = {
            "index": target, "key": key, "count": count, "enchantments": enchant,
        }
    return slots


def _with_default_tutorial(quests: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Insert finished tutorial quest if caller didn't. Critical — without this
    Kaetram's applyTutorialBypass overrides the seeded (x, y)."""
    merged: list[dict[str, Any]] = []
    seen_tutorial = False
    for q in quests or []:
        merged.append(dict(q))
        if q.get("key") == TUTORIAL_FINISHED_QUEST["key"]:
            seen_tutorial = True
    if not seen_tutorial:
        merged.insert(0, dict(TUTORIAL_FINISHED_QUEST))
    return merged


def seed_player(
    username: str,
    *,
    position: Sequence[int] = (15, 15),
    hit_points: int = 100,
    mana: int = 20,
    inventory: Iterable[dict[str, Any]] | None = None,
    bank: Iterable[dict[str, Any]] | None = None,
    equipment: Iterable[dict[str, Any]] | None = None,
    quests: Iterable[dict[str, Any]] | None = None,
    achievements: Iterable[dict[str, Any]] | None = None,
    skills: Iterable[dict[str, Any]] | None = None,
    statistics: dict[str, Any] | None = None,
    player_info_overrides: dict[str, Any] | None = None,
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
) -> dict[str, Any]:
    """Upsert every provided player document. Returns the exact seeded state
    dict so the caller can persist it alongside run artifacts.

    `inventory` semantics:
      - `None` (default) → `STARTER_KIT` (5 starter weapons; matches what a
        real player has after the tutorial — see `applyTutorialBypass()` in
        Kaetram-Open `quests.ts`). This is the "post-tutorial vanilla" seed
        that production restarts and most tests want.
      - `[]` or `()`     → explicitly empty (use this when a test needs to
        verify behavior on an empty inventory).
      - `[items, ...]`   → exactly those items (overrides the kit).

    Tests that call `seed_player(name, ...)` without specifying `inventory=`
    will now receive the starter kit. This restores parity with the
    `prompts/game_knowledge.md` claim that "starter kit is already in your
    inventory" — previously the prompt was lying because the default was
    None → empty 25 slots.
    """
    x, y = int(position[0]), int(position[1])
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        info = _default_player_info(
            username, x, y,
            hitPoints=hit_points, mana=mana,
            **(player_info_overrides or {}),
        )
        _upsert(db, "player_info", username, info)

        # Default to STARTER_KIT when caller didn't specify. Distinguished
        # from explicit `inventory=[]` which means "empty by intent."
        inv_items = STARTER_KIT if inventory is None else inventory
        inv_slots = _inventory_slots(inv_items)
        _upsert(db, "player_inventory", username, {"slots": inv_slots})

        if bank is not None:
            _upsert(db, "player_bank", username, {"slots": _inventory_slots(bank)})

        if equipment is not None:
            _upsert(db, "player_equipment", username, {"equipments": list(equipment)})

        merged_quests = _with_default_tutorial(quests)
        _upsert(db, "player_quests", username, {"quests": merged_quests})

        if achievements is not None:
            _upsert(db, "player_achievements", username, {"achievements": list(achievements)})

        if skills is not None:
            normalized_skills = _normalize_skills(skills)
            _upsert(db, "player_skills", username, {"skills": normalized_skills})

        if statistics is not None:
            _upsert(db, "player_statistics", username, dict(statistics))

        return {
            "username": username.lower(),
            "player_info": info,
            "inventory_slots": inv_slots,
            "bank_slots": _inventory_slots(bank) if bank is not None else None,
            "equipment": list(equipment) if equipment is not None else None,
            "quests": merged_quests,
            "achievements": list(achievements) if achievements is not None else None,
            "skills": normalized_skills if skills is not None else None,
            "statistics": dict(statistics) if statistics is not None else None,
        }
    finally:
        client.close()


def cleanup_player(
    username: str,
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
) -> dict[str, int]:
    """Delete the player's docs from every player_* collection. Returns the
    per-collection deleted count."""
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        result: dict[str, int] = {}
        for coll in ALL_COLLECTIONS:
            r = db[coll].delete_many({"username": username.lower()})
            result[coll] = int(r.deleted_count)
        return result
    finally:
        client.close()


def snapshot_player(
    username: str,
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
) -> dict[str, Any]:
    """Read-only dump of all 9 collections for this username. Strips the
    `_id` BSON ObjectId so the result is JSON-serializable."""
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        out: dict[str, Any] = {"username": username.lower()}
        for coll in ALL_COLLECTIONS:
            doc = db[coll].find_one({"username": username.lower()}, {"_id": 0})
            out[coll] = doc
        return out
    finally:
        client.close()


# --- Convenience metrics derived from a snapshot ---------------------------

def summarize_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """Flatten a snapshot_player() dict into a handful of scalars the runner
    uses for before/after diffing. Robust to missing collections."""
    info = snap.get("player_info") or {}
    stats = snap.get("player_statistics") or {}
    skills_doc = snap.get("player_skills") or {}
    quests_doc = snap.get("player_quests") or {}
    inv_doc = snap.get("player_inventory") or {}

    mob_kills = stats.get("mobKills") or {}
    kills_total = sum(int(v) for v in mob_kills.values() if isinstance(v, (int, float)))

    xp_total = 0
    if isinstance(skills_doc.get("skills"), list):
        for skill in skills_doc["skills"]:
            xp_total += int(skill.get("experience", 0) or 0)

    quest_state: dict[str, dict[str, Any]] = {}
    if isinstance(quests_doc.get("quests"), list):
        for q in quests_doc["quests"]:
            key = q.get("key")
            if not key:
                continue
            quest_state[key] = {
                "stage": int(q.get("stage", 0) or 0),
                "sub_stage": int(q.get("subStage", 0) or 0),
            }

    items: list[dict[str, Any]] = []
    if isinstance(inv_doc.get("slots"), list):
        for slot in inv_doc["slots"]:
            if slot.get("key"):
                items.append({
                    "index": slot.get("index"),
                    "key": slot.get("key"),
                    "count": slot.get("count", 0),
                })

    return {
        "position": {"x": info.get("x"), "y": info.get("y")} if info else None,
        "hit_points": info.get("hitPoints"),
        "kills_total": kills_total,
        "xp_total": xp_total,
        "quests": quest_state,
        "inventory": items,
    }
