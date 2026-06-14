"""r11 OPD trainer (Modal) — reverse-KL on-policy distillation of r10 toward base+scaffold.

Continues training the EXISTING r10 LoRA (loaded as trainable init) on pre-tokenized
records from `scripts/opd/opd_modal_data.py` (each carries per-token `advantages` and
`behavior_logprobs`). Loss is clipped importance-sampling reverse-KL:

    IS   = exp(logp_current - behavior_logprob)   clipped to [IS_CLIP_LOW, IS_CLIP_HIGH]
    loss = -(IS * advantage).mean()   over action tokens only

Memory note: a full-vocab logits forward at 16K x 248K would OOM, so we run the
transformer BODY to hidden states and apply the LM head ONLY at the unmasked (action)
positions (~50-200 tokens/record) — the only positions the loss touches.

Records staged on the checkpoint volume (upload with `modal volume put`):
    modal volume put kaetram-model-vol \
        dataset/opd_r11/round1/records.jsonl /opd_r11/round1/records.jsonl

Run:  modal run finetune/train_opd_modal.py
"""
import modal

app = modal.App("kaetram-qwen-opd")

# Self-contained (no cross-module import — that crash-loops in Modal containers /
# DataLoader workers). Image is byte-identical to train_modal.train_image so Modal
# reuses the cached build. Constants/volumes mirror train_modal.
MODEL_ID = "unsloth/Qwen3.5-9B"
MAX_SEQ_LEN = 16384
LORA_R = 64
LORA_ALPHA = 64
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

train_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("cmake", "build-essential", "git")
    .uv_pip_install(
        "torch==2.8.0", "accelerate>=1.10.0", "bitsandbytes>=0.49.2", "datasets==4.3.0",
        "huggingface_hub>=1.3.0,<2.0", "peft>=0.18.0", "transformers==5.5.0", "trl==0.24.0",
        "unsloth==2026.5.2", "unsloth_zoo>=2026.5.1", "xformers==0.0.32.post2",
    )
    .run_commands(
        "pip install "
        "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/"
        "flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp311-cp311-linux_x86_64.whl "
        "--no-build-isolation"
    )
    .env({
        "HF_HOME": "/model_cache",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_DISABLE_XET": "1",
        "TRITON_CACHE_DIR": "/model_cache/.triton_cache",
    })
    .add_local_python_source("render")
)
_notification_secrets = []

R10_EXPERIMENT = "kaetram-qwen3.5-9b-r10"
OPD_EXPERIMENT = "kaetram-qwen3.5-9b-r11"
RECORDS_PATH = "/checkpoints/opd_r11/round1/records.jsonl"

# OPD hyperparameters — gentler than SFT (this is a recovery nudge, not a fresh fit).
LR = 1e-5
EPOCHS = 1
BATCH_SIZE = 2
GRAD_ACCUM = 8          # effective 16
IS_CLIP_LOW, IS_CLIP_HIGH = 0.2, 5.0   # cispo/ppo-style trust region on the IS ratio
LOGGING_STEPS = 2
SAVE_STEPS = 100

with train_image.imports():
    import unsloth  # noqa: F401 — must import first to apply patches
    import torch
    from unsloth import FastLanguageModel


def _load_records(path):
    import json
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def _opd_collator(features):
    """Pad pre-tokenized records; carry advantages + behavior_logprobs row-aligned."""
    import torch
    maxlen = max(len(f["input_ids"]) for f in features)

    def pad(key, fill, dtype):
        return torch.tensor(
            [f[key] + [fill] * (maxlen - len(f[key])) for f in features], dtype=dtype)

    input_ids = pad("input_ids", 0, torch.long)
    labels = pad("labels", -100, torch.long)
    advantages = pad("advantages", 0.0, torch.float)
    behavior = pad("behavior_logprobs", 0.0, torch.float)
    attention_mask = torch.tensor(
        [[1] * len(f["input_ids"]) + [0] * (maxlen - len(f["input_ids"])) for f in features],
        dtype=torch.long)
    return {"input_ids": input_ids, "attention_mask": attention_mask,
            "labels": labels, "advantages": advantages, "behavior_logprobs": behavior}


