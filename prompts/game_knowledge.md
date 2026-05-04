## PRIMARY OBJECTIVE

You are scored on the **CORE 3** quests. Finish them before anything else.
After the Core 3 are done, advance to EXTRA. Bonus quests are completable
add-ons; off-limits quests are broken — don't waste turns on them.

`interact_npc` only reads dialogue by default. To commit, call
`interact_npc(name, accept_quest_offer=True)` — and only after `query_quest`
returns `live_gate_status.gated: false` (see system.md Rule 10).

**Observe-time signals.** Every `observe()` carries `live_gate_status` (per-quest blockers vs current state), `station_locations` (nearest crafting tile per skill), and `mob_stats.level/aggressive` (mob threat). Read these and act on them — don't just verbalize.

## QUEST CATALOG

Single source of truth for every quest. Use the **Exact Quest Name** column
verbatim when calling `query_quest(quest_name)`. Ordering within a tier is
suggested play order. `query_quest` returns the full walkthrough, current
stage, items needed, and `live_gate_status` evaluated against your state.

| # | Tier | Exact Quest Name | NPC + coords | Gate / Prereq | Reward / Unlock | Why / one-line action |
|---|------|------------------|--------------|---------------|-----------------|------------------------|
| 1 | **CORE** | Foresting | Forester (216, 114) | None | `ironaxe` | Warmup — 10+10 oak logs, turn in twice |
| 2 | **CORE** | Herbalist's Desperation | Herby Mc. Herb (333, 281) | **None for acceptance** — Foraging 5 is a *progress* gate (all three required nodes — bluelily, tomato, paprika — share Lv5). Talk to Herby Mc. Herb at (333, 281) immediately on arriving at Lakesworld — Stage 0→1 has no skill requirement. | `hotsauce` + 1500 Foraging XP | Turn in blue lily, then paprika + tomato |
| 3 | **CORE** | Rick's Roll | Rick (1088, 833) | None | **1987 gold** | Fish + cook 5 shrimp, deliver `seaweedroll` |
| 4 | EXTRA | Desert Quest | Dying Soldier (288, 134) | None | Unlocks `crullfield` + `lakesworld` warps | Deliver `cd` to Wife, then return |
| 5 | EXTRA | Royal Drama | Royal Guard (282, 887) | None | **10000 gold** | `royalguard2 → ratnpc → king2` |
| 6 | EXTRA | Royal Pet | King (284, 884) | **Royal Drama** finished | `catpet` (pet) | Deliver 3 books, return to King |
| 7 | EXTRA | Scientist's Potion | Scientist (763, 666) | None | **Alchemy unlock on start** | Talk once and accept |
| 8 | EXTRA | Ancient Lands | Ancient Monument (415, 294) | `icesword` from Ice Knight (808, 813), L62 | `snowpotion` + mountain gate | Bring `icesword` to Monument |
| — | bonus | Anvil's Echoes | Blacksmith (~199, 169) | None | `bronzeboots` | Talk to Blacksmith twice |
| — | bonus | Scavenger | Village Girl (~136, 146) | None | **7500 gold** | Turn in `tomato x2 + strawberry x2 + string x1` |
| — | bonus | Clam Chowder | (coastal NPC) | Practical: Fishing 10 + Cooking 15 + Fletching 3 | **7500 gold** | 5 clams, then 2 chowders, then 2 more |
| — | bonus | Sorcery and Stuff | Sorcerer | None | `staff` (recently fixed — verify on completion) | Re-assemble lost staff; bead farm now placed |
| — | bonus | Evil Santa | Mountain Sherpa | None | (verify) — unlocks `iceworld` warp | Stage-1 door at (525, 340-345); `candykey` from `santaelf` ~1.5% |

### Off-limits — don't accept these (broken / impossible)

- **Miner's Quest / Miner's Quest II** — circular bug: the only 2 `nisocrock` placements are behind `reqQuest=minersquest2`, and MQ2 requires MQ. Not completable.
- **The Coder's Glitch / Glitch II / Coder's Fallacy** — missing talisman item definitions (`skeletonkingtalisman` etc.); chain blocked.

---

## CURRENT TREE TRUTHS

- Tutorial is auto-finished at spawn; starter kit (bronzeaxe, knife, fishingpole, coppersword, woodenbow) is already in your inventory. Ignore all tutorial dialogue / NPCs.
- 3 CORE + 5 EXTRA + 5 bonus = 13 completable quests; 5 are Off-limits (see table above).
- Start **Scientist's Potion** to unlock Alchemy.
- Cooking is always available on a cauldron click. Fletching requires a `knife` (already in spawn kit). **Mining and Smithing/Smelting are not part of the playthrough** — buy ores and finished weapons/armor from the Miner shop instead.
- **Ancient Lands** (EXTRA) opens the mountain gate. **Evil Santa** (bonus) unlocks `iceworld`.
- Liar quests (in-game reward strings disagree with what you actually receive — trust `query_quest`'s `actual_rewards`): `Foresting` (Rusted Axe → ironaxe), `Royal Drama`, `Rick's Roll`, `Scientist's Potion`, `Herbalist's Desperation` (Mystical Potion → hotsauce + 1500 Foraging XP), `Anvil's Echoes` (Smithing Gloves → bronzeboots only), `Scavenger` (fake shopping list), `Clam Chowder` (fish clams, don't kill them).

