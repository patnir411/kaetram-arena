# Preference Learning Landscape

Survey of preference optimization methods relevant to game agent distillation. Covers what each method needs, what it gives, and where it fits in our pipeline.

---

## Methods We Use or Plan to Use

### KTO — Kahneman-Tversky Optimization (arxiv 2402.01306)

**What it is:** Post-SFT preference learning using binary labels (desirable/undesirable) instead of pairwise preferences. Based on prospect theory — humans weight losses more than gains.

**What it needs:** Unpaired examples with binary labels. No need to match good/bad pairs for the same prompt.

**Why we chose it (over DPO):** Our data is unpaired — we have 640 sessions labeled by outcome (XP gain, deaths, quest progress), not pairs of good/bad actions from the same game state. KTO fits our data natively. DPO would require constructing artificial pairs. (KAE-13)

**Our implementation:** Score sessions 0-1 from outcome signals → top 40% desirable, bottom 30% undesirable → sliding windows (size=5, stride=2) → TRL `KTOTrainer` on r6 SFT checkpoint.

**Key result from paper:** Matches DPO on MT-Bench within 0.1-0.5 points using unpaired data.

**Status:** Code complete (April 5). Smoke test passed (10/10 steps, train_loss=0.617, KL active). Full run **deferred indefinitely** as of 2026-04-25 (benchmark pivot, PR #29). Pipeline still scaffolded.

### GRPO — Group Relative Policy Optimization (DeepSeek, 2024)

**What it is:** RL method that generates multiple completions per prompt, scores them with a reward function, and trains the model to prefer higher-scoring completions. No critic model needed (unlike PPO).

**What it needs:** A reward function + multiple generations per prompt. We use: XP gain, quest progress, death avoidance, action quality.

**Our implementation:** `finetune/train_grpo_modal.py` with `NUM_GENERATIONS=4`, flat reward from outcome signals.

**Known issues (KAE-12):**
- Length bias: vanilla GRPO normalizes loss per-response length → model learns "write more = lower loss" (reward hacking via verbosity)
- Zero-gradient waste: obvious game states (dead → respawn) produce identical rewards across all completions → zero gradient → wasted compute

**Status:** Implemented but needs Dr. GRPO + DAPO patches before production use.

### Tree-GRPO (arxiv 2509.21240, ICLR 2026)

**What it is:** Tree-structured variant of GRPO. Instead of independent completions, branches share common reasoning prefixes and diverge at decision points. 1.5x more efficient, gives step-level credit assignment.

**Example:** From "Level 5, HP 30%, Rat nearby": all branches share "I'm low HP and there's a Rat..." then diverge into heal/attack/flee. The tree shows which decision point mattered.

**Key result:** 69% improvement over flat GRPO, 75% cost reduction. Code: github.com/AMAP-ML/Tree-GRPO.

**Our fit:** Natural match — our MCTS planner (`world/mcts.py`) already does tree search. Could inform branching structure. But requires custom trainer (TRL doesn't support trees natively). (KAE-18)

**Status:** Backlog. Estimated 2 weeks for custom trainer.

---

## Methods That Fix Known GRPO Issues

### Dr. GRPO (arxiv 2503.20783)

**Problem solved:** GRPO's per-response length normalization rewards verbose reasoning.

**Fix:** Remove per-response length normalization. Use group-level mean length as uniform scaling factor. Literally 2 lines of code in loss computation.

**Our situation:** We currently use `reward_step_penalty = -0.02` (flat) as a hack for verbosity. Dr. GRPO solves it properly. (KAE-12)

### DAPO — Dynamic Sampling (arxiv 2503.14476)

**Problem solved:** Prompts where all completions score identically → zero gradient → wasted compute.

**Fix:** Skip zero-variance prompts. Oversample until batch has actual reward variance. Also: separate clip bounds (epsilon_low vs epsilon_high) so rare-but-correct actions can increase faster.

**Our situation:** Many game states have obvious answers (dead → respawn, HP=0 → heal). DAPO would skip these and focus compute on ambiguous states. ~50 lines of code. (KAE-12)

---

## Methods We Decided Against (For Now)

### DPO — Direct Preference Optimization (arxiv 2305.18290)

**Why not:** Requires paired preferences — good and bad completions for the SAME prompt. Our data is unpaired (sessions scored independently). Constructing pairs would require: (a) replaying game states, or (b) finding sessions with similar starting states but different outcomes. Both are expensive and fragile.

**When it might make sense:** If we implement self-play (KAE-16) and can generate multiple completions per game state, DPO becomes viable. KTO is the bridge until then.

### PPO — Proximal Policy Optimization

**Why not:** Requires a trained critic/value model. For a 9B policy model, the critic adds ~9B more parameters. H100 80GB can't hold both during training (model + ref + critic + optimizer). Also: PPO is notoriously unstable for LLM alignment — requires careful reward normalization, clip tuning, entropy bonuses.

**When it might make sense:** If we move to a smaller model (3B) or get access to multi-GPU training. Even then, GRPO is likely sufficient.

### RLHF (human feedback)

**Why not:** We have automated reward signals (XP, quests, deaths) that are more reliable and cheaper than human labels for game actions. Human feedback would only add value for subjective qualities like "interesting" gameplay, which isn't our training objective.

---

## Complementary Methods (Future Pipeline)

### Agent-FLAN (arxiv 2403.12881)
Including failed trajectories with recovery improves robustness +2-5%. Relevant for KTO: our "undesirable" sessions where the agent dies but then recovers teach the model what recovery looks like.

### STaR / ReST-EM (arxiv 2203.14465, 2312.06585)
Self-training: model generates data, keep good examples, retrain. GPT-J approached GPT-3.5 on math via iterative self-training. Directly maps to KAE-16 (self-play loop).

### ETO (arxiv 2403.04163)
Exploration-based Training Optimization: self-play → score → DPO. +12% on SciWorld after 3 iterations. Combines self-play (KAE-16) with preference learning (KTO/DPO).

### WMPO (arxiv 2511.09515)
World Model Policy Optimization: world model rollouts + on-policy GRPO. Showed emergent self-correction. Directly maps to KAE-17 (world model synthetic rollouts).

---

## Our Pipeline Sequence (status as of 2026-04-28)

```
r8 SFT (DONE Apr 14 — correct loss masking, but train/inference mismatch caused underperformance)
  → Eval: base vs r8-SFT (DONE Apr 15 — base 2x kills, higher level than r8-SFT)
    → r9 SFT (DONE Apr 16 — fixed system prompt, 100% reasoning, MAX_SEQ_LEN 16384)
      → Eval: base vs r9-sft, curious personality (DONE Apr 16-17 — base beat r9 in early eval:
        2.5 vs 1.5 quests, 26.5 vs 28.5 kills, L20 vs L24, higher combat churn on r9-sft)
        → Root cause: zero observe supervision + personality-prompt mismatch in training
          → r10 dataset built May 6 (post-Core-3 Claude only; 9,352 / 934 records, observe ≈ 47%)
            → r10 SFT launch: pending — kicks off against the rebuilt corpus
              → r9-KTO / Dr. GRPO+DAPO / self-play / Tree-GRPO: ALL DEFERRED
```

**Pipeline focus.** The project has settled on "quest completion as the headline benchmark" — per-quest pass rate on the Core 3 (Foresting + Herbalist's Desperation + Rick's Roll), not loss curves. Active backlog is prompt + game_knowledge patches plus harness reliability work; KTO/GRPO scaffolding remains but is parked.

**Eval infrastructure status (still live):** `serve_modal.py` points at the latest deployed weights; `serve_modal_base.py` runs the base baseline (both A100, scale to zero). `eval_harness.py` (parallel model runs, log-based metrics), `eval_compare.py` (Glass's delta, bootstrap CIs), `eval_offline.py` (action-prediction accuracy on held-out Claude sessions) all functional. The headline eval is per-quest pass rate on the Core 3.

**KTO/GRPO take-aways still hold for the paper.** When/if the preference-RL ladder resumes, KTO remains the right offline bridge (unpaired session-level labels), Dr. GRPO + DAPO remain the correct GRPO patches, and Tree-GRPO remains the natural fit for the existing MCTS planner. The pipeline isn't wrong — it just isn't the bottleneck right now.
