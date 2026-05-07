# Why KTO Over PPO/DPO

We chose KTO (Kahneman-Tversky Optimization) as the first preference learning stage after SFT. This was not obvious — PPO and DPO are more established. Here's the reasoning.

---

## The Problem KTO Solves

SFT treats every Claude action as equally worth imitating. But some sessions end in death loops. Some sessions complete quests. Training the model on both equally teaches it to average good and bad behavior.

We needed a way to say "this session was good, learn more from it" and "this session was bad, learn less from it" — without requiring paired comparisons.

---

## Why Not DPO

DPO needs **pairs**: a good completion and a bad completion for the **same prompt**. Our data doesn't have this. Each game state is unique — the agent is at a specific position, HP, level, with specific mobs nearby. Finding two sessions that start from the same state and diverge is nearly impossible in a live game.

We could construct synthetic pairs by replaying game states and generating multiple completions, but that requires:
1. A game state replay mechanism (we don't have one)
2. Multiple model completions per state (expensive with Claude as teacher)
3. Careful matching to avoid spurious pairs

KTO sidesteps all of this. It just needs a binary label per example.

## Why Not PPO

PPO needs a **critic model** — a separate neural network that estimates the value of each game state. For Qwen3.5-9B:
- Policy model: ~18GB
- Reference model: ~18GB
- Critic model: ~18GB (same architecture)
- Optimizer states: ~12GB

Total: ~66GB. H100 80GB could barely fit it, with no room for batch computation. And PPO is notoriously unstable for LLM training — requires careful tuning of clip ratios, entropy bonuses, and reward normalization.

GRPO avoids the critic model entirely (generates multiple completions, uses relative scoring). But GRPO needs multiple completions per prompt at training time, which multiplies compute.

## Why KTO Fits

KTO needs exactly what we have:
1. **Unpaired examples** — each session scored independently
2. **Binary labels** — top 40% = desirable, bottom 30% = undesirable (middle skipped)
3. **Automated scoring** — XP gain, quest progress, deaths, click_tile rate, stuck rate
4. **Post-SFT application** — builds directly on r6 checkpoint

Memory footprint (current approach): model (18GB) + LoRA + optimizer (~4GB) = ~22GB on H100 80GB. Very comfortable.

**Note:** Early implementation used an explicit plain-HF ref model (18GB additional), totalling ~40GB. This was later abandoned — see Reference Model section below for the full story.

The KTO paper (arxiv 2402.01306) showed it matches DPO on MT-Bench within 0.1-0.5 points using unpaired data. For our use case (game actions, not chat quality), binary labels from game outcomes are arguably more signal-rich than human preferences anyway.

---

## Key Design Decisions

### Scoring function (`score_sessions.py`)

Positive signals (weights sum to 0.85; remainder absorbed by negatives — verified against `score_sessions.py` Apr 24, 2026):
- XP delta / 300 (15%) — raw XP gain
- Level delta / 3.0 (13%) — scales across multi-level sessions
- Quest progression (20%) — actual quest state changes: completions (1.0), stage advances (0.4), new accepts (0.2)
- Progress events / 4.0 (10%) — level up, XP gain between consecutive turns
- Unique positions / 20.0 (14%) — exploration breadth
- Average per-turn quality score (13%)

Penalties:
- Respawn count (deaths)
- click_tile rate (fallback actions)
- Repetitive loop rate (stuck behavior)
- Stuck rate (failed navigation)
- Death flags

**Decision:** `level_delta / 3.0` not `/ 1.0` — a session that gains 3 levels shouldn't saturate the score. Codex reviewed and confirmed.

**Decision:** Removed `attack_rate > 0.80` penalty — was biasing against AGGRESSIVE personality sessions, which legitimately spend 80%+ of turns in combat. The personality system is intentionally diverse; the scoring shouldn't penalize one style.

### Labeling thresholds

- Top 40% → desirable
- Bottom 30% → undesirable
- Middle 30% → neutral (skipped)

The gap prevents borderline sessions from being mislabeled. A session with score 0.51 shouldn't be "desirable" just because it's barely above median.

### Sliding windows (`build_kto_dataset.py`)

Window size=5, stride=2. Each window is 5 consecutive turns from a labeled session.

**Why windows, not full sessions?** Sessions are 50-150 turns. KTO works on prompt-completion pairs. A full session is too long for one training example. Windows give the model local context (what happened in the last 5 turns) with a session-level label (was this a good or bad session?).

**Local quality gating:** Even in a "desirable" session, some windows are bad (agent stuck for 5 turns). Positive windows need score >= 0.45. Negative windows need score <= 0.60. This prevents label noise.

### Reference model

**Final approach (Apr 5):** `ref_model=None` + `precompute_ref_log_probs=True`.

TRL's PEFT-native path: when `ref_model=None`, KTOTrainer uses the training model with adapters disabled (`null_ref_context`) as the reference. With `precompute_ref_log_probs=True`, all reference log probs (main dataset + KL dataset) are computed once during preprocessing and cached as dataset columns. During `trainer.train()` there is no reference model in GPU memory at all.

**Why we got here (full story):**
1. **Explicit plain-HF ref model (bf16):** Original approach. 18GB ref + 18GB policy = 36GB baseline. OOMed during `_compute_kl_logps` — KTO's third forward pass (KL computation) pushed peak to ~85GB > 80GB H100.
2. **8-bit ref model:** Tried `BitsAndBytesConfig(load_in_8bit=True)` to cut ref model from 18GB → 9GB. Failed with `AttributeError: 'Parameter' object has no attribute 'CB'` — bitsandbytes 8-bit quantization is incompatible with Unsloth's compiled Qwen3.5 module on cu128.
3. **batch_size=1:** KTOTrainer raises `ValueError` — requires `per_device_train_batch_size > 1` because the KL dataset is built by mismatching examples within each batch.
4. **ref_model=None + precompute_ref_log_probs=True (current):** Codex recommendation. Eliminates ref model from training memory entirely. Preprocessing takes ~90 min (forward pass over full dataset once), but training runs at ~22GB peak. Smoke test confirmed: 10/10 steps, `train_loss=0.617`, KL active, eval clean.

**Remaining known issue:** Unsloth's `save_pretrained` raises RuntimeError (LoRA count mismatch 128 vs 256) when TRL's adapter toggling during precompute leaves Unsloth's module registry out of sync. Fix: fallback to `model.base_model.save_pretrained()` (commit 34314ad). Unverified in smoke test but standard PEFT save — low risk.

---

## What KTO Won't Fix

- **Action format errors** — model generating malformed tool calls. Fix: guided decoding (KAE-14).
- **Tool selection confusion** — model choosing wrong tool from 17 options (trimmed from 22 via PR #29 modular MCP refactor Apr 25; under RAG-MCP 19-tool threshold). Fix: context-dependent tool filtering (KAE-15).
- **Long-horizon planning** — model forgetting quest objectives after 15 turns. Fix: memory module (KAE-20).
- **Verbosity reward hacking** — longer reasoning getting lower per-token loss. Fix: Dr. GRPO (KAE-12).

KTO teaches judgment (prefer good outcomes). These other issues are about capability (can the model execute correctly).

---

## References

- KTO: arxiv 2402.01306
- DPO: arxiv 2305.18290
- Agent-FLAN: arxiv 2403.12881
- KAE-13 (Linear): full implementation details and run order
