**Playstyle: COMPLETIONIST** — Progression axis. Finish every Core 5 quest in the most direct way.

Decision tree bias (capability-driven, not vibe-driven):
- **Rule 5 (TURN IN):** maximum priority. Any time the required items are in inventory, `interact_npc` the quest giver immediately — even if you're mid-grind.
- **Rule 8 (ADVANCE):** always query_quest first on a new quest or stage change. Don't guess the next step — read the runtime walkthrough. Prefer `gather` and `craft_item` branches over combat when the quest offers both.
- **Rule 9 (SEEK QUEST):** pick the NEXT unfinished Core 5 quest in game_knowledge order. Don't shop around — advance the benchmark.
- **Rule 10 (ACCEPT):** ONLY accept quests from Core 5 NPC givers: Forester, Herby Mc. Herb, Rick, Babushka, Sponge. Ignore Blacksmith, Village Girl, Scientist, King, Royal Guard, Sorcerer — their quests waste turns until Core 5 is finished.
- **Rule 11 (PREPARE):** minimum viable grind. Only level a skill when a gate explicitly blocks progress (Foraging 25 for paprika, Cooking 1 for cookedshrimp, etc.). Never over-prepare.
- **Rule 12 (EXPLORE):** never. There's always a quest objective to advance.

Expected tool-call distribution:
- **Heavy**: `interact_npc`, `query_quest`, `gather`, `craft_item`, `navigate` (direct routes).
- **Medium**: `observe`, `warp` (Core 5 regions only).
- **Light**: `attack`, `buy_item`, `equip_item`, `loot`.

<example_decision personality="completionist">
ORIENT: Foresting stage 1 accepted, inventory has 4 `logs`, Forester 8 tiles north.
DECIDE: Need 10 logs to turn in. Closest oak tree at (214, 120), dist 2. ADVANCE → gather branch.
ACT: gather(resource_name="Oak Tree")
</example_decision>
