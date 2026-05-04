# Behavior Differentiation Audit (Claude-only, latest data)

> **Status: ARCHIVAL (n=30, 2026-04-16).** This audit was conducted under the legacy
> AGGRESSIVE/METHODICAL/CURIOUS personality system. KAE-46 replaced that system with
> outcome-anchored capability archetypes (GRINDER/COMPLETIONIST/EXPLORER_TINKERER) on
> 2026-04-25 — partly motivated by this audit's "task pressure dominates personality"
> finding. A larger n=731 follow-up audit (Linear `KAE-40`) reproduces the headline
> conclusion across an independent sample.
>
> Preserved as historical evidence behind the personality reframe.

---

> **Verdict: CONDITIONAL SIGNAL with a sharp finding — task pressure dominates personality.**
>
> 30 latest Claude sessions (April 16, 2026), 10 per personality. Match/partial/mismatch tally: **12 / 11 / 7** (40% / 37% / 23%) — above 33% chance, below clean separation. *Curious* is the only personality that survives blind judgment cleanly (5 strong / 5 partial / 0 mismatch). *Methodical* has the highest mismatch rate (4/10). *Aggressive* has the highest within-class variance (extremes from "persists at 4% HP" to "doesn't engage anyone for 39 turns then dies").
>
> **The single biggest finding:** the Scavenger quest's Goblin-strawberry-RNG farming loop pulls all three personalities into the same ~100-turn Goblin grind at coordinates (~189-193, 199-209). This pattern shows up in S06 (aggressive), S12/S14/S15/S17/S16 (methodical), and S25/S27/S28/S29 (curious). Personality only re-emerges *outside* this attractor — typically as curious agents fanning out to NPCs after the grind, or as aggressive agents pushing past safe HP thresholds when not grinding. This directly mirrors the literature finding (arXiv:2512.07462, 2602.01063) that personality prompts weaken under goal-directed pressure.
>
> Recommendation: a 3-expert MoE on the current personality definitions is unlikely to produce strong specialization. The sharper alternative is a 2-expert split (exploratory vs combat-pursuant) or a pivot to functional-role specialization. Confidence: medium-high — n=30 is qualitative, but the latest-data result reproduces the pattern from the prior Claude+Gemini+Codex audit, so the signal is consistent across two independent samples.

## Method

**Sample (Claude-only, latest):** 30 sessions, 10 per personality, all from April 16, 2026 (the most recent collection day). Selected by `ls -t agent_N/logs/*.log` newest-first then filtered to size > 200 KB (logs/ symlink resolves to the latest run dir under `agent_N/runs/`). All sessions are from the current personality definitions in `prompts/personalities/{aggressive,methodical,curious}.md`. No Gemini, no Codex. Same character per personality (agent_0=aggressive=mature L23-67 chars, agent_1=methodical=L1-L78 mix, agent_2=curious=L1-L78 mix).

**Blinding:** Each session was flattened into `/tmp/blind_views/SXX.txt` (action timeline + periodic HP/level/position/inventory snapshots + reasoning excerpts). Sessions assigned anonymous `S01`-`S30` IDs. 30 general-purpose subagents dispatched in parallel, each given one blind view. Subagents were instructed to emit exactly three sentences (combat / resource / scope) with concrete turn-ID citations and were forbidden from using the words "aggressive", "methodical", or "curious" anywhere in output, including when the agent's own reasoning text used those self-labels.

**Synthesis:** Subagent outputs compared against ground-truth labels in `/tmp/sample_map.json` and judged Match / Partial / Mismatch against the prompt definitions:
- AGGRESSIVE: HP threshold 30%, attacks above-level mobs, pushes new zones early.
- METHODICAL: HP threshold 60%, needs 2+ food before quest mobs, infrastructure quest order.
- CURIOUS: NPC-first, enters every building, zone rotation every 30 turns.

