"""
Modal finetune script for Qwen3.5-9B on Kaetram gameplay data.

Uses Unsloth for 2x faster training + 70% less memory. bf16 LoRA (NOT QLoRA —
4-bit is not recommended for Qwen3.5 due to quantization differences).

Exports merged safetensors for SGLang serving on Modal.

Usage:
    # First time: authenticate with Modal
    modal setup

    # Run finetuning (uses H100 GPU, ~$6-8 total)
    modal run finetune/train_modal.py

    # Deploy serving endpoint
    modal deploy finetune/serve_modal.py
"""

import pathlib
from dataclasses import dataclass
from typing import Optional

import modal

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

app = modal.App("kaetram-qwen-finetune")

# Persistent volumes — cache model weights, store results
model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

# Container image — CUDA devel base for flash-attn compilation (Qwen3.5 is a unified VLM,
# Unsloth routes through vision.py which needs FA2 compiled with nvcc)
train_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.6.3-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install("cmake", "build-essential", "git")
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
    # flash-attn must be installed AFTER torch (build dependency, needs nvcc from CUDA devel)
    .run_commands("pip install flash-attn --no-build-isolation")
    .env({"HF_HOME": "/model_cache", "TOKENIZERS_PARALLELISM": "false"})
)

with train_image.imports():
    # unsloth must be imported first to apply patches
    import unsloth  # noqa: F401,I001
    import datasets
    import torch
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "unsloth/Qwen3.5-9B"  # Unsloth-optimized, Apache 2.0
MAX_SEQ_LEN = 8192   # Round 6: halved from 16k (median=3.6k, P90=12.7k — 8k covers ~90%)
LORA_R = 64       # Round 2: 4x more capacity (was 16)
LORA_ALPHA = 64   # alpha = r recommended for Qwen3.5
LORA_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# Training
BATCH_SIZE = 2    # Round 6: doubled (8k context fits batch=2 on H100 80GB)
GRAD_ACCUM = 8    # effective batch = 16 (2 * 8)
LR = 1e-4
WARMUP_RATIO = 0.05
WEIGHT_DECAY = 0.01
MAX_STEPS = -1  # -1 = use num_train_epochs
EPOCHS = 1      # Round 6: 1 epoch — standard SFT, loss converges within epoch 1
SAVE_STEPS = 50
EVAL_STEPS = 50
LOGGING_STEPS = 10

# Loss masking: zero loss on input tokens (Structured Agent Distillation, arxiv 2505.13820)
# Only trains on assistant responses (<think> reasoning + tool calls)
MASK_INPUT_TOKENS = True

# Output
EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r6-optimized"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_kaetram_dataset(train_bytes: bytes, val_bytes: bytes, metadata_bytes: bytes, tokenizer):
    """Load Kaetram SFT data and format with the chat template.

    Records contain only gameplay messages (no system prompt or tools).
    System prompt and tool definitions are injected from metadata.
    """
    import json

    metadata = json.loads(metadata_bytes)
    system_prompt = metadata["system_prompt"]
    tool_definitions = metadata["tools"]
    personality_suffixes = metadata.get("personality_suffixes", {})

    def parse_and_format(raw_bytes):
        records = json.loads(raw_bytes)
        rows = []
        for rec in records:
            # Reconstruct system message with personality
            personality = rec.get("personality")
            sys_content = system_prompt
            if personality and personality in personality_suffixes:
                sys_content += personality_suffixes[personality]

            messages = [{"role": "system", "content": sys_content}]

            for msg in rec["messages"]:
                m = {"role": msg["role"]}

                # Handle content (may be string, list, or absent for tool-call-only)
                content = msg.get("content")
                if isinstance(content, list):
                    m["content"] = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                elif isinstance(content, str):
                    m["content"] = content
                elif content is None and "tool_calls" not in msg:
                    m["content"] = ""

                # Handle tool_calls (assistant messages calling MCP tools)
                if "tool_calls" in msg:
                    tool_calls = []
                    for tc in msg["tool_calls"]:
                        tc = dict(tc)
                        if "function" in tc:
                            func = dict(tc["function"])
                            args = func.get("arguments", {})
                            if isinstance(args, str):
                                func["arguments"] = json.loads(args)
                            tc["function"] = func
                        tool_calls.append(tc)
                    m["tool_calls"] = tool_calls

                # Handle tool results
                if "tool_call_id" in msg:
                    m["tool_call_id"] = msg["tool_call_id"]
                if "name" in msg and msg["role"] == "tool":
                    m["name"] = msg["name"]

                messages.append(m)

            # Apply chat template with tools
            try:
                formatted = tokenizer.apply_chat_template(
                    messages,
                    tools=tool_definitions,
                    tokenize=False,
                    add_generation_prompt=False,
                )
            except TypeError:
                formatted = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=False,
                )
            rows.append({"text": formatted})
        return datasets.Dataset.from_list(rows)

    train_ds = parse_and_format(train_bytes)
    val_ds = parse_and_format(val_bytes)
    return train_ds, val_ds


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

