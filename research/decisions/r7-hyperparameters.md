# r7 Hyperparameter Decisions

Research-backed rationale for every training parameter in the r7 SFT and r7-KTO runs. Written April 9, 2026.

---

## SFT Hyperparameters

### LoRA Rank: r=64, alpha=64

**Decision:** Keep r=64 from r6. Do not reduce to r=32.

**Why:** We're teaching a completely novel domain — game agent tool calling with spatial reasoning, combat decisions, quest logic. This is not incremental refining of existing capabilities; the model has never seen MCP tool calls or ASCII game maps. Higher rank gives more capacity for learning this new skill distribution.

**Research:**
- arXiv 2602.04998 ("Learning Rate Matters: Vanilla LoRA Suffices") showed r=32 matches r=64 for general instruction tuning on <50k records. But this was tested on chat/instruction tasks where the model already has strong priors.
- Raschka's guide: r=32 for 7B matches full finetune up to 50k examples. Our 6.4k records is well under this, but the domain novelty justifies extra capacity.
- Compute difference between r=32 and r=64 is marginal on H100 — not worth the risk of underfitting a novel domain.

**Trainable params:** ~50M (0.5% of 9B total). Low overfitting risk with 1 epoch.

### rsLoRA: Disabled (attempted and reverted)

**Decision:** Tried `use_rslora=True` in r7 (new). Training diverged at step 60. Reverted to `use_rslora=False`.

**What happened:** Loss dropped normally for 50 steps (2.38 → 0.11), then grad_norm spiked from 0.16 → 642 → 70M → 6B. Loss jumped to 5.66 by step 80. Warmup (20 steps) masked the issue; full LR at step ~25 started exponential gradient accumulation.

**Why it failed:** rsLoRA scales adapters by `1/sqrt(r)` instead of `1/r`. With our config `r=alpha=64`, standard LoRA gives effective scaling `alpha/r = 1.0`. rsLoRA gives `alpha/sqrt(r) = 64/8 = 8.0` — an **8x effective LR increase**. The Kalajdzievski 2023 paper assumes alpha is retuned when switching to rsLoRA; we did not rebalance alpha.

**Lesson:** If rsLoRA is ever re-attempted, alpha must be reduced (e.g., alpha=8 with r=64 to get effective scaling ~1.0 under rsLoRA). The comment on `train_modal.py:359` is load-bearing — do not remove it.

### Learning Rate: 1e-4

**Decision:** Keep 1e-4 from r6. Do not increase to 2e-4.

**Why:** Consensus starting point for LoRA SFT on 7-9B models. Multiple practitioners report 2e-4 causes instability on 9B models (Reddit, HN threads). With standard LoRA scaling (`alpha/r = 1.0`), 1e-4 is the safe default.

**Research:**
- arXiv 2602.04998: optimal LoRA LR is ~10x full-FT, landing at 1e-4 to 5e-4.
- Unsloth docs: recommend 1e-4 to 2e-4 range.
- Google Gemini SFT guide: start conservative, increase only if loss plateaus.

### Epochs: 1

**Decision:** Keep 1 epoch. Do not increase to 2.

**Why:** Strong practitioner consensus that multi-epoch SFT degrades results on instruction data. The model memorizes formatting quickly; extra epochs amplify this memorization without improving reasoning.

**Research:**
- arXiv 2501.17161 ("SFT Memorizes, RL Generalizes"): SFT inherently memorizes, more epochs amplify this. RL (KTO/GRPO) is needed for generalization.
- Raschka, Unsloth docs, Google Gemini SFT guide: all recommend 1 epoch for instruction SFT. Increase data, not epochs.
- LIMA (arXiv 2305.11206): 1 epoch on 1k examples was sufficient for 65B.

**Our dataset size (6.4k records, ~23.7M tokens) is well-validated:**
- DEITA (arXiv 2312.15685): 6k high-quality examples matched 300k low-quality.
- FireAct (arXiv 2310.05915): 500 trajectories is the emergence threshold for agent SFT. We are 12x above this.
- Agent-FLAN (arXiv 2403.12881): 34k for multi-task multi-benchmark. Single-domain needs far less.

### Loss Masking: train_on_responses_only (r8 fix)

**Decision:** Mask all system/user tokens. Train on assistant responses only (`<think>` reasoning + tool calls).

**r7 implementation (`completion_only_loss=True`) was silently broken.** TRL ignores `completion_only_loss` for `dataset_text_field="text"` datasets without an explicit `response_template`. r5-r7 trained on ALL tokens including game state JSON. r8 fixed this with Unsloth's `train_on_responses_only(instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")` which scans tokenized `input_ids` for assistant markers — no template tags needed, handles multi-turn correctly.

**Why:** Game state JSON and ASCII maps are observation tokens — they should inform the model's predictions but not be predicted themselves. Loss masking prevents the model from wasting capacity memorizing observation format.

