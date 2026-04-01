## QUEST COMPLETION GUIDE

Your mission is to complete all quests. This guide organizes them by phase.

---

### PHASE 1: MUDWICH STARTER QUESTS (Levels 1-15)

**Foresting** — Forester (~216, 114)
- Chop 10 Oak logs (click tree, wait 5s), turn in. Chop 10 more, turn in.
- Reward: **Iron Axe** (requires Strength 10 to equip — grind Hack style first)
- TIP: Oak trees are abundant north of Mudwich near the Forester.

**Anvil's Echoes** — Blacksmith (~199, 169)
- Find his lost hammer (explore south coast y > 200, check cave entrances)
- Reward: Smithing Boots, unlocks Smithing skill + workshop door

**Scavenger** — Village Girl (~136, 146) → Old Lady via door at (147, 113)
- Accept from Village Girl, then walk onto door tile at (147, 113) to reach Old Lady (~776, 106)
- Collect: 2 tomatoes, 2 strawberries, 1 string
- NOTE: Old Lady is NOT in village. Use the door portal. This is a multi-session quest.
- TOMATO SOURCE: **Foraging is the intended path.** Level Foraging to 15, then harvest Tomato Plants near Mudwich (closest at (141, 114), (149, 114), (137, 157)). Mob drops are only ~1.25% per kill — extremely rare.
- STRAWBERRY SOURCE: Mob drop ~0.8% per kill (fruits table). OR level Foraging — Raspberry Bush at L15 is a substitute.
- STRING: Common drop from any mob (100% chance from "ordinary" table). You probably already have these.
- TO LEVEL FORAGING: Harvest Blueberry Bushes (L1 req) and Tree Stumps (L1 req) to reach L10, then Corn/Peach Bushes (L5 req) to reach L15.

**Snek Problem** — Bike Lyson (~166, 114) — THIS IS AN ACHIEVEMENT, NOT A QUEST
- Kill 25 Sneks east across bridge at x≈220-240, y≈160
- Navigate: `__navigateTo(230, 160)` crosses the bridge automatically
- NOTE: This is tracked as an achievement ("boxingman"), not a quest. It auto-tracks kills. Return to Bike Lyson after 25 kills to claim reward (run ability + 2000 Str XP). No quest accept panel — just talk to him.

**Royal Drama** — Royal Guard 2 (~282, 887)
- Find the missing king in the sewers
- Reward: 10,000 gold

### PHASE 2: INTERMEDIATE QUESTS (Levels 10-25)

**Desert Quest** — Dying Soldier (~288, 134) — CRITICAL, DO THIS EARLY
- Stage 0: Talk to Dying Soldier at (288, 134) in the desert. He gives you a CD.
- Stage 1: Deliver the CD to Village Girl. She is accessed via the door at (~194, 218) on the south beach (teleports to interior at ~735, 101).
- Stage 2: Return to Dying Soldier to complete.
- Reward: **Unlocks Crullfeld warp at (266, 158) and Lakesworld warp at (319, 281)**. Without this quest, Patsow and Lakesworld are INACCESSIBLE.

**Sorcery and Stuff** — Sorcerer via door at (~194, 218)
- Door on south beach teleports to Sorcerer area (~706, 101)
- Deliver 3 magic beads
- BEAD SOURCE: **Kill Warrior Crabs** in the underwater cave (unlocked by Crab Problem achievement). Warrior Crabs have a guaranteed bead drop. Grinding random mobs for beads is only ~0.5% per kill — don't bother.
- WARNING: Sorcerer room is tiny. Exit via door at (708, 104). Don't walk east.
- Reward: Magic Staff, unlocks Sorcerer shop

**Miner's Quest** — Miner (~323, 178)
- Mine and deliver 15 nisoc ores (click ore rocks, wait 5s each)
- NISOC ORE ROCKS are at (~657, 644) and (~656, 645) — far southeast, ~350 tiles from Mudwich. Use `__safeWarp(2)` (Lakesworld at 319,281) to cut the distance, then `__navigateTo(657, 644)`. If navigation gets stuck en route, WARP BACK and retry from Lakesworld — do NOT manually hop tile by tile. Mining level 1 is sufficient.
- Reward: 2000 Mining XP, unlocks Miner shop

