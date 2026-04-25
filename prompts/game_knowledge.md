## PRIMARY OBJECTIVE

You are scored on **5 quests**. That is your whole job. Quest acceptance
is opt-in: `interact_npc` only reads dialogue by default — when you've
decided to start a quest, call `interact_npc(name, accept_quest_offer=True)`.
Talking to an NPC does NOT commit you to anything.

### CORE 5 (must complete — do these first, in any sensible order)

| # | Quest | NPC | Why it's core |
|---|-------|-----|---------------|
| 1 | **Foresting** | Forester (216, 114) | Warmup — gather 10+10 oak logs, turn in twice |
| 2 | **Herbalist's Desperation** | Herby Mc. Herb (333, 281) | Foraging primitive + Foraging Lv25 skill gate |
| 3 | **Rick's Roll** | Rick (1088, 833) | Fishing + cooking + door nav + 2-step delivery |
| 4 | **Arts and Crafts** | Babushka (702, 608) | Crafting + Fletching + Cooking at 3 different stations |
| 5 | **Sea Activities** | Sponge (52, 310) | Real combat (picklemob) + 7-stage multi-NPC courier |

### EXTRA 5 (after Core 5 — bonus, not scored)

| # | Quest | NPC | Prereq / note |
|---|-------|-----|---------------|
| 6 | Desert Quest | Dying Soldier (288, 134) | Unlocks Crullfield + Lakesworld warps |
| 7 | Royal Drama | Royal Guard (282, 887) | Multi-region + sewer door → 10k gold |
| 8 | Royal Pet | King (284, 884) | **Requires Royal Drama finished first** |
| 9 | Scientist's Potion | Scientist (763, 666) | 1-stage Alchemy unlock |
| 10 | Ancient Lands | Ancient Monument (415, 294) | Capstone — need `icesword` from Ice Knight at (808, 813), lvl 62 |

### Off-limits — broken or zero-value, don't accept these

You can talk to these NPCs to learn dialogue, but `accept_quest_offer=True`
on these is wasted time — the rewards/items either don't exist or repeat
what Core 5 already unlocks.

- **Miner's Quest / Miner's Quest II** — upstream circular bug: the only 2 `nisocrock` placements in the map are behind `reqQuest=minersquest2`, and MQ2 requires MQ. Not completable.
- **Sorcery and Stuff** — reward item `staff` doesn't exist; Hermit Crab Warrior bead farm isn't placed in the current processed world.
- **Evil Santa** — stage-1 door not playtest-verified.
- **The Coder's Glitch / Glitch II / Fallacy** — missing talisman items; not reliably completable.
- **Anvil's Echoes, Scavenger, Clam Chowder** — completable but add no primitive Core 5 doesn't already cover. Only pursue these after all 10 above are done.

---

## CURRENT TREE TRUTHS

