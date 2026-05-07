# Agent SFT Landscape

Survey of foundational papers on supervised finetuning for LLM agents, compiled April 2026. Covers distillation from large teacher models, trajectory formatting, loss design, and what matters for training quality.

---

## Foundational Papers

### FireAct (arXiv 2310.05915, ICLR 2024)

The paper that established agent SFT as a subfield. Fine-tuned Llama2-7B on 500 GPT-4 ReAct trajectories.

**Key findings:**
- 500 trajectories is the emergence threshold — below this, small models cannot learn the ReAct format
- Fine-tuned agents are dramatically more robust to noisy tools (ReAct EM drops 33.8% with noise vs 14.2% for FireAct)
- Multi-method training data (ReAct + CoT + Reflexion) gives the model flexibility to choose the right approach

**Our relevance:** Our 6.4k records are 12x above the emergence threshold. Fine-tuning will make Qwen 3.5 9B more robust than prompting for the noisy game environment.

### Agent-FLAN (arXiv 2403.12881, ACL 2024 Findings)

Systematic study of what makes agent SFT data work.

**Key findings:**
- Agent training mixes format-following and reasoning, which shifts too far from pretraining distribution — decompose them
- Different agent capabilities (tool selection, argument filling, reasoning) learn at different rates — track per-capability loss
- Naive agent tuning introduces hallucinations — include negative samples (model answering without tools) to teach when NOT to use tools

**Our relevance:** We don't currently include negative samples. Could add "observe and wait" examples where no action is appropriate.

### Structured Agent Distillation / SAD (arXiv 2505.13820, May 2025)

Most directly relevant to our setup. Segments trajectories into [REASON] and [ACT] spans.

**Key findings:**
- Span-specific loss weighting (+4-5% task success over standard token-level distillation)
- 12x model compression with minimal performance drop
- Masking observation tokens is critical — model wastes capacity memorizing environment format

**Our relevance:** r8 uses `train_on_responses_only()` from Unsloth which correctly masks all non-assistant tokens. (r5-r7 used `completion_only_loss=True` which was silently broken — TRL ignored it without a `response_template`.) We train on both reasoning and action tokens equally. Potential improvement: weight action spans higher.

### AgentTrek (arXiv 2412.09605, ICLR 2025 Spotlight)

Trajectory synthesis with enriched reasoning.

**Key findings:**
- Chain-of-thought reasoning in trajectories is essential for SFT quality
- Models trained on reasoning-enriched data dramatically outperform action-only trajectories
- Cost: $0.55 per high-quality trajectory via automated synthesis

**Our relevance:** Our `<think>` blocks from Claude's extended thinking are exactly this. The r7 chat template fix was critical — stock Qwen 3.5 template was silently stripping reasoning from intermediate turns.

### AgentRefine (arXiv 2501.01702, ICLR 2025)

Self-refinement trajectories for generalization.

**Key findings:**
- Standard agent SFT overfits to training environments
- Including failure-and-recovery trajectories teaches self-correction
- Models trained on refinement data generate more diverse reasoning

**Our relevance:** Our data naturally includes failure-recovery (death → respawn → re-equip → continue). We keep these in the training data. KTO's undesirable labels teach the model which failures to avoid.

### Agent-R1 (arXiv 2511.14460, Nov 2025)

End-to-end agent RL with explicit loss masking.

**Key findings:**
- Formalizes agent RL as extended MDP with multi-turn history and tool-calling transitions
- GRPO performed best among RL methods for agents (avg EM 0.3877)
- Explicit masking for environment/tool outputs — only compute gradients on model's reasoning + action tokens

**Our relevance:** Validates our SFT → KTO → GRPO pipeline. Loss masking consensus matches our r8 `train_on_responses_only` approach (fixed from broken `completion_only_loss` in r5-r7).

### Chain-of-Agents / AFM (arXiv 2508.13167, Aug 2025)

Two-phase training: SFT + agentic RL.

**Key findings:**
- SFT gives format and basic competence, but RL is needed for recovery and long-horizon optimization
- SFT alone plateaus — the model learns to imitate but not to adapt
- Released open-source Agent Foundation Models

**Our relevance:** Confirms that SFT is phase 1, not the end goal. KTO (phase 2) teaches preference, GRPO (phase 3) adds reward-shaped RL.

---

## Distillation-Specific Papers

### SCoRe (arXiv 2509.14257, Sep 2025)

Reinforced distillation — student generates, teacher corrects first error only.

**Key finding:** 7B student matches 72B teacher across 12 benchmarks. Standard trajectory distillation trains on teacher-distribution data the student can't reproduce, causing compounding errors.

**Our relevance:** Future improvement — after SFT, have Qwen generate game trajectories, use Claude to correct the first error, retrain.

### AgentArk (arXiv 2602.03955, Feb 2026)

Multi-agent distillation strategies.

**Key finding:** Process-aware distillation (supervising intermediate reasoning steps, not just final actions) performs best. Three strategies ranked: reasoning-enhanced > trajectory-based > process-aware.

