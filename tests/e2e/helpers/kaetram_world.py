"""Quest + NPC reference constants — source-verified from Kaetram-Open's
PLAYTHROUGH.md and packages/server/data/map/world.json (entities table).

Coordinates were extracted by scanning world.json's `entities` dict (tile
index = y * width + x, width=1152 on the current tree) for each NPC key on
2026-04-20. If NPCs move on a future map rebuild these need to be re-derived.

Usage from a test:

    from .kaetram_world import NPCS, adjacent_to
    seed_player(user, position=adjacent_to('forestnpc'), ...)
"""

from __future__ import annotations

# Canonical NPC key → (x, y) for NPCs that matter to the quest flow.
NPCS: dict[str, tuple[int, int]] = {
    # Mudwich starting area
    "forestnpc":          (216, 114),   # Forester — Foresting quest
    "boxingman":          (166, 114),   # Bike Lyson — achievement (run ability)
    "blacksmith":         (199, 169),   # Anvil's Echoes
    "miner":              (323, 178),   # Miner's Quest + II
    "villagegirl2":       (136, 146),   # Scavenger — Village Girl
    "lavanpc":            (288, 134),   # Dying Soldier — Desert Quest

    # Programmer's house / tutorial remnant
    "royalguard2":        (282, 887),   # Royal Drama
    "king":               (284, 884),   # Royal Pet (delivers 3 books)

    # Under the Sea / beach / fishing zone
    "beachnpc":           (121, 231),   # Bubba — achievement (crabs)
    "sponge":             (52, 310),    # Sea Activities (start)
    "picklenpc":          (691, 838),   # Sea Activities (Sea Cucumber)
    "rick":               (1088, 833),  # Rick's Roll
    "rickgf":             (455, 924),   # Lena — delivery

    # Castle / Aynor region
    "king2":              (1138, 717),  # Royal Drama finale
    "ratnpc":             (1087, 698),  # Royal Drama (sewer Rat)

    # Delivery targets for Royal Pet
    "redbikinigirlnpc":   (294, 489),   # Flaris
    "fisherman":          (324, 318),
    "shepherdboy":        (361, 348),

    # Skill quest givers
    "sorcerer":           (706, 101),   # Sorcery and Stuff
    "scientist":          (763, 666),   # Scientist's Potion (Alchemy unlock)
    "iamverycoldnpc":     (702, 608),   # Babushka — Arts and Crafts (Crafting unlock)
    "herbalist":          (333, 281),   # Herbalist's Desperation
    "oldlady":            (776, 106),   # Scavenger turn-in
    "oldlady2":           (919, 590),   # Clam Chowder turn-in
    "bluebikinigirlnpc":  (676, 359),   # Pretzel — Clam Chowder
    "doctor":            (698, 550),   # Doctor — Clam Chowder
    "picklemob":         (858, 815),   # Sea Activities mob (use attack(), not interact_npc)

    # Endgame
    "ancientmanumentnpc": (415, 294),   # Ancient Lands
    "villagegirl":        (735, 101),   # Wife (Desert Quest turn-in)
}

# Nice display names for assertions — matches the in-game "name" field so
# interact_npc lookups and log readability align.
NPC_DISPLAY_NAMES: dict[str, str] = {
    "forestnpc": "Forester",
    "blacksmith": "Blacksmith",
    "miner": "Miner",
    "villagegirl2": "Village Girl",
    "villagegirl": "Wife",  # same sprite, but internal name is Wife
    "oldlady": "Old Lady",
    "oldlady2": "Old Lady",
    "rick": "Rick",
    "rickgf": "Lena",
    "royalguard2": "Royal Guard",
    "king2": "King",
    "king": "King",
    "sorcerer": "Sorcerer",
    "scientist": "Scientist",
    "iamverycoldnpc": "Babushka",
    "herbalist": "Herby Mc. Herb",
    "sponge": "Sponge",
    "picklenpc": "Sea Cucumber",
    "bluebikinigirlnpc": "Pretzel",
    "ancientmanumentnpc": "Ancient Monument",
    "beachnpc": "Bubba",
    "boxingman": "Bike Lyson",
    "lavanpc": "Dying Soldier",
    "redbikinigirlnpc": "Flaris",
    "fisherman": "Fisherman",
    "shepherdboy": "Shepherd Boy",
    "ratnpc": "Rat",
    "doctor": "Doctor",
    "picklemob": "Sea Cucumber",
}