---

## MOB PROGRESSION

XP per kill scales with mob HP. Move up quickly once low mobs stop paying.

| Level | Best Target | HP | XP/kill | Location |
|-------|-------------|----|---------|----------|
| 1-5 | Rat | 20 | 40 | Mudwich (~150-200, 150-170) |
| 5-10 | Goblin | 90 | 180 | West of Mudwich (~190, 204) |
| 10-20 | Spooky Skeleton | 140 | 280 | Cave area (~110-200, 108-200) |
| 20-30 | Snow Rabbit | 340 | 680 | Snow zone (x:445-506, y:251-367) |
| 30-40 | Golem | 350 | 700 | Scattered (x:377-700, y:118-747) |
| 40-50 | Wolf | 350 | 700 | Snow approach (x:268-440, y:254-362) |
| 50-60 | Snow Wolf | 1185 | 2370 | Snow zone (x:451-785, y:251-767) |
| 60-80 | Dark Skeleton | 740 | 1480 | Widespread (x:38-1145, y:411-851) |
| 80+ | Frozen Bat | 825 | 1650 | Ice zone (x:549-701, y:282-367) |

Goblins past Lv20 give poor XP. Dark Skeletons are the main efficient late grind.

---

## RESOURCE LOCATIONS

Coords are first valid placement in `world.json` — useful as a `navigate(x,y)` target. Stand on a walkable shore/adjacent tile, then `gather(name)`.