**Crab Problem** — Bubba (~121, 231) — THIS IS AN ACHIEVEMENT, NOT A QUEST
- Kill 10 crabs on the beach (y≈210-230) for the `crabproblem` achievement
- APPROACH: Navigate to (121, 200) first (north of beach), then walk south to crabs. Do NOT path through x=105-115 corridor — it deadlocks.
- NOTE: This is an achievement, not a quest. Kills auto-track. Return to Bubba after 10 kills to claim reward (696 Acc XP).
- Unlocks: underwater cave entrance (where Warrior Crabs live — needed for magic beads)

**Herbalist's Desperation** — Herbalist (~333, 281)
- Stage 1: Gather 3 Blue Lilies (Foraging L10 required — harvest Blue Lily Bushes at y≈250-360, e.g. (278, 250), (327, 288), (332, 296))
- Stage 2: Gather 2 Paprikas (Foraging L25) + 2 Tomatoes (Foraging L15)
- TO REACH L10 FORAGING: Harvest Blueberry Bushes (L1 req) repeatedly. Bushes respawn every 30 seconds.
- Reward: Hot Sauce, 1500 Foraging XP

**Arts and Crafts** — Babushka (~702, 608)
- Craft a pendant, bowl, and stew
- Reward: crafting bench access

**Rick's Roll** — Rick (~1088, 833)
- Cook shrimps, make seaweed roll, deliver to girlfriend
- Reward: 1987 gold

### PHASE 3: ADVANCED QUESTS (Levels 25+)

**Coder's Glitch** — The Coder (tutorial area NPC)
- Prerequisites: Foresting + Sorcery complete, Strength 20, Accuracy 15, Defense 15
- Kill Skeleton King (1850 HP, Patsow boss area), return talisman
- Reward: 5000 Str XP + Club weapon

**Coder's Glitch II** — The Coder
- Prerequisites: Coder's Glitch + Miner's Quest + Scavenger complete, Str 40, Acc 25, Def 30
- Kill: Ogre Lord (2850 HP) → Queen Ant (4200 HP) → Forest Dragon (6942 HP)
- Reward: 7500 Acc + 4500 Str + 3000 Def XP + Iron Round Shield

**Ancient Lands** — Ancient Monument
- Bring an ice sword to the monument
- Reward: Snow Potion, unlocks mountain passage + Aynor warp

**Evil Santa** — Sherpa (snow area)
- Infiltrate Santa's factory, kill Santa (7500 HP boss)
- Reward: ice world access

**Sea Activities** — Sponge (beach)
- Recover stolen money, fight Sea Cucumber mob
- Reward: 10,000 gold

**Clam Chowder** — Pretzel (ice area)
- Make clam chowder, find missing grandmother
- Reward: 7500 gold

### SKIP THESE

- **Scientist's Potion** — Stub quest, incomplete (1 stage only)
- **Coder's Fallacy** — Blocked by Scientist's Potion stub

---

### MOB REFERENCE

| Mob | HP | Level | Location | Good for |
|-----|-----|-------|----------|----------|
| Crab | 15 | 1 | Beach y=210-230 | Levels 1-5, crabproblem achievement |
| Rat | 20 | 1 | Near Mudwich | Levels 1-5 |
| Batterfly | 65 | 4 | Fields around Mudwich | Levels 5-15 |
| Snek | 85 | 16 | East across bridge x≈220-240 | Levels 10-20 |
| Goblin | 90 | 7 | West of village | ~1% tomato/strawberry (use Foraging instead) |
| Vulture | 100 | 16 | Desert border | Levels 10-20 |
| Desert Scorpion | 124 | 24 | Desert x≈236-319 | Levels 20-30 |
| Spooky Skeleton | 140 | 14 | NW of village | Levels 10-20 |
| Ogre | 150 | 18 | Patsow x≈321-400 | Levels 15-25 |
| Cactus | 160 | 16 | Desert | ~1% tomato/strawberry (use Foraging instead) |
| Old Ogre | 256 | 23 | Patsow | Levels 20-30 |
| Iron Ogre | 300 | 20 | Deep Patsow (miniboss) | Levels 20-30 |
| Worker Ant | 300 | 46 | Endgame area | Levels 40-55 |
| Angry Rooster | 320 | ~28 | Desert area | Levels 25-35 |
| Scary Skeleton | 375 | 30 | Desert/Patsow | Levels 25-40 |
| Pirate Skeleton | 460 | 54 | Underwater pirate area | Levels 45-60 |
| Soldier Ant | 526 | 60 | Endgame area | Levels 55-70 |
| Zombie | 1250 | 69 | Underwater cave | Levels 60-80 |

