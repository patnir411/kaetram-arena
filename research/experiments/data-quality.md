# Data Quality

How raw Claude gameplay sessions became clean SFT training data. Documents every filter, threshold, and quality gate in the pipeline, with before/after metrics.

---

## Pipeline Overview

```
135 active raw logs across agents 0-2 (post-Core-3 Claude only; pre-Core-3 + non-Claude under dataset/raw/_archive/)
  Layout: dataset/raw/agent_*/runs/run_<TS>/  (parser globs runs/run_*; never recurses into _archive/)
  → extract_turns.py (OODA turn extraction; observe is a first-class turn, not consumed for game_state)
    → convert_to_qwen.py (quality scoring + format + observe-bigram dedup + window=3)
      → r10 dataset: 9,352 train / 934 val = 10,286 records (observe ≈ 47% of tool calls)
```

**Pipeline timeline:**
- April 8: 509 raw → 395 extracted → 3,957/488 (4,445 total)
- April 9 (r7): 618 raw → 575 sessions → 14,091 turns → 6,423/646 (7,069 total)
- April 16 (r9): 675 raw → 650 extracted → 5,871/575 (6,446 — degenerate filtering removed 623)
- May 6 (r10 built): post-Core-3 Claude corpus only — 135 sessions × 5 runs, 9,766 raw turns → **9,352 train / 934 val = 10,286 records**. metadata.json carries provenance (source_runs, prompt_commit, core3_only, record_counts).

**Run-directory layout.** `dataset/raw/agent_*/runs/run_<TS>/` is the canonical home for per-orchestrator-launch session bundles. The parser globs `agent_*/runs/run_*` — pre-Core-3 runs and every non-Claude harness run live under `dataset/raw/_archive/<harness>/agent_N/run_*` and are skipped automatically.

**Why r10 is small.** The corpus contracted from a multi-prompt-version backlog (~1,700 sessions, mixed harnesses) to a single-prompt-version on-policy build. Quantity loss is intentional: pre-Core-3 sessions trained behaviors that don't match the live world (Sea Activities + Arts and Crafts trajectories, legacy AGGRESSIVE/METHODICAL/CURIOUS personalities). The 10,286 r10 records are all Claude Sonnet on the Core 3 prompt with current grinder/completionist/explorer_tinkerer archetypes.

---

## Exclusions (What Got Removed Entirely)

### Agent exclusions
- **Agent 3 (EFFICIENT):** Dropped April 3. 45% click_tile rate, lowest level reached (37 vs 57-73 for others). Broken behavior — personality prompt created a prep loop where agent alternated between "should I gather food?" and "should I fight?" without doing either. (KAE-1)
- **Agent 4 (Codex):** 39 dead sessions, all stubs. Codex harness had early MCP connection issues. Raw data deleted from VM.
- **Pre-March 28 data:** Before personality prompts were dialed in. METHODICAL was especially contaminated (had a catch-22 food-before-ACCEPT gate that deadlocked quest progression).

### Date cutoff
March 28, 2026. Personalities fully stable from this date. Earlier data from agents 0-2 kept but de-prioritized.

---

## Filters Applied (r5 rebuild, April 4)

### 1. click_tile filter
**Before:** 37.9% of multi-turn window actions were blind click_tile calls with no reasoning.
**After:** 4.7%.
**How:** Removed turns where action = `click_tile(x, y)` AND reasoning < 20 chars. These were mechanical "click somewhere" turns with no decision-making.
**Why it matters:** click_tile is the fallback action — agent clicks a pixel coordinate when it doesn't know what tool to use. Training on this teaches the model to give up instead of reasoning.

### 2. Repetitive loop filter
**Before:** 23% of turns were part of 3+ consecutive identical actions (e.g., `navigate(188, 157)` repeated 5 times).
**After:** 0.2%.
**How:** Detect runs of 3+ identical (action_type, arguments) tuples. Score down the entire run to 0.05 (below min_score threshold).
**Why it matters:** Agent gets stuck against walls or in combat loops. These turns contain no new reasoning — just repeated attempts at the same failed action.

### 3. Reasoning trimming
**Before:** Avg 1,654 chars, some over 5,000.
**After:** Avg 426 chars, max 800.
**How:** Trim to 500 chars in convert_to_qwen.py, prioritizing last 2-3 sentences (the decision) via reversed sentence iteration.
**Why it matters:** Claude's extended thinking produces long reasoning chains. Most of it is restating the game state. The decision (last 2-3 sentences) is what matters for distillation. RAG-MCP (arxiv 2505.03275) confirms reasoning quality degrades when context > 3K tokens.

### 4. Agent 3/4 code-level exclusion
**How:** `EXCLUDED_AGENTS` set in `extract_turns.py`. Skips agent_3 and agent_4 directories entirely. Raw data deleted from VM for agent_4.
**Why separate from date cutoff:** Agent 3 (EFFICIENT) produced data after March 28 but the personality itself was broken. Code-level exclusion is more reliable than date filtering.

### 5. Desert quest waste filter
**How:** Turns where agent repeatedly navigates to x=770-790 and reasoning mentions "wife" or "stuck" are scored down. The Wife NPC was unreachable due to a wrong door coordinate (194,218 = Sorcerer, not Wife at 310,264).
**Fix applied:** Correct coordinates added to game_knowledge.md on April 2. Filter catches legacy data.

---

## Quality Scoring (convert_to_qwen.py)

Each turn is scored 0.0-1.0 on three axes:

| Axis | Weight | What it measures |
|------|--------|-----------------|
| State completeness | 0.4 | Does game state have player_position, player_stats, nearby_entities? |
| Action quality | 0.3 | MCP tool call (0.3) > click_tile (0.05) > no action (0) |
| Reasoning quality | 0.3 | Length (10-500 chars optimal), game keyword presence, no hallucination markers |

