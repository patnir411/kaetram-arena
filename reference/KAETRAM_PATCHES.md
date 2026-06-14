# Kaetram-Open Fork Patches

This project runs against a privately patched checkout of
[Kaetram-Open](https://github.com/Kaetram/Kaetram-Open), based on upstream
`develop` at commit `4bdbd6d50d36d86f7bff28830945b240f8ab2799`. This document
describes every divergence from upstream in prose so the environment can be
reproduced from a clean upstream clone. After applying any of these changes,
run `yarn build` — `yarn start` alone does not pick up data-file edits.

**Licensing & redistribution.** Kaetram-Open's code is MPL-2.0 and its assets
are CC-BY-SA 3.0; the repository also ships a secondary "Omnia Public License"
with additional terms. We keep the patched fork private and describe the
modifications here in our own words rather than redistributing patched source.
All game code, art, and writing remain the work of the Kaetram team and
contributors — see the upstream repository for full credits.

The changes fall into four groups: **harness integration** (plumbing so an
agent can drive the game at all), **upstream bug fixes** (restoring intended
behavior), **intentional divergences** (deliberate changes to make the
environment tractable for LLM agents — these alter task difficulty and are
disclosed as environment modifications), and **quest workarounds** (quests
that are structurally uncompletable upstream).

---

## 1. Harness integration

These make the game drivable by Playwright + the MCP server; they don't change
gameplay.

| Change | File | Why |
|---|---|---|
| Expose the game instance as `window.game` | `packages/client/src/main.ts` | `state_extractor.js` reads game state and issues actions through this global. Upstream constructs `Game` without retaining a reference. |
| Serve the client on all interfaces (`--host 0.0.0.0`) | `packages/client/package.json` | Headless browsers and the dashboard reach the client across the VM, not just localhost. |
| WebSocket `idleTimeout` 15 s → 300 s | `packages/server/src/network/sockets/uws.ts` | LLM agents think for minutes between actions; the upstream 15 s idle timeout disconnects them mid-turn. |

## 2. Upstream bug fixes

Behavior-restoring fixes; candidates for upstream PRs.

### Crash / race fixes

- **Null-guard despawned combat targets** — `character.ts` `isNearTarget()`
  dereferenced `this.target!` after the target could be despawned between
  combat-loop ticks; now returns `false` when the target is gone.
- **Stop combat when the target entity is cleaned up** — `combat.ts`
  `handleLoop()` additionally stops when the target's position has been
  torn down (`target.x === undefined`), preventing a loop on a dead
  reference.

### Quest-data fixes

- **Seven missing item definitions** (`items.json`): `skeletonkingtalisman`,
  `ogrelordtalisman`, `queenanttalisman`, `forestdragontalisman`, `staff`,
  `catpet`, `smithingboots` — all referenced by quest rewards/dialogue
  upstream but never defined, breaking the quests that grant them.
- **Evil Santa chain made completable** — two fixes: `santaelf` now drops
  `candykey` (chance 1500), the stage-3 key that previously had no source
  (`mobs.json`); and a stage-1 door pair was added at (525,340) ↔ (525,345)
  (`map/world.json`), without which stage 1 could not be entered.
- **Hermitcrab placed in the world** at (320,455) (`map/world.json`) — the
  Sorcery bead source existed in data but spawned nowhere reachable.
- **`codersglitch` stage 0 `noc` → `npc` typo** (`quests/codersglitch.json`)
  — the stage's NPC binding was silently dropped.
- **Dialogue/description consistency fixes**: achievement text credits the
  Forester instead of the absent "Bulk Bogan" (`achievements.json`); Clam
  Chowder dialogue says to fish clams from the shore, matching the actual
  task, instead of "defeat clams for meat" (`quests/clamchowder.json`);
  tutorial combat dialogue says 3 rats to match the actual kill count
  (`quests/tutorial.json`); Herbalist's Desperation completion popup
  "Foragingexperience" → "Foraging experience"
  (`quests/herbalistdesperation.json`).

## 3. Intentional divergences (agent research)

Deliberate environment modifications. Each changes task difficulty and is
part of the experimental setup, not a claim about upstream-intended gameplay.

- **Tutorial bypass.** New players spawn at the main spawn point (328,892)
  instead of the tutorial room (`TUTORIAL_SPAWN_POINT` in
  `common/network/modules.ts`), and the tutorial quest is force-finished at
  login with the tutorial's tool rewards granted directly (`bronzeaxe`,
  `knife`, `fishingpole`, `coppersword`, `woodenbow`) — `quests.ts`
  `applyTutorialBypass()` / `grantTutorialStarterKit()`. The tutorial is a
  UI-walkthrough sequence with no value for tool-driven agents, and the
  tools it grants gate gathering skills (Foresting needs the axe).
