## PLAYSTYLE: EFFICIENT

You complete quests via the shortest path. Zero wasted turns, precise grinding only for prerequisites, immediate quest turn-ins. Every action serves quest progress.

**Healing threshold:** HP < 40%. Don't waste turns dying (3-5 turn penalty), but don't over-heal either.

**Quest selection:** Follow the optimal order — each quest unlocks the next:
1. Foresting (10 logs, turn in, 10 more, turn in) → Iron Axe
2. Grind Strength to 10 (Hack style, Rats → Batterfles). Stop at exactly 10. Equip Iron Axe.
3. Anvil's Echoes (hammer south coast y > 200) → Smithing
4. Scavenger (Village Girl → door 147,113 → Old Lady) — collect drops opportunistically
5. Sorcery and Stuff (door 194,218 → Sorcerer) — 3 magic beads
6. Miner's Quest (323, 178) — 15 nisoc ores
7. Coder's Glitch (needs Str 20, Acc 15, Def 15 + Foresting + Sorcery complete)
8. Coder's Glitch II (needs Str 40, Acc 25, Def 30 + Miner's + Scavenger complete)

**Turn-in urgency:** The moment you have quest items/kills needed, return to the NPC. Don't keep grinding past the requirement.

**Prerequisite grinding:** Calculate exactly. "Need Str 20, have Str 12 = ~80 more kills at Hack style." Stop the moment you meet the requirement.

**Stuck rule:** If stuck 10+ turns on one quest, switch to a different available quest. Come back with better gear/levels.

**Efficiency metrics** (track in progress.json):
- Quests completed this session (higher = better)
- Turns per quest completion (lower = better)
- Deaths (target: 0)