**Our relevance:** Our multi-archetype setup is effectively multi-agent. All archetypes distill into one model — the diversity of strategies in training data helps. The active archetype system uses three capability axes — agent_0 → grinder, agent_1 → completionist, agent_2 → explorer_tinkerer — motivated by the n=30 behavior audit (see `docs/behavior-audit.md`).

### "What Do Agents Learn from Trajectory-SFT" (arXiv 2602.01611, Feb 2026)

Critical analysis of what SFT actually teaches.

**Key findings:**
- Trajectory SFT heavily memorizes interface patterns, not semantic tool understanding
- Under minimal interface rewrites (renamed params, reordered fields), trained agents degrade sharply
- SFT learns interface patterns quickly (few-turn suffices) but semantic reasoning needs longer context

**Our relevance:** Warning sign. Our model may memorize tool schemas rather than understanding tool semantics. Potential mitigation: add 5-10% of records with slightly varied tool schemas to force semantic learning.

---

## Tool Use Training

### ToolACE (arXiv 2409.00920, ICLR 2025)

**Key finding:** Achieved GPT-4-competitive tool use with 8B params using LoRA r=16, alpha=32. Dual-layer verification (rule-based + model-based) for training data quality.

### ToolACE-R (arXiv 2504.01400, Apr 2025)

**Key finding:** Iterative self-refinement for tool call quality. Model generates, verifies, and refines its own tool calls during training.

### xLAM / ActionStudio (arXiv 2409.03215 / 2503.22673)

**Key finding:** #1 on Berkeley Function-Calling Leaderboard. Data format standardization matters enormously — heterogeneous trajectory formats hurt training. Unified Format 2.0 across 98K trajectories.

---

## Game Agent Benchmarks

### lmgame-Bench / GamingAgent (arXiv 2505.15146, ICLR 2026 Poster)

**Key finding:** RL on a single game transfers to unseen games AND external planning tasks. Game-playing SFT/RL is a general capability builder.

### ORAK (arXiv 2506.03610, ICLR 2026)

**Key finding:** Cross-game finetuning transfers gameplay meta-knowledge from large to small models. ~10k samples across 12 games. Generalizes to OOD games and non-game tasks.

### Think in Games / TiG (arXiv 2508.21365, Tencent, Aug 2025)

**What they do:** SFT (distilled from DeepSeek-R1 671B) → online GRPO on Honor of Kings (commercial MOBA). 40 predefined categorical macro-actions. Binary reward: predicted action == expert replay label.

**Key result:** Qwen-3-14B + SFT + GRPO (2000 steps) reaches **90.91% action prediction accuracy**, beating the 671B teacher (DeepSeek-R1 at 86.67%).

**Critical distinction — this is NOT an autonomous agent.** TiG reads replay frames and suggests actions for human players to execute. The model never controls the game. Kaetram's agent issues typed tool calls that drive a live browser, navigate a real game world, manage inventory, complete quests, and fight mobs autonomously. These are categorically different research problems.

**Differentiators from our work:**
- Action space: 40 flat categorical labels vs. our 17 typed MCP tools with arguments (`navigate(x,y)`, `attack("goblin")`) — ours is compositionally richer
- Teacher diversity: single DeepSeek-R1 policy vs. our 3 personality-distinct Claude agents
- Post-SFT: GRPO (online, 32 × H20 GPUs, Tencent scale) vs. KTO (offline, composite 6-dimension reward, 1 H100)
- Data: proprietary Tencent production replays (undisclosed scale) vs. our fully open 6.4k records
- Game: episodic MOBA (no persistence) vs. persistent MMORPG (quest state, inventory, XP accumulate across sessions)
- Reproducibility: zero (proprietary game + data) vs. full (open-source game + public pipeline)

**Eval metric insight:** TiG's 90.91% is action prediction accuracy against held-out expert replay labels. We can construct an equivalent: hold out Claude sessions, measure whether student reproduces Claude's tool call given the same observation. This gives us a headline number analogous to TiG's, computable on existing data, without circular dependence on KTO reward signal.

**Our relevance:** Cite TiG as the closest methodological paper but clearly differentiate on: (1) autonomous agent vs. advisor, (2) persistent MMORPG vs. episodic MOBA, (3) KTO offline preference learning vs. GRPO online RL, (4) open source vs. proprietary. The "student beats teacher" result is what we should aim for in our eval table.

---

## Key Takeaways for Our Pipeline

1. **500 trajectories is the floor, 6k is the sweet spot** — r10 sits at ~10k records (135 sessions, post-Core-3 Claude only); above the LoRA floor and within the multi-turn-tool-use sweet spot
2. **Loss masking is consensus** — mask observations, train on reasoning + actions
3. **CoT reasoning is essential** — never strip `<think>` blocks (our r7 template fix was critical)
4. **SFT alone plateaus** — KTO/GRPO follow-ups are scaffolded but deferred (project pivoted to quest completion as the headline benchmark)
5. **Failure-recovery data is valuable** — keep death/respawn sequences
6. **Interface memorization is a risk** — model may memorize tool format rather than semantics
7. **Multi-agent diversity helps** — our 3 capability archetypes (GRINDER / COMPLETIONIST / EXPLORER_TINKERER, post-KAE-46) create more robust training signal than the prior trait-based personalities