**Caveats:**
- Sessions hit the 150-turn orchestrator cap, truncating long-tail behavior in most runs.
- The agent's own reasoning text occasionally echoes "aggressive"/"methodical"/"curious" verbatim. Subagents were told to ignore these self-labels — but it's a soft constraint.
- Same character per personality means character maturity (L1 vs L78) is partially confounded with personality. Mitigated by sampling level diversity within each personality (aggressive: L1-L67, methodical: L1-L78, curious: L1-L78).

## Q1: Behavioral signal presence

The 30 latest Claude sessions show real behavioral variation, not collapse — but the variance *within* each personality is large.

**Aggressive (S01-S10) — wide variance:**
- S04 persists at HP=11/279 (4%) through skeleton dungeon, never eats, dies. Quintessentially aggressive.
- S03 does a below-level Water Guardian boss fight + 14+ Wolf attacks in a chain + dies to a Wolf at HP=0/429. Quintessentially aggressive.
- S07 is a Level-1 character that never picks a fight harder than a Rat, dies to passive gather damage, visits 6 NPCs across 4 quests. Quintessentially *curious*-coded.
- S06 farms Goblins for ~100 turns at full HP, never eats, never visits an NPC. Quintessentially *methodical*-coded — except for the no-eating part.

**Methodical (S11-S20) — high mismatch:**
- S19 explicitly demonstrates the prompted "2+ food gate" — at T113 HP=99/99 L6 it kills Rats specifically to restock food before engaging Goblins. This is the cleanest methodical match in the entire sample.
- S13 eats proactively at the prompted 60% threshold (T002 at 48%, T021 at 49%, T048 at 37%).
- But S12, S14, S15, S17 all do "100+ consecutive Goblin attacks at one spot, never eat, no preparation" — the inverse of the methodical prompt.

**Curious (S21-S30) — most consistent:**
- S30 talks to 12 distinct NPCs (Programmer, Royal Guard, Enchantment Vendor, Banker, Clerk, Kosmetics Vendor, Miner, Billey, Herby, Secret Agent, Villager, Bike Lyson) across 3 zones in one session.
- S23 cycles Scavenger + Sorcery + Miner's + Herbalist quests across Mudwich + Crullfield + Lakesworld + Crab Cave + an underground section.
- S27 visits 9 NPCs (Programmer, Royal Guard, 4 vendors, Mad Scientisto, Banker, Herby) before settling into combat at T079.
- Even S26, a low-level death-prone session, cycles 4 distinct NPC/quest interactions.

**Stopping condition met:** the first 9 sessions did NOT collapse into "all three look identical" — clear differences exist. But the differences are noisy and concentrated in specific personality × situation combinations.

## Q2: Label fidelity