**Research:**
- Structured Agent Distillation (arXiv 2505.13820): +8 percentage points task success from loss masking alone. Segment-aware masking on [REASON] and [ACT] spans.
- Agent-R1 (arXiv 2511.14460): Explicit masking for environment/tool outputs in policy gradient — only compute gradients on model's own reasoning + action tokens. Consensus best practice.
- arXiv 2401.13586 ("Does Prompt Loss Matter?"): For short completions (tool calls), a small prompt loss weight (0.1) can regularize. Our completions include `<think>` blocks (medium length), so full masking is appropriate.

### Packing: Disabled

**Decision:** Keep `packing=False`.

**Why:** Naive packing concatenates sequences and can cause attention cross-contamination — model attends across sequence boundaries.

**Research:**
- NAACL 2025 ("Threshold Filtering Packing"): up to 7% degradation on GSM8K from naive packing.
- TRL's SFTTrainer supports proper per-sequence attention masking with packing, but practitioners report subtle bugs.
- Our dataset is small (6.4k records). The throughput gain from packing is minimal. Not worth the risk.

### Sequence Length: 16384 (r9+) / 8192 (r7-r8)

**Decision (r7-r8):** max_seq_len=8192 — median record ~3.6k tokens, P90 ~12.7k.

**Decision (r9+):** Restored to **16384**. See "r9+ Updates" below.

### Batch Size / Gradient Accumulation: 2 / 8 (effective 16)

**Decision:** Keep effective batch size of 16.

**Why:** Standard for LoRA SFT on single GPU. Larger effective batch (32-64) could help but requires more grad_accum steps and slower iterations. 16 is well-tested.

### Chat Template Patch

**Decision:** Patch Qwen 3.5 chat template to preserve `<think>` in all assistant turns (new in r7).

**Why:** Stock template silently drops reasoning_content from assistant messages before `last_query_index`. In our multi-turn training windows, intermediate assistant turns had all reasoning stripped — the model was learning to skip thinking on follow-up turns. This is the single most impactful fix in r7.

**Bug:** QwenLM/Qwen3#1831. The Jinja template splits `content` on `</think>`, extracts `reasoning_content`, but only re-injects it for messages after the last user query.

**Fix:** Change the condition from `loop.index0 > ns.last_query_index` to `reasoning_content` (truthy check). If reasoning exists, always emit it with `<think>` tags.

### Paraphrase Augmentation

**Decision:** Keep system prompt intro paraphrasing (ORAK ICLR 2026, Consistency Alignment arXiv 2403.14221). Now also varies personality instructions (was broken in r6 — personality=None for all records).

**What NOT to paraphrase:** Reasoning/thinking content. AgentTrek (arXiv 2412.09605) showed CoT in trajectories is essential for SFT quality. Paraphrasing reasoning risks corrupting the chain-of-thought structure.

---

## KTO Hyperparameters

### Beta: 0.1

**Decision:** Keep beta=0.1 from r6-KTO.

**Why:** Default and recommended starting point. Controls the tradeoff between preference learning strength and KL divergence from the reference model. Higher beta (0.3-0.5) is more conservative.

**Research:**
- KTO paper (arXiv 2402.01306): tested 0.1-0.5, 0.1 is default.
- TRL docs: recommend 5e-7 to 5e-6 LR with beta=0.1.

### Learning Rate: 5e-7

**Decision:** Keep 5e-7 from r6-KTO.

**Why:** Conservative end of recommended range (5e-7 to 5e-6). Preference learning is delicate — too high LR destroys SFT capabilities. With beta=0.1, LR should not exceed 1e-6.

### Reference Model: None + Precomputed

**Decision:** Keep `ref_model=None + precompute_ref_log_probs=True`.

**Why:** Eliminates ~18 GB reference model from GPU memory during training. TRL PEFT path: uses training model with adapters disabled as reference, precomputes all ref log probs during preprocessing. Training at ~22 GB peak.

**Caveat:** TRL issue #2423 reported precomputed log probs loading bug in some versions. Our image pins `trl>=0.19.1` which should have the fix.

### Desirable/Undesirable Ratio: 40/30 with 30% gap

**Decision:** Keep from r6-KTO.

**Why:** Matches KTO paper's best result on OASST (~4:3 effective ratio). The 30% neutral gap prevents borderline sessions from being mislabeled. Prospect theory's loss aversion means undesirable labels need to be clearly bad — the gap ensures this.

**Research:**
- KTO paper: 4:3 optimal on OASST, 1:1 on UltraFeedback. Task-dependent.
- MaKTO (arXiv 2501.14225): step-level labeling outperforms trajectory-level. Our sliding windows approximate this.

### Session Scoring Weights (updated in r7)

