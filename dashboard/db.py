"""Direct MongoDB reader for Kaetram game server player data.

Queries the game's MongoDB database for authoritative player state,
bypassing the slow and fragile session log parsing approach.
Falls back gracefully (returns None) when the database is unavailable.
"""

import json
import math
import os
import time
import logging

from dashboard.constants import MONGO_HOST, MONGO_PORT, MONGO_DB

log = logging.getLogger(__name__)

# ── Kaetram game constants (from packages/common/network/modules.ts) ──

# Skills enum → name mapping
SKILL_NAMES = {
    0: "Lumberjacking",
    1: "Accuracy",
    2: "Archery",
    3: "Health",
    4: "Magic",
    5: "Mining",
    6: "Strength",
    7: "Defense",
    8: "Fishing",
    9: "Cooking",
    10: "Smithing",
    11: "Crafting",
    13: "Fletching",
    15: "Foraging",
    16: "Eating",
    17: "Loitering",
    18: "Alchemy",
}

# Combat skills (used for getCombatLevel)
COMBAT_SKILL_TYPES = {1, 2, 3, 4, 6, 7}  # Accuracy, Archery, Health, Magic, Strength, Defense

# Equipment slot enum → name mapping
EQUIPMENT_NAMES = {
    0: "Helmet", 1: "Pendant", 2: "Arrows", 3: "Chestplate",
    4: "Weapon", 5: "Shield", 6: "Ring", 7: "Armour Skin",
    8: "Weapon Skin", 9: "Legplates", 10: "Cape", 11: "Boots",
}

MAX_LEVEL = 120

# Cache TTL — avoid hammering MongoDB on every dashboard poll
_CACHE_TTL = 3  # seconds


# ── Quest & Achievement definitions ──
# MongoDB does NOT store stageCount or quest names — those are computed
# at server runtime from JSON definition files. We load them here so we
# can accurately determine quest completion status (isFinished = stage >= stageCount).

def _load_quest_definitions() -> dict[str, dict]:
    """Load quest definitions from Kaetram-Open to get name + stageCount.

    Returns dict keyed by quest key, e.g.:
        {"foresting": {"name": "Foresting", "stageCount": 3, "description": "..."}, ...}
    """
    quest_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "Kaetram-Open", "packages", "server", "data", "quests"
    )
    # Also try absolute path as fallback
    if not os.path.isdir(quest_dir):
        quest_dir = "/home/patnir41/projects/Kaetram-Open/packages/server/data/quests"

    defs = {}
    if not os.path.isdir(quest_dir):
        log.warning(f"Quest definitions not found at {quest_dir}")
        return defs

    for filename in os.listdir(quest_dir):
        if not filename.endswith(".json"):
            continue
        key = filename[:-5]
        try:
            with open(os.path.join(quest_dir, filename)) as f:
                raw = json.load(f)
            defs[key] = {
                "name": raw.get("name", key.replace("_", " ").title()),
                "stageCount": len(raw.get("stages", {})),
                "description": raw.get("description", ""),
                "difficulty": raw.get("difficulty", ""),
            }
        except Exception as e:
            log.warning(f"Failed to load quest definition {filename}: {e}")

    log.info(f"Loaded {len(defs)} quest definitions")
    return defs


def _load_achievement_definitions() -> dict[str, dict]:
    """Load achievement definitions from Kaetram-Open to get names.

    Returns dict keyed by achievement key, e.g.:
        {"firstrock": {"name": "First Rock", "description": "Mine your first rock!"}, ...}
    """
    ach_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "Kaetram-Open", "packages", "server", "data", "achievements.json"
    )
    if not os.path.isfile(ach_path):
        ach_path = "/home/patnir41/projects/Kaetram-Open/packages/server/data/achievements.json"

    if not os.path.isfile(ach_path):
        log.warning(f"Achievement definitions not found at {ach_path}")
        return {}

    try:
        with open(ach_path) as f:
            raw = json.load(f)
        defs = {}
        for key, data in raw.items():
            defs[key] = {
                "name": data.get("name", key.replace("_", " ").title()),
                "description": data.get("description", ""),
            }
        log.info(f"Loaded {len(defs)} achievement definitions")
        return defs
    except Exception as e:
        log.warning(f"Failed to load achievement definitions: {e}")
        return {}


# Load at module init (one-time cost, ~21 quest files + 1 achievements file)
QUEST_DEFS = _load_quest_definitions()
ACHIEVEMENT_DEFS = _load_achievement_definitions()


