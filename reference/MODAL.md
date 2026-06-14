# Modal Infrastructure Reference

> All training, serving, and evaluation for the Kaetram Qwen3.5 agents (9B SFT lane + 2B/4B/27B scaffold & OPD lanes) run on [Modal](https://modal.com). This doc covers the complete setup.

---

## Quick Commands

```bash
# Training
modal run finetune/train_modal.py              # SFT (H200; multi-day at 16k seq)
modal run finetune/train_grpo_modal.py         # GRPO (planned stub — not yet implemented)
modal run finetune/train_kto_modal.py          # KTO (planned stub — not yet implemented)

# Merge a checkpoint (without full training)
modal run finetune/train_modal.py::merge_checkpoint --checkpoint-name checkpoint-150

# Serving
modal deploy finetune/serve_modal.py           # Deploy finetuned model
modal deploy finetune/serve_modal_base.py      # Deploy base model

# Stop endpoints (save $)
modal app stop kaetram-qwen-serve
modal app stop kaetram-qwen-base

# Check running apps
modal app list
```

---

## Files

| File | Purpose |
|------|---------|
| `finetune/train_modal.py` | SFT training (LoRA on H200; `merge_checkpoint` helper on H100) |
| `finetune/train_grpo_modal.py` | GRPO reinforcement learning (planned stub) |
| `finetune/train_kto_modal.py` | KTO preference learning (planned stub) |
| `finetune/serve_modal.py` | SGLang serving for finetuned model (A100) |
| `finetune/serve_modal_base.py` | SGLang serving for base Qwen3.5-9B (A100) |
| `finetune/serve_modal_2b.py` | SGLang serving for base Qwen3.5-2B + `/v1/score` (L4 serving; flip to A100 for batch `/v1/score` builds) — OPD student lane |
| `finetune/serve_modal_4b.py` | SGLang serving for base Qwen3.5-4B + `/v1/score` (A100) — OPD teacher lane |
| `finetune/serve_modal_27b.py` | SGLang serving for Qwen3.5-27B (H100) — capacity sanity check |
| `finetune/serve_modal_2b_opd.py` | SGLang serving for the OPD round-1 2B student + `/v1/score` (L4; A100 for batch scoring) |
| `finetune/serve_modal_2b_opd_r2.py` | SGLang serving for the OPD round-2 2B student + `/v1/score` (L4) |
| `finetune/serve_modal_2b_opd_r3.py` | SGLang serving for the OPD round-3 2B student + `/v1/score` (L4) |
| `finetune/train_opd_2b.py` | OPD trainer (round-parametrized): 4B teacher → base-2B student, clipped-IS reverse-KL (H100) |
| `finetune/train_opd_modal.py` | 9B OPD trainer (r10 → base+scaffold) — lane parked, never run (H200) |
| `play_qwen.py` | Inference client (calls Modal endpoints via OpenAI SDK) |
| `eval_harness.py` | Eval orchestrator (spawns play_qwen.py against both endpoints) |
| `scripts/run-eval.sh` | Eval launcher (parallel base vs SFT comparison) |

---

## Modal Apps

| App Name | File | GPU | Purpose |
|----------|------|-----|---------|
| `kaetram-qwen-finetune` | train_modal.py | H200 | SFT training (`merge_checkpoint` on H100) |
| `kaetram-qwen-grpo` | train_grpo_modal.py | H100 80GB | GRPO training (planned) |
| `kaetram-qwen-kto` | train_kto_modal.py | H100 80GB | KTO training (planned) |
| `kaetram-qwen-serve` | serve_modal.py | A100 40GB | Finetuned model inference |
| `kaetram-qwen-base` | serve_modal_base.py | A100 40GB | Base model inference |
| `kaetram-qwen-2b` | serve_modal_2b.py | L4 | Base 2B inference + `/v1/score` (OPD student; A100 for batch scoring) |
| `kaetram-qwen-4b` | serve_modal_4b.py | A100 | Base 4B inference + `/v1/score` (OPD teacher) |
| `kaetram-qwen-27b` | serve_modal_27b.py | H100 | 27B capacity sanity check |
| `kaetram-qwen-2b-opd` | serve_modal_2b_opd.py | L4 | OPD round-1 2B student inference + gate scoring |
| `kaetram-qwen-2b-opd-r2` | serve_modal_2b_opd_r2.py | L4 | OPD round-2 2B student inference + gate scoring |
| `kaetram-qwen-2b-opd-r3` | serve_modal_2b_opd_r3.py | L4 | OPD round-3 2B student inference + gate scoring |
| `kaetram-qwen-2b-opd-finetune` | train_opd_2b.py | H100 | OPD training (4B → 2B), round-parametrized |
| `kaetram-qwen-opd` | train_opd_modal.py | H200 | 9B OPD training (parked, never run) |

---

## Modal Volumes (Persistent Storage)

| Volume Name | Mount Path | Contents |
|-------------|-----------|----------|
| `kaetram-model-cache` | `/model_cache` | HuggingFace model weights cache |
| `kaetram-model-vol` | `/checkpoints` | Training checkpoints, LoRA adapters, merged models |

Volume structure:
```
/checkpoints/
  kaetram-qwen3.5-9b-r10/
    adapter/              # LoRA adapter weights
    merged/               # Full merged safetensors (for SGLang serving)
    training_metrics.json # Loss curves, eval results
    checkpoint-50/        # Intermediate checkpoints (save_steps=50)
    checkpoint-100/
    checkpoint-150/
```

Volumes persist across container restarts. Checkpoints saved during training survive timeouts.

---

## SFT Training (train_modal.py)

### Model & LoRA Config

| Parameter | Value | Notes |
|-----------|-------|-------|
| Base model | `unsloth/Qwen3.5-9B` | Unsloth-optimized, Apache 2.0 |
| MAX_SEQ_LEN | 16,384 | Truncation gate (`convert_to_qwen._drop_overlong`) drops any record over this. |
| LoRA rank (r) | 64 | Increased from 16 in round 2 |
| LoRA alpha | 64 | alpha = r recommended for Qwen3.5 |
| LoRA targets | q/k/v/o/gate/up/down_proj | All attention + MLP projections |
| use_rslora | False | rsLoRA diverged at r=64/alpha=64 (8x effective LR trap) |
| Gradient checkpointing | "unsloth" | Unsloth-optimized, lower VRAM |

### Training Hyperparameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Batch size | 2 | Per device |
| Gradient accumulation | 8 | Effective batch = 16 |
| Learning rate | 1e-4 | |
| LR scheduler | Cosine | |
| Warmup ratio | 0.05 | |
| Weight decay | 0.01 | |
| Epochs | 1 | Loss converges within 1 epoch |
| Save steps | 50 | Checkpoints to Modal volume |
| Eval steps | 50 | Validation loss check |
| Logging steps | 10 | Loss to stdout |
| save_total_limit | 3 | Keeps last 3 checkpoints |

### Loss Masking

Uses Unsloth's `train_on_responses_only()`:
- `instruction_part="<|im_start|>user\n"` — masks user messages + tool results
- `response_part="<|im_start|>assistant\n"` — trains on assistant responses only
- Qwen3.5 renders `role:tool` as `<|im_start|>user` with `<tool_response>` wrapper, so tool results are correctly masked

### Chat Template Patch

Qwen3.5's stock template strips `<think>` reasoning from intermediate assistant turns (only keeps it on the last turn — QwenLM/Qwen3 #1831, still open against Qwen3.5 as of May 2026). `patch_qwen_chat_template()` in `finetune/render.py` fixes this to preserve reasoning on every turn. Single source of truth — imported by `convert_to_qwen.py` (truncation gate), `train_modal.py`, `serve_modal.py`, `serve_modal_base.py` (the deferred `train_kto_modal.py` / `train_grpo_modal.py` stubs will re-import it when implemented). Verified by `tests/unit/test_think_roundtrip.py` against `unsloth/Qwen3.5-9B`.

### Data Augmentation

- **System prompt intro**: 4 paraphrase variants for training rows (`SYSTEM_PROMPT_INTRO_VARIANTS` in `finetune/render.py`); validation rows use the canonical intro from `prompts/system.md` unchanged.
- **Personality suffixes**: 3 archetypes (`grinder` / `completionist` / `explorer_tinkerer`), one `.md` file each in `prompts/personalities/`. Substituted at `__PERSONALITY_BLOCK__` in the system prompt.
- **Body split**: `\n\n<game_knowledge>` marker — everything after is byte-identical across variants.

### Container Image

```
Base: nvidia/cuda:12.6.3-devel-ubuntu22.04
Python: 3.11
Key packages: unsloth[cu128-torch270]>=2025.7.8, transformers>=5.0.0, trl>=0.19.1
flash-attn: compiled from source (needs nvcc from devel image)
```

### Timeout & Cost

| Parameter | Value |
|-----------|-------|
| Timeout | 72 hours |
| GPU | H200 141GB (`merge_checkpoint` helper: H100) |
| Typical duration | scales with corpus size; multi-day at MAX_SEQ_LEN=16,384 (r10: ~43h for 9,363 records) |
| Typical cost | $50-200 depending on corpus size and resume strategy (r10 actual: $197 for the final ~43h run, billing-verified) |

**Cost note**: at MAX_SEQ_LEN=16,384 training is HBM-bandwidth-bound (gradient offloading) — several minutes per step; the H200's bandwidth is why this workload moved off H100. Step count = `(train_records / batch_size / grad_accum) * epochs`. Budget accordingly; use checkpoint-resume if a single window won't fit.

### Training Data Input

Reads from local disk, uploads as bytes to Modal:
- `dataset/qwen_sft/train.json` — training records
- `dataset/qwen_sft/val.json` — validation records  
- `dataset/qwen_sft/metadata.json` — system prompt, tool definitions, personality suffixes

### Email Notifications

Sends email on start/finish/failure via Modal Secrets (`notification_env()`). Includes loss summary and duration.

---

## Serving (serve_modal.py)

### SGLang Engine Config

| Parameter | Value |
|-----------|-------|
| GPU | A100 40GB |
| dtype | bfloat16 |
| context_length | 32,768 |
| mem_fraction_static | 0.92 |
| Min containers | 1 (always warm) |
| Scaledown window | 600s (10 min idle) |
| Request timeout | 300s |

### Model Loading Priority

The serving endpoint checks these locations in order (run name controlled by `SFT_EXPERIMENT` env, defaults to `kaetram-qwen3.5-9b-r10`):
1. Cached merged model at `/model_cache/kaetram-merged-{SFT_EXPERIMENT}/`
2. GRPO merged at `/checkpoints/kaetram-qwen3.5-9b-grpo/merged/`
3. SFT merged at `/checkpoints/{SFT_EXPERIMENT}/merged/`
4. Adapter-only (load base + merge adapter on startup)
5. Base model fallback

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Status + model name |
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Main inference (OpenAI-compatible) |

### Tool Call Parsing

The endpoint parses Qwen3.5 Coder XML from model output:
```xml
<tool_call>
<function=attack>
<parameter=mob_name>Rat</parameter>
</function>
</tool_call>
```

Returns structured `tool_calls` array in OpenAI response format.

### Endpoint URLs

| Model | URL |
|-------|-----|
| Finetuned | `https://workspace--kaetram-qwen-serve-inference-serve.modal.run/v1` |
| Base | `https://workspace--kaetram-qwen-base-inference-serve.modal.run/v1` |

---

## Base Model Serving (serve_modal_base.py)

Same as finetuned serving but:
- Always loads `Qwen/Qwen3.5-9B` (no checkpoint logic)
- `min_containers=0` (scales to zero when idle, cheaper)
- `scaledown_window=300s` (5 min)
- Model ID: `"kaetram-base"`

---

## Evaluation Flow

```
run-eval.sh
  ├─ eval_harness.py --models r10-sft=<modal-url> (port 9061)
  └─ eval_harness.py --models base=<modal-url>    (port 9071)
       ├─ Per episode: reset MongoDB → spawn play_qwen.py → collect logs
       ├─ Sub-session continuation (restart every ~30 turns, preserve DB)
       └─ Compute metrics from logs → results.json
```

### Eval Output

```
dataset/eval/runs/
  YYYYMMDD_HHMMSS_[personality]/
    r10-sft/results.json
    r10-sft/episode_001.jsonl
    base/results.json
    base/episode_001.jsonl
  latest → (symlink)
```

### Scenarios

Time-based (`eval_harness.SCENARIOS`): each scenario is a wall-clock budget; `play_qwen` runs its warm-session loop for `duration_minutes`, rotating sessions on context overflow. Same budget for SFT and base → fair A/B regardless of model speed. (The Core-3 OPD evals use a separate 6h × 3-archetype protocol via `run-eval.sh`.)

| ID | Name | Duration | Description |
|----|------|----------|-------------|
| A | Rat Grind | 5 min | Kill 10 rats from Level 1 in Mudwich |
| B | Snek Quest | 20 min | Complete Bike Lyson snake quest |
| C | Multi-Zone | 15 min | Visit 3+ zones via warping |
| D | Open Play | 30 min | 30 minutes open-ended from Level 1 |

---

## Checkpoint Management

### Merging a Checkpoint

If training times out, merge an intermediate checkpoint:

```bash
# List what's on the volume
modal volume ls kaetram-model-vol /checkpoints/kaetram-qwen3.5-9b-r10/

# Merge checkpoint-150 into full model
modal run finetune/train_modal.py::merge_checkpoint --checkpoint-name checkpoint-150
```

This produces merged safetensors at `/checkpoints/{experiment}/merged/` ready for `serve_modal.py`.

### Deploying After Merge

```bash
# Update serve_modal.py model loading path if needed, then:
modal deploy finetune/serve_modal.py

# Test
curl https://workspace--kaetram-qwen-serve-inference-serve.modal.run/health
```

### Resuming Training

Not built in currently. To resume from a checkpoint:
1. Load model + LoRA adapter from checkpoint directory
2. Pass `resume_from_checkpoint` to SFTTrainer
3. Requires ~10 lines of code change to `train_modal.py`

---

## Cost Summary

| Operation | GPU | Duration | Cost |
|-----------|-----|----------|------|
| SFT training | H200 | scales with corpus; multi-day at 16k seq (r10: ~43h) | $50-200 (r10 actual: $197) |
| GRPO training | H100 | ~6h (deferred) | ~$24 |
| KTO training | H100 | ~8h (deferred) | ~$32 |
| Checkpoint merge | H100 | 30 min | ~$2 |
| Finetuned serving (warm) | A100 | per hour | ~$1.10/hr |
| Base serving (idle) | A100 | 0 when idle | $0 idle, ~$1.10/hr active |
| Eval run (3 ep × 2 models) | A100 | ~4h | ~$4.40 |

**Cost optimization**: Stop serving endpoints when not evaluating (`modal app stop`). Base model uses `min_containers=0` so it costs nothing when idle.

---

## Known Issues & Gotchas

1. **rsLoRA trap**: `use_rslora=True` with `r=alpha=64` gives 8x effective LR (rsLoRA scales `1/sqrt(r)` not `1/r`). Keep `use_rslora=False`.
2. **Qwen3.5 chat template `<think>` stripping**: Stock template drops intermediate-turn reasoning. Patched at runtime via `finetune/render.patch_qwen_chat_template()`. Verified against `unsloth/Qwen3.5-9B`.
3. **MAX_SEQ_LEN=16,384 is slow**: ~5.5 min/step on H100 due to gradient offloading. Step count = `(train_records / batch_size / grad_accum) * epochs`; budget for multi-day runs.
4. **`tools=` kwarg deliberately omitted**: training and serving both pass conversation messages to `apply_chat_template` without `tools=`. The tool table is embedded in `prompts/system.md` as markdown. Passing `tools=` would emit a second JSON-schema block the model was never trained on. Source of truth: `finetune/render.render_record`.
5. **TRL `SFTConfig.max_seq_length` was renamed to `max_length`** in TRL ≥0.20 (TRL #3910); the old name is silently ignored. `train_modal.py` already uses `max_length`.
6. **TRL `train_on_responses_only` + truncation can silently zero per-record loss** (TRL #3927, still open as of May 2026). Defenses: (a) `_drop_overlong` truncation gate in `convert_to_qwen.py` keeps every record under MAX_SEQ_LEN; (b) `train_modal.py` wraps the data collator with a `(labels != -100).any(dim=-1).all()` per-batch assert that aborts on any all-masked record.
7. **Modal volume commit**: `checkpoint_vol.commit()` is called after training completes. If container is killed mid-save, the latest checkpoint may be incomplete — previous checkpoints are safe.