| Signal | Weight | Rationale |
|--------|--------|-----------|
| XP delta | 15% | Raw progression signal. Reads as 0 on level-up (XP resets), so not dominant. |
| Level delta | 15% | Coarser but reliable. `/3.0` normalization — 3+ levels shouldn't saturate. |
| Quest progression | 20% | **New in r7.** Actual state changes: completions (1.0), stage advances (0.4), accepts (0.2). Replaces NPC-talk-count proxy. |
| Progress events | 10% | Per-turn XP/level deltas. Catches incremental progress within sessions. |
| Exploration | 15% | Unique positions visited. Rewards spatial diversity. |
| Turn quality | 15% | Avg per-turn score (state completeness + action quality + reasoning quality). |

---

## r7 Results (Apr 10, 2026)

**Training:** 402 steps, ~14.5h on H100 80GB. Final train loss: 0.072. Grad norms stable 0.007-0.017 throughout.

**Loss curve:** 2.38 → 0.55 → 0.30 → 0.12 → 0.09 → 0.072. Standard LoRA SFT pattern — rapid format learning (first 50 steps), then slow conditional refinement.

**Deployment:** Merged model saved to Modal volume `kaetram-model-vol`. Serving via SGLang on A100 40GB. Chat template patch applied at inference. Tested with `play_qwen.py` — model produces correct Qwen XML tool calls, follows priority system (heal before attack at low HP), and plays the game autonomously.

**Known limitation (fixed in r8):** `completion_only_loss=True` was not actually masking — TRL ignores it for `text`-field datasets. r7 trained on all tokens including game state JSON. r8 fixed this with `train_on_responses_only()` from Unsloth.

---

## r9+ Updates (Apr 15-17, 2026)

### Sequence Length: 8192 → 16384

**Decision:** Restored MAX_SEQ_LEN=16384 in `finetune/train_modal.py:77`.

**Why:** r9 introduced an aligned full-length system prompt (~3.8k tokens) plus the curated tool surface (~1.2k tokens). 5-turn sliding windows on top of that exceed 8k regularly — at 8192 we were silently truncating ~12% of multi-turn windows (anything past P88), losing late-window assistant turns. H100 80GB handles 16k comfortably with our batch=2 / grad_accum=8 config. r10 keeps 16384.

### `<think>` reasoning coverage: 30.6% → 100%

**Decision:** Every assistant turn in the SFT dataset must carry non-empty `reasoning_content`.

**Why:** Pre-r9 audits showed only 30.6% of assistant turns had `<think>` blocks preserved through `convert_to_qwen.py`. The combination of (a) the Qwen3 chat template `<think>`-stripping bug (QwenLM/Qwen3 #1831, patched in r7) and (b) extraction paths that dropped reasoning when only a tool_use block was present meant most intermediate turns trained as "action-only". r9+ enforces 100% coverage: turns without recoverable reasoning are filtered out rather than included naked, and `convert_to_qwen.py` always emits `<think>` when `reasoning_content` is present.

### Degenerate / low-quality filtering thresholds

Applied in r9+ via `convert_to_qwen.py` and `tests/test_dataset_filters.py`:

- **Drop turns missing `<think>`** when reasoning is recoverable (no naked tool calls in training data).
- **Drop turns where the assistant action is `click_tile`** as the primary navigation method — historical fallback that pollutes the action distribution.
- **Drop turns inside detected stuck loops** (3+ consecutive identical navigate/cancel_nav cycles).
- **Drop turns where observe-result was used to fabricate `<game_state>` injection** (r10 routes observe through tool_result; see CURRENT STATUS in CLAUDE.md).
- **Personality label parity:** every record must resolve to a real `prompts/personalities/<name>.md` file (no `None`, no 2-sentence `PERSONALITY_SUFFIXES` dict). Regression-tested in `tests/test_prompt_parity.py`.

These thresholds shrink raw turn counts but raise quality density — r10 (23,382 train / 2,590 val) embeds 61,412 tool calls vs r7's 21,976 on a comparable raw log corpus, with `observe` now first-class (54% of calls).

---

## References

- arXiv 2602.04998 — Learning Rate Matters: Vanilla LoRA May Suffice
- arXiv 2501.17161 — SFT Memorizes, RL Generalizes
- arXiv 2505.13820 — Structured Agent Distillation
- arXiv 2511.14460 — Agent-R1: Training Powerful LLM Agents with End-to-End RL
- arXiv 2310.05915 — FireAct: Toward Language Agent Fine-tuning
- arXiv 2412.09605 — AgentTrek: Agent Trajectory Synthesis
- arXiv 2305.11206 — LIMA: Less Is More for Alignment
- arXiv 2312.15685 — DEITA: What Makes Good Data for Alignment
- arXiv 2403.12881 — Agent-FLAN: Agent Tuning Data and Methods
- arXiv 2402.01306 — KTO: Kahneman-Tversky Optimization
- arXiv 2501.14225 — MaKTO: Multi-Agent KTO for Werewolf
- arXiv 2401.13586 — Instruction Fine-Tuning: Does Prompt Loss Matter?
- arXiv 2403.14221 — Consistency Alignment
- QwenLM/Qwen3#1831 — Chat template tool calling bug
- Kalajdzievski 2023 — rsLoRA: Rank-Stabilized LoRA