def _build_xp_table():
    """Build the Kaetram XP→level table (RuneScape formula).

    From packages/server/src/info/loader.ts:
        LevelExp[0] = 0
        for i in 1..MAX_LEVEL-1:
            points = floor(0.25 * floor(i + 300 * 2^(i/7)))
            LevelExp[i] = points + LevelExp[i-1]
    """
    table = [0] * MAX_LEVEL
    for i in range(1, MAX_LEVEL):
        points = int(0.25 * int(i + 300 * math.pow(2, i / 7.0)))
        table[i] = points + table[i - 1]
    return table


_LEVEL_EXP = _build_xp_table()


def _exp_to_level(experience: int) -> int:
    """Convert XP to level using Kaetram's table (formulas.ts expToLevel)."""
    if experience < 0:
        return 1
    for i in range(1, MAX_LEVEL):
        if experience < _LEVEL_EXP[i]:
            return i
    return MAX_LEVEL


def _get_max_hp(health_level: int) -> int:
    """Max HP = 39 + health_level * 30 (formulas.ts:365)."""
    return 39 + health_level * 30


def _get_max_mana(magic_level: int) -> int:
    """Max Mana = 20 + magic_level * 24 (formulas.ts:375)."""
    return 20 + magic_level * 24


class MongoReader:
    """Lazy-connecting MongoDB reader for player game state."""

    def __init__(self, host: str = MONGO_HOST, port: int = MONGO_PORT, db_name: str = MONGO_DB):
        self._host = host
        self._port = port
        self._db_name = db_name
        self._client = None
        self._db = None
        self._cache: dict[str, tuple[float, dict]] = {}  # username -> (timestamp, state)

    def _connect(self):
        """Lazy connect — only called on first use."""
        if self._client is not None:
            return
        try:
            from pymongo import MongoClient
            self._client = MongoClient(
                self._host, self._port,
                serverSelectionTimeoutMS=2000,
                connectTimeoutMS=2000,
                socketTimeoutMS=3000,
            )
            self._db = self._client[self._db_name]
            # Ping to verify connection
            self._client.admin.command("ping")
        except Exception as e:
            log.warning(f"MongoDB connection failed: {e}")
            self._client = None
            self._db = None

    def _reconnect(self):
        """Force reconnect on failure."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._db = None

    def get_player_state(self, username: str) -> dict | None:
        """Query all player collections and return normalized game state dict.

        Returns None if the database is unavailable or the player doesn't exist.
        """
        # Check cache first
        cached = self._cache.get(username)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]

        self._connect()
        if self._db is None:
            return None

        try:
            state = self._query_player(username)
            if state:
                self._cache[username] = (time.time(), state)
            return state
        except Exception as e:
            log.warning(f"MongoDB query failed for {username}: {e}")
            self._reconnect()
            return None

    def _query_player(self, username: str) -> dict | None:
        """Execute queries against all relevant collections."""
        db = self._db

        # player_info — core info
        info = db["player_info"].find_one(
            {"username": username},
            {"_id": 0, "password": 0, "resetToken": 0}
        )
        if not info:
            return None

        state = {}

        # ── Skills (must compute first — needed for level, maxHP, maxMana) ──
        skill_levels = {}  # type -> level
        skills_doc = db["player_skills"].find_one({"username": username}, {"_id": 0})
        if skills_doc and "skills" in skills_doc:
            skills = []
            for s in skills_doc["skills"]:
                exp = s.get("experience", 0)
                skill_type = s.get("type", -1)
                level = _exp_to_level(exp)
                skill_levels[skill_type] = level
                name = SKILL_NAMES.get(skill_type, f"skill_{skill_type}")
                skills.append({
                    "type": skill_type,
                    "name": name,
                    "experience": exp,
                    "level": level,
                })
            state["skills"] = skills

        # Compute derived stats from skills
        health_level = skill_levels.get(3, 1)   # Health = type 3
        magic_level = skill_levels.get(4, 1)    # Magic = type 4
        max_hp = _get_max_hp(health_level)
        max_mana = _get_max_mana(magic_level)

        # Combat level = 1 + sum(skill.level - 1) for all combat skills
        combat_level = 1
        for skill_type in COMBAT_SKILL_TYPES:
            combat_level += skill_levels.get(skill_type, 1) - 1

        # ── Player stats ──
        state["player_stats"] = {
            "hp": info.get("hitPoints", 0),
            "max_hp": max_hp,
            "mana": info.get("mana", 0),
            "max_mana": max_mana,
            "level": combat_level,
        }

        # ── Player position ──
        state["player_position"] = {
            "x": info.get("x", 0),
            "y": info.get("y", 0),
            "orientation": info.get("orientation", 0),
        }

        # ── Equipment ──
        equip_doc = db["player_equipment"].find_one({"username": username}, {"_id": 0})
        if equip_doc and "equipments" in equip_doc:
            equipment = []
            for e in equip_doc["equipments"]:
                key = e.get("key", "")
                if key:  # Only include equipped slots
                    slot_type = e.get("type", 0)
                    equipment.append({
                        "type": slot_type,
                        "slot": EQUIPMENT_NAMES.get(slot_type, f"slot_{slot_type}"),
                        "key": key,
                        "name": key.replace("_", " ").title(),
                        "count": e.get("count", 1),
                    })
            state["equipment"] = equipment

        # ── Inventory ──
        inv_doc = db["player_inventory"].find_one({"username": username}, {"_id": 0})
        if inv_doc and "slots" in inv_doc:
            inventory = []
            for slot in inv_doc["slots"]:
                key = slot.get("key", "")
                if key:
                    inventory.append({
                        "name": key.replace("_", " ").title(),
                        "key": key,
                        "count": slot.get("count", 1),
                    })
            state["inventory"] = inventory

        # ── Quests ──
        # MongoDB does NOT store stageCount or quest name — we must look them
        # up from the quest definition JSON files loaded at module init.
        # Server logic: isFinished() = stage >= stageCount  (quest.ts:484)
        quest_doc = db["player_quests"].find_one({"username": username}, {"_id": 0})
        if quest_doc and "quests" in quest_doc:
            quests = []
            completed = 0
            total = len(quest_doc["quests"])
            for q in quest_doc["quests"]:
                key = q.get("key", "")
                stage = q.get("stage", 0)
                sub_stage = q.get("subStage", 0)

                # Get authoritative stageCount and name from definitions
                qdef = QUEST_DEFS.get(key, {})
                stage_count = qdef.get("stageCount", 1)
                name = qdef.get("name", key.replace("_", " ").title())
                description = qdef.get("description", "")
                difficulty = qdef.get("difficulty", "")

                # Match server logic: isFinished() = stage >= stageCount
                # Agents skip the tutorial via warp — mark it as done
                started = stage > 0
                finished = stage >= stage_count or key == "tutorial"

                if finished:
                    completed += 1

                status = "DONE" if finished else ("IN PROGRESS" if started else "NEW")

                quests.append({
                    "key": key,
                    "name": name,
                    "description": description,
                    "difficulty": difficulty,
                    "stage": stage,
                    "subStage": sub_stage,
                    "stageCount": stage_count,
                    "started": started,
                    "finished": finished,
                    "status": status,
                })
            state["quests"] = quests
            state["quest_summary"] = {
                "completed": completed,
                "total": total,
                "remaining": total - completed,
            }

        # ── Achievements ──
        ach_doc = db["player_achievements"].find_one({"username": username}, {"_id": 0})
        if ach_doc and "achievements" in ach_doc:
            achievements = []
            ach_completed = 0
            ach_total = len(ach_doc["achievements"])
            for a in ach_doc["achievements"]:
                key = a.get("key", "")
                stage = a.get("stage", 0)
                stage_count = a.get("stageCount", 1)

                # Get name from definitions
                adef = ACHIEVEMENT_DEFS.get(key, {})
                name = adef.get("name", key.replace("_", " ").title())
                description = adef.get("description", "")

                started = stage > 0
                finished = stage >= stage_count and stage > 0

                if finished:
                    ach_completed += 1

                achievements.append({
                    "key": key,
                    "name": name,
                    "description": description,
                    "stage": stage,
                    "stageCount": stage_count,
                    "started": started,
                    "finished": finished,
                })
            state["achievements"] = achievements
            state["achievement_summary"] = {
                "completed": ach_completed,
                "total": ach_total,
                "remaining": ach_total - ach_completed,
            }

        # ── Statistics ──
        stats_doc = db["player_statistics"].find_one(
            {"username": username}, {"_id": 0, "username": 0}
        )
        if stats_doc:
            state["statistics"] = stats_doc

        state["_source"] = "mongodb"
        return state


# Module-level singleton
_reader: MongoReader | None = None


def get_reader() -> MongoReader:
    """Get or create the module-level MongoReader singleton."""
    global _reader
    if _reader is None:
        _reader = MongoReader()
    return _reader
