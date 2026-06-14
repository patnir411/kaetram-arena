# Training Runs

History of the Qwen3.5 finetuning runs: the 9B SFT era (r1–r10) and the r11 era — a scaffold reframe across model sizes, then on-policy distillation (4B teacher → base-2B student, rounds 1–3). Each entry records what changed, what broke, and what improved. The r11/OPD detail lives in `research/experiments/opd-2b.md`; this file is the round index.

---

## Run Timeline

| Run | Date | Type | Records | Key Change | Result |
|-----|------|------|---------|------------|--------|
| r1-r3 | Mar 26-31 | SFT | ~500-800 | Initial training, raw data | Model loaded but poor action quality |
| r4 | Apr 3 | SFT | ~1,200 | Loss masking (KAE-10) | Stopped training on game state tokens |
| r5 | Apr 4 | SFT | 3,853 train / 465 val | Quality filters + native MCP tools | First playable model, deployed on Modal |
| r6 | Apr 4-5 | SFT | 3,853 train / 465 val | the maintainer's optimized run, 2 epochs | Deployed and tested end-to-end |
| r6-KTO | Apr 5 | KTO | 2,771 train / 273 val KTO windows | Preference learning on scored sessions | Pipeline validated — 10/10 smoke steps, train_loss=0.617, KL active. Awaiting full run. |
| r7 | Apr 9-10 | SFT | 6,423 train / 646 val | Chat template fix, personality labels, expanded dataset | COMPLETE. Final loss 0.072. Deployed and tested. rsLoRA attempted and reverted (8x LR trap). |
| r8 | Apr 13-14 | SFT | 6,419 train / 646 val (4 filtered from r7's 6,423) | Loss masking fix (train_on_responses_only) | COMPLETE. Deployed on Modal. Eval harness set up (base vs r8-SFT). |
| r9 | Apr 15-16 | SFT | 5,871 train / 575 val | Train/inference alignment fix (system prompt, reasoning, seq length) + degenerate filtering | COMPLETE Apr 16. Deployed via `serve_modal.py`. In early curious eval lost to base (1.5 quests / 28.5 kills / L24 vs base 2.5 / 26.5 / L20). Root cause → r10 P0 fixes. |
| r10 | May 7-10 | SFT (dataset) | 8,510 train / 853 val (9,363 total) | Claude corpus only, 3 agents (grinder / completionist / explorer_tinkerer). Mixed-mode thinking-ratio gate (≤25% no-think) + strict-16,384 truncation gate (dropped 4,799 overlong). Reasoning rendered verbatim; `_drop_overlong` is the only length authority. | Dataset rebuilt 2026-05-10. **COMPLETE** — final run ~43h on H100 ($197, billing-verified; "~22h" was an optimistic ETA); eval May 19–22 showed **3.5× SFT regression below base** (Mann-Whitney exact p=0.029); see `r10-discussion.md`. |
| r9-KTO | DEFERRED | KTO | TBD | Preference learning on r9 merged weights | Deferred indefinitely — pipeline focuses on the quest-completion benchmark over preference-RL. |
| r11-scaffold | May 28–Jun 4 | harness (no training) | — | R11 scaffold/state-contract reframe across model sizes | base-9B Core-3 4→19/30; scaffold transfers down the size ladder (27B 15, 4B 17, 2B 12) — capacity isn't the lever. See `opd-2b.md`, `r11-direction.md`. |
| opd-r1 | Jun 10 | OPD | 5,564 / 574 | 4B teacher → base-2B student, clipped-IS reverse-KL (init==generator) | Core-3 **12/30** — style transferred, competence didn't (visitation coupling + teacher-forcing copy-prior). |
| opd-r2 | Jun 12 | OPD | 7,024 / 825 | + env-state seeding at the Herbalist wall (bucket-B) | **15/30** — first weights-driven lift; Herbalist stage-1 passed 3/3 unseeded. |
| opd-r3 | Jun 13 | OPD | 8,856 / 1,040 | + counterfactual-canonicalized grading + full-ladder seeding + harness recovery | **18/30** — program best, past the 4B teacher (17); Herbalist stage-2 broke; Rick's 0/4 (cook-incompetent teacher). |

---

## r4 — Loss Masking (Apr 3)

**What changed:** Added `DataCollatorForCompletionOnlyLM` with response template `<|im_start|>assistant`. Zeroes loss on all system/user tokens (game state JSON, ASCII maps, prompts). Only trains on assistant responses.

**Why:** Structured Agent Distillation (arxiv 2505.13820) showed +8 percentage points task success from this alone. Model was wasting capacity memorizing game state JSON formatting. (KAE-10)

**Config:** LoRA r=64, alpha=16, 3 epochs, experiment name `r4-lossmasked`.

**Result:** Meaningful quality improvement. Model stopped reproducing game state verbatim in outputs.

---

## r5 — Quality Filters + Native MCP Tools (Apr 4)

**What changed (8 PRs, #15-#22):**
1. click_tile filter — removed 913 blind no-reasoning click_tiles (37.9% → 4.7%)
2. Repetitive loop filter — consecutive identical actions (23% → 0.2%)
3. Reasoning trimming — avg 1,654 → 426 chars, max capped at 800
4. Agent 3/4 exclusion — EFFICIENT (45% click_tile) and Codex (dead sessions) removed
5. Native MCP tool format — `attack(Rat)` dispatches to JS helpers directly, not `browser_run_code`
6. Realistic JSON tool results — replaced fake "Targeting mob" strings with actual game state changes
7. Reasoning-action alignment scoring — bonus for match, penalty for mismatch
8. Modal timeout 24h, epochs reduced to 2 (overfitting risk with r=64 on 3.2K records)

**Dataset:** 3,853 train / 465 val. Action distribution: navigate 27.8%, attack 15.2%, interact_npc 11.7%, click_tile 4.7%, repetitive 0.2%.

**Config:** LoRA r=64, alpha=16, 2 epochs, `completion_only_loss=True`, experiment `r5-mcp-tools`.

**Result:** First model that plays the game end-to-end via native tool calls. Deployed on Modal, tested with `play_qwen.py`. Model is rough but harness works.

---

## r6 — Optimized Training (Apr 4-5)

**What changed:** the maintainer's optimized run on same r5 dataset. Specific optimizations not documented — need to backfill from the maintainer.

**Result:** Deployed and tested. Serve stopped to save Modal cost. Superseded by r8 SFT (loss masking fix).

---

## r6-KTO — Preference Learning (Apr 5)

**What changed:** Post-SFT preference training using binary desirable/undesirable labels from game outcomes.

**Pipeline (4 new scripts, KAE-13):**
1. `score_sessions.py` — Scores sessions 0-1 from: XP delta (15%), level delta (15%), quest progression via actual state changes (20% — completions 1.0, stage advances 0.4, accepts 0.2), progress events (10%), unique positions (15%), avg turn score (15%). Penalties: respawns, click_tile rate, repetitive loops, stuck rate, deaths. Top 40% → desirable, bottom 30% → undesirable.
2. `build_kto_dataset.py` — Sliding windows (size=5, stride=2) over labeled sessions. Local window quality gating: positive floor 0.45, negative ceiling 0.60.
3. `finetune/train_kto_modal.py` — KTO on r6 merged. Current path uses `ref_model=None + precompute_ref_log_probs=True` to avoid keeping a second 9B reference model resident during training. LR=5e-7, beta=0.1, desirable_weight capped at 3.0.
4. `inspect_kto_dataset.py` — Dry-run: label balance, session counts, sample inspection.

**Key design decisions (Codex-reviewed):**
- `level_delta/3.0` not /1.0 — scales across multi-level sessions without saturating
- Removed `attack_rate > 0.80` penalty — was biasing against AGGRESSIVE personality sessions
- Canonical Qwen tokenizer for chat-template formatting — avoids Unsloth/Qwen3-VL template drift that broke prompt/completion splitting
- `ref_model=None + precompute_ref_log_probs=True` — TRL PEFT path. Precomputes reference log probs up front instead of holding a separate reference model in GPU memory during training

**Status:** Pipeline fully validated. Smoke test ran 10/10 steps cleanly — `train_loss=0.617`, KL divergence active (0.14→0.32 across steps), eval ran at steps 5 and 10. Save fallback in place (commit 34314ad). Ready for full run — the maintainer to greenlight.

**Smoke test path (5 attempts, each teaching something):**
1. batch=4 → OOM at `rejected_logits` (ref model forward)
2. batch=2, explicit bf16 ref → OOM at `_compute_kl_logps` (KL pass)
3. batch=1 → `ValueError`: KTOTrainer requires batch > 1 (KL dataset mismatching)
4. batch=2, 8-bit ref → `AttributeError: weight.CB` (bitsandbytes + Unsloth cu128 incompatible)
5. `ref_model=None + precompute_ref_log_probs=True`, batch=2 → training passed, save raised Unsloth LoRA mismatch → fallback fix → full pass

---

## r7 — Expanded Dataset + Critical Fixes (Apr 9)

**What changed:**
1. **Chat template fix (QwenLM/Qwen3#1831)** — Stock Qwen 3.5 template silently drops `<think>` reasoning from all assistant messages before `last_query_index` in multi-turn conversations. Our multi-turn training windows (70% of records) had CoT stripped from all intermediate turns — model was learning "action only, no reasoning" for follow-up turns. Patched to always emit `<think>` when `reasoning_content` is present.
2. **Personality labels** — `detect_personality()` was returning None for all records (metadata.json path mismatch). Added fallback mapping from agent_N directory to personality. Dataset now labeled: 39% aggressive, 31% methodical, 29% curious. Paraphrase augmentation now varies personality instructions during training.
3. **rsLoRA attempted and reverted** — Added `use_rslora=True` (Kalajdzievski 2023). Training diverged immediately. With `alpha=r=64`, rsLoRA scales by `alpha/sqrt(r) = 8.0` instead of standard `alpha/r = 1.0` — an 8x effective LR. Reverted to `use_rslora=False` (commit `685f649`). See CLAUDE.md gotchas for details.
4. **Expanded dataset** — 575 sessions extracted (was 395), 14,091 turns → 6,423 train / 646 val (was 3,957/488). ~62% more data. 618 raw session logs on disk.
5. **Quest progression scoring** — KTO session scoring now uses actual quest state deltas (completions, stage advances, new accepts) instead of NPC-talk-count proxy.

**Dataset:** 6,423 train / 646 val (7,069 total). ~23.7M tokens. Action distribution: navigate 27.7%, attack 14.9%, cancel_nav 10.7%, interact_npc 10.7%, warp 9.4%, stuck_reset 7.5%, move 7.1%, click_tile 3.9%.

**Config:** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, `completion_only_loss=True`, bf16, H100 80GB. See `research/decisions/r7-hyperparameters.md` for parameter rationale.

**Status:** COMPLETE. Launched Apr 9 ~15:12 UTC, finished Apr 10 ~05:30 UTC (~14.5h). Final train loss: 0.072. Loss curve: 2.38 → 0.072, grad norms stable 0.007-0.017 throughout. First attempt died at 8h timeout (step 222/402); retried with 18h cap. Model deployed and tested via `play_qwen.py` — produces correct XML tool calls, follows priority system.

**Estimated:** 402 steps, ~12-14h wall time on H100.

---

## r8 — Loss Masking Fix (Apr 12)

**What changed:**
- **`train_on_responses_only` replaces broken `completion_only_loss`:** r5–r7 used `completion_only_loss=True` in `SFTConfig` with `dataset_text_field="text"`. TRL's `DataCollatorForCompletionOnlyLM` needs a `response_template` to identify where completions start — without one it silently skips masking. r5–r7 trained on ALL tokens including game state JSON, ASCII maps, and system prompts. Fix: removed `completion_only_loss`, added `train_on_responses_only(instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")` from Unsloth after trainer init. This correctly zeros labels on all non-assistant tokens and trains only on `<think>` reasoning + tool calls.

**Note on r4 vs r5–r7:** r4 used `DataCollatorForCompletionOnlyLM` explicitly with a response template — this worked correctly. r5+ switched to `completion_only_loss=True` without a `response_template`, which silently regressed to full-token loss. r8 returns to correct masking.

**What's the same:** Dataset identical to r7 (6,423 train / 646 val). All 26 post-r7 logs are Gemini — zero new Claude data (verified by direct VM inspection Apr 12). r8 improvement comes entirely from correct loss masking.

**Config:** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, bf16, H100 80GB. Experiment: `kaetram-qwen3.5-9b-r8`.

**Status:** COMPLETE. Launched Apr 13 ~16:30 UTC, finished ~06:30 UTC Apr 14 on Modal H100. Unsloth 2026.4.2, TRL 0.24.0, Transformers 5.5.0. `train_on_responses_only` applied successfully — 4/6,423 samples removed (all labels -100 after truncation). 402 steps. Merged weights deployed via `serve_modal.py`. Eval harness set up with `dataset/eval/` (base vs r8-SFT system prompts).

---

## r9 — Train/Inference Alignment Fix (Apr 15)

**What changed:** Three fixes to eliminate the distribution shift between training and inference that caused r8-SFT to underperform the base model.

1. **System prompt aligned with inference (commit `40a2dfc`):** Replaced the hardcoded 50-line `SYSTEM_PROMPT` in `convert_to_qwen.py` (2,490 chars, "Priority System" with 8 rules, legacy tool names `heal`/`equip`/`click`/`set_style`/`wait`) with dynamic loading of `prompts/system.md` + `game_knowledge.md` (11,382 chars, OODA loop, 12-rule decision tree, XML-tagged sections, correct MCP tool names, full mob stats + quest walkthroughs). The model now trains on the exact same instructions it receives at inference.

2. **Reasoning on 100% of turns:** Changed `include_thinking=is_last` to `include_thinking=True` in `build_multi_turn_records()`. r8 had `<think>` blocks on only 30.6% of assistant messages (6,346/20,735). r9 has 100% (22,796/22,796). The model learns "think before every action" as the default.

3. **Sequence length restored:** `MAX_SEQ_LEN` bumped from 8,192 to 16,384 in `train_modal.py`. With the old 8k limit, 55.5% of records were being silently truncated — losing the final assistant turns (the actual training signal). At 16k, ~91% of records fit.

**Additional fix:** `_BODY_SPLIT_MARKER` in `train_modal.py` updated from `"## Entity Types"` (didn't exist in the new prompt) to `"<game_knowledge>"` with try/except fallback. Paraphrase intro variants updated to match the new prompt structure.

**Why r8 failed:** The base model followed the OODA system prompt faithfully because it's a strong instruction follower with no conflicting signal. r8-SFT was trained on different instructions ("Priority System"), different tool names, no game knowledge, and action-without-reasoning on 69% of turns. At inference, the model fought its own training. r9 removes this contradiction.

**Eval context (r8 vs base, Apr 15):** 2 episodes each, scenario D (Open Play, 300 turns), aggressive personality. Base: 17.5 kills avg, level 20, 2 quests. r8-SFT: 8.5 kills avg, level 14.5, 1.5 quests. Both 100% tool parse rate, 0 deaths. r8-SFT faster (58 min avg vs 79 min) but less productive per turn.

**Config:** Same as r8 (LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, bf16, H100 80GB) except `MAX_SEQ_LEN=16384`. Experiment: `kaetram-qwen3.5-9b-r9`.

**Second commit (998b865) — additional fixes before launch:**
- **F3:** Removed `tools=` parameter from `apply_chat_template` — tool definitions were being double-injected (once via system prompt, once via template parameter), inflating token count.
- **F8:** Removed `<memory>` block injection — was leaking session-local agent memory into training records.
- **F9/F10:** Degenerate session filter — removes sessions with >50% `click_tile` actions (blind clicking) or >75% stuck loops (stuck_reset/cancel_nav cycles). These sessions contain no useful learning signal.
- **F14:** Experiment name set to `kaetram-qwen3.5-9b-r9`.

**Dataset:** 5,871 train / 575 val (was 6,380/689 before degenerate filtering). Same source data as r7/r8 (583 Claude logs). 21 tool definitions in metadata (was 15). 100% `<think>` coverage (was 30.6%).

**Status:** COMPLETE Apr 16. 367 steps. Deployed via `serve_modal.py`. **Lost to base in early curious-personality eval** (2 episodes: base 2.5 quests / 26.5 kills / L20 vs r9-sft 1.5 quests / 28.5 kills / L24, higher combat churn). Root-cause diagnosis surfaced two P0 train/eval gaps that became r10's reason to exist:
1. Zero observe supervision in training data (`extract_turns.py` was discarding observe `tool_use` blocks to populate `game_state` and dropping them from the assistant turn stream).
2. Personality prompt mismatch — training used a 2-sentence `PERSONALITY_SUFFIXES` dict, eval loaded the full ~1.5 KB `prompts/personalities/<name>.md` file.

---

## r10 — Claude Corpus, Mixed-Mode Thinking Ratio + Strict 16,384 Truncation Gate

**Source.** Live Claude Sonnet runs × 3 agents (grinder / completionist / explorer_tinkerer). Source runs and counts are stamped per build in `dataset/qwen_sft/metadata.json::source_runs[]` and `session_count`. Every non-Claude harness run lives under `dataset/raw/_archive/` and is invisible to the build pipeline.

**Pipeline stages:**
1. **Raw OODA extraction** (`extract_turns.py`): each session log → ordered observe / action turns with reasoning attribution. Observe is a first-class turn; the immediately-following action is a second turn that inherits the observe's `game_state`.
2. **Conversion** (`convert_to_qwen.py`): mixed mode, window=3 multi-turn records plus ≈30% single-turn observe→action records. System prompt is NOT embedded in records — `train_modal.py` injects it from `metadata.json` at training time so render parity with eval is structural.
3. **Thinking-ratio gate** (`_enforce_thinking_ratio`): enforces ≤25% non-thinking assistant turns (`max_no_think_ratio=0.25`). Sonnet emits no-CoT tool calls ~47% of the time on repetitive actions (attack/gather/drop); without this gate the corpus is dominated by no-think turns and the model unlearns CoT. Records ranked by descending no-think share; pure-grind-loop records dropped first.
4. **Truncation gate** (`_drop_overlong`): renders each record through `finetune/render.render_record(rng=None)` — same path the trainer uses — and drops any record exceeding `MAX_SEQ_LEN=16,384`. Load-bearing safety net for TRL #3927: with `train_on_responses_only` masking, truncation that eats every assistant token of a record silently zeros per-record loss. Strict drop, no inner truncation.
5. **Provenance metadata.** `metadata.json` stamps `version`, `built_at`, `prompt_commit`, `harness`, `source_runs[]`, `session_count`, `raw_turns`, `record_counts`, `thinking_ratio` (counts + share), `truncation_gate` (max/p99/p50 token counts of kept records), `personality_labels`, plus the full `system_prompt` and `personality_suffixes` for trainer-side substitution.

**Auto-test gate (run pre-train):**
- `test_dataset_filters` — observe present in training data; metadata `personality_suffixes` byte-match `prompts/personalities/*.md`; `__PERSONALITY_BLOCK__` placeholder preserved; no `<game_state>` injection in user messages.
- `test_observe_supervision` — observe is at least 30% of tool calls; user message is exactly `"What should you do?"`; multi-turn role sequence matches inference.
- `test_truncation` — no record exceeds `MAX_SEQ_LEN=16,384` post-tokenize, measured via `finetune/render.render_record` with the patched chat template.
- `test_think_roundtrip` — `<think>` blocks survive `apply_chat_template` on multi-turn records under the patched template; no-think turns render the canonical empty `<think>\n\n</think>` wrapper per Qwen3 Thinking Mode Fusion.
- `test_prompt_parity` — train-time `SYSTEM_PROMPT` and eval-time `eval_harness.resolve_system_prompt` produce byte-identical output for every personality; intro paraphrase variants share the same body as `prompts/system.md` after `\n\n<game_knowledge>`.
- `test_chat_template` — single-source-of-truth check: `patch_qwen_chat_template` lives only in `finetune/render.py` and is imported (not duplicated) by every Modal entry point.

**Config.** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, bf16, `MAX_SEQ_LEN=16,384`, `packing=False`, `dataset_text_field="text"`, `max_length=MAX_SEQ_LEN` (TRL #3910 — `max_seq_length` was the old name, silently ignored). Loss masking via Unsloth's `train_on_responses_only` with `<|im_start|>user\n` / `<|im_start|>assistant\n` markers. Data collator wrapped with a `(labels != -100).any(dim=-1).all()` per-batch assert to abort on any all-masked record (TRL #3927 guard). Experiment: `kaetram-qwen3.5-9b-r10`. Modal timeout: 72h.

**Status.** COMPLETE. Dataset rebuilt 2026-05-10 (9,363 records after truncation gate dropped 4,799 overlong). Reasoning rendered verbatim; `_drop_overlong` is the only length authority. Trained on Modal H100 ~43h ($197, billing-verified; "~22h" was an optimistic ETA). `r10-sft` deployed via `serve_modal.py` (env-overridable `SFT_EXPERIMENT`, defaults to `kaetram-qwen3.5-9b-r10`, `min_containers=0` since May 11).

**Eval result (May 19–22, finalized; n=4 base / n=3 SFT, all 3h+, clean wire after `play_qwen.py` JSON-dict fix `7bf7c8d`):**

| Run | Harness | Duration | Stages/30 |
|-----|---------|----------|-----------|
| `run_20260510_173852` | base | 3h | 7 |
| `run_20260510_211339` | base | 6h | 7 |
| `run_20260519_223921` | base | 3h | 7 |
| `run_20260520_143530` | base | 3h | 7 |
| `run_20260520_014319` | r10-sft | 3h | 3 |
| `run_20260520_044433` | r10-sft | 3h | 1 |
| `run_20260520_173902` | r10-sft | 3h | 2 |

Base: identical reproduction `(grinder=1, completionist=3✅ Foresting, explorer=3✅ Foresting) = 7/30` four times in a row across 12 days. SFT: mean **2.0/30**, std 1.0 — **3.5× regression** with perfect separation: Mann-Whitney exact per-run **p=0.029** (scipy's default returns the tie-degraded 0.016 — base is all-7s; use `method='exact'`; per-agent p=0.001 dropped as pseudo-replicated, agents within a run aren't independent). Fisher Foresting completion (8/12 vs 1/9) **p=0.016 OR=16**. Foresting completion rate: **base 67% (8/12), SFT 11% (1/9)**, 6.0× drop. Herbalist's + Rick's Roll: 0 progress across every run (Claude teacher also can't accept these per Apr 28 strike-team audit — teacher ceiling caps both arms). All numbers reproducible via `scripts/r10_stats.py`.

**Mechanism — corpus prior becomes inference prior.** Completionist tool-mix (mean over runs):

| Tool | Base (n=3 3h) | SFT (n=3) | Ratio | Corpus % | SFT inference % | Base inference % |
|---|---|---|---|---|---|---|
| `interact_npc` | 63.3 | 11.3 | **5.6× suppression** | 2.4 | **2.1** | 10.8 |
| `query_quest` | 65.7 | 13.7 | **4.8× suppression** | 2.2 | **2.5** | 11.2 |
| `navigate` | 32 | 143.7 | **4.49× amplification** | 27.7 | **26.4** | 5.5 |

SFT inference matches the training-target distribution to ~1pp on the decision verbs (`interact_npc`, `query_quest`, `navigate`); `observe` is the exception (~8pp lower at inference). Base runs the dialogue verbs ~5× more often (10.8%, 11.2%) — chat-model pretraining prior is preserving dialogue-eagerness that the corpus under-represents. The SFT student successfully imitated a teacher whose corpus distribution under-samples the verbs Core 3 requires (especially `interact_npc(Forester)` × 3 for Foresting completion). This is catastrophic capability suppression via verb-imbalanced demonstration data — not a training bug, but the cross-entropy loss objective doing exactly what it's specified to do on a misspecified corpus.

Buggy May 12 SFT run (`run_20260512_120516`, pre-fix `play_qwen.py`) deleted from corpus 2026-05-20. Full eval matrix + statistical tests + r11 plan in `research/experiments/r10-discussion.md`.

---

## r9-KTO — Preference Learning (DEFERRED)

Originally planned to replace r8-KTO using r9 merged weights. **Deferred indefinitely** as of 2026-04-25 — the project pivoted from "SFT → KTO → GRPO ladder" to "quest completion as the benchmark" (PR #29). Pipeline still scaffolded (`finetune/train_kto_modal.py` validated via r6-KTO smoke 10/10 steps) but no current launch plan.

---

## r8-KTO — Preference Learning (SUPERSEDED by r9-KTO)

**What changed from r6-KTO:**
1. Quest progression scoring weights: XP 15%, levels 15%, quest progression 20% (actual state deltas), progress events 10%, exploration 15%, turn quality 15%.
2. Chat template fix applied to `fmt_tok` in KTO script.
3. Experiment name → `kaetram-qwen3.5-9b-r8-kto`.
4. Will rebuild KTO dataset on r8 extracted data. Base SFT will be r8 merged weights (with correct loss masking).

**Config:** Same as r6-KTO: beta=0.1, LR=5e-7, `ref_model=None + precompute_ref_log_probs=True`, window_size=5, stride=2.

---

## Infrastructure Notes

**Platform:** Modal (H100 80GB for SFT/KTO training, A100 40GB for inference serving). Unsloth for LoRA, TRL for KTO/GRPO trainers. SGLang for inference.

**Serving endpoints (Modal):**
- `kaetram-qwen-serve` — finetuned model (SGLang, A100, `serve_modal.py`) — defaults to r10 (`SFT_EXPERIMENT` env-overridable). `min_containers=0` (scale to zero, $0 when idle; updated May 11). BASE_MODEL_ID reverted to `Qwen/Qwen3.5-9B` (May 12) — SGLang's transformers can't load unsloth's tokenizer_config.json.
- `kaetram-qwen-base` — unfinetuned Qwen3.5-9B baseline (SGLang, A100, `serve_modal_base.py`). `min_containers=0` since May 22 (commit `0992c82`) — $0/hr idle, ~3-6min cold start.
- Cold start ~3-6 min (model download + SGLang init).

**Known issues:**
- Unsloth LoRA count mismatch: PEFT save fails when adapter count != expected. Fallback to standard PEFT save implemented (commit 34314ad).
- Qwen3-VL tokenizer routing: Unsloth r6 tokenizer routes through Qwen3-VL processor, causing `processing_class` errors. Fix: use base tokenizer explicitly.
- Orphaned Chromium/MCP processes: Agent restart leaves zombie processes. Fix: process group kill with SIGTERM → SIGKILL timeout (commit 5e1b4df).
- Explicit reference-model KTO runs OOMed repeatedly on H100 80GB. Current workaround is `ref_model=None + precompute_ref_log_probs=True`, which removes the separate reference-model residency cost at training time.
- **Tool count drift (April 8 → resolved Apr 25):** MCP server peaked at **22 tools** (was 18 at r5 training time). PR #29 modular MCP refactor (Apr 25) collapsed the surface to **17 typed model-visible tools** — safely under the RAG-MCP 19-tool degradation threshold (arxiv 2505.03275). Legacy wrappers retained only for `extract_turns.py` log back-compat. Context-dependent tool filtering (KAE-15) is lower priority now but may still help for future tool additions.

---

## r11 — scaffold reframe + OPD (rounds 1–3, Jun 7–14 2026)

After r10's negative result the program pivoted twice. First a **scaffold reframe** (no weight training): harness/state-contract engineering moved the base-9B Core-3 envelope 4 → 19/30, and the scaffold transfers down the size ladder (27B 15/30, 4B 17/30, 2B 12/30) — capacity is not the lever. Then an **on-policy distillation** lane: teacher = scaffolded 4B (17/30), student = base 2B (12/30), ~an order of magnitude cheaper per round than the 9B lane.

| Round | Core-3 /30 | Lever | Outcome |
|---|---|---|---|
| base-2B | 12 | — | scaffold floor |
| opd-r1 | 12 | reverse-KL OPD | style transferred, competence didn't (visitation coupling; teacher-forcing copy-prior) |
| opd-r2 | **15** | + env-state seeding at the Herbalist wall | first weights-driven lift; stage-1 passed 3/3 unseeded |
| opd-r3 | **18** | + counterfactual grading + full-ladder seeding + harness recovery | program best, past the 4B teacher; Herbalist stage-2 broke; Rick's 0/4 (cook-incompetent teacher) |

A controlled ablation (r2 weights + harness recovery = 17/30) decomposes r2→r3 as **harness → stages, weights → speed**. Full method, results, and literature alignment: **`research/experiments/opd-2b.md`**; paper: **`reference/overview.pdf`**. Trainer: `finetune/train_opd_2b.py` (round-parametrized). The 9B OPD lane (`train_opd_modal.py`) was parked with round-1 data built but never trained.

---

## What's Next

*(Snapshot as of mid-May 2026 — superseded by the r11 scaffold reframe + OPD work above; see `opd-2b.md` for the current frontier.)*

**Pivoted away from the SFT/KTO/GRPO ladder as of 2026-04-25.** PR #29 collapsed `mcp_game_server.py` into a modular `mcp_server/` package and scaffolded the per-step quest reachability suite under `tests/e2e/quests/reachability/`, making quest completion (not loss curves) the headline metric. `--opencode` added as a 4th harness peer alongside Claude/Codex/Gemini, routing Qwen via NVIDIA NIM. Capability archetypes (GRINDER / COMPLETIONIST / EXPLORER_TINKERER) replaced the AGGRESSIVE/METHODICAL/CURIOUS personality system (closed Apr 25). Apr 27 (`61cf94f`) Tier-A unblock pass shipped: `live_gate_status`, `quest_resume.json` cross-session memory (later removed May 7, `09e611d`), `recent_failures` injection, `mob_stats` enrichment in observe, `station_locations`, BFS→warp navigation fallback, and `migrate_logs_to_runs.py` (1,384 sessions → 237 runs) — log layout moved to `dataset/raw/agent_*/runs/run_<TS>/`. Apr 27 (`ef3bac4`) wired xAI/Grok-4.1-Fast-Reasoning as a 5th harness path.

Apr 28 strike-team audit (8 parallel agents on `collaborator/kae-50-q2-q3-strike-team`) traced Herbalist's Desperation + Rick's Roll failures: Herbalist's = decision gap (game_knowledge claims ~440 blueberry gathers to Lv25 vs real ~873; Blue Lily requires Foraging Lv10 but stage 0 needs 3 — structural wall at L1). Rick's Roll = data hallucination + capability gap (agents invent a non-existent "L25 zone gate" and pivot to Desert Quest, dying at L8 to L16 Sneks). Live VM: 0/3 agents accepted Herbalist's or Rick's Roll across 38 min of a 4 hr Sonnet run.

**Data scale (May 3, updated May 22):** Active corpus (post-archive-split): **42 runs / 3,723 sessions** across 3 agents (agent_0: 16/1,063, agent_1: 13/1,345, agent_2: 13/1,315). Includes both Claude collection and Qwen eval runs. 1,694 sessions archived. Rick's Roll stage-2+ knowledge **now in `game_knowledge.md`** (shipped May 1 commit `154badc` — puzzle-room door chain, Lena coords, all 7 decoy ladders, 2-call turn-in caveat).

**Active backlog (revised priorities):** r10 dataset rebuilt 2026-05-10 (9,363 records); since trained (~43h on H100, $197 billing-verified) and evaluated — 3.5× SFT regression (see `r10-discussion.md`), after which the program moved to the r11 scaffold reframe + OPD (see the r11 section above). `quest_resume.json` removed from the agent entirely (May 7, `09e611d`). Eval pipeline upgraded: `core3_stages_advanced` headline metric, N-model Bonferroni FWER, `serve_modal.py` defaults to r10. KAE-49 (paper-variables catalog) shipped.

**Qwen agent infrastructure (current — May 10 rewrite):**
- Qwen is a peer harness inside `orchestrate.py` (alongside Claude/Codex/Gemini/OpenCode) with two variants: `--qwen-sft N` (finetuned, default endpoint, model label `r10-sft`) and `--qwen-base N` (unfinetuned, model label `kaetram-base`). Mixable in one run for direct A/B. `QwenAdapter` (`cli_adapter.py`) spawns `play_qwen.py` per session against the corresponding Modal SGLang endpoint (`QWEN_SFT_ENDPOINT` / `QWEN_BASE_ENDPOINT`).
- Usernames: personality-based — `QwenGrinder` / `QwenCompletionist` / `QwenExplorer` (so the in-game bot maps 1:1 to the personality variant under eval). SFT vs base is reflected in `metadata.json::model`, not the username, so a 3-agent SFT run and a 3-agent base run share the same Mongo player rows.
- Sessions: bounded by Qwen's 16K trained context, not turn count. **play_qwen runs a warm-session loop** — when next call would overflow, the inner loop rolls into a new session (fresh `messages = [system, bootstrap(N+1)]`, new log file), but MCP/Chromium/login/Xvfb/ffmpeg all persist across rollovers. Mongo state carries per-username across sessions (same as Claude). Since June, the new bootstrap also carries a small programmatic state snapshot from the previous session (`_build_session_note` — not model-authored memory). orchestrate only respawns play_qwen on hard process death (rare crash recovery).
- Logs: play_qwen emits Claude-shaped stream-json (`type:"system"|"assistant"|"user"|"result"` with nested `message.content[]` blocks of `thinking`/`text`/`tool_use`/`tool_result`), so dashboard activity feed, `scripts/log_analysis/`, `extract_turns.py`, and the heartbeat ingest are all harness-agnostic.
- Multi-agent: `restart-agent.sh --qwen-sft 3 --grinder 1 --completionist 1 --explorer 1 --hours 3` runs 3 finetuned-Qwen agents in parallel on ports 9001/9011/9021. Swap to `--qwen-base 3` for the base lane.
- Eval: `eval_harness.py` (separate from orchestrate) drives r10-sft vs base on dedicated ports 9061/9071. **Time-based scenarios** (`duration_minutes` per scenario); each episode spawns one warm-loop play_qwen process that rotates sessions internally for the duration. Same JSONL log shape.

Backlog (by priority from Linear):
- **High:** Dr. GRPO + DAPO patches for GRPO (KAE-12), guided decoding via GBNF grammar (KAE-14), context-dependent tool filtering (KAE-15)
- **Medium:** Memory module for play_qwen.py — inject memory.txt into system prompt (KAE-20, Stage 1 = no retraining), self-play data loop (KAE-16), world model synthetic rollouts (KAE-17), Tree-GRPO (KAE-18), ORAK 3-stream SFT (KAE-19)