### KEY LOCATIONS

**Mudwich Village** (~188, 157) — main hub, warp target
- Blacksmith: ~199, 169 (Anvil's Echoes)
- Village Girl: ~136, 146 (Scavenger)
- Forester: ~216, 114 (Foresting)
- Bike Lyson: ~166, 114 (Snek Problem)

**Door Portals** (walk onto tile to teleport):
- (147, 113) NW → Old Lady area (~776, 106)
- (194, 218) south beach → Sorcerer (~706, 101)

**Combat Zones**:
- Rats: everywhere near Mudwich
- Crabs: beach y=210-230, Bubba at (121, 231)
- Batterfles: fields around Mudwich
- Sneks: east across bridge x≈220-240, y≈160
- Goblins: west of village
- Desert: x≈236-319, past guard at (231, 145)
- Patsow: x≈321-400, upper plateau y≈130-148, lower valley via door at (343, 132)

**Warps**: 0=Mudwich (always), Crullfield at (266, 158) (requires Desert Quest complete), Lakesworld at (319, 281) (requires Desert Quest complete), Undersea (waterguardian achievement), Patsow at (343, 127) (patsow achievement), Aynor (Ancient Lands quest)

**DANGER ZONES — pathfinding deadlocks, AVOID navigating through these**:
- Beach wall corridor: x=105-115, y=210-235 — narrow passages between walls and water, pathfinder loops endlessly. To reach Bubba (121, 231), approach from the NORTH along x=121, not from the east through the walls.
- South beach cliffs: y > 225 east of x=130 — terrain looks passable but isn't. Use the door portal at (194, 218) instead of walking along the coast.
- Nisoc ore area (657, 644): ~350 tiles from Mudwich. Warp to Lakesworld first (if unlocked), then navigate. If stuck en route, WARP BACK — do not retry tile by tile.

**Patsow Access Chain** (IMPORTANT):
1. Complete Desert Quest → unlocks Crullfeld warp at (266, 158)
2. Enter Crullfeld → find chest at (~280, 165) → grants "ogresgateway" achievement
3. ogresgateway unlocks door at (273, 162) → inner Patsow with Ogres, bosses, ore

### GAME MECHANICS (quick reference)

- **Attack styles**: Hack=6 (Str+Def), Chop=7 (Acc+Def), Defensive=3 (Def). All give Health XP.
- **Iron Axe**: requires Strength 10. Grind Hack style to reach Str 10, complete Foresting, equip.
- **Gathering**: click node, wait 5s. Success rate = tool_level + skill_level vs difficulty. Trees respawn 25s, rocks 30s.
- **Quest item drops**: Mob drops for quest items are RARE (0.5-1.25% per kill). Foraging is the intended path for tomatoes, strawberries, blue lilies, and paprika. Warrior Crabs (underwater cave) are the intended source for magic beads (guaranteed drop).
- **Foraging levels**: Blueberry/TreeStump=1, Corn/Peach=5, Blue Lily=10, Tomato/Raspberry=15, Blackberry/Mustard=20, Paprika/Cumin=25. Level up by harvesting lower-tier bushes (30s respawn). Click a bush to forage — you must meet the level requirement.
- **Achievements vs Quests**: Snek Problem and Crab Problem are ACHIEVEMENTS, not quests. They auto-track kills. Return to NPC after completing kills — no quest accept panel needed.
- **Shops**: Start Shop (burgers 450g), Forester (axes), Miner (ores), Sorcerer (staves). Unlock via quest completion.
- **Patsow lower valley**: must use door at (343, 132) or (375, 132) to descend. Walking off ledge doesn't work.