| SID | Label | Match | Reason |
|-----|-------|-------|--------|
| S01 | aggressive | partial | Flees at HP=149/519 (29%), eats nothing, dies — borderline 30% threshold but no combat picking |
| S02 | aggressive | **MATCH** | Persists to HP=2/519 (0.4%), late warp, dies; dies again at HP=49/519 (9%) |
| S03 | aggressive | **MATCH** | Below-level boss fight + 14+ Wolf chain, dies at HP=0/429 |
| S04 | aggressive | **MATCH** | Pushes through HP=11/279 (4%), Skeleton dungeon, multiple low-HP crises |
| S05 | aggressive | partial | Pushes through traversal at 42% HP, but mob choice and broad NPC visits are non-aggressive |
| S06 | aggressive | **MIS** | 100-turn Goblin grind at full HP, never eats, no NPC visits — locked grind, not aggression |
| S07 | aggressive | **MIS** | L1 character, only fights Rats, 6 NPC visits across 4 quests — reads curious |
| S08 | aggressive | partial | Reactive Orc fight at 53%, otherwise quest-focused, multi-quest cycling |
| S09 | aggressive | partial | Skeleton farming at 72-77% HP, never eats, multi-zone — combat persistence okay |
| S10 | aggressive | **MATCH** | Persists at "30% threshold" (T109 HP=318/519), Orc grind, never eats |
| S11 | methodical | **MATCH** | Eats at 65% then panic-stacks at 48%, multi-quest infrastructure |
| S12 | methodical | **MIS** | 130 consecutive Goblin attacks, never eats, narrow grind — opposite of methodical |
| S13 | methodical | **MATCH** | Eats proactively at 48%/49%/37% thresholds, multi-quest, food-run warp |
| S14 | methodical | **MIS** | 140-turn Goblin grind, never eats, no NPC visits — locked grind |
| S15 | methodical | **MIS** | Goblin grind at full HP, never eats, no NPC visits |
| S16 | methodical | partial | Eats proactively to free inventory (T058/T064/T072), but locked single grind |
| S17 | methodical | **MIS** | 70-turn grind, eats at 32% HP (panic), narrow scope |
| S18 | methodical | partial | Reactive eating + 5 NPC chain after Goblin grind — broad scope is curious-coded |
| S19 | methodical | **MATCH** | Explicit "2+ food gate" at T113, eats at 60% threshold, NPC chain |
| S20 | methodical | partial | Top-up eating at 78%, but pushes Water Guardian boss (aggressive-coded) |
| S21 | curious | partial | Combat-grinding heavy but multi-quest 4 lines / 3 zones |
| S22 | curious | **MATCH** | Multiple mob types, multi-quest, multi-zone, multiple NPCs |
| S23 | curious | **MATCH** | Boss + Snek + Goblins, 8+ NPCs, 5 zones |
| S24 | curious | **MATCH** | Billey+Herby+Sorcerer+Bubba+Vendor chatter, multi-quest, multi-zone |
| S25 | curious | partial | Goblin grind 90 turns then NPC fan-out (delayed expression) |
| S26 | curious | partial | L1 character dies, but cycles 4 NPCs/quests |
| S27 | curious | **MATCH** | 9 NPCs/zones explored before combat |
| S28 | curious | partial | Narrow grind 100 turns then broad NPC fan-out |
| S29 | curious | partial | Goblin grind then post-death NPC chain (5+ NPCs) |
| S30 | curious | **MATCH** | 12 NPCs, 3 zones, 4 quests + achievement |

**Tally:**
| Personality | MATCH | partial | MIS | Strong-match rate |
|-------------|------:|--------:|----:|------:|
| aggressive | 4 | 3 | 3 | 40% |
| methodical | 3 | 3 | 4 | 30% (chance level for 3 classes) |
| curious | 5 | 5 | 0 | 50% |
| **TOTAL** | **12** | **11** | **7** | **40%** |

40% strong match is above the 33% chance baseline — but the floor is *methodical* (30%, at chance) and the ceiling is *curious* (50%, with zero mismatches). This reproduces the prior audit's finding almost exactly: the headline shifts by less than 5 percentage points across two independent samples.

## Q3: Where personality survives vs collapses

This was originally framed as a teacher-vs-personality question, but with Claude-only data the question becomes: **what game contexts allow personality to express, and which collapse all three personalities into the same behavior?**

**The Scavenger Goblin-farming attractor.** The single most striking finding is that *every personality has at least one session showing 100+ consecutive Goblin attacks at coordinates (~189-193, 199-209)* — the spawn for the Scavenger quest's strawberry RNG drop. Concrete examples:
- S06 (aggressive): 100+ Goblin attacks T012-T116
- S12 (methodical): 130 consecutive Goblin attacks T007-T145
- S14 (methodical): 140 turns Goblin farming
- S15 (methodical): ~140 turns Goblin grinding
- S17 (methodical): 70 turns Goblin grind T011-T102
- S25 (curious): 90 turns Goblin grind T009-T105
- S27 (curious): 70+ Goblin attacks T079-T149
- S28 (curious): 100+ Goblin attacks T004-T111

Within these "Goblin-farming" sessions, the three personalities look almost indistinguishable in observable behavior. Eating frequency varies somewhat (S25 never eats, S17 panic-eats at 32%, S16 eats at 99% to free inventory), but the *macro behavior* — same spot, same mob, same loop — is identical.

