"""Single source of truth for the canonical Kaetram benchmark start."""
from __future__ import annotations

from typing import Any


STARTER_INVENTORY = [
    {"slot": 0, "key": "bronzeaxe", "count": 1},
    {"slot": 1, "key": "knife", "count": 1},
    {"slot": 2, "key": "fishingpole", "count": 1},
    {"slot": 3, "key": "coppersword", "count": 1},
    {"slot": 4, "key": "woodenbow", "count": 1},
]
CANONICAL_INITIAL_STATE = {
    "pos": {"x": 328, "y": 892},
    "stats": {"hp": 69, "max_hp": 69, "level": 1, "xp": 0},
    "equipment": {},
    "skills": {},
    "inventory": STARTER_INVENTORY,
    "active_quests": [],
    "finished_quests": ["Miner's Quest"],
    "is_dead": False,
    "indoors": False,
}
CANONICAL_DB_QUESTS = [
    {
        "key": "minersquest",
        "stage": 2,
        "subStage": 0,
        "completedSubStages": [],
    },
]


def seed_canonical_player(username: str, *, db_name: str) -> dict[str, Any]:
    """Create the exact fresh player state used by recovered headline runs."""
    from bench.seed import STARTER_KIT, seed_player

    seeded = seed_player(
        username,
        position=(328, 892),
        hit_points=69,
        mana=20,
        inventory=list(STARTER_KIT),
        bank=[],
        equipment=[],
        quests=CANONICAL_DB_QUESTS,
        achievements=[],
        skills=[],
        statistics={},
        db_name=db_name,
    )
    return {
        "schema_version": "kaetram-canonical-start-receipt-v1",
        "username": username.lower(),
        "database": db_name,
        "expected_first_observation": CANONICAL_INITIAL_STATE,
        "seeded_documents": sorted(
            key
            for key, value in seeded.items()
            if key not in {"username", "player_info"} and value is not None
        ),
    }


def initial_state_projection(payload: dict) -> dict:
    """Select persistent fields that distinguish a clean benchmark player."""
    inventory = [
        {
            "slot": item.get("slot"),
            "key": item.get("key"),
            "count": item.get("count"),
        }
        for item in payload.get("inventory", [])
        if isinstance(item, dict)
    ]
    inventory.sort(key=lambda item: (item["slot"] is None, item["slot"]))
    finished = [
        quest.get("name")
        for quest in payload.get("finished_quests", [])
        if isinstance(quest, dict)
    ]
    return {
        "pos": payload.get("pos"),
        "stats": payload.get("stats"),
        "equipment": payload.get("equipment"),
        "skills": payload.get("skills"),
        "inventory": inventory,
        "active_quests": payload.get("active_quests"),
        "finished_quests": finished,
        "is_dead": payload.get("is_dead"),
        "indoors": payload.get("indoors"),
    }


def state_mismatches(
    actual: dict,
    expected: dict = CANONICAL_INITIAL_STATE,
) -> list[dict]:
    """Return stable field-level differences for audit and launch gates."""
    return [
        {"field": field, "expected": expected[field], "actual": actual.get(field)}
        for field in expected
        if actual.get(field) != expected[field]
    ]
