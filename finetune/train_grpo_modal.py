"""
Modal GRPO (Group Relative Policy Optimization) script for Kaetram agent.

Uses the SFT-finetuned Qwen3.5-9B as starting policy, then applies GRPO with
game-derived reward functions. The model generates completions (actions + reasoning)
and gets scored on gameplay quality — learning *what works*, not just *what was
demonstrated*.

Prerequisites:
    - SFT model trained via train_modal.py (adapter saved on Modal volume)
    - Prompt dataset generated via convert_to_qwen.py --format grpo

Usage:
    modal run finetune/train_grpo_modal.py

    # With custom SFT checkpoint
    modal run finetune/train_grpo_modal.py --sft-checkpoint kaetram-qwen3.5-9b-r3-multiturn
"""

import pathlib
from dataclasses import dataclass
from typing import Optional

import modal

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

app = modal.App("kaetram-qwen-grpo")

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

train_image = (
    modal.Image.debian_slim(python_version="3.11")
    .uv_pip_install(
        "accelerate>=1.9.0",
        "datasets>=3.6.0",
        "hf-transfer>=0.1.9",
        "huggingface_hub>=0.34.2",
        "peft>=0.16.0",
        "transformers>=5.0.0",
        "trl>=0.19.1",
        "unsloth[cu128-torch270]>=2025.7.8",
        "unsloth_zoo>=2025.7.10",
    )
    .env({"HF_HOME": "/model_cache", "TOKENIZERS_PARALLELISM": "false"})
)

with train_image.imports():
    import unsloth  # noqa: F401,I001
    import datasets
    import torch
    from trl import GRPOTrainer, GRPOConfig
    from unsloth import FastLanguageModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "unsloth/Qwen3.5-9B"
MAX_SEQ_LEN = 32768
LORA_R = 64
LORA_ALPHA = 64
LORA_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# GRPO-specific
NUM_GENERATIONS = 4       # group size G (completions per prompt)
MAX_COMPLETION_LEN = 512  # max tokens for action + reasoning
MAX_PROMPT_LEN = 4096     # game state context

# Training
BATCH_SIZE = 1
GRAD_ACCUM = 4    # effective: 1 prompt × 4 generations × 4 accum = 16 samples
LR = 5e-6         # 20x lower than SFT — RL needs gentle updates
WARMUP_RATIO = 0.1
WEIGHT_DECAY = 0.01
EPOCHS = 1
LOGGING_STEPS = 10
SAVE_STEPS = 100

# Output
EXPERIMENT_NAME = "kaetram-qwen3.5-9b-grpo"
GGUF_QUANT = "q4_k_m"
SFT_CHECKPOINT = "kaetram-qwen3.5-9b-r4-lossmasked"  # default SFT starting point


# ---------------------------------------------------------------------------
# Reward functions
# ---------------------------------------------------------------------------

def _parse_action(text: str) -> tuple[str, str]:
    """Extract action type and full action from model completion."""
    import re
    m = re.search(r"<action>\s*(\w+)\(([^)]*)\)\s*</action>", text)
    if m:
        return m.group(1), f"{m.group(1)}({m.group(2)})"
    m = re.search(r"<action>\s*(.*?)\s*</action>", text, re.DOTALL)
    if m:
        action = m.group(1).strip()
        am = re.match(r"(\w+)\(", action)
        return (am.group(1) if am else "unknown"), action
    return "invalid", ""


def _extract_reward_context(prompt_text: str) -> dict:
    """Extract game state and reward context from the prompt."""
    import json, re
    ctx = {}
    m = re.search(r"<game_state>\s*(.*?)\s*</game_state>", prompt_text, re.DOTALL)
    if m:
        try:
            ctx["state"] = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            ctx["state"] = {}
    else:
        ctx["state"] = {}
    m = re.search(r"<reward_context>\s*(.*?)\s*</reward_context>", prompt_text, re.DOTALL)
    if m:
        try:
            ctx["reward"] = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            ctx["reward"] = {}
    else:
        ctx["reward"] = {}
    return ctx


def reward_valid_action(completions: list[str], **kwargs) -> list[float]:
    """Reward for producing a correctly formatted action."""
    rewards = []
    valid_actions = {
        "attack", "interact_npc", "talk_npc", "navigate", "move", "click",
        "click_entity", "click_tile", "equip", "heal", "warp", "quest_accept",
        "set_style", "wait", "respawn", "update_memory",
    }
    for text in completions:
        action_type, _ = _parse_action(text)
        if action_type in valid_actions:
            rewards.append(0.2)
        elif action_type == "invalid":
            rewards.append(-1.0)
        else:
            rewards.append(-0.3)
    return rewards


def reward_format(completions: list[str], **kwargs) -> list[float]:
    """Reward for correct output format: <think>...</think><action>...</action>."""
    rewards = []
    for text in completions:
        has_think = "<think>" in text and "</think>" in text
        has_action = "<action>" in text and "</action>" in text
        if has_think and has_action:
            rewards.append(0.3)
        elif has_action:
            rewards.append(0.1)
        else:
            rewards.append(-0.5)
    return rewards