- Tutorial is auto-finished at spawn; starter kit (bronzeaxe, knife, fishingpole, coppersword, woodenbow) is already in your inventory. Ignore all tutorial dialogue / NPCs.
- 5 quests are the primary benchmark; 5 more extend the playthrough; 8 are Off-limits (see table above).
- Start **Arts and Crafts** to unlock Crafting. Start **Scientist's Potion** to unlock Alchemy.
- Smithing, Smelting, and Cooking are always available on station click. Fletching requires a `knife` from Clerk.
- `undersea` access requires the `waterguardian` achievement (kill Water Guardian at (293, 729), lvl 36, 350 HP). **Ancient Lands** (EXTRA #10) opens the mountain gate. Evil Santa is Off-limits, so `iceworld` stays locked.
- Trust runtime truth over stale flavor text. Liar quests still active: `Foresting` (Rusted Axe → ironaxe), `Royal Drama`, `Rick's Roll`, `Sea Activities`, `Scientist's Potion`, `Arts and Crafts`, `Herbalist's Desperation` (Mystical Potion → hotsauce + 1500 Foraging XP), `Anvil's Echoes` (Smithing Gloves → bronzeboots only), `Scavenger` (fake shopping list), `Clam Chowder` (fish clams, don't kill them).

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
| Blue Lily Bush | Foraging | 10 | (278–441, 250–363), e.g. (278, 250) | Arts and Crafts (`string`) |
| Tomato Bush | Foraging | 15 | (113–386, 107–326), e.g. (220, 108) | Herbalist's, Scavenger |
| Paprika Bush | Foraging | 25 | (286–390, 240–484), e.g. (298, 301) | Herbalist's |
| Strawberry Bush | Foraging | 1 | various | Scavenger (bonus) |
| Beryl Rock | Mining | 1 + **pickaxe** | Babushka mine (643–665, 643–656), e.g. (645, 643) | Arts and Crafts |
| Copper Rock | Mining | 1 | (639–670, 634–651) | Smithing |
| Tin Rock | Mining | 1 | (638–666, 628–649) | Smithing |
| Coal Rock | Mining | 1 | (645–668, 627–649) | Smelting |
| Iron Rock | Mining | 15 | (642–646, 588–598) | Smithing |
| Gold Rock | Mining | 40 | (655–665, 639–654) | Smithing |
| Shrimp Fishing Spot | Fishing | 1 + **fishingpole** | (269–383, 328–397), e.g. (325, 360) shore at (324, 360) | Rick's Roll |
| Tuna Fishing Spot | Fishing | 25 | (269–376, 296–402) | — |
| Clam Spot | Fishing | 5 + **fishingpole** | (268–381, 253–398), e.g. (322, 318) | Clam Chowder (bonus) |

⚠️ Spots **in water** require approach from a shore tile, not standing on the spot. If `gather` reports "No resource matching X nearby" but you're at the listed coords, you're probably on the wrong tile — `observe` to see `nearby_entities` and pick the spot with `kind: rock|fish|tree|forage`.

⚠️ Mining beryl needs a **pickaxe** (bronzepickaxe minimum). The starter `bronzeaxe` does NOT mine rocks.

## SKILL PROGRESSION STRATEGY

The skill XP table is steep. Estimated `gather`/`attack` count to hit each level (single-target XP per action):

| Skill gate | XP needed | Suggested grind |
|---|---|---|
| Foraging 10 | 1,355 | ~70 blueberry gathers (Mudwich) |
| Foraging 15 | 2,740 | ~140 blueberry, OR switch to tomato at Lv10+ |
| Foraging 25 | 8,730 | ~440 blueberry, OR switch to paprika once accessible |
| Mining 15 | 2,740 | ~140 copper/tin/coal at Lv1+ |
| Fishing 5 | 511 | ~25 shrimp |

For Herbalist's Desperation specifically: pick blue lily early (Lv10 gate), grind to 15 on tomato (better XP/action than blueberry once unlocked), grind to 25 on paprika (highest XP/action of the three).

## ACHIEVEMENTS (NOT QUESTS)

These are NPC task chains or kill rewards. They do not show up in the quest panel.

| Achievement | NPC | Task | Reward |
|-------------|-----|------|--------|
| Rat Infestation | Forester (~216,114) | Kill 20 rats / ice rats | 369 Str XP |
| Boxing Man | Bike Lyson (~166,114) | Kill 25 sneks | **Run ability** + 2000 Str XP |
| Oh Crab! | Bubba (~121,231) | Kill 10 crabs / hermit crabs | 696 Acc XP |
| Zombie Lovers | Zombie Girlfriend (undersea) | Kill 20 zombies | 4269 Str XP |

Boss kills also grant achievements. Highest-value route kill: Water Guardian for `undersea`.

---

## QUEST CATALOG

Use exact quest names from this table when calling `query_quest(quest_name)`. Call it before any gated, multi-step, or expensive quest. It returns status, requirements, unlocks, reward caveats, walkthrough, and boss notes.

| Tier | Exact Quest Name | Gate / Caveat | Reward / Unlock | One-line action |
|------|------------------|---------------|-----------------|-----------------|
| **CORE** | Foresting | None | `ironaxe` | Turn in logs 10 + 10 to Forester |
| **CORE** | Herbalist's Desperation | **Foraging 25** practical gate | `hotsauce` + 1500 Foraging XP | Turn in blue lily, then paprika + tomato |
| **CORE** | Rick's Roll | None | **1987 gold** | Fish/cook 5 shrimp, then deliver Rick's `seaweedroll` |
| **CORE** | Arts and Crafts | None | **Crafting unlock on start** | `berylpendant → bowlsmall → stew` (`stew` needs `bowlmedium`) |
| **CORE** | Sea Activities | **`waterguardian` required for undersea access** | **10000 gold net** + sea quest gates | Sponge/Pickle talk chain, then kill `picklemob` |
| EXTRA | Desert Quest | None | Unlocks `crullfield` + `lakesworld` warps | Deliver `cd` to Wife, then return |
| EXTRA | Royal Drama | None | **10000 gold** | `royalguard2 → ratnpc → king2` |
| EXTRA | Royal Pet | **Royal Drama** complete | `catpet` (pet) | Deliver 3 books, then return to King |
| EXTRA | Scientist's Potion | None | **Alchemy unlock on start** | Talk once and accept |
| EXTRA | Ancient Lands | Need `icesword` from Ice Knight at **(808, 813)** | `snowpotion` + mountain gate | Bring `icesword` to Ancient Monument |
| bonus | Anvil's Echoes | None | `bronzeboots` | Talk to Blacksmith twice |
| bonus | Scavenger | None | **7500 gold** | Turn in `tomato x2 + strawberry x2 + string x1` |
| bonus | Clam Chowder | Practical: Fishing 10 + Cooking 15 + Fletching 3 | **7500 gold** | Turn in 5 clams, then 2 chowders, then 2 more chowders |
| Off-limits | Sorcery and Stuff | Reward `staff` doesn't exist; bead farm not placed in current world | — | — |
| Off-limits | Miner's Quest | Circular: nisocrocks only inside `reqQuest=minersquest2` gate | — | — |
| Off-limits | Miner's Quest II | Depends on Miner's Quest which is impossible | — | — |
| Off-limits | Evil Santa | Stage-1 door not playtest-verified | — | — |
| Off-limits | The Coder's Glitch | Missing `skeletonkingtalisman` item definition | — | — |
| Off-limits | The Coder's Glitch II | 3 talisman items don't exist | — | — |
| Off-limits | Coder's Fallacy | Prereqs blocked by Coder chain | — | — |

---

## STORES / WARPS

Use `buy_item(npc_name, item_index, count)`:
- **Babushka** (ingredients store): access via door at **(483,275)**. Items: 0=Blue Lily, 1=Tomato, 2-3=Mushroom, 4=Egg, 5=Corn, 6=Raw Pork, 7=Raw Chicken. ⚠️ Store is unavailable while `Arts and Crafts` is active — Babushka's NPC slot is claimed by quest dialogue. Gather ingredients from the world instead, or finish the quest first.
- **Miner** (~323,178): 0=Coal, 1=Copper Ore, 2=Tin Ore, 3=Bronze Ore, 4=Gold Ore
- **Forester** (~216,114): 0=Bronze Axe(1000g), 1=Iron Axe(5000g)
- **Clerk** (startshop, Mudwich): 0=Arrow, 1=Knife, 2=Flask, 3=Mana Flask, 4=Burger, 5=Big Flask

**Warps** - use `warp(location)`:
| Destination | warp() arg | Coords | Unlock |
|-------------|------------|--------|--------|
| Mudwich | `mudwich` | (188,157) | Always |
| Crullfield | `crullfield` | (266,158) | Finish Desert Quest |
| Lakesworld | `lakesworld` | (319,281) | Finish Desert Quest |
| Patsow | `patsow` | (343,127) | Enter Patsow area |
| Aynor | `aynor` | (411,288) | Finish Ancient Lands |
| Undersea | `undersea` | (43,313) | Kill Water Guardian |

---

## KEY QUEST LOCATIONS

- **Ice Knight** at **(808, 813)** — drops `icesword` for Ancient Lands quest (L62, verified live placement).
- **Strawberry drop rate** — ~8% per fruit-table kill (goblins, ogres, ants, bosses). Plan ~25 kills for 2 strawberries. Only relevant for bonus `Scavenger` quest.

---

## GAME MECHANICS

- Attack styles: Hack = Str+Def, Chop = Acc+Def, Defensive = Def. All styles also give Health XP.
- `gather` handles trees, rocks, bushes, and fish spots. "No items gained" usually means low skill, wrong tool, or exhausted node.
- `string` = `bluelily` at Crafting Lv1.
- `clamchowder` = `clamobject + potato + bowlsmall` at Cooking Lv15. Fish clams at coastal `clamspot` nodes, not mob drops.
- Item drops despawn after 64s. Inventory has 25 slots.