**Bonuses and penalties:**
- +0.05 alignment bonus: reasoning mentions action keywords (e.g., reasoning says "attack" + action is `attack(Rat)`)
- -0.10 mismatch penalty: reasoning says "heal" but action is "attack"
- -0.50 login screen: player at (0,0)
- -0.15 empty reasoning: < 10 chars

**Threshold:** `--min-score 0.3` (default). Turns below this are dropped.

---

## Final Dataset Composition (r7 rebuild, April 9)

| Metric | Value |
|--------|-------|
| Train records | 6,423 |
| Val records | 646 |
| Total | 7,069 |
| Split method | 90/10 stratified by session |
| navigate | 27.7% |
| attack | 14.9% |
| cancel_nav | 10.7% |
| interact_npc | 10.7% |
| warp | 9.4% |
| stuck_reset | 7.5% |
| move | 7.1% |
| click_tile | 3.9% |

**Source data (audit 2026-04-28 from session_log entry, post-Tier-A run-directory migration):**

| Agent | Total logs (May 3) | Total logs (May 2) | Total logs (May 1) | Total logs (Apr 28) | Total logs (Apr 16, prior) |
|-------|---------------------|---------------------|---------------------|----------------------------|
| agent_0 (GRINDER) | 583 | 570 | 559 | 490 | 223 |
| agent_1 (COMPLETIONIST) | 573 | 561 | 550 | 479 | 216 |
| agent_2 (EXPLORER_TINKERER) | 538 | 525 | 515 | 453 | 210 |
| **Total** | **1,694** | **1,656** | **1,624** | **1,422** | **675** |

Logs now sit at `dataset/raw/agent_*/runs/run_<TS>/`; pre-Apr 27 sessions were folded in by `migrate_logs_to_runs.py`. As of May 3: 294 runs / 1,694 sessions across 3 agents. Harness mix beyond Claude has expanded — Codex, Gemini, opencode (Qwen via NVIDIA NIM, DeepSeek V4, Grok), and xAI paths now share the same agent slots — but `INCLUDED_HARNESSES = {"claude", "unknown"}` in `convert_to_qwen.py` still gates training data to Claude only.

Only Claude logs feed into training. Gemini/Codex are collected for comparison but excluded via `INCLUDED_HARNESSES = {"claude", "unknown"}` in `convert_to_qwen.py`. The 650 extracted count slightly exceeds 583 claude logs because extraction runs on all harnesses — the harness filter applies at the convert step.

**Archetype split (within Claude training data):**
- Agent 0 (grinder): ~33% of dataset, combat-heavy sessions
- Agent 1 (completionist): ~28% of dataset, quest-focused
- Agent 2 (explorer_tinkerer): ~38% of dataset, exploration-heavy sessions

**Previous builds for reference:**
| Build | Train | Val | Total | Notes |
|-------|-------|-----|-------|-------|
| r5 (Apr 4) | 3,853 | 465 | 4,318 | First quality-filtered dataset |
| Apr 5 rebuild | 3,957 | 488 | 4,445 | +127 records, click_tile 5.6% |
| r7 (Apr 9) | 6,423 | 646 | 7,069 | +62% data, chat template fix, personality labels |
| r9 (Apr 15) | 5,871 | 575 | 6,446 | Degenerate filtering (-623), 100% reasoning, real system prompt |
| r10 (May 6, current) | 9,352 | 934 | 10,286 | Post-Core-3 Claude only: 5 runs × 3 agents = 135 sessions, 9,766 raw turns. Provenance metadata stamped at build time (source_runs, prompt_commit, core3_only). |

**r7-specific improvements:**
- Chat template fix (QwenLM/Qwen3#1831): `<think>` reasoning preserved in all assistant turns, not just the last
- Personality labels: every record tagged with personality (was None for all r6 records)
- click_tile rate down to 3.9% (from 5.6% in previous rebuild)

---

## Known Remaining Issues

1. **Training on game state format:** Loss masking (r4+) handles this, but early runs (r1-r3) trained on everything.
2. **Archetype imbalance:** grinder produces more combat turns, explorer_tinkerer more NPC interactions. Stratified split by session helps but doesn't guarantee action-type balance.
3. **Session length bias:** Long sessions (100+ turns) dominate the dataset. Short sessions (< 20 turns) are often crashes or rate-limit kills.
4. **Qwen tokenizer mismatch:** Qwen3.5 and Qwen3-VL share a base but have different special tokens. Training uses Qwen3.5 tokenizer; must match at inference.
5. **Corpus size for r10.** 10,286 records is well above the LoRA SFT floor (~1-5k for narrow task fine-tunes) but at the bottom of the full-fine-tune sweet spot (~10-30k). If r11 needs more data, collect more 6h Claude runs on the current Core 3 prompt and rebuild — every additional 6h run contributes ~30 sessions / ~2k records.
6. **accept_quest underrepresented:** Only 8 `accept_quest` actions in the full 7,069-record dataset despite active questing in logs. Likely a conversion/filter issue — `interact_npc` auto-accepts most quests, so explicit `accept_quest` calls are rare. May not be a bug.
7. **Multi-harness data exclusion:** Codex and Gemini harness logs are collected but excluded from Qwen SFT training via `INCLUDED_HARNESSES` filter in `convert_to_qwen.py`. Only Claude data trains the student model.

---

## Research References

- LIMA (arxiv 2305.11206): 1,000 clean examples match 50K+ noisy for instruction following
- Structured Agent Distillation (arxiv 2505.13820): Loss masking = +8pp task success
- RAG-MCP (arxiv 2505.03275): Reasoning degrades above ~19 tools and ~3K token prompts