| Resource | Skill | Lvl gate | Coords (cluster) | Used by |
|---|---|---|---|---|
| Oak Tree | Lumberjacking | 1 | Mudwich north (~210, 110) | Foresting |
| Blueberry Bush | Foraging | 1 | Mudwich (105–238, 103–209), e.g. (155, 103) | Foraging grind |
| Blue Lily Bush | Foraging | 5 | (278–441, 250–363), e.g. (278, 250) | (foraging XP) |
| Tomato Plant Thingy | Foraging | 5 | (113–386, 107–326), e.g. (220, 107) | Herbalist's, Scavenger |
| Paprika Bush | Foraging | 5 | (286–390, 240–484); e.g. (286, 326) or (305, 360) — **avoid (298, 300)** (real bush there but in a 1k-tile disjoint pocket BFS can't reach from Mudwich/Lakesworld) | Herbalist's |
| Strawberry Bush | Foraging | 1 | various | Scavenger (bonus) |
| Shrimp Fishing Spot | Fishing | 1 + **fishingpole** | (269–383, 328–397), e.g. (325, 360) shore at (324, 360) | Rick's Roll |
| Tuna Fishing Spot | Fishing | 25 | (269–376, 296–402) | — |
| Clam Spot | Fishing | 5 + **fishingpole** | (268–381, 253–398), e.g. (322, 318) | Clam Chowder (bonus) |

**Mining is not part of the agent playthrough.** Copper/tin/coal/iron/gold ores are all sold by the Miner shop (see Stores section). Do not attempt to mine — Miner's Quest I/II are off-limits.

⚠️ Spots **in water** require approach from a shore tile, not standing on the spot. If `gather` reports "No resource matching X nearby" but you're at the listed coords, you're probably on the wrong tile — `observe` to see `nearby_entities` and pick the spot with `kind: rock|fish|tree|forage`.

## SKILL PROGRESSION STRATEGY

The skill XP table is steep. Estimated `gather`/`attack` count to hit each level. **XP per gather (verified vs `Kaetram-Open/packages/server/data/foraging.json` + formula in `packages/server/src/info/loader.ts:44-50`):** blueberry 10, corn 15, bluelily 20, tomato 25, paprika 50.

| Skill gate | XP needed | Suggested grind |
|---|---|---|
| Foraging 5 | 511 | ~52 blueberry gathers @ 10 XP each, OR ~34 corn @ 15 XP (Mudwich) — unlocks blue lily, tomato, AND paprika together |
| Fishing 5 | 511 | ~25 shrimp |

For Herbalist's Desperation specifically: a single Foraging 1→5 grind on blueberry unlocks all three required nodes simultaneously. Paprika gives the highest XP/pull (50) — switch to it after Lv5 if continuing to grind. `gather` matches resource_name as a case-insensitive substring of the entity's display name, so `"Tomato"` works (display name is `Tomato Plant Thingy`); use the full names from the table above to be safe.

## ACHIEVEMENTS (NOT QUESTS)

These are NPC task chains or kill rewards. They do not show up in the quest panel.

| Achievement | NPC | Task | Reward |
|-------------|-----|------|--------|
| Rat Infestation | Forester (~216,114) | Kill 20 rats / ice rats | 369 Str XP |
| Boxing Man | Bike Lyson (~166,114) | Kill 25 sneks | **Run ability** + 2000 Str XP |
| Oh Crab! | Bubba (~121,231) | Kill 10 crabs / hermit crabs | 696 Acc XP |

Boss kills also grant achievements.

---

## STORES / WARPS

Use `buy_item(npc_name, item_index, count)`:
- **Miner** (~323,178 OR ~1007,664): general outfitter. Buy via `buy_item(npc_name="Miner", item_index=N)`:
  - **Ores**: 0=Coal(3g), 1=Copper Ore(5g), 2=Tin Ore(5g), 3=Bronze Ore(8g), 4=Gold Ore(20g)
  - **Starter swords**: 6=Copper Sword(10g), 7=Tin Sword(10g)
  - **Bronze kit** (~560g full): 8=Bronze Sword(120g), 9=Bronze Helmet(100g), 10=Bronze Chestplate(140g), 11=Bronze Legplates(100g), 12=Bronze Boots(100g)
  - **Gold kit** (~3700g full): 13=Gold Sword(700g), 14=Gold Helmet(700g), 15=Gold Chestplate(900g), 16=Gold Legplates(700g), 17=Gold Boots(700g)
  - Buy bronze kit early (after Rick's Roll's 1987g). Gold kit is the late-game upgrade.
- **Forester** (~216,114): 0=Bronze Axe(100g), 1=Iron Axe(500g) — Foresting quest gives ironaxe free, so usually skip this shop.
- **Clerk** (startshop, Mudwich): 0=Arrow(1g), 1=Knife(50g), 2=Flask(10g), 3=Mana Flask(10g), 4=Burger(45g), 5=Big Flask(55g), 6=Big Mana Flask(50g)

**Warps** - use `warp(location)`:
| Destination | warp() arg | Coords | Unlock |
|-------------|------------|--------|--------|
| Mudwich | `mudwich` | (188,157) | Always |
| Crullfield | `crullfield` | (266,158) | Finish Desert Quest |
| Lakesworld | `lakesworld` | (319,281) | Finish Desert Quest |
| Patsow | `patsow` | (343,127) | Enter Patsow area |

---

## KEY QUEST LOCATIONS

- **Ice Knight** at **(808, 813)** — drops `icesword` for Ancient Lands quest (L62, verified live placement).
- **Strawberry drop rate** — ~8% per fruit-table kill (goblins, ogres, ants, bosses). Plan ~25 kills for 2 strawberries. Only relevant for bonus `Scavenger` quest.
- **Rick's Roll is L1-safe end-to-end.** The shrimp fishing spots near (325, 360) require Fishing 1 only (no level gate, no aggressive mobs in 8-tile radius). The corridor Mudwich (188,157) → door 1025 (379, 388) → Rick (1088, 833) passes through Mudwich outskirts and farmland — no aggressive mobs above L7. Door 1025 is unguarded. Do NOT classify Rick's Roll as 'unreachable' based on travel distance.
- **Rick's Roll route pin-chain.** Long-distance navigation cap is ~100 tiles per `navigate(x,y)` call. Recommended pin chain: (245,170) → (285,190) → (293,242) → (311,254) → (324,301) → (340,345) → (367,348) → (375,370) → traverse_door(379,388 → 1138,800) → navigate(1088,833). Each leg under 100 tiles.

---

## GAME MECHANICS

- Attack styles: Hack = Str+Def, Chop = Acc+Def, Defensive = Def. All styles also give Health XP.
- `string` = `bluelily` at Crafting Lv1.
- `clamchowder` = `clamobject + potato + bowlsmall` at Cooking Lv15. Fish clams at coastal `clamspot` nodes, not mob drops.
- Item drops despawn after 64s. Inventory has 25 slots.
- **Chained crafts no-op.** The Crafting/Fletching menu stays open after `craft_item`; a second `craft_item` on the same skill in one open session no-ops (selected_name updates client-side, inventory does not change). Force the menu closed (move 1 tile or `interact_npc` on a non-shop NPC) before the next `craft_item`.
- **Quest turn-ins often need TWO `interact_npc` calls.** First call opens dialogue, second consumes items / advances stage. If after one call your inventory is unchanged and `quest_state_changed=false`, call again before assuming failure (canonical case: Rick stage-1 cookedshrimp delivery).