**Where personality DOES express:**
1. **Curious agents fan out *after* the grind.** S25, S28, S29 all do the Goblin grind first, then visit 4-12 NPCs across multiple zones. S27 visits 9 NPCs *before* the grind. This is the cleanest curious signal.
2. **Aggressive agents push past safe HP thresholds when not in farming mode.** S02 (HP=2/519), S03 (HP=0/429 to Wolf), S04 (HP=11/279), S10 (HP=318/519 at "30% threshold"). These are all combat-context behaviors that don't appear in methodical or curious sessions.
3. **Methodical's "2+ food gate" appears in only one session (S19).** Out of 10 methodical sessions, exactly one demonstrates the prompt-specified preparation behavior. The 60% HP threshold appears in 2-3 sessions (S11, S13, S20). Most methodical sessions look like "Goblin grinder who happens to not push past 30% HP because Goblins don't damage them enough."

**Where personality COLLAPSES:**
1. **Goblin farming for Scavenger.** All three personalities do it in roughly identical fashion.
2. **Crab Cave wall-pathing for Sorcery.** S01 (aggressive) and S02 (aggressive) and S08 (aggressive) and S22 (curious) all spend extensive turns navigating the same Crab Cave corridor — looks the same regardless of personality.
3. **Low-HP death from no food.** S01, S02, S04, S07, S22, S26, S27, S28, S29 all die or near-die because of the same pattern: scarce food + reactive eating too late. This failure mode crosses all three personalities.

**The "task pressure dominates personality" finding** is consistent with the literature the brief cited (arXiv:2512.07462, 2602.01063). The Kaetram quest structure creates strong attractors (specific NPC + specific mob + specific drop), and under those attractors all three personality prompts produce the same trajectory.

## Q4: State-conditional differentiation

Where personality DOES express, it lives in three observable dimensions:

**1. NPC interaction count (strongest signal).**
- Curious median: 5-8 distinct NPCs per session (S22, S23, S24, S25, S27, S28, S29, S30)
- Methodical median: 1-3 distinct NPCs per session
- Aggressive median: 0-2 distinct NPCs per session

This is the cleanest single dimension. It directly separates curious from the other two with high reliability.

**2. Low-HP combat persistence.**
- Aggressive: 4 sessions push past the 30% HP threshold (S02, S03, S04, S10).
- Methodical: 1 session pushes past 30% (S22 dies at 0%).
- Curious: 2 sessions push past 30% (S23, S27 — both also die).

This is a real signal but noisy because half of all sessions never reach low HP.

**3. Proactive food eating (when it happens at all).**
- Methodical proactive eats: S13 (eats at 48% before action), S19 (food-gate behavior), S16 (eats at 99% for inventory mgmt — wrong reason but matches the *frequency*).
- Aggressive almost never proactive eats.
- Curious is mixed.

Out of the 30 sessions, roughly **half never eat at all**. When food is never used, this dimension is invisible. The 60% vs 30% threshold is a real per-session signal *when triggered*, but it triggers in only ~10 of 30 sessions.

**Where differentiation is ABSENT:**
- **Mob choice (above-level vs at-level).** Aggressive instructions to "fight hard mobs" are largely ignored. The handful of exceptions (S02 Dark Skeletons, S03 Water Guardian + Wolves, S20 Water Guardian, S22 Spooky Skeleton, S23 Water Guardian) are spread across all three personalities, not concentrated in aggressive.
- **Quest order.** Methodical's "infrastructure quest order" never visibly expresses. All personalities pick up whichever quest is in front of them.
- **Boss attempts.** S03 (aggressive), S20 (methodical), S23 (curious) all attempt Water Guardian. No personality monopolizes boss attempts.

## Q5: Recommendation

**Verdict: CONDITIONAL SIGNAL.**