def reward_healing_awareness(completions: list[str], prompts: list[str], **kwargs) -> list[float]:
    """Reward healing when HP is low, penalize ignoring low HP."""
    import json, re
    rewards = []
    for text, prompt in zip(completions, prompts):
        ctx = _extract_reward_context(prompt)
        state = ctx["state"]
        ps = state.get("player_stats", {})
        hp = ps.get("hp", 100)
        max_hp = ps.get("max_hp", 100)
        hp_ratio = hp / max_hp if max_hp > 0 else 1.0

        action_type, _ = _parse_action(text)

        if hp_ratio < 0.5:
            # HP is low — should heal
            if action_type == "heal":
                rewards.append(0.5)   # Correct: healed when needed
            elif action_type == "attack":
                rewards.append(-0.3)  # Bad: attacked when should heal
            else:
                rewards.append(-0.1)
        else:
            rewards.append(0.0)  # HP is fine, no bonus/penalty
    return rewards


def reward_combat_awareness(completions: list[str], prompts: list[str], **kwargs) -> list[float]:
    """Reward engaging appropriate targets and penalize no-ops."""
    import json, re
    rewards = []
    for text, prompt in zip(completions, prompts):
        ctx = _extract_reward_context(prompt)
        state = ctx["state"]
        action_type, _ = _parse_action(text)

        # Check if there are mobs nearby
        entities = state.get("nearby_entities", [])
        has_mobs = any(e.get("type") == 3 for e in entities if isinstance(e, dict))
        has_quest = bool(state.get("quests"))
        is_dead = state.get("ui_state", {}).get("is_dead", False)

        if is_dead:
            # Should respawn
            if action_type == "respawn":
                rewards.append(0.5)
            else:
                rewards.append(-0.3)
        elif has_mobs and action_type == "attack":
            rewards.append(0.2)
        elif has_quest and action_type in ("navigate", "interact_npc", "quest_accept"):
            rewards.append(0.3)
        elif action_type == "wait":
            rewards.append(-0.1)  # Light penalty for waiting
        else:
            rewards.append(0.0)
    return rewards


def reward_memory_timing(completions: list[str], prompts: list[str], **kwargs) -> list[float]:
    """Reward update_memory at appropriate moments (level up, quest progress, etc)."""
    import json, re
    rewards = []
    for text, prompt in zip(completions, prompts):
        action_type, _ = _parse_action(text)
        if action_type != "update_memory":
            rewards.append(0.0)
            continue

        ctx = _extract_reward_context(prompt)
        reward_ctx = ctx.get("reward", {})

        # Memory writes are good after significant events
        if reward_ctx.get("xp_delta", 0) > 0 or reward_ctx.get("level_delta", 0) > 0:
            rewards.append(0.3)  # Good: saving after progress
        elif reward_ctx.get("died"):
            rewards.append(0.2)  # OK: saving after death (lessons learned)
        else:
            rewards.append(0.1)  # Neutral: saving progress is generally fine
    return rewards


def reward_step_penalty(completions: list[str], **kwargs) -> list[float]:
    """Small per-step penalty to prevent infinite loops."""
    return [-0.02] * len(completions)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_grpo_dataset(data_bytes: bytes, tokenizer):
    """Load prompt-only dataset for GRPO training.

    Each record has 'prompt' (list of messages without the assistant turn)
    and optional 'reward_context' for scoring.
    """
    import json

    records = json.loads(data_bytes)
    rows = []
    for rec in records:
        messages = rec.get("prompt", rec.get("messages", []))
        # Keep only system + user messages (no assistant)
        prompt_msgs = [m for m in messages if m["role"] != "assistant"]
        # Format content
        formatted_msgs = []
        for msg in prompt_msgs:
            content = msg["content"]
            if isinstance(content, list):
                text = "\n".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            else:
                text = str(content)
            formatted_msgs.append({"role": msg["role"], "content": text})

        # Apply chat template for the prompt
        prompt_text = tokenizer.apply_chat_template(
            formatted_msgs, tokenize=False, add_generation_prompt=True
        )
        rows.append({"prompt": prompt_text})

    return datasets.Dataset.from_list(rows)


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

