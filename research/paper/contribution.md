# Paper 1: Contribution & Framing

Working notes for Paper 1 of the two-paper roadmap. This is a historical thinking document, not paper-ready prose. The July 18, 2026 submission audit in [submission-readiness.md](submission-readiness.md), [claims-evidence-matrix.md](claims-evidence-matrix.md), and [literature-positioning.md](literature-positioning.md) supersedes any conflicting framing below.

**Context:** This is Paper 1 (Kaetram distillation). Paper 2 (RuneScape adversarial multi-agent) is in [paper2-runescape-vision.md](paper2-runescape-vision.md). The two papers are fully independent — do not conflate them. Paper 1 evaluates a distillation hypothesis and its infrastructure; it does not yet prove a method effect. Paper 2 is the agent safety contribution.

**Publication strategy:** arXiv plus one archival venue at a time. The working
target is NAACL 2027 through the October 12, 2026 ARR cycle only if the matched
causal and transfer experiments are complete; TMLR is the rolling fallback.

**Current framing:** reachability-targeted persistent-player-state initialization for on-policy distillation in persistent tool-using agents (4B teacher to 2B student). The method is prospective: direct player snapshots must be selected by a frozen visitation/teacher-advantage/recoverability rule and compared against natural OPD, random-valid and progress-matched resets, TCOD-B2F, and Guided-OPD. The implementation does not restore a complete shared world. The historical round-two 12/30 to 15/30 sequence used a hand-selected milestone and is motivation, not validation; the 18/30 round-three result combines weights with a recovery affordance. The original 9B SFT result is a motivating negative result, not the current method contribution.

---

## One-Sentence Framing

Structured **plan-execution distillation** for game agents: given prompt scaffolding (typed MCP tool API + procedural quest knowledge), we distill a frontier LLM's tool-use trajectories into a 9B model using capability-archetype-diverse teacher data and outcome-based preference refinement.

**Note on framing.** The paper isolates the SFT distillation effect *given* the procedural scaffolding the agent receives at inference (`prompts/system.md` + `prompts/game_knowledge.md`). It does NOT claim the model "learned to play Kaetram" — game-knowledge baked into the prompt (NPC coords, quest walkthroughs, gate calculus) means the agent is executing an annotated runbook. The student-vs-teacher comparison is therefore a *plan-execution-fidelity* claim, not a *world-model-acquisition* claim. The no-knowledge ablation (see Limitations) is the future-work step that would tighten this to the stronger framing.

---

## What's Novel

### 1. MCP-based structured distillation for game agents

