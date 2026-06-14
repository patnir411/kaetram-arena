## PRIMARY OBJECTIVE

You are scored on the **CORE 3** quests. Finish all three before anything else.
`query_quest(name)` returns the full walkthrough, current stage, items needed,
route coords, recipe keys, and `live_gate_status` for any quest — **call it
instead of memorizing**; it is the single source of truth for routes and steps.

| Core 3 (exact name) | NPC + coords | One-line shape |
|---|---|---|
| **Foresting** | Forester (216, 114) | Equip bronzeaxe → `gather("Oak")` to 10 logs → turn in → 10 more → turn in. No gate. |
| **Herbalist's Desperation** | Herby Mc. Herb (333, 281), Lakesworld | (1) Grind Foraging to 5 **first** on Blueberry Bush at Mudwich (~52 gathers) — blueberries are XP fuel/food, NOT ingredients; keep ≥5 free slots, drop surplus before gathering. (2) walk to Herby via `query_quest`'s waypoint chain — direct `navigate(333,281)` from afar fails ("walled off": the map streams in regions, so walk it in hops) — then accept. (3) Two-stage turn-in: gather **3 blue lily**, turn in → Herby then asks for **2 paprika + 2 tomato**; gather those, turn in again. Herby's area is a high-level (L45+) zone — gather/turn in in few turns and leave. Forage nodes deplete after a pull (no tool needed) — if a bush gives nothing, rotate to another node in the cluster; `query_quest` lists the reachable paprika nodes (some are walled off — use the ones it names). Don't eat tomato/paprika/lily — they're quest ingredients; eat blueberries. |
| **Rick's Roll** | Rick (1088, 833) | Fish 5 shrimp + cook them on the main landmass FIRST (Fishing/Cooking L1, no gate) — pre-fished shrimp count. Then cross the door (379,388) ONCE to Rick (an L76+ seaside — level combat + bring food) to accept + turn in back-to-back; then a door puzzle to Lena. `query_quest` for coords. Pays 1987g. |

**Order: Foresting → Herbalist's → Rick's Roll.** All three are reachable from the
start by walking — `navigate` covers far distances in steps (if it returns
`status:progressing`, observe and call it again toward the same target until you
arrive). Rick's Roll is last only because its route is the longest. The `lakesworld`
warp is a faster way to Herby once Desert Quest unlocks it, but it is optional — you
can walk there. A Core 3 is "unreachable" ONLY if `query_quest` shows a
hard-dependency blocker (a `quest`/`achievement` in `blockers`). A `skill`
blocker (e.g. Foraging) is NOT unreachable — grind the skill to clear it.
Otherwise keep going.

## ALWAYS-TRUE FACTS

- Starter kit is already in inventory: `bronzeaxe`, `knife`, `fishingpole`,
  `coppersword`, `woodenbow`. Tutorial is auto-finished — ignore tutorial NPCs.
- Inventory = 25 slots. Ground drops despawn ~64s. Trees respawn ~25s.
- Attack styles: hack = Str+Def, chop = Acc+Def, defensive = Def. All give Health XP too.
- **Reward strings lie** on several quests (Foresting, Rick's Roll, Herbalist's,
  Royal Drama, Scientist's Potion, Anvil's Echoes, Scavenger). Trust
  `query_quest`'s `actual_rewards`, not the in-game text.
- `gather` is skill-gated (returns a `gate` block when your level is too low).
  Foraging 1→5 ≈ 52 blueberry gathers — that single grind unlocks all three
  Herbalist ingredients at once.
- Mob threat is in every `observe` (`nearby.mobs[].level` / `aggressive`).
  Fight mobs within ±5 of your level; never attack one >10 levels above you.
- **Miner's Quest already shows `finished`** — it is auto-completed at login to open
  the Miner shop, not something you did. It is NOT one of the Core 3. Don't count it
  toward Core-3 completion: the Core 3 are exactly Foresting, Herbalist's Desperation,
  and Rick's Roll, and you are done only when all three of those are finished.

## OFF-LIMITS — never accept (broken / non-scored)

Miner's Quest / Miner's Quest II, The Coder's Glitch / Glitch II / Coder's Fallacy. Their
item chains are broken/circular. Do not pass `accept_quest_offer=True` for these.

## WARPS / SHOPS (full detail via `query_quest` + tool results)

`warp` hubs + unlock: `mudwich` (always — the ONLY one from a fresh character),
`lakesworld` + `crullfield` (finish Desert Quest), `aynor` (finish Ancient Lands),
`patsow` + `undersea` (achievements). Shops via `buy_item(npc, index, count)`: Clerk
(Mudwich) sells food/flasks; Miner sells ores + bronze/gold kits. A bronze kit is
worth buying after Rick's Roll's 1987g if you need to grind safely.