@app.function(
    image=train_image,
    gpu="H100",
    timeout=6 * 3600,  # 6 hours — GRPO takes longer than SFT
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
)
def train(prompt_data: bytes, sft_checkpoint: str = SFT_CHECKPOINT):
    """Run GRPO training starting from SFT checkpoint."""
    import json
    import os

    print(f"Prompt data: {len(prompt_data):,} bytes")
    print(f"SFT checkpoint: {sft_checkpoint}")

    # Load base model
    print(f"Loading {MODEL_ID}...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=False,
        load_in_16bit=True,
    )

    # Load SFT LoRA adapter if available
    sft_adapter_path = f"/checkpoints/{sft_checkpoint}/adapter"
    if os.path.exists(sft_adapter_path):
        print(f"Loading SFT adapter from {sft_adapter_path}...")
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, sft_adapter_path)
        model = model.merge_and_unload()
        print("SFT adapter merged into base model.")
    else:
        print(f"WARNING: SFT adapter not found at {sft_adapter_path}, starting from base model.")

    # Configure fresh LoRA for GRPO
    print("Configuring LoRA for GRPO...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=LORA_TARGETS,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Load dataset
    print("Loading prompt dataset...")
    train_ds = load_grpo_dataset(prompt_data, tokenizer)
    print(f"Prompts: {len(train_ds)} records")

    # GRPO config
    output_dir = f"/checkpoints/{EXPERIMENT_NAME}"
    config = GRPOConfig(
        output_dir=output_dir,
        num_generations=NUM_GENERATIONS,
        max_completion_length=MAX_COMPLETION_LEN,
        max_prompt_length=MAX_PROMPT_LEN,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        num_train_epochs=EPOCHS,
        bf16=True,
        logging_steps=LOGGING_STEPS,
        save_strategy="steps",
        save_steps=SAVE_STEPS,
        save_total_limit=3,
        report_to="none",
        seed=42,
    )

    # Initialize trainer with reward functions
    print("Initializing GRPOTrainer...")
    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[
            reward_format,
            reward_valid_action,
            reward_healing_awareness,
            reward_combat_awareness,
            reward_memory_timing,
            reward_step_penalty,
        ],
        config=config,
        train_dataset=train_ds,
        tokenizer=tokenizer,
    )

    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total params: {total_params:,}, Trainable: {trainable_params:,} ({100*trainable_params/total_params:.2f}%)")

    # Train
    print("Starting GRPO training...")
    result = trainer.train()
    print(f"Training complete: {result.metrics}")

    # Save LoRA adapter
    adapter_dir = f"{output_dir}/adapter"
    print(f"Saving GRPO LoRA adapter to {adapter_dir}...")
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Export GGUF
    gguf_dir = f"{output_dir}/gguf"
    print(f"Exporting GGUF ({GGUF_QUANT}) to {gguf_dir}...")
    model.save_pretrained_gguf(gguf_dir, tokenizer, quantization_method=GGUF_QUANT)

    # Save metrics
    metrics = {
        "train_loss": result.metrics.get("train_loss"),
        "train_runtime": result.metrics.get("train_runtime"),
        "epochs": EPOCHS,
        "num_prompts": len(train_ds),
        "num_generations": NUM_GENERATIONS,
        "model_id": MODEL_ID,
        "sft_checkpoint": sft_checkpoint,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "gguf_quant": GGUF_QUANT,
        "max_seq_len": MAX_SEQ_LEN,
        "reward_funcs": [
            "reward_format", "reward_valid_action", "reward_healing_awareness",
            "reward_combat_awareness", "reward_memory_timing", "reward_step_penalty",
        ],
    }
    with open(f"{output_dir}/training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    checkpoint_vol.commit()

    print(f"\nDone! Files saved to Modal volume 'kaetram-model-vol':")
    print(f"  Adapter:  /checkpoints/{EXPERIMENT_NAME}/adapter/")
    print(f"  GGUF:     /checkpoints/{EXPERIMENT_NAME}/gguf/")
    print(f"  Metrics:  /checkpoints/{EXPERIMENT_NAME}/training_metrics.json")
    print(f"\nDownload GGUF:")
    print(f"  modal volume get kaetram-model-vol /checkpoints/{EXPERIMENT_NAME}/gguf ./kaetram-grpo-gguf")
    return metrics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(sft_checkpoint: str = SFT_CHECKPOINT):
    """Upload prompt data and launch GRPO training."""
    import os

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(project_dir, "dataset", "qwen_grpo", "prompts.json")

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"GRPO prompt data not found: {prompt_path}\n"
            f"Generate it with: python3 convert_to_qwen.py --input dataset/extracted/ "
            f"--output dataset/qwen_grpo/ --format grpo"
        )

    print("Uploading prompt data...")
    with open(prompt_path, "rb") as f:
        prompt_data = f.read()

    print(f"  Prompts: {len(prompt_data):,} bytes")
    print(f"  SFT checkpoint: {sft_checkpoint}")
    print(f"  Model: {MODEL_ID}")
    print(f"  Method: GRPO (G={NUM_GENERATIONS}, LoRA r={LORA_R})")
    print(f"  Reward funcs: format, valid_action, healing, combat, memory, step_penalty")
    print(f"Launching on Modal H100...")

    metrics = train.remote(prompt_data, sft_checkpoint)

    print(f"\n{'='*60}")
    print("GRPO TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Loss:     {metrics.get('train_loss', '?')}")
    print(f"  Runtime:  {metrics.get('train_runtime', 0):.0f}s")
    print(f"  Prompts:  {metrics.get('num_prompts')}")
    print(f"\nDownload GGUF:")
    print(f"  modal volume get kaetram-model-vol /checkpoints/{EXPERIMENT_NAME}/gguf ./kaetram-grpo-gguf")