@app.function(
    image=train_image,
    gpu="H100",  # 80GB VRAM — bf16 LoRA on 9B fits easily
    timeout=6 * 3600,  # 6 hours (generous buffer for ~2-3h expected)
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
)
def train(train_data: bytes, val_data: bytes, metadata: bytes):
    """Run Unsloth bf16 LoRA finetune and save merged safetensors."""
    import json

    print(f"Training data: {len(train_data):,} bytes")
    print(f"Validation data: {len(val_data):,} bytes")
    print(f"Metadata: {len(metadata):,} bytes")

    # Load model with Unsloth — bf16, NOT 4-bit (QLoRA not recommended for Qwen3.5)
    print(f"Loading {MODEL_ID}...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=False,
        load_in_16bit=True,
    )

    # Configure LoRA
    print("Configuring LoRA...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=LORA_TARGETS,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",  # Unsloth optimized — lower VRAM
        random_state=42,
    )

    # Load and format dataset
    print("Loading dataset...")
    train_ds, val_ds = load_kaetram_dataset(train_data, val_data, metadata, tokenizer)
    print(f"Train: {len(train_ds)} records, Val: {len(val_ds)} records")

    # SFTConfig with completion_only_loss (Structured Agent Distillation, arxiv 2505.13820)
    output_dir = f"/checkpoints/{EXPERIMENT_NAME}"
    print(f"Loss masking: completion_only_loss={MASK_INPUT_TOKENS}")
    sft_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=EPOCHS,
        max_steps=MAX_STEPS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        optim="adamw_8bit",
        bf16=True,
        logging_steps=LOGGING_STEPS,
        eval_strategy="steps",
        eval_steps=EVAL_STEPS,
        save_strategy="steps",
        save_steps=SAVE_STEPS,
        save_total_limit=3,
        report_to="none",
        seed=42,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LEN,
        packing=False,
        completion_only_loss=MASK_INPUT_TOKENS,
    )

    # Trainer
    print("Initializing SFTTrainer...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        args=sft_config,
    )

    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total params: {total_params:,}, Trainable: {trainable_params:,} ({100*trainable_params/total_params:.2f}%)")

    # Train
    print("Starting training...")
    result = trainer.train()
    print(f"Training complete: {result.metrics}")

    # Save LoRA adapter
    adapter_dir = f"{output_dir}/adapter"
    print(f"Saving LoRA adapter to {adapter_dir}...")
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Save merged model (safetensors) for SGLang serving on Modal
    merged_dir = f"{output_dir}/merged"
    print(f"Saving merged safetensors to {merged_dir}...")
    model.save_pretrained_merged(merged_dir, tokenizer, save_method="merged_16bit")

    # Save metrics
    metrics = {
        "train_loss": result.metrics.get("train_loss"),
        "train_runtime": result.metrics.get("train_runtime"),
        "epochs": EPOCHS,
        "train_records": len(train_ds),
        "val_records": len(val_ds),
        "model_id": MODEL_ID,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "save_method": "merged_16bit",
        "max_seq_len": MAX_SEQ_LEN,
        "loss_masking": MASK_INPUT_TOKENS,
    }
    with open(f"{output_dir}/training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # Commit volume so everything persists
    checkpoint_vol.commit()

    print(f"\nDone! Files saved to Modal volume 'kaetram-model-vol':")
    print(f"  Adapter:  /checkpoints/{EXPERIMENT_NAME}/adapter/")
    print(f"  Merged:   /checkpoints/{EXPERIMENT_NAME}/merged/")
    print(f"  Metrics:  /checkpoints/{EXPERIMENT_NAME}/training_metrics.json")
    print(f"\nDeploy serving endpoint:")
    print(f"  modal deploy finetune/serve_modal.py")
    return metrics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main():
    """Upload training data and launch the finetune job."""
    import os

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    train_path = os.path.join(project_dir, "dataset", "qwen_sft", "train.json")
    val_path = os.path.join(project_dir, "dataset", "qwen_sft", "val.json")
    metadata_path = os.path.join(project_dir, "dataset", "qwen_sft", "metadata.json")

    if not os.path.exists(train_path):
        raise FileNotFoundError(f"Training data not found: {train_path}")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(
            f"Metadata not found: {metadata_path}\n"
            "Run: python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/"
        )

    print(f"Uploading training data...")
    with open(train_path, "rb") as f:
        train_data = f.read()
    with open(val_path, "rb") as f:
        val_data = f.read()
    with open(metadata_path, "rb") as f:
        metadata = f.read()

    print(f"  Train: {len(train_data):,} bytes")
    print(f"  Val:   {len(val_data):,} bytes")
    print(f"  Metadata: {len(metadata):,} bytes")
    print(f"  Model: {MODEL_ID}")
    print(f"  Method: bf16 LoRA (r={LORA_R}, alpha={LORA_ALPHA})")
    print(f"  Export: merged safetensors (for Modal SGLang serving)")
    print(f"  Max seq len: {MAX_SEQ_LEN}")
    print(f"Launching on Modal H100...")

    metrics = train.remote(train_data, val_data, metadata)

    print(f"\n{'='*60}")
    print("TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Loss:     {metrics.get('train_loss', '?'):.4f}")
    print(f"  Runtime:  {metrics.get('train_runtime', 0):.0f}s")
    print(f"  Records:  {metrics.get('train_records')} train / {metrics.get('val_records')} val")
    print(f"\nDeploy serving endpoint:")
    print(f"  modal deploy finetune/serve_modal.py")