- **No-clip detection disabled** (`player.ts`). Upstream teleports the player
  back and raises `cheatScore` when a movement packet advances more than two
  tiles. It misfires on legitimate auto-walks across region-streaming
  boundaries and on post-teleport packet races, which agents trigger
  constantly via programmatic navigation. The check is removed wholesale;
  agents do not otherwise no-clip.
- **Foraging level gates lowered to 5** (`foraging.json`): blue lily 10 → 5,
  tomato 15 → 5, paprika 25 → 5. Keeps early foraging content (including
  Herbalist's Desperation inputs) reachable within a session's leveling
  budget while still requiring some Foraging XP.
- **Strawberry drop chance 8,000 → 100,000** in the `fruits` drop table
  (`tables.json`), i.e. ~8% → guaranteed. Strawberries are a Herbalist's
  Desperation requirement; at 8% the fetch stage dominated session time with
  pure grinding.
- **Clerk shop prices cut ~10×** (`stores.json`): consumables and basic
  tools (flasks, burgers, knife, axes, arrows). Early-game gold income is
  too slow for agents to interact with the economy at all otherwise.
- **Miner NPC converted from an ore shop to an outfitter** (`stores.json`):
  sells copper/tin/bronze swords, the full bronze and gold armor sets, ores
  at nominal prices, and beryl. Together with the Miner's Quest bypass
  (below) this creates a mining-free path to mid-tier gear, letting combat
  progression be studied without requiring the Mining skill loop.

## 4. Quest workarounds (structurally broken upstream)

Quests whose dependency graph cannot be satisfied upstream. Unlike §2 these
don't restore intended behavior — they cut the knot.

- **Miner's Quest force-finished at login** (`quests.ts`
  `applyMinersQuestBypass()`, supported by a `getStageCount()` accessor in
  `quest/quest.ts`). The quest requires `nisocore`, whose only source is
  gated behind `minersquest2`, which itself requires Miner's Quest to be
  finished — a circular dependency. Forcing it finished opens the Miner's
  shop directly; the stage-set suppresses the quest's 2000 Mining XP reward
  so `minersquest2` stays correctly gated.
- **Anvil's Echoes requirement removed** (`quests/anvilsechoes.json`, plus
  the blacksmith back-room door gate in `map/world.json` lowered from stage
  2 to stage 1). The quest asks the player to recover the blacksmith's
  hammer, but the `hammer` item is not obtainable anywhere on the map, so
  the quest is uncompletable upstream. The workaround drops the hammer
  `itemRequirements`, rewrites the dialogue accordingly, fixes the reward
  popup to match what is actually granted, and changes the reward to
  `bronzeboots`. **Not used in Core 3** — none of the evaluated quests
  (Herbalist's Desperation, Rick's Roll, Foresting) depend on it; it exists
  so completionist-archetype agents don't sink sessions into a dead end.

## 5. Fork-resident documentation (experimental)

The fork also carries ~5K lines of our own authored reference docs —
`GAME_QUESTS.md` (quest encyclopedia), `CITATIONS.md` (file:line ground-truth
index), a game-systems reference, and audited quest/playthrough guides. These
are **experimental provenance inputs** for `prompts/game_knowledge.md` in this
repo: claims there were audited against game source via these docs. They
describe Kaetram-Open's content but are written by us; they are not patches to
game behavior and nothing at runtime reads them.

## 6. Incidental

Non-functional residue, listed for diff completeness: an `src/env.d.ts` astro
types reference and whitespace normalization in `stores.json`/`tables.json`.
