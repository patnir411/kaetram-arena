"""Canonical NPC + quest reference — source of truth for e2e tests.

Coordinates derived from Kaetram-Open's packages/server/data/map/world.json
`entities` table (tile index = y * width + x, width=1152 on current tree).
Last extracted 2026-04-20 against Kaetram-Open commit 20cac4aac.

Quests metadata derived from Kaetram-Open's PLAYTHROUGH.md. Only "verified
working" quests are listed; broken or skipped quests omitted.

If Kaetram-Open rebuilds its map or renames NPCs, this file is the single
place to update. tests/e2e/game/test_world_npcs.py verifies these constants
are still live in world.json on every run.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# NPCs — canonical (key → (x, y))
# -----------------------------------------------------------------------------

NPCS: dict[str, tuple[int, int]] = {
    # Mudwich starting area
    "forestnpc":          (216, 114),   # Forester — Foresting quest
    "boxingman":          (166, 114),   # Bike Lyson — run-ability achievement
    "blacksmith":         (199, 169),   # Anvil's Echoes
    "miner":              (323, 178),   # Miner's Quest + II
    "villagegirl2":       (136, 146),   # Scavenger — Village Girl
    "lavanpc":            (288, 134),   # Dying Soldier — Desert Quest

    # Programmer's house / tutorial remnant
    "royalguard2":        (282, 887),   # Royal Drama
    "king":               (284, 884),   # Royal Pet
    "coder":              (331, 890),   # Tutorial start

    # Beach / Under-the-Sea
    "beachnpc":           (121, 231),   # Bubba — crabs achievement
    "sponge":             (52, 310),    # Sea Activities
    "picklenpc":          (691, 838),   # Sea Activities — Sea Cucumber
    "picklemob":          (858, 815),   # Sea Activities — picklemob boss (reachability tests use this)
    "rick":               (1088, 833),  # Rick's Roll
    "rickgf":             (455, 924),   # Lena — delivery

    # Castle / Aynor region
    "king2":              (1138, 717),  # Royal Drama finale
    "ratnpc":             (1087, 698),  # Royal Drama sewer rat

    # Royal Pet delivery targets
    "redbikinigirlnpc":   (294, 489),   # Flaris
    "fisherman":          (324, 318),
    "shepherdboy":        (361, 348),

    # Skill quest givers
    "sorcerer":           (706, 101),   # Sorcery and Stuff
    "scientist":          (763, 666),   # Scientist's Potion — Alchemy unlock
    "iamverycoldnpc":     (702, 608),   # Babushka — Arts and Crafts — Crafting unlock
    "herbalist":          (333, 281),   # Herbalist's Desperation
    "oldlady":            (776, 106),   # Scavenger turn-in
    "oldlady2":           (919, 590),   # Clam Chowder turn-in
    "doctor":             (698, 550),   # Clam Chowder intermediate
    "bluebikinigirlnpc":  (676, 359),   # Pretzel — Clam Chowder
    "villagegirl":        (735, 101),   # Wife — Desert Quest turn-in

    # Endgame
    "ancientmanumentnpc": (415, 294),   # Ancient Lands
}

NPC_DISPLAY_NAMES: dict[str, str] = {
    "forestnpc": "Forester",
    "blacksmith": "Blacksmith",
    "miner": "Miner",
    "villagegirl2": "Village Girl",
    "villagegirl": "Village Girl",
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
    "picklemob": "Sea Cucumber",
    "bluebikinigirlnpc": "Pretzel",
    "ancientmanumentnpc": "Ancient Monument",
    "beachnpc": "Bubba",
    "boxingman": "Bike Lyson",
    "lavanpc": "Dying Soldier",
    "redbikinigirlnpc": "Flaris",
    "fisherman": "Fisherman",
    "shepherdboy": "Shepherd Boy",
    "ratnpc": "Rat",
    "coder": "Programmer",
    "doctor": "Doctor",
}


def adjacent_to(npc_key: str, *, dy: int = 1) -> tuple[int, int]:
    """Coord 1 tile south of the given NPC (override dy for a different side).
    Used to seed test players within interact_npc range."""
    if npc_key not in NPCS:
        raise KeyError(f"unknown NPC key: {npc_key}")
    x, y = NPCS[npc_key]
    return (x, y + dy)


# -----------------------------------------------------------------------------
# Quests — verified working per PLAYTHROUGH.md
# -----------------------------------------------------------------------------

QUESTS: dict[str, dict] = {
    "foresting": {
        "npc_key":  "forestnpc",
        "display":  "Forester",
        "reward":   "ironaxe",
        "stage_count": 3,
        # Stage 1 + 2 turn-ins each need 10 logs.
        "turn_in_items": {1: [("logs", 10)], 2: [("logs", 10)]},
    },
    "desertquest": {
        "npc_key":  "lavanpc",
        "display":  "Dying Soldier",
        "reward":   "completion only",
        "stage_count": 3,
    },
    "anvilsechoes": {
        "npc_key":  "blacksmith",
        "display":  "Blacksmith",
        "reward":   "bronzeboots",
        "stage_count": 2,
    },
    "royaldrama": {
        "npc_key":  "royalguard2",
        "display":  "Royal Guard",
        "reward":   "10000 gold",
        "stage_count": 3,
    },
    "royalpet": {
        "npc_key":  "king",
        "display":  "King",
        "reward":   "completion (catpet reward materializes post-PR1)",
        "stage_count": 3,
    },
    "sorcery": {
        "npc_key":  "sorcerer",
        "display":  "Sorcerer",
        "reward":   "staff (post-PR1)",
        "stage_count": 2,
        "turn_in_items": {1: [("bead", 3)]},
    },
    "ricksroll": {
        "npc_key":  "rick",
        "display":  "Rick",
        "reward":   "1987 gold",
        "stage_count": 4,
    },
    "seaactivities": {
        "npc_key":  "sponge",
        "display":  "Sponge",
        "reward":   "10000 gold",
        "stage_count": 7,
    },
    "scientistspotion": {
        "npc_key":  "scientist",
        "display":  "Scientist",
        "reward":   "alchemy unlock",
        "stage_count": 1,
    },
    "artsandcrafts": {
        "npc_key":  "iamverycoldnpc",
        "display":  "Babushka",
        "reward":   "completion + crafting unlock (on start)",
        "stage_count": 4,
    },
    "minersquest": {
        "npc_key":  "miner",
        "display":  "Miner",
        "reward":   "completion",
        "stage_count": 2,
        "turn_in_items": {1: [("nisocore", 15)]},
    },
    "herbalistdesperation": {
        "npc_key":  "herbalist",
        "display":  "Herby Mc. Herb",
        "reward":   "hotsauce + 1500 foraging xp",
        "stage_count": 3,
    },
    "scavenger": {
        "npc_key":  "villagegirl2",
        "display":  "Village Girl",
        "reward":   "7500 gold",
        "stage_count": 3,
    },
    "clamchowder": {
        "npc_key":  "bluebikinigirlnpc",
        "display":  "Pretzel",
        "reward":   "7500 gold",
        "stage_count": 7,
    },
    "ancientlands": {
        "npc_key":  "ancientmanumentnpc",
        "display":  "Ancient Monument",
        "reward":   "snowpotion",
        "stage_count": 2,
        "turn_in_items": {1: [("icesword", 1)]},
    },
}
