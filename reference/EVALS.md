# Evaluating Game-Playing LLM Agents: SOTA Research & Protocol

> **Current OPD 2B follow-up:** see the manifest-driven
> [weights × recovery factorial and held-out no-walkthrough runbook](../research/experiments/opd-2b-factorial.md).

**Compiled:** April 13, 2026
**Sources:** ICLR 2025-2026, NeurIPS 2024-2025, ACL 2025, arXiv, Berkeley BFCL, Anthropic, OpenAI, DeepEval, Reddit (r/LocalLLaMA, r/MachineLearning), practitioner guides
**Scope:** Evaluating finetuned vs base LLM game agents, MCP tool-use evaluation, MMORPG agent benchmarks, statistical methodology for agent comparison
**Application:** Qwen 3.5 9B base vs r10-sft on Kaetram (2D MMORPG, 17 MCP tools as of 2026-04-18; historically 22 during r7-r8 data collection)

---

## Table of Contents

1. [Game Agent Evaluation Benchmarks (2025-2026)](#1-game-agent-evaluation-benchmarks-2025-2026)
2. [Tool-Use and MCP Evaluation Frameworks](#2-tool-use-and-mcp-evaluation-frameworks)
3. [SFT Model Evaluation Best Practices](#3-sft-model-evaluation-best-practices)
4. [Metrics Taxonomy for Game Agents](#4-metrics-taxonomy-for-game-agents)
5. [Proposed Eval Protocol: Qwen Base vs r10-sft](#5-proposed-eval-protocol-qwen-base-vs-r10-sft)
6. [Fixed Evaluation Scenarios](#6-fixed-evaluation-scenarios)
7. [Statistical Methodology](#7-statistical-methodology)
8. [Automated Eval Frameworks](#8-automated-eval-frameworks)
9. [Implementation Using Existing Infrastructure](#9-implementation-using-existing-infrastructure)
10. [Anti-Patterns and Pitfalls](#10-anti-patterns-and-pitfalls)
11. [References](#11-references)

---

## 1. Game Agent Evaluation Benchmarks (2025-2026)

### Orak (ICLR 2026) — Most Directly Relevant

Orak is the closest benchmark to our setup: it uses an **MCP-based plug-and-play interface** where each game is exposed as an MCP server with typed tools. The agent calls structured actions, not raw code.

**Key results:**
- Fine-tuned LLaMA-3.2-3B outperformed base model in 3/5 games
- Demonstrated OOD transfer to unseen games (WebShop: 0% -> 8.4%)
- Dataset: ~11K samples from expert LLM trajectories with 10x paraphrase augmentation
- 3-20 trials per game, normalized scores

**Evaluation protocol:** Normalized game scores (0-1), averaged over multiple seeds. Reports mean and standard error. Separate in-distribution and out-of-distribution evaluations to test generalization.

**Why this matters for us:** Same architecture pattern (MCP tools + SFT from expert trajectories). Their LLaMA-3.2-3B is comparable in scale to our Qwen3.5-9B. Their 11K samples with 10x augmentation aligns with our ~6.4K records. Their OOD transfer result motivates testing whether our finetuned model generalizes to novel quest scenarios.

### GamingAgent / lmgame-Bench (ICLR 2026)

6 games evaluated with two complementary metric types:
- **Progression Rewards** — How far did the agent get? Continuous score reflecting partial progress.
- **Long-Horizon Rewards** — Did the agent solve the overall task? Binary success metric.

**Statistical methodology:** Paired-sample t-tests for significance, Glass's delta for effect size (uses base model SD as denominator — appropriate when comparing finetuned to unfinetuned). Key finding: **prompt standardization reduced variance 33.8-63.5%** across games. They identified 4 latent capability factors through factor analysis.

**Why this matters for us:** Their dual-metric approach (progression + binary success) maps directly to our needs. We should report both XP/turn (progression) and quest completion rate (binary success). Their use of Glass's delta is the right effect size measure for base-vs-finetuned comparisons.

### BALROG (ICLR 2025)

Benchmark for Agentic LLM and VLM Reasoning On Games. 6 games (BabyAI, TextWorld, Crafter, Baba Is AI, MiniHack, NetHack).

**Key metric:** Progression Score — normalized 0-100 per game, measuring how far the agent gets toward game completion.

**Methodology:** 5-25 seeds per game depending on variance, reports mean +/- standard error. Key insight: **vision often hurts performance** — text-only observations can outperform multimodal ones. They identified a "knowing-doing gap" where models understand game mechanics but fail to execute.

**Why this matters for us:** The "knowing-doing gap" is exactly what SFT should close — the base model may "know" game mechanics but fail to execute the right tool calls. Our eval should specifically test whether SFT closes this gap.

### HeroBench (August 2025) — Most Similar Game Domain

RPG-focused benchmark with 844 tasks across categories directly relevant to Kaetram:
- Gather resources
- Craft items
- Level skills
- Fight monsters

**Metrics:** Success Rate (binary) + Progress Score (partial credit, 0-1). 9 difficulty levels allow measuring where each model's capability ceiling lies.

**Why this matters for us:** Most similar task domain to Kaetram. Their difficulty-level approach suggests we should test across multiple difficulty tiers (rat grinding vs quest completion vs multi-zone navigation).

### TITAN (2025) — Actual MMORPG Testing

Tested LLM agents in actual MMORPGs with three metrics:
- **Task Success Rate** (95% reported)
- **State Coverage** (74%) — fraction of reachable game states visited
- **Bug Detection Rate** (82%) — secondary metric for QA applications

5 runs averaged per condition. State abstraction similar to our `observe()` output: location, vitals (High/Med/Low), nearby NPCs, active objectives.

**Why this matters for us:** Validates that MMORPG evaluation is feasible with structured state abstractions. Their state coverage metric maps to our "unique positions visited" metric from `score_sessions.py`.

### AgentBench (NeurIPS 2024)

8 environments including game tasks. Key contribution: **standardized difficulty levels** and **partial credit scoring**. Found that open-source models (at the time) scored <10% on complex reasoning tasks vs frontier models >60%.

**Why this matters for us:** The gap they found between open-source and frontier models is the gap SFT should narrow. Our eval should quantify how much of Claude's performance Qwen3.5-9B recovers after finetuning.

---

## 2. Tool-Use and MCP Evaluation Frameworks

### MCPAgentBench

Purpose-built for evaluating MCP tool-calling agents. Four metrics:

| Metric | Formula | What It Measures |
|--------|---------|-----------------|
| **Task Finish Score (TFS)** | `sum(IsFinished * |G|) / sum(|G|)` | Weighted task completion (harder tasks worth more) |
| **Task Efficiency Finish Score** | TFS + execution order penalty | Completion quality + did the agent do things in a sensible order? |
| **Token Efficiency** | `tokens_used / tokens_optimal` | How verbose is the agent? Lower is better. |
| **Time Efficiency** | `wall_time / optimal_time` | Real-time performance. |

**Why this matters for us:** TFS maps directly to our quest completion scoring. Token efficiency matters because our student model (9B params) should ideally be more token-efficient than the teacher (Claude Opus/Sonnet).

### MCP-Radar

5-dimensional evaluation for MCP tool-calling:

1. **Result Accuracy** — Did the final result match the expected outcome?
2. **Dynamic Tool Selection Rate** — Penalizes ANY wrong tool call, even if final result is correct. Formula: `1 - (wrong_calls / total_calls)`. Key insight: a model that "gets there eventually" by trying many tools is worse than one that picks correctly first.
3. **First Error Position** — How far into the sequence does the first wrong call occur? Later is better.
4. **Computational Resource Efficiency** — Tokens consumed relative to task complexity.
5. **Response Time Efficiency** — Latency per tool call.

**Why this matters for us:** Dynamic Tool Selection Rate is worth tracking even with the tightened 17-tool surface (down from 22 as of 2026-04-18). We sit below the RAG-MCP ~19-tool degradation threshold now, which should reduce raw tool-selection confusion, but whether SFT still improves tool-selection accuracy over the base model remains the core question.

### BFCL V4 (Berkeley Function Calling Leaderboard)

AST-based function calling evaluation. V4 adds holistic agentic evaluation beyond single-turn tool calls.

**Key finding:** "Single-turn tool calls work well across most models, but memory, dynamic decision-making, and long-horizon reasoning remain open challenges."

**Evaluation dimensions:**
- **Exact match** — Did the model produce the exact correct function call?
- **Structural accuracy** — Correct function name + correct argument types?
- **Semantic accuracy** — Would the call achieve the intended effect even if not identical?

**Why this matters for us:** We should report both exact-match (same tool + same args as Claude teacher) and semantic accuracy (would the action achieve a reasonable outcome?) for our offline eval.

### DeepEval Agent Metrics

6 metrics across 3 evaluation layers:

**Reasoning Layer:**
- `PlanQualityMetric` — Does the agent's reasoning in `<think>` blocks reflect a coherent plan?
- `PlanAdherenceMetric` — Does the agent follow through on its stated plan?

**Action Layer:**
- `ToolCorrectnessMetric` — `correct_tools / total_called`
- `ArgumentCorrectnessMetric` — `correct_args / total_args` (given correct tool)

**Execution Layer:**
- `TaskCompletionMetric` — Binary or partial credit task success
- `StepEfficiencyMetric` — `optimal_steps / actual_steps`

**Why this matters for us:** This 3-layer taxonomy cleanly separates what we need to measure. The base model might reason well (plan quality) but fail at execution (tool correctness), or vice versa. Knowing WHERE the model fails informs what to fix.

---

## 3. SFT Model Evaluation Best Practices

### Base vs Finetuned Comparison Dimensions

From AgentMerge (2025) and the broader SFT evaluation literature, the key dimensions:

| Dimension | What to Measure | Risk |
|-----------|----------------|------|
| **Task completion** | Primary success metric. Did the model do the thing? | Overfitting to training distribution |
| **Tool use accuracy** | Right tools, right arguments, right sequence | Training on tool format without understanding semantics |
| **Reasoning quality** | CoT quality in `<think>` blocks | Parroting teacher reasoning without adaptation |
| **Generalization** | Performance on held-out scenarios not in training data | Catastrophic narrowing |
| **Catastrophic forgetting** | Did finetuning break general capabilities? | Over-specialization |
| **Efficiency** | Fewer steps/tokens to achieve same goals | Verbosity from teacher mimicry |

### The Overfitting Trap (AgentMerge, 2025)

AgentMerge found that SFT on expert trajectories can cause **catastrophic forgetting of reasoning abilities** — the model learns to mimic actions without understanding why. Ablation studies must maintain uniform dataset sizes to control for this.

**Implication for us:** We should test whether the finetuned model can recover from novel failure states (e.g., death in an unfamiliar area) or if it only works in training-distribution scenarios (Mudwich area, rat/snek combat).

### Offline vs Online Evaluation

Two complementary evaluation modes:

**Offline (no gameplay):**
- Action prediction accuracy on held-out Claude sessions
- Tool selection F1 given the same observation
- Argument match accuracy given correct tool
- Reasoning coherence scoring (LLM-as-judge)
- Fast, cheap, reproducible — run on static data

**Online (live gameplay):**
- Actual quest completion, XP gain, deaths
- Measures the full agent loop including error recovery
- Expensive (Modal GPU costs), noisy, requires game server
- The ground truth — offline metrics are proxies for this

**Best practice:** Use offline metrics for rapid iteration (hyperparameter tuning, data quality checks), online metrics for final comparison (paper table).

### Action Prediction Accuracy (proposed in `paper/contribution.md`)

Hold out 10-15% of Claude sessions. For each observation in the held-out set, ask: does the finetuned model predict the same tool call Claude made?

This is directly analogous to Orak's evaluation and TiG's 90.91% action prediction headline. It avoids circular dependence on the KTO reward signal (which uses XP/level delta/exploration — different from the action prediction metric).

**Metric:** `action_match = sum(model_tool == teacher_tool) / total_observations`

Also measure:
- **Top-3 accuracy** — Is the correct tool in the model's top 3 predictions? (Relevant because multiple tools can be valid at any game state)
- **Argument accuracy** — Given correct tool, are the arguments correct? (e.g., correct mob name for `attack()`, correct coordinates for `navigate()`)

---

## 4. Metrics Taxonomy for Game Agents

### Tier 1: Primary Metrics (Paper Table)

These are the metrics that go in the main results table. They should be interpretable to a reviewer who hasn't played Kaetram.

| Metric | Formula | Source | Why Primary |
|--------|---------|--------|-------------|
| **Quest Completion Rate** | `sessions_with_quest_complete / total_sessions` | `score_sessions.py` quest metrics | Binary success — the clearest signal |
| **XP per Turn** | `xp_delta / n_turns` | `score_sessions.py` xp_delta | Progression efficiency — continuous, comparable |
| **Survival Rate** | `sessions_with_0_deaths / total_sessions` | `score_sessions.py` death_flags | Measures basic competence |
| **Tool Parse Rate** | `valid_tool_calls / total_model_outputs` | Session log parsing | Does the model produce syntactically valid tool calls at all? |
| **Deaths per Session** | `total_deaths / total_sessions` | `score_sessions.py` respawn_count | Inverse safety metric |

### Tier 2: Diagnostic Metrics (Appendix / Analysis)

These explain WHY Tier 1 metrics differ between models.

| Metric | Formula | What It Reveals |
|--------|---------|-----------------|
| **Action Distribution Entropy** | `-sum(p_i * log(p_i))` over 17 tools | Higher = more diverse tool use. Base model may spam one tool. |
| **Navigation Efficiency** | `1 - (stuck_reset + cancel_nav) / total_nav_actions` | Does the model navigate effectively? |
| **Combat Win Rate** | `kills / attack_attempts` | Does the model fight effectively? |
| **HP Management** | `eat_food_calls_below_50pct_hp / eat_food_calls_total` | Does the model eat food at appropriate times? |
| **Reasoning-Action Alignment** | LLM-as-judge: does `<think>` content match the chosen action? | Quality of CoT reasoning |
| **Turns to First Quest** | Turn number of first quest completion | How quickly can the model complete a quest? |
| **Unique Positions** | Count of distinct (x,y) positions visited | Exploration breadth |
| **Repetitive Action Rate** | `triples_of_same_action / total_actions` | Behavioral loops (a failure mode) |

### Tier 3: Offline Metrics (No Live Gameplay)

| Metric | Formula | Data Source |
|--------|---------|-------------|
| **Action Prediction Accuracy** | `correct_tool / total_observations` on held-out Claude data | 10-15% held-out Claude sessions |
| **Tool Selection F1** | Per-tool precision/recall vs Claude teacher | Same held-out data |
| **Argument Accuracy** | `correct_args / total` given correct tool | Same held-out data |
| **Reasoning Coherence** | LLM-as-judge 1-5 score on `<think>` blocks | Sampled from model outputs |
| **Perplexity on Held-out Data** | Model perplexity on val split | `dataset/qwen_sft/val.json` |

---

## 5. Proposed Eval Protocol: Qwen Base vs r10-sft

### Design

**Models under evaluation:**
1. **Qwen 3.5 9B base** (unfinetuned) — `serve_modal_base.py`, eval port 9071
2. **Qwen 3.5 9B r10-sft** (finetuned) — `serve_modal.py`, eval port 9061
3. (Future: **r10-sft + KTO** — after KTO training completes)

**Controlled variables:**
- Same MCP server (`mcp_game_server.py`) with identical 17 tools
- Same system prompt (resolved from `prompts/system.md`)
- Same game server (Kaetram-Open, same version)
- Same harness (`play_qwen.py`) with identical parameters
- Same starting state (Level 1, Mudwich, Bronze Axe — reset via MongoDB)

**Independent variable:** Model weights only.

### Phase 1: Offline Evaluation (Fast, Cheap)

Run immediately after r10-sft training completes, before any live gameplay.

1. **Perplexity comparison** on val split (live counts in `dataset/qwen_sft/metadata.json`)
   - Expect: r10-sft << base perplexity on game-formatted data
   - This is a sanity check, not a headline metric

2. **Action prediction on held-out Claude sessions**
   - Hold out 10% of Claude sessions (~64 records) — select randomly, stratified by personality
   - For each observation, feed to both models, compare predicted tool call vs Claude's actual tool call
   - Report: top-1 accuracy, top-3 accuracy, per-tool F1, argument accuracy

3. **Reasoning quality comparison**
   - Sample 50 observations, get both models' `<think>` outputs
   - Use Claude as judge: rate 1-5 on relevance, coherence, game awareness
   - Report: mean score + distribution

### Phase 2: Online Evaluation (Live Gameplay)

Run after offline eval confirms the finetuned model is functional.

1. **Fixed scenarios** (see Section 6) — 30 episodes per model per scenario
2. **Open-ended gameplay** — 50 episodes per model, 300 turns each, fresh Level 1 start
3. **Compute all Tier 1 + Tier 2 metrics** from session logs
4. **Statistical comparison** (see Section 7)

### Phase 3: Ablation Studies (After Main Results)

| Ablation | Compare | What It Shows |
|----------|---------|---------------|
| r10-sft vs r10-sft+KTO | SFT only vs SFT+preference learning | KTO value-add |
| 1 personality vs 3 personalities | Train on AGGRESSIVE-only, eval same protocol | Diversity value |
| Loss-masked (r8) vs unmasked (r7) | Same data, different loss masking | Masking value |
| 17 tools vs filtered tools | Full toolset vs scenario-specific subset | Tool count impact |

---

## 6. Fixed Evaluation Scenarios

Controlled scenarios with defined success criteria. Each tests a different capability.

### Scenario A: Rat Grind (Basic Combat Loop)

| Parameter | Value |
|-----------|-------|
| Start State | Level 1, Mudwich center (188, 157), Bronze Axe |
| Success Criteria | Kill 10 rats (verify via XP gain or kill count) |
| Max Turns | 100 |
| Tests | Basic OODA loop: observe -> identify target -> attack -> loot -> repeat |
| Expected Base Behavior | May struggle with tool syntax, random tool selection |
| Expected SFT Behavior | Clean combat loop, appropriate eat_food calls |

### Scenario B: Quest Completion (Multi-Step Task)

| Parameter | Value |
|-----------|-------|
| Start State | Level 5, Mudwich, adequate gear |
| Success Criteria | Accept + complete Bike Lyson quest (kill 25 Sneks) |
| Max Turns | 200 |
| Tests | NPC interaction, quest tracking, navigation to Snek area, sustained combat, quest turn-in |
| Expected Base Behavior | May not know how to interact with NPCs or track quest progress |
| Expected SFT Behavior | Navigate to Bike Lyson, accept quest, navigate to Snek area, grind, return |

### Scenario C: Multi-Zone Navigation (Exploration)

| Parameter | Value |
|-----------|-------|
| Start State | Level 10, Mudwich |
| Success Criteria | Visit 3+ distinct zones (tracked via warp targets or position clusters) |
| Max Turns | 150 |
| Tests | Warp usage, zone awareness, NPC discovery, map navigation |
| Expected Base Behavior | May get stuck in one area, not know warp locations |
| Expected SFT Behavior | Use warp tool effectively, explore multiple zones |

### Scenario D: Open-Ended Play (Holistic Assessment)

| Parameter | Value |
|-----------|-------|
| Start State | Level 1, Mudwich, Bronze Axe |
| Success Criteria | No fixed criteria — measure all metrics over 300 turns |
| Max Turns | 300 |
| Tests | Full agent capability: combat, questing, navigation, resource management, error recovery |
| This is the paper's main result | Compare XP/turn, quest completion, deaths, exploration |

---

## 7. Statistical Methodology

### Sample Size

**Minimum:** 30 episodes per model per scenario (Central Limit Theorem).
**Recommended:** 50 episodes for open-ended play (Scenario D) — higher variance requires more samples.
**Orak precedent:** 3-20 trials. **BALROG precedent:** 5-25 seeds. **TITAN precedent:** 5 runs. We're being more rigorous than most published work at 30-50.

### Significance Testing

**Primary test:** Welch's t-test (does not assume equal variance between base and finetuned). For metrics that are clearly non-normal (e.g., deaths per session — count data), use Mann-Whitney U test instead.

**Effect size:** Glass's delta — uses the base model's standard deviation as the denominator. This is the correct effect size for base-vs-finetuned comparisons because we care about improvement relative to baseline variability, not pooled variability.

```
Glass's delta = (mean_sft - mean_base) / sd_base
```

Interpretation: |d| < 0.2 small, 0.2-0.8 medium, > 0.8 large.

### Multiple Comparisons

With 5 Tier 1 metrics, apply **Bonferroni correction**: significance threshold = 0.05 / 5 = 0.01. Alternatively, use **Benjamini-Hochberg FDR** (less conservative, more power).

### Confidence Intervals

**Bootstrap resampling** with 1000+ resamples for 95% CIs on all metrics. This is more robust than parametric CIs for small sample sizes and non-normal distributions.

```python
import numpy as np

def bootstrap_ci(data, n_boot=1000, alpha=0.05):
    boots = [np.mean(np.random.choice(data, len(data), replace=True)) for _ in range(n_boot)]
    return np.percentile(boots, [100*alpha/2, 100*(1-alpha/2)])
```

### Reporting Format

For each metric, report:

```
Metric: Mean +/- 95% CI [p-value, Glass's delta]
```

Example:
```
Quest Completion Rate:
  Base:     0.07 +/- [0.02, 0.14]
  r10-sft: 0.43 +/- [0.31, 0.56]
  p < 0.001, Glass's d = 2.14 (large)
```

---

## 8. Automated Eval Frameworks

### Inspect AI (UK AISI)

Open-source, supports MCP natively, has scorer abstractions. Could wrap our `play_qwen.py` as an Inspect task with custom scorers.

**Pros:** MCP-native, well-documented, active development, supports multi-turn evaluation.
**Cons:** Overhead of adapting our harness to their task format.
**Verdict:** Consider for standardization but not blocking — our existing infrastructure is sufficient.

### Custom Harness (Recommended for V1)

Leverage existing `play_qwen.py` + `extract_turns.py` + `score_sessions.py` pipeline:

1. `play_qwen.py` runs episodes and produces JSONL logs
2. `extract_turns.py` parses logs into structured turns
3. `score_sessions.py` computes per-session metrics
4. New `eval_compare.py` aggregates across models, computes statistics, produces comparison table

This is lower overhead than integrating a framework and reuses proven infrastructure.

### LLM-as-Judge for Reasoning Quality

Use Claude (or GPT-4) as a judge for reasoning quality. For each sampled `<think>` block:

**Prompt:**
```
You are evaluating the reasoning quality of a game-playing AI agent.
Given the game observation and the agent's reasoning, rate on a 1-5 scale:

1 = Incoherent or irrelevant reasoning
2 = Mentions game elements but reasoning is confused
3 = Reasonable but misses important context
4 = Good reasoning, considers relevant factors
5 = Excellent reasoning, clear plan tied to game state

Observation: {observation}
Agent reasoning: {think_block}
Agent action: {tool_call}

Rating (1-5):
Justification (1 sentence):
```

Run on 50 samples per model. Report mean + distribution.

---

## 9. Implementation Using Existing Infrastructure

### What Already Exists

| Component | File | What It Does |
|-----------|------|--------------|
| Agent harness | `play_qwen.py` | Runs episodes, produces JSONL logs |
| Base model endpoint | `finetune/serve_modal_base.py` | Unfinetuned Qwen3.5-9B on Modal A100 |
| Finetuned endpoint | `finetune/serve_modal.py` | r10-sft model on Modal A100 |
| Eval launcher | `scripts/run-eval.sh` | Starts game servers (9061/9071), runs r10-sft vs base in parallel |
| Turn extraction | `extract_turns.py` | Parses JSONL logs into OODA cycles |
| Session scoring | `score_sessions.py` | 21-metric session quality score |
| Turn scoring | `convert_to_qwen.py:score_turn()` | Per-turn quality (state completeness, action quality, reasoning) |
| Dashboard | `dashboard.py` | Live comparison via `/api/compare` endpoint |
| DB reset | `scripts/reset-state.sh` | Reset MongoDB to fresh Level 1 |

### What Needs to Be Built

| Component | Purpose | Complexity |
|-----------|---------|------------|
| `eval_harness.py` | Run N episodes per model, manage DB resets between episodes, collect logs | Medium — wraps `play_qwen.py` with episode management |
| `eval_compare.py` | Aggregate session scores across models, compute statistics, produce comparison table | Low — statistics on `score_sessions.py` output |
| `eval_offline.py` | Action prediction accuracy on held-out Claude sessions | Medium — feed observations to model, compare outputs |
| Held-out split | Reserve 10% of Claude sessions for offline eval | Low — modify `convert_to_qwen.py` to produce eval split |

### Eval Run Cost Estimate

- **Modal A100 GPU**: ~$2.50/hr per model
- **50 episodes x 300 turns**: ~4-6 hours per model (assuming ~5 min/episode)
- **Two models**: ~$25-30 total
- **Offline eval**: Negligible (inference on ~64 held-out records)

---

## 10. Anti-Patterns and Pitfalls

### Don't Evaluate on Training Data
The val split (646 records) was created by the same pipeline as the train split. While stratified by session, it's not a true held-out test set. Create a separate eval split from sessions the model has never seen.

### Don't Use a Single Episode
Game agent performance has high variance. A single lucky/unlucky episode proves nothing. Minimum 30 episodes per condition.

### Don't Conflate Offline and Online Metrics
High action prediction accuracy (offline) doesn't guarantee good gameplay (online). The model might predict the right action 80% of the time but fail catastrophically on the 20% — which compounds over a 300-turn episode. Both are needed.

### Don't Ignore the Base Model's Tool Syntax
The base Qwen 3.5 9B may not produce valid MCP tool calls AT ALL without finetuning. If so, the "tool parse rate" metric is the first and most important result — it shows the minimum value of SFT.

### Don't Compare on Too Many Metrics
Bonferroni correction gets punishing fast. Pick 5 primary metrics, report the rest as supplementary. Reviewers want a clear story, not a 30-row table.

### Don't Forget Catastrophic Forgetting
After SFT, test whether the model can still do basic tasks that any instruction-tuned model should handle (e.g., "what is 2+2?" or "summarize this text"). If it can't, the finetuning was too aggressive.

### Don't Ignore Session Warm-up Effects
The first few turns of every episode are login + initial observation. These are deterministic and shouldn't count toward progression metrics. Start measurement after the first `observe()` call returns valid game state.

---

## 11. References

### Game Agent Benchmarks

1. **Orak: An MCP-Based Benchmark for Game Playing LLM Agents** — ICLR 2026. MCP-based plug-and-play game evaluation. Fine-tuned LLaMA-3.2-3B on expert trajectories. 12 games, 3-20 trials.

2. **GamingAgent / lmgame-Bench** — ICLR 2026. 6 games, progression + long-horizon rewards. Paired t-tests, Glass's delta. Identified 4 latent capability factors.

3. **BALROG: Benchmarking Agentic LLM and VLM Reasoning on Games** — ICLR 2025. Progression Score (0-100), 6 games, 5-25 seeds. Vision often hurts performance.

4. **HeroBench: RPG Agent Evaluation** — August 2025. 844 tasks, 9 difficulty levels. Success Rate + Progress Score. RPG-specific (combat, crafting, gathering, leveling).

5. **TITAN: Testing LLM Agents in MMORPGs** — 2025. Task Success Rate, State Coverage. Tested in actual MMORPGs.

6. **AgentBench: Evaluating LLMs as Agents** — NeurIPS 2024. 8 environments, standardized difficulty levels, partial credit scoring.

7. **SmartPlay: A Benchmark for LLMs as Intelligent Agents** — NeurIPS 2024. 6 games, 9 capability dimensions, multi-turn evaluation.

### Tool-Use Evaluation

8. **MCPAgentBench: Benchmarking MCP Tool-Calling Agents** — 2025. Task Finish Score, Token Efficiency, Time Efficiency.

9. **MCP-Radar: 5-Dimensional MCP Evaluation** — 2025. Result Accuracy, Dynamic Tool Selection Rate, First Error Position.

10. **BFCL V4 (Berkeley Function Calling Leaderboard)** — 2025. AST-based function calling eval, V4 adds holistic agentic evaluation. gorilla.cs.berkeley.edu/leaderboard

11. **DeepEval: Agent Evaluation Framework** — 2025. 6 metrics across reasoning/action/execution layers. docs.confident-ai.com

### Agent SFT and Distillation

12. **AgentMerge: Merging Agent Capabilities** — 2025. Found catastrophic forgetting from SFT on expert trajectories. Uniform dataset sizes critical for ablation.

13. **SAD: Distilling LLM Agents** — 2025. Teacher-student distillation for game agents. Action prediction accuracy as primary metric.

14. **FireAct: Toward Language Agent Fine-Tuning** — 2024. SFT on ReAct trajectories. Action accuracy + task success rate.

### Statistical Methods

15. **Glass, G.V. (1976).** Primary, secondary, and meta-analysis of research. *Educational Researcher*, 5(10), 3-8. Glass's delta for effect size.

16. **Efron, B. & Tibshirani, R. (1993).** *An Introduction to the Bootstrap.* Chapman and Hall. Bootstrap confidence intervals.

17. **Benjamini, Y. & Hochberg, Y. (1995).** Controlling the false discovery rate. *Journal of the Royal Statistical Society*, 57(1), 289-300. FDR correction for multiple comparisons.

### Practical Guides

18. **RAG-MCP: Tool Count Degradation** — arXiv 2505.03275. Performance degrades beyond ~19 tools. Relevant: we have 17 tools as of 2026-04-18 (down from 22), now just below the threshold.

19. **r/LocalLLaMA discussions on finetuned model evaluation** — Consensus: always compare to base model, use held-out test sets, report confidence intervals, test for catastrophic forgetting.

20. **Inspect AI** — UK AISI open-source eval framework. MCP-native, multi-turn scorer support. github.com/UKGovernmentBEIS/inspect_ai

21. **Anthropic: Evaluating Claude Models** — Best practices for evaluating AI model outputs. docs.anthropic.com