def adjacent_to(npc_key: str, *, dy: int = 1) -> tuple[int, int]:
    """Return a coord 1 tile south of the given NPC (change dy for another
    side). Enough for interact_npc to auto-walk into range."""
    if npc_key not in NPCS:
        raise KeyError(f"unknown NPC key: {npc_key}")
    x, y = NPCS[npc_key]
    return (x, y + dy)


# Quest metadata — a subset of PLAYTHROUGH.md structured for tests. Each entry
# says where to seed, what starting state the quest should be in, and what
# outcome a successful test expects. Stage 0 = unstarted (dialogue will start
# it); stage >0 = mid-quest (bringing required items should advance it).
QUESTS: dict[str, dict] = {
    "foresting": {
        "npc": "forestnpc",
        "display": "Forester",
        "reward": "ironaxe",
        "stages": 3,
        "accept_requires": [],
        "stage1_turn_in": [{"index": 0, "key": "logs", "count": 10}],
    },
    "desertquest": {
        "npc": "lavanpc",
        "display": "Dying Soldier",
        "reward": "courier only",
        "stages": 3,
    },
    "anvilsechoes": {
        "npc": "blacksmith",
        "display": "Blacksmith",
        "reward": "bronzeboots",
        "stages": 2,
    },
    "royaldrama": {
        "npc": "royalguard2",
        "display": "Royal Guard",
        "reward": "10000 gold",
        "stages": 3,
    },
    "ricksroll": {
        "npc": "rick",
        "display": "Rick",
        "reward": "1987 gold",
        "stages": 4,
    },
    "scavenger": {
        "npc": "villagegirl2",
        "display": "Village Girl",
        "reward": "7500 gold",
        "stages": 3,
    },
    "seaactivities": {
        "npc": "sponge",
        "display": "Sponge",
        "reward": "10000 gold",
        "stages": 7,
    },
    "scientistspotion": {
        "npc": "scientist",
        "display": "Scientist",
        "reward": "alchemy unlock",
        "stages": 1,
    },
    "artsandcrafts": {
        "npc": "iamverycoldnpc",
        "display": "Babushka",
        "reward": "crafting unlock (on start)",
        "stages": 4,
    },
    "minersquest": {
        "npc": "miner",
        "display": "Miner",
        "reward": "—",
        "stages": 2,
    },
    "herbalistdesperation": {
        "npc": "herbalist",
        "display": "Herby Mc. Herb",
        "reward": "hotsauce + 1500 foraging xp",
        "stages": 3,
    },
    "ancientlands": {
        "npc": "ancientmanumentnpc",
        "display": "Ancient Monument",
        "reward": "snowpotion",
        "stages": 2,
    },
    "sorcery": {
        "npc": "sorcerer",
        "display": "Sorcerer",
        "reward": "(broken — staff doesn't exist)",
        "stages": 2,
    },
    "royalpet": {
        "npc": "king",
        "display": "King",
        "reward": "(catpet broken, completion only)",
        "stages": 3,
        "requires_quest": "royaldrama",
        "substage_npcs": ["shepherdboy", "redbikinigirlnpc", "fisherman"],
    },
    "minersquest2": {
        "npc": "miner",
        "display": "Miner",
        "reward": "mining cave access",
        "stages": 3,
        "requires_quest": "minersquest",
        "requires_mining_level": 30,
    },
    "clamchowder": {
        "npc": "bluebikinigirlnpc",
        "display": "Pretzel",
        "reward": "7500 gold",
        "stages": 7,
    },
}