def _make_trainer_cls():
    """Build the OPD Trainer subclass inside the image (transformers available there)."""
    from transformers import Trainer

    class OPDTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            import torch
            labels = inputs["labels"]
            advantages = inputs["advantages"]
            behavior = inputs["behavior_logprobs"]
            input_ids = inputs["input_ids"]
            attention_mask = inputs["attention_mask"]

            # Transformer BODY only (no full-vocab logits) → hidden states.
            base = model.get_base_model() if hasattr(model, "get_base_model") else model
            body = base.model            # Qwen3_5Model
            lm_head = base.lm_head
            hidden = body(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

            # Next-token alignment: predict token t+1 from hidden at t.
            hidden = hidden[:, :-1, :]
            labels_s = labels[:, 1:]
            adv_s = advantages[:, 1:]
            beh_s = behavior[:, 1:]
            mask = labels_s != -100

            idx = mask.nonzero(as_tuple=False)
            if idx.numel() == 0:
                return (torch.zeros((), device=hidden.device, requires_grad=True),) if return_outputs \
                    else torch.zeros((), device=hidden.device, requires_grad=True)
            b, t = idx[:, 0], idx[:, 1]
            hid_act = hidden[b, t]                         # [N_act, H]
            logits_act = lm_head(hid_act).float()         # [N_act, V] — only action positions
            logp_act = torch.log_softmax(logits_act, dim=-1)
            tgt = labels_s[b, t].unsqueeze(-1)            # [N_act, 1]
            cur = logp_act.gather(-1, tgt).squeeze(-1)    # [N_act]

            # REINFORCE on the reverse-KL advantage (single offline on-policy round:
            # init == data generator, so the IS correction is ~1 and its merged-vs-
            # adapter miscalibration would only saturate the clip and kill gradients).
            # advantage = -(logp_student - logp_teacher); minimizing -(adv*logp) lowers
            # logp on r10's over-confident tokens → pulls the policy toward base.
            adv = adv_s[b, t].clamp(-3.0, 3.0)   # bound extreme per-token advantages
            loss = -(adv * cur).mean()

            if return_outputs:
                metrics = {"adv_mean": adv.mean().detach(), "cur_mean": cur.mean().detach()}
                return loss, metrics
            return loss

    return OPDTrainer


@app.function(
    image=train_image,
    gpu="H200",
    timeout=12 * 3600,
    volumes={"/model_cache": model_cache_vol, "/checkpoints": checkpoint_vol},
    secrets=_notification_secrets,
)
def train_opd(max_steps: int = -1):
    import os
    os.environ["UNSLOTH_RETURN_LOGITS"] = "1"
    import safetensors.torch
    from peft import set_peft_model_state_dict
    from transformers import TrainingArguments
    from render import patch_qwen_chat_template

    print(f"Loading {MODEL_ID} + r10 adapter as trainable init...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID, max_seq_length=MAX_SEQ_LEN, load_in_4bit=False, load_in_16bit=True)
    patch_qwen_chat_template(tokenizer)
    model = FastLanguageModel.get_peft_model(
        model, r=LORA_R, target_modules=LORA_TARGETS, lora_alpha=LORA_ALPHA,
        lora_dropout=0, bias="none", use_rslora=False,
        use_gradient_checkpointing=True, random_state=42)

    # Load the existing r10 LoRA weights into the adapter (continue training, no merge).
    r10_adapter = f"/checkpoints/{R10_EXPERIMENT}/adapter"
    weights = {}
    for fn in os.listdir(r10_adapter):
        if fn.endswith(".safetensors"):
            weights.update(safetensors.torch.load_file(os.path.join(r10_adapter, fn)))
    if not weights:
        raise FileNotFoundError(f"No adapter safetensors in {r10_adapter}")
    set_peft_model_state_dict(model, weights)
    print(f"  loaded {len(weights)} r10 adapter tensors")

    import datasets
    recs = _load_records(RECORDS_PATH)
    print(f"OPD records: {len(recs)}")
    ds = datasets.Dataset.from_list(recs)

    output_dir = f"/checkpoints/{OPD_EXPERIMENT}"
    args = TrainingArguments(
        output_dir=output_dir, num_train_epochs=EPOCHS, max_steps=max_steps,
        per_device_train_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03,
        optim="adamw_8bit", bf16=True, logging_steps=LOGGING_STEPS,
        save_strategy="steps", save_steps=SAVE_STEPS, save_total_limit=2,
        report_to="none", seed=42, remove_unused_columns=False,
        dataloader_num_workers=0,
    )
    OPDTrainer = _make_trainer_cls()
    trainer = OPDTrainer(model=model, args=args, train_dataset=ds, data_collator=_opd_collator)

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"trainable params: {trainable:,}")
    result = trainer.train()
    print(f"done: {result.metrics}")

    print("saving adapter + merged...")
    model.save_pretrained(f"{output_dir}/adapter")
    tokenizer.save_pretrained(f"{output_dir}/adapter")
    model.save_pretrained_merged(f"{output_dir}/merged", tokenizer, save_method="merged_16bit")
    checkpoint_vol.commit()
    print(f"saved -> {output_dir}/{{adapter,merged}}")


@app.local_entrypoint()
def main(max_steps: int = -1):
    train_opd.remote(max_steps=max_steps)