Prior game agent work (GamingAgent ICLR 2026, CRADLE, Voyager) has the agent write raw code or click pixels. We use a **custom MCP server with 17 typed game tools** (model-visible at inference; post PR #29 modular refactor) — the teacher (Claude) and student (Qwen) both call the same structured API. This means:
- Training data is naturally structured (tool name + typed arguments, not free-text code)
- No action space mismatch between teacher and student
- The tool API acts as an abstraction layer — game internals change without breaking training data

Conservative version of this claim: this appears meaningfully different from prior game-agent work that relies on raw code generation, browser automation, or pixel clicking. Phrase this as "to our knowledge" until the related-work article is compiled more fully.

### 2. Capability-archetype-diverse teacher data

Instead of one teacher policy, we run **3 Claude agents with distinct capability archetypes**:
- **completionist** — quest-completion-prioritized, infrastructure ordering, methodical preparation before risk
- **grinder** — combat/XP-throughput-prioritized, repeated mob loops, risk-tolerant HP thresholds
- **explorer_tinkerer** — exploration- and NPC-first, zone rotation, novelty-seeking behavior

**Why we moved off personality framing:** the n=30 hand-coded audit and n=731 automated audit (Apr 24-28) found that **task pressure dominates personality** — under quest deadlines and shared decision-tree priorities, personality-flavored agents converged to similar action distributions. Capability archetypes are defined along orthogonal capability axes (completion vs. throughput vs. exploration) that survive that pressure, rather than cosmetic style flavor that washes out.

This is not just data augmentation — each archetype appears to produce different **decision boundaries** at similar game states. The student model may learn a richer action distribution than any single teacher would provide, but this still needs an explicit ablation under the new framing.

Archetype injection via prompt modification is lightweight (< 20 lines per archetype) and doesn't require retraining the teacher. Keep this as a secondary claim until the archetype-diversity ablation is run.

### 3. Outcome-based preference refinement (KTO on game sessions) — *planned, currently deferred*

> Status: KTO is **deferred indefinitely** (the pipeline focuses on the quest-completion benchmark over preference-RL; see `training-runs.md` r9-KTO). It is a planned extension, not part of the current Paper-1 evidence. The post-SFT lever the program actually validated is on-policy distillation (a separate write-up, `opd-2b.md`).

The plan: after SFT, apply KTO using **game outcomes as reward signals** — XP gain, quest completion, deaths, navigation efficiency. This is interesting in combination:
- KTO is typically applied to chat/instruction data with human labels
- We use automated game metrics as labels — more signal-rich and cheaper than human feedback
- The scoring function is game-specific (not generic RLHF)

---

## What's Interesting But Secondary

### World model for reward shaping
2.2M param Transformer predicting combat outcomes. **Deprecated / not in use** — `world/` targets an older log shape and is not maintained against the current MCP harness. Not a paper claim.

### Multi-harness comparison
Same game, same tools, 4 harnesses: Claude, Codex, Gemini, OpenCode (6 models: Grok-4.1-Fast, Qwen3.5-35A3B, Qwen3.5-397A17B, Qwen3-80A3B, DeepSeek-V4-Flash, DeepSeek-V4-Pro). Model-aware bot usernames enable per-model log separation. Interesting for analysis but not a paper contribution unless we do a rigorous comparison. All harnesses fully integrated end-to-end; `analyze.py metrics` (Apr 29) provides the scorer but cross-harness comparison hasn't been run.

### Finetuned vs base model live comparison
Dashboard Qwen Live tab (Apr 10) shows split-screen MJPEG streaming of finetuned r7 (agent_4) vs base Qwen3.5-9B (agent_5) playing simultaneously. Useful for qualitative analysis in the paper, but quantitative eval protocol still needed.

### Self-play improvement loop
Planned (KAE-16) but not implemented. If it works, it's a strong contribution: student generates own data → score → retrain → iterate. STaR/ReST-EM pattern applied to game agents.

---

## Key Ablations Needed

| Ablation | What it shows | Status |
|----------|---------------|--------|
| SFT only vs SFT + KTO | KTO improves over pure imitation | **r10-KTO DEFERRED** — pipeline pivoted to quest-completion benchmark. r6-KTO smoke test passed (10/10 steps clean); scaffolding intact in `finetune/train_kto_modal.py`. |
| **r10-SFT vs base (Core 3 stages)** | **SFT regresses 3.5×; mechanism is corpus-prior-becomes-inference-prior** | **COMPLETE (May 22).** n=4 base / n=3 SFT, all 3h+, clean wire. Base 7/30 every run, SFT mean 2.0/30. Stats: Mann-Whitney exact per-run p=0.029, Fisher Foresting completion p=0.016 OR=16. Full eval in `r10-discussion.md`. |
| 1 archetype vs 3 archetypes | Diversity improves student policy | Need to train on completionist-only, compare against 3-archetype mix |
| Loss masking vs full loss | Training on game state tokens hurts | r8 (correct masking) vs r7 (broken masking, same data) — natural ablation. Same dataset, only difference is loss masking. |
| Train/inference alignment | Matching prompts matters | r8 (mismatched prompt) vs r9 (aligned prompt) — r8 eval showed base 2x better than r8-SFT. r9 fixes alignment. |
| 17 tools vs filtered tools | Tool filtering helps small models | Pending KAE-15. Post PR #29 we are at **17 model-visible tools** — under the RAG-MCP 19-tool threshold. Ablation now compares 17-tool curated surface vs further-filtered subsets. |
| With/without click_tile filter | Data quality > quantity | r5 vs pre-filter comparison (have data) |
| ORAK 3-stream vs monolithic SFT | Decomposed training improves action accuracy | Pending KAE-19 |

**Most paper-ready now: r10-SFT vs base (May 19–22, n=4 base / n=3 SFT, all 3h+).** Clean negative result with mechanism:

- Base hits **7/30 Core 3 stages identically across 4 runs** (1/3✅/3✅ — zero variance over 12 days, fresh Mongo state per run).
- r10-SFT hits **2.0/30 mean** (3, 1, 2) — **3.5× regression** with perfect separation (every base run > every SFT run): Mann-Whitney exact per-run **p=0.029** (scipy's default returns the tie-degraded 0.016 — base is all-7s; use `method='exact'`; per-agent p=0.001 dropped as pseudo-replicated). Fisher Foresting completion (8/12 vs 1/9) **p=0.016, OR=16**.
- Foresting completion rate: **67% base → 11% SFT, 6.0× drop.**
- Mechanism: completionist `interact_npc` suppressed 5.6×, `query_quest` suppressed 4.8×, `navigate` amplified 4.49×. **SFT inference matches the training-target distribution to ~1pp on the decision verbs** (interact_npc 2.4% ↔ SFT 2.1%, query_quest 2.2% ↔ 2.5%, navigate 27.7% ↔ 26.4%; observe is the exception at ~8pp); base runs the dialogue verbs ~5× more often (10.8%, 11.2%), preserving a chat-model dialogue-eager prior the corpus under-represents. The SFT student faithfully imitated a teacher whose distribution under-samples the verbs Core 3 requires. Full eval matrix + stats in `r10-discussion.md` (reproducible via `scripts/r10_stats.py`).

This is the headline paper-ready ablation. The earlier r7/r8/r9 deltas (loss masking, prompt parity, observe supervision) are now methodological lessons that *led to* r10 — they remain useful for the related-work / ablation table but no longer carry the contribution alone.

**Other paper-ready evidence:** loss masking (r7 vs r8, natural ablation), train/inference alignment (r8 vs r9), click_tile filtering (r5 vs pre-filter). Personality diversity is promising but still needs a direct 1-archetype-vs-3 ablation. **Eval harness implemented** (Apr 15) — `eval_harness.py`, `eval_compare.py`, `eval_offline.py` ready for further ablation production. **Paper metrics scorer** added Apr 29 (`analyze.py metrics`).

---

## Rough Paper Outline

1. **Introduction** — Game environments as testbeds for agent distillation. Problem: frontier LLMs play games well but are too expensive to deploy. Can we distill their gameplay reasoning into a 9B model?

2. **Related Work** — Game-playing agents (GamingAgent, CRADLE, Voyager), agent distillation (SAD, ORAK, AgentArk), preference learning (KTO, GRPO, DPO), world models for planning.

3. **Method**
   - 3.1 Kaetram environment + MCP tool API (17 typed model-visible tools, OODA loop)
   - 3.2 Capability-archetype-diverse data collection (completionist / grinder / explorer_tinkerer; how they differ; why this replaced the older personality framing per KAE-46 audits)
   - 3.3 SFT with loss masking and quality filtering
   - 3.4 KTO preference refinement with game outcome scoring (planned — deferred)
   - 3.5 (if ready) GRPO reward shaping

4. **Experiments**
   - 4.1 Setup: Qwen3.5-9B, Modal H100, dataset stats (9,363 records from 135 Claude sessions)
   - 4.2 Baseline: **4 qwen-base runs identically reach 7/30 Core 3 stages** (1/3✅/3✅ — Foresting completed by completionist + explorer, grinder reaches Foresting 1/3). Zero variance across 12 days. See `r10-discussion.md` §"Eval matrix."
   - 4.3 Main result: **r10-SFT regresses to 2.0/30 mean** (3, 1, 2) — 3.5× drop (Mann-Whitney exact p=0.029). Mechanism (corpus-prior-becomes-inference-prior): SFT inference matches the training-target distribution to ~1pp on the decision verbs; base preserves chat-model dialogue prior; SFT imitates a verb-imbalanced teacher. Foresting completion rate 67% → 11%.
   - 4.4 Ablations (see table above)
   - 4.5 Qualitative analysis: example game sessions, reasoning quality

5. **Analysis** — What the student model learns vs doesn't learn. Where it fails. Context window limitations. Tool selection accuracy.

6. **Conclusion** — Structured tool APIs appear to make game-agent distillation practical. Outcome-based preference learning (KTO) was the planned post-SFT refinement lever but is deferred; capability-archetype diversity (completionist / grinder / explorer_tinkerer) is promising but still partially unverified.

---

## Limitations & Future Work

**Knowledge-leakage in the prompt.** `prompts/game_knowledge.md` contains NPC coordinates, quest walkthroughs, station-finding rules, gate calculus (e.g. exact blueberry counts for Foraging gates), and the Rick's Roll multi-room puzzle pin chain. The agent does not learn this from gameplay — it is given. The Core-3 result therefore measures *plan-execution fidelity given a procedural plan*, not *world-model acquisition*. A no-knowledge ablation arm — running base + r10-sft against `prompts/system.md` only, with `__GAME_KNOWLEDGE_BLOCK__` stripped — would isolate the SFT effect from the scaffolding effect. This is scoped as future work (requires a `--no-knowledge` flag in `eval_harness.py:resolve_system_prompt()`; not implemented in v1). Note: `quest_resume.json` cross-session memory was a second axis of scaffolding but was removed entirely (commit `09e611d`, May 7) — sessions are amnesic of model-authored memory. (June caveat: the base-Qwen lane now injects a small programmatic state snapshot at context rollover, `play_qwen._build_session_note` — deterministic tool output, not model-authored, and absent from the Claude collection lane; any cross-lane comparison should state whether it was active.)

**Train/eval scaffolding asymmetry (resolved).** Training-time data collection previously injected a `quest_resume.json` block into the system prompt at session start (cross-session memory), while eval ran fresh-Mongo with no carryover. This was a confound. As of May 7 (commit `09e611d`), `quest_resume.json` injection was removed entirely — both training and eval are now amnesic. The r10 dataset (rebuilt May 7) was collected under a mix of pre- and post-removal sessions; the r10 training-runs entry documents which source runs had resume active.

**Harness × model conflation.** All training trajectories are Claude Sonnet via the Claude Code CLI. The base comparator is unfinetuned Qwen3.5-9B served via SGLang (`finetune/serve_modal_base.py`). A reviewer can argue we have not separated "distillation works" from "Sonnet > base Qwen on this task." Mitigation requires either same-model-different-harness or same-harness-different-model runs. Cross-harness infrastructure is in place (Codex/Gemini/OpenCode fully integrated, 6 OpenCode models), but cross-model SFT corpora have not been collected. Tracked in `VARIABLES.md` §"Three most dangerous unisolated variables."

**Statistical power.** The current prospective contract schedules 20
frozen-checkpoint evaluation clusters. Its 80% calculation is explicitly
conditional on a three-stage minimum relevant paired difference and
paired-difference SD no greater than three stages after multiplicity adjustment
across seven estimands. It does not replicate the training procedure. That
variance bound has no independent pilot support yet. Before compute, justify it
with independent pilot data or a conservative prespecified variance grid (or
use blinded variance re-estimation with a fixed maximum); neither 20 nor the
historical default of 50 is automatically confirmatory.

**Archetype-diversity claim is provisional.** The n=731 automated audit found that "task pressure dominates personality" — under quest deadlines, archetype-flavored agents converge to similar action distributions. We retain capability-archetype labels because they appear to produce different *decision boundaries*, but the 1-archetype-vs-3 ablation is not yet run. If the ablation ties, the claim should be downgraded to "data-augmentation strategy" rather than a research contribution.

## Related design docs

- [VARIABLES.md](VARIABLES.md) — Design-variables catalog (KAE-49). Enumerates every knob (data, training, archetype, eval) we vary across runs and which ablation each feeds. Read alongside this contribution doc.

---

## Figures & Tables Needed

| Figure | What it shows | Data source |
|--------|---------------|-------------|
| Architecture diagram | End-to-end pipeline: Claude → MCP → logs → SFT → KTO → Qwen | Manual |
| Action distribution | What actions each personality produces (stacked bar) | extract_turns.py output |
| Training loss curves | r7/r8 SFT + r8-KTO loss over steps | Modal training logs |
| Ablation table | All ablations with metrics | Training runs |
| Example gameplay | Side-by-side: Claude vs finetuned Qwen on same scenario | play_qwen.py screenshots |
| Score distribution | Session scores before/after KTO | score_sessions.py output |

---

## Open Questions

1. **Evaluation metric — IMPLEMENTED (Apr 15):**
   - **Offline action prediction accuracy:** `eval_offline.py` holds out Claude sessions, measures whether finetuned Qwen reproduces Claude's tool call given the same observation. Directly analogous to TiG's 90.91% headline. Avoids circular dependence on KTO reward signal.
   - **Live gameplay metrics:** `eval_harness.py` runs N episodes per model with DB reset between episodes. Log-based metrics: XP/turn, quest completion, deaths, tool call success rate. `eval_compare.py` computes Glass's delta, bootstrap CIs, Bonferroni correction. `scripts/run-eval.sh` wrapper for parallel model runs.
   - Dashboard eval tab shows live progress.
   - Both metrics together give a strong story: "student reproduces teacher at X% and achieves Y% quest completion vs Z% baseline."
   - **Status: implemented. r8/r9 preliminary evals done (base outperformed both). Full eval matrix deferred — benchmark pivot.**
2. **Baseline:** Vanilla Qwen3.5-9B (no finetuning) deployed as baseline (`serve_modal_base.py`, agent_5). Comparison table: base → r8-SFT (broken alignment, shows what goes wrong) → r9-SFT (fixed alignment) → r9-KTO. The r8→r9 delta tells the data quality story.
3. **Reproducibility:** N=20 runs per model per condition. Same seed conditions. Report mean ± std. Kaetram-Open is public — full reproduction possible.
4. **Core intro framing (vs. all comparables, not just TiG):** "Unlike prior work where LLMs serve as decision advisors for human players (TiG), generate raw code or click pixels (CRADLE, Voyager), or operate in episodic single-player environments (Orak, GamingAgent), our agent operates fully autonomously in a persistent open world using a shared typed tool API as the teacher-student interface." This single sentence covers all five main comparables simultaneously.
5. **Ethics section:** Agent plays a game, no human subjects. Address: compute cost of teacher data collection, environmental impact of 24/7 agent runs.
