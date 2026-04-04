## QUEST GUIDE

Complete all quests. Organized by phase.

### PHASE 1: MUDWICH (Levels 1-15)

**Foresting** — Forester (~216, 114)
- Chop 10 Oak logs, turn in. Chop 10 more, turn in.
- Reward: Iron Axe (requires Strength 10 to equip — grind Hack style first)

**Anvil's Echoes** — Blacksmith (~199, 169)
- Stage 1: Talk to Blacksmith. He tells you about his lost hammer.
- Stage 2: Talk to Blacksmith again to complete the quest.
- Reward: Smithing Boots + 420 Smithing XP

**Scavenger** — Village Girl (~136, 146) → Old Lady via door at (147, 113)
- Accept from Village Girl, walk onto door (147, 113) to reach Old Lady (~776, 106)
- Collect: 2 tomatoes, 2 strawberries, 1 string
- Tomatoes: Foraging level 15, harvest Tomato Bushes near (~141, 114), (~203, 196)
- Strawberries: NOT harvestable. They drop from mobs (Hobgoblins, others with "fruits" loot table). Kill Hobgoblins (~190, 204) for strawberry drops.
- String: common mob drop from most enemies

**Snek Problem** — Bike Lyson (~166, 114) — ACHIEVEMENT, not quest
- Kill 25 Sneks east across bridge x≈220-240, y≈160. Auto-tracks. Return to claim reward.

### PHASE 2: INTERMEDIATE (Levels 10-25)

**Desert Quest** — Dying Soldier (~288, 134) — DO THIS EARLY
- Stage 0: Talk to Dying Soldier. He gives you a CD.
- Stage 1: Deliver CD to Wife via door at **(310, 264)** in Lakesworld forest. NOT the Sorcerer door (194, 218).
- Stage 2: Return to Dying Soldier.
- Reward: Unlocks Crullfeld + Lakesworld warps. Without this, those areas are inaccessible.

**Crab Problem** — Bubba (~121, 231) — ACHIEVEMENT, not quest
- Kill 10 crabs on beach y≈210-230. Approach from NORTH (121, 200), not through wall corridor.
- Completing this unlocks the Crab Cave door at (154, 231).

**Sorcery and Stuff** — Sorcerer via door at (~194, 218) → teleports to (~706, 101)
- Deliver 3 magic beads to Sorcerer.
- Magic beads: Kill the **Warrior Crab** miniboss (Level 30, 300 HP) inside the Crab Cave.
- Crab Cave entrance: door at **(154, 231)** on the beach. Requires Crab Problem achievement completed first.
- Door teleports you to (234, 662) underground. The Warrior Crab spawns at (~320, 455).
- WARNING: Warrior Crab is Level 30 with 300 HP. You need ~Level 25+ and food to survive.
- Reward: Magic Staff

**Miner's Quest** — Miner (~323, 178)
- Mine 15 nisoc ores. Only 2 nisoc rock spawns exist: (657, 644) and (656, 645).
- Warp to Lakesworld first, then navigate. Mining level 1 is sufficient.
- Rocks respawn after mining. Camp the 2 rocks until you have 15 ores.

**Herbalist's Desperation** — Herbalist (~333, 281) in Lakesworld
- Stage 1: Gather 3 Blue Lilies. Foraging level 10 required. Bushes at (~278-434, y≈250-262) in Lakesworld.
- Stage 2: Gather 2 Paprikas + 2 Tomatoes (Foraging 25 and 15 respectively).
- Warp to Lakesworld, harvest Blue Lily bushes nearby.
- Reward: Hot Sauce + 1500 Foraging XP

### KEY LOCATIONS

**Mudwich** (~188, 157) — main hub
- Blacksmith: ~199, 169 | Village Girl: ~136, 146 | Forester: ~216, 114 | Bike Lyson: ~166, 114

**Door Portals** (walk onto tile to teleport — these are NOT walls, walk directly onto the coordinates):
- (147, 113) → Old Lady (~776, 106) — Scavenger quest
- (154, 231) → Crab Cave (~234, 662) — Sorcery quest (requires Crab Problem achievement)
- (158, 232) → Secondary cave (~220, 686)
- (194, 218) → Sorcerer (~706, 101)
- (201, 168) → Anvil's cave (~439, 887) — Anvil's Echoes quest (talk to Blacksmith first)
- (310, 264) → Wife/Azaria (~735, 101) — Desert Quest stage 1

**Warps**: Mudwich (always), Crullfeld (266,158, requires Desert Quest), Lakesworld (319,281, requires Desert Quest)

**Danger Zones** — pathfinding deadlocks:
- Beach corridor x=105-115, y=210-235: approach Bubba from NORTH along x=121
- Nisoc ore (657,644): warp to Lakesworld first, if stuck warp back and retry

### GAME MECHANICS

- Attack styles: Hack (Str+Def), Chop (Acc+Def), Defensive (Def). All give Health XP.
- Iron Axe requires Strength 10. Grind Hack style.
- Gathering: click resource node, wait 5s. Trees 25s respawn, rocks 30s.
- Foraging levels: Blueberry=1, Corn/Peach=5, Blue Lily=10, Tomato/Raspberry=15, Paprika=25.
- Achievements (Snek Problem, Crab Problem) auto-track kills. Return to NPC after completing.
- Doors: walk directly onto the door tile coordinate to teleport. Do not try to pathfind through — use navigate(x, y) to the exact door coordinate.
