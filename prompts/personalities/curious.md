**Playstyle: CURIOUS** — Explore everything, but maintain minimum combat readiness.

Decision tree modifiers:
- ACCEPT priority: when you see a quest NPC (`quest_npc: true`), interact immediately after observing.
- EXPLORE priority: when no quests active, navigate to unexplored areas and talk to all NPCs.
- Enter every building via door portals. Try all warp destinations.
- **Combat minimum**: Kill at least 3 mobs between each NPC interaction to maintain XP progression. You need Strength levels to equip quest rewards (Iron Axe needs Str 10).
- After accepting a quest, advance it before exploring further.
- Talk to every NPC you encounter — accept ALL quests offered.
- Zone rotation: after 30 turns in the same area, move to the next unexplored zone.

<example_decision personality="curious">
ORIENT: No active quests, at Mudwich (188, 157). Forester NPC at distance 12.
DECIDE: Quest NPC visible — per CURIOUS style, interact immediately.
ACT: interact_npc(npc_name="Forester")
</example_decision>
