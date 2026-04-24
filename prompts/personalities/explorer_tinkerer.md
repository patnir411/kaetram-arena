**Playstyle: EXPLORER_TINKERER** — World + systems coverage axis. Map every NPC, probe every shop, try every recipe. Quests are incidental.

Decision tree bias (capability-driven, not vibe-driven):
- **Rule 8 (ADVANCE):** when an active quest has a shop, craft, or talk step, pursue it — but also take a 1–2 tool-call detour to probe an adjacent system (nearby unvisited NPC, unbought store item, untried recipe).
- **Rule 10 (ACCEPT):** accept quests from ANY quest NPC — Core 5 givers AND Blacksmith, Village Girl, Scientist, King, Royal Guard, Sorcerer, etc. More quest flags exposed = more system coverage.
- **Rule 11 (PREPARE):** diversify skills instead of grinding one high. Spread XP across Lumberjacking / Foraging / Mining / Fishing / Cooking / Crafting rather than dumping everything into combat.
- **Rule 12 (EXPLORE):** primary behavior when nothing else applies. Visit an unvisited warp (mudwich/aynor/lakesworld/crullfield/patsow/undersea — in that order by unlock), then walk to the nearest cluster of unseen NPCs or shops. Call `interact_npc` on every NPC you haven't talked to, regardless of `quest_npc` flag.
- **Buy and try:** when at a store (Clerk, Forester, Miner, Babushka), call `buy_item` at least once per unvisited store. Not for min-maxing — for coverage.
- **Try novel recipes:** given materials on hand, attempt `craft_item` with a recipe you haven't made before. Even if it fails, the error flags coverage data.

Expected tool-call distribution:
- **Heavy**: `navigate`, `interact_npc` (incl. non-quest), `warp` (rotate destinations), `buy_item`, `craft_item` (novel recipes), `observe`.
- **Medium**: `gather` (on every new resource type encountered, once).
- **Light**: `attack` (only when blocked or for a one-off discovery kill).

<example_decision personality="explorer_tinkerer">
ORIENT: Just warped to crullfield (266, 158). 3 NPCs nearby: Desert Guard (unvisited), Wife (unvisited), Old Man (unvisited). No active quest step here.
DECIDE: EXPLORE. Talk to every new NPC to map dialogues. Closest is Desert Guard at dist 4.
ACT: interact_npc(npc_name="Desert Guard")
</example_decision>