Same verdict as the prior audit, but the Claude-only sample makes the finding sharper: **the dominant variance in agent behavior is task-attractor (specific quest objectives), not personality**. Personality re-emerges in the cracks between attractors — most reliably in the NPC/zone-diversity dimension for curious, and in the low-HP-combat-persistence dimension for aggressive. Methodical does not reliably express in either dimension.

### Concrete next actions

**Option A — Narrow MoE (preferred).** Build a 2-expert split on the *one axis that actually differentiates*: **exploratory** (curious) vs **task-pursuant** (aggressive + methodical merged). The data does not support methodical/aggressive as separate behavioral modes — within Claude they collapse together under task pressure. A 2-expert MoE has cleaner training signal and less expert collapse risk than 3.

**Option B — Functional-role specialization.** Specialize on OODA stages instead of personality: an "exploration/NPC-discovery" expert, a "combat decision" expert, a "navigation/pathing" expert. This addresses a real failure mode (S01 burned 30+ turns on Crab Cave wall-pathing, S22 died on Lakesworld navigation) and has better task-grounded signal than personality.

**Option C — Decouple personality from task.** If you want to preserve the 3-personality MoE, you'd need to either:
- Filter the training data to *exclude* the Scavenger Goblin-farming attractor (drop sessions where all 3 personalities collapse to identical behavior), OR
- Diversify the quest set so no single attractor dominates.

Without one of these, the trained MoE will see ~30-40% of the data showing identical behavior across all three "personalities", which is exactly the recipe for expert collapse.

**What to NOT do:** A 3-expert MoE on the unfiltered current data is unlikely to produce three distinct experts. The methodical expert in particular will receive contradictory signal — the prompt asks for preparation behavior, but the actual data shows methodical agents grinding identically to aggressive agents.

### Confidence

- **High confidence:** curious is differentiable in the NPC/zone-diversity dimension. 5 of 5 latest curious-Claude sessions show clear NPC/zone breadth even when they include combat grinding.
- **High confidence:** task pressure (Scavenger Goblin farming) is a real attractor that collapses all three personalities. 8 of 30 sessions show the identical Goblin-grinding pattern across all three labels.
- **Medium confidence:** methodical is at chance — only 1 of 10 sessions shows clean prompt-spec behavior (S19 with "2+ food gate"). Could be sample noise, but reproduces from the prior audit's 30% strong-match rate.
- **Medium confidence:** aggressive expresses in low-HP combat persistence — 4 of 10 latest aggressive sessions push past 30% HP, but 3 of 10 mismatched (look like other personalities).

## Appendix: Sessions sampled

All Claude, all April 16, 2026, 10 per personality (30 total):

