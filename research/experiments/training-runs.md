# Training Runs

History of all Qwen3.5-9B finetuning runs, from initial SFT through KTO preference learning. Each entry records what changed, what broke, and what improved.

---

## Run Timeline

| Run | Date | Type | Records | Key Change | Result |
|-----|------|------|---------|------------|--------|
| r1-r3 | Mar 26-31 | SFT | ~500-800 | Initial training, raw data | Model loaded but poor action quality |
| r4 | Apr 3 | SFT | ~1,200 | Loss masking (KAE-10) | Stopped training on game state tokens |
| r5 | Apr 4 | SFT | 3,853 train / 465 val | Quality filters + native MCP tools | First playable model, deployed on Modal |
| r6 | Apr 4-5 | SFT | 3,853 train / 465 val | Niral's optimized run, 2 epochs | Deployed and tested end-to-end |
| r6-KTO | Apr 5 | KTO | 2,771 train / 273 val KTO windows | Preference learning on scored sessions | Pipeline validated — 10/10 smoke steps, train_loss=0.617, KL active. Awaiting full run. |
| r7 | Apr 9-10 | SFT | 6,423 train / 646 val | Chat template fix, personality labels, expanded dataset | COMPLETE. Final loss 0.072. Deployed and tested. rsLoRA attempted and reverted (8x LR trap). |
| r8 | Apr 13-14 | SFT | 6,419 train / 646 val (4 filtered from r7's 6,423) | Loss masking fix (train_on_responses_only) | COMPLETE. Deployed on Modal. Eval harness set up (base vs r8-SFT). |
| r9 | Apr 15-16 | SFT | 5,871 train / 575 val | Train/inference alignment fix (system prompt, reasoning, seq length) + degenerate filtering | COMPLETE Apr 16. Deployed via `serve_modal.py`. In early curious eval lost to base (1.5 quests / 28.5 kills / L24 vs base 2.5 / 26.5 / L20). Root cause → r10 P0 fixes. |
| r10 | May 6 | SFT (dataset) | 9,352 train / 934 val | Post-Core-3 Claude corpus only: 5 runs × 3 agents = 135 sessions, 9,766 raw OODA turns. Provenance stamped in `metadata.json` (`source_runs`, `prompt_commit`, `core3_only`). | Dataset built 2026-05-06 from active corpus. LoRA training pending. Auto-test gate (5 dataset suites) green. |
| r9-KTO | DEFERRED | KTO | TBD | Preference learning on r9 merged weights | Deferred indefinitely — pipeline focuses on the quest-completion benchmark over preference-RL. |

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

**What changed:** Niral's optimized run on same r5 dataset. Specific optimizations not documented — need to backfill from Niral.

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

**Status:** Pipeline fully validated. Smoke test ran 10/10 steps cleanly — `train_loss=0.617`, KL divergence active (0.14→0.32 across steps), eval ran at steps 5 and 10. Save fallback in place (commit 34314ad). Ready for full run — Niral to greenlight.

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

## r10 — Post-Core-3 Claude Corpus (May 6, dataset built, training pending)

**Source.** Active raw corpus only: 5 Claude Sonnet runs × 3 agents = **135 sessions** spanning May 4 – May 6, 2026. All sessions ran on the Core 3 prompt (commit `c4dcf8b` or later) under the current grinder / completionist / explorer_tinkerer archetypes. Pre-Core-3 raw runs and every non-Claude harness run live under `dataset/raw/_archive/` and are invisible to the build pipeline.

**Pipeline stages:**
1. **Raw OODA extraction** (`extract_turns.py`): 135 session_*.log → 135 turns.jsonl files, **9,766 raw turns**. Observe emitted as first-class turn; standalone post-observe action emitted as second turn.
2. **Conversion** (`convert_to_qwen.py`): mixed mode, window=3, 70/30 multi-turn-vs-single-turn split. **10,286 SFT records** (9,352 train + 934 val) after the degenerate filter (80 records dropped, 0.8%) and observe→observe bigram filter.
3. **Provenance metadata** stamped at build time: `version`, `built_at`, `prompt_commit`, `core3_only`, `harness`, `source_runs[]`, `session_count`, `raw_turns`, `record_counts`, `personality_labels`. Closes the discoverability gap that made "what's in r10?" require grepping research docs.

**Tool-call distribution.** observe 47%, navigate 19%, warp 5%, gather 3%, respawn 2%, interact_npc 2%, query_quest 2%, attack 2%, cancel_nav 1%. The 3.7:1 navigate-to-warp ratio is the headline behavioral imbalance — agents that succeed at Rick's Roll mix in more `warp` calls when BFS_NO_PATH fires; a future prompt update sharpening the BFS-fails-twice→warp rule should shift this in r11+.

**Auto-test gate (5 suites, all green on rebuild):**
- `test_dataset_filters` — observe present in training data; metadata personality_suffixes byte-match `prompts/personalities/*.md`; `__PERSONALITY_BLOCK__` placeholder preserved.
- `test_observe_supervision` — observe is at least 30% of tool calls.
- `test_truncation` — no record exceeds `MAX_SEQ_LEN=16384` post-tokenize.
- `test_loop_noise` — no observe→observe adjacency, no 3+ identical consecutive tool names.
- `test_think_roundtrip` — `<think>` blocks survive `apply_chat_template` on multi-turn records.

**Config (planned).** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, bf16, `MAX_SEQ_LEN=16384`. Experiment: `kaetram-qwen3.5-9b-r10`. Qwen3.5-9B thinking-general decode params wired into `serve_modal*.py`.

**Status.** Dataset built 2026-05-06 from the active corpus. LoRA training pending — once kicked off, `r10-sft` deploys via `serve_modal.py` and is evaluated against `r9-sft` + base on the Core 3 quest benchmark.

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
- `kaetram-qwen-serve` — finetuned model (SGLang, A100, `serve_modal.py`) — pointed at r9 (deployed Apr 16).
- `kaetram-qwen-base` — unfinetuned Qwen3.5-9B baseline (SGLang, A100, `serve_modal_base.py`)
- Both scale to 0 when idle ($0 cost). Cold start ~3-6 min (model download + SGLang init).

**Known issues:**
- Unsloth LoRA count mismatch: PEFT save fails when adapter count != expected. Fallback to standard PEFT save implemented (commit 34314ad).
- Qwen3-VL tokenizer routing: Unsloth r6 tokenizer routes through Qwen3-VL processor, causing `processing_class` errors. Fix: use base tokenizer explicitly.
- Orphaned Chromium/MCP processes: Agent restart leaves zombie processes. Fix: process group kill with SIGTERM → SIGKILL timeout (commit 5e1b4df).
- Explicit reference-model KTO runs OOMed repeatedly on H100 80GB. Current workaround is `ref_model=None + precompute_ref_log_probs=True`, which removes the separate reference-model residency cost at training time.
- **Tool count drift (April 8 → resolved Apr 25):** MCP server peaked at **22 tools** (was 18 at r5 training time). PR #29 modular MCP refactor (Apr 25) collapsed the surface to **17 typed model-visible tools** — safely under the RAG-MCP 19-tool degradation threshold (arxiv 2505.03275). Legacy wrappers retained only for `extract_turns.py` log back-compat. Context-dependent tool filtering (KAE-15) is lower priority now but may still help for future tool additions.

---

## What's Next

**Pivoted away from the SFT/KTO/GRPO ladder as of 2026-04-25.** PR #29 collapsed `mcp_game_server.py` into a modular `mcp_server/` package and scaffolded the per-step quest reachability suite under `tests/e2e/quests/reachability/`, making quest completion (not loss curves) the headline metric. `--opencode` added as a 4th harness peer alongside Claude/Codex/Gemini, routing Qwen via NVIDIA NIM. Capability archetypes (GRINDER / COMPLETIONIST / EXPLORER_TINKERER) replaced the AGGRESSIVE/METHODICAL/CURIOUS personality system (closed Apr 25). Apr 27 (`61cf94f`) Tier-A unblock pass shipped: `live_gate_status`, `quest_resume.json` cross-session memory, `recent_failures` injection, `mob_stats` enrichment in observe, `station_locations`, BFS→warp navigation fallback, and `migrate_logs_to_runs.py` (1,384 sessions → 237 runs) — log layout moved to `dataset/raw/agent_*/runs/run_<TS>/`. Apr 27 (`ef3bac4`) wired xAI/Grok-4.1-Fast-Reasoning as a 5th harness path.

Apr 28 strike-team audit (8 parallel agents on `barathvelmu/kae-50-q2-q3-strike-team`) traced Herbalist's Desperation + Rick's Roll failures: Herbalist's = decision gap (game_knowledge claims ~440 blueberry gathers to Lv25 vs real ~873; Blue Lily requires Foraging Lv10 but stage 0 needs 3 — structural wall at L1). Rick's Roll = data hallucination + capability gap (agents invent a non-existent "L25 zone gate" and pivot to Desert Quest, dying at L8 to L16 Sneks). Live VM: 0/3 agents accepted Herbalist's or Rick's Roll across 38 min of a 4 hr Sonnet run.

**Data scale (May 3):** 294 runs / 1,694 sessions across 3 agents (agent_0: 102 runs/583 sessions, agent_1: 95/573, agent_2: 97/538). Rick's Roll stage-2+ knowledge **now in `game_knowledge.md`** (shipped May 1 commit `154badc` — puzzle-room door chain, Lena coords, all 7 decoy ladders, 2-call turn-in caveat).

**Active backlog (revised priorities):** ship Q2/Q3 prompt-data fixes (game_knowledge grind tables, Rick's Roll stage-2+ puzzle-room details, Rule 9 tightening) once Niral validates. KAE-49 (paper-variables catalog) in flight. r10 training launch is unlikely on the frozen artifact; if/when training resumes the relabel to GRINDER/COMPLETIONIST/EXPLORER_TINKERER would need to be applied at convert time.

**Qwen agent infrastructure (Apr 10):**
- Finetuned model: agent_4 slot, `QwenBot` username, `start-qwen.sh`
- Base model: agent_5 slot, `QwenBase` username, `start-qwen.sh --base`
- Dashboard: Qwen Live tab with split-screen MJPEG streaming (4 FPS), log polling
- Management: `start-qwen.sh`, `stop-qwen.sh`, `restart-qwen.sh`, `status-qwen.sh`

Backlog (by priority from Linear):
- **High:** Dr. GRPO + DAPO patches for GRPO (KAE-12), guided decoding via GBNF grammar (KAE-14), context-dependent tool filtering (KAE-15)
- **Medium:** Memory module for play_qwen.py — inject memory.txt into system prompt (KAE-20, Stage 1 = no retraining), self-play data loop (KAE-16), world model synthetic rollouts (KAE-17), Tree-GRPO (KAE-18), ORAK 3-stream SFT (KAE-19)