| SID | Personality | Log | Turns | Blind summary |
|-----|-------------|-----|------:|---------------|
| S01 | aggressive | agent_0/logs/session_7_20260416_152001.log | 39 | /tmp/blind_summaries/S01.txt |
| S02 | aggressive | agent_0/logs/session_6_20260416_142811.log | 150 | /tmp/blind_summaries/S02.txt |
| S03 | aggressive | agent_0/logs/session_5_20260416_140112.log | 150 | /tmp/blind_summaries/S03.txt |
| S04 | aggressive | agent_0/logs/session_4_20260416_131739.log | 150 | /tmp/blind_summaries/S04.txt |
| S05 | aggressive | agent_0/logs/session_3_20260416_122725.log | 150 | /tmp/blind_summaries/S05.txt |
| S06 | aggressive | agent_0/logs/session_2_20260416_120352.log | 150 | /tmp/blind_summaries/S06.txt |
| S07 | aggressive | agent_0/logs/session_1_20260416_113203.log | 150 | /tmp/blind_summaries/S07.txt |
| S08 | aggressive | agent_0/logs/session_7_20260416_110848.log | 70 | /tmp/blind_summaries/S08.txt |
| S09 | aggressive | agent_0/logs/session_6_20260416_103351.log | 150 | /tmp/blind_summaries/S09.txt |
| S10 | aggressive | agent_0/logs/session_5_20260416_100756.log | 150 | /tmp/blind_summaries/S10.txt |
| S11 | methodical | agent_1/logs/session_9_20260416_150924.log | 83 | /tmp/blind_summaries/S11.txt |
| S12 | methodical | agent_1/logs/session_8_20260416_144556.log | 152 | /tmp/blind_summaries/S12.txt |
| S13 | methodical | agent_1/logs/session_7_20260416_141634.log | 150 | /tmp/blind_summaries/S13.txt |
| S14 | methodical | agent_1/logs/session_6_20260416_134747.log | 150 | /tmp/blind_summaries/S14.txt |
| S15 | methodical | agent_1/logs/session_5_20260416_132718.log | 151 | /tmp/blind_summaries/S15.txt |
| S16 | methodical | agent_1/logs/session_4_20260416_130233.log | 151 | /tmp/blind_summaries/S16.txt |
| S17 | methodical | agent_1/logs/session_3_20260416_123653.log | 150 | /tmp/blind_summaries/S17.txt |
| S18 | methodical | agent_1/logs/session_2_20260416_115927.log | 150 | /tmp/blind_summaries/S18.txt |
| S19 | methodical | agent_1/logs/session_1_20260416_113203.log | 150 | /tmp/blind_summaries/S19.txt |
| S20 | methodical | agent_1/logs/session_8_20260416_110117.log | 81 | /tmp/blind_summaries/S20.txt |
| S21 | curious | agent_2/logs/session_6_20260416_145323.log | 150 | /tmp/blind_summaries/S21.txt |
| S22 | curious | agent_2/logs/session_5_20260416_140543.log | 150 | /tmp/blind_summaries/S22.txt |
| S23 | curious | agent_2/logs/session_4_20260416_131759.log | 150 | /tmp/blind_summaries/S23.txt |
| S24 | curious | agent_2/logs/session_3_20260416_123516.log | 150 | /tmp/blind_summaries/S24.txt |
| S25 | curious | agent_2/logs/session_2_20260416_120839.log | 150 | /tmp/blind_summaries/S25.txt |
| S26 | curious | agent_2/logs/session_1_20260416_113203.log | 150 | /tmp/blind_summaries/S26.txt |
| S27 | curious | agent_2/logs/session_7_20260416_104947.log | 150 | /tmp/blind_summaries/S27.txt |
| S28 | curious | agent_2/logs/session_6_20260416_102210.log | 150 | /tmp/blind_summaries/S28.txt |
| S29 | curious | agent_2/logs/session_5_20260416_095144.log | 150 | /tmp/blind_summaries/S29.txt |
| S30 | curious | agent_2/logs/session_4_20260416_090844.log | 150 | /tmp/blind_summaries/S30.txt |

Sample-map JSON: `/tmp/sample_map.json`. Blind view source files: `/tmp/blind_views/SXX.txt`. Blind subagent summaries: `/tmp/blind_summaries/SXX.txt`. Raw logs in `dataset/raw/agent_N/runs/`.

### Key per-session evidence cited

- S04 extreme low-HP push: T035 HP=11/279 (4%) inside skeleton dungeon, no eat.
- S19 "2+ food gate": T113 reasoning explicitly states it kills Rats to restock food before Goblin engagement.
- S30 NPC breadth: 12 distinct NPCs in one session (Programmer, Royal Guard, 2 Vendors, Banker, Clerk, Miner, Billey, Herby, Secret Agent, Villager, Bike Lyson).
- Goblin farming attractor: S06, S12, S14, S15, S17, S25, S27, S28 all show 70-140 turns of consecutive Goblin attacks at ~(189-193, 199-209). Personality-blind.
- S07 inversion: aggressive label, but L1 character that only fights Rats, visits 6 NPCs, dies to gather damage — reads curious.
- S23 personality match: 8 NPCs + 5 zones + 4 quests in one session, classic curious behavior.
