"""OPD trainer (Modal) — reverse-KL distillation of the 2B student toward the 4B teacher.

Round-parametrized: --init-model / --records-path / --experiment select the round
(defaults = round 2: fresh LoRA on the merged r1 checkpoint; round 1 used
--init-model unsloth/Qwen3.5-2B --records-path /checkpoints/opd_2b/round1/records.jsonl).
Always a FRESH LoRA on the policy that generated the rollouts, so training starts
exactly on-policy, on pre-tokenized records from
`scripts/opd/opd_2b_data.py` (each carries per-token `advantages`, `behavior_logprobs`,
and a per-record `step_weight`). Loss is PPO-clipped importance-sampling on the
reverse-KL advantage (the TML/tinker `importance_sampling` form), per action token:

    ratio  = exp(logp_current - behavior_logp)
    loss_t = -step_weight * min(ratio * adv, clamp(ratio, 1-EPS, 1+EPS) * adv)

The pessimistic clip bounds per-token policy movement to ~EPS probability-ratio per
round in BOTH directions. Unclipped REINFORCE (-adv * logp) is unbounded for the
majority adv<0 tokens in a fixed batch — it pays the optimizer to drive their logp
to -inf and collapses the policy (observed: loss -3.6 -> -233, model degenerated to
an 'observe' loop). Since behavior == base-2B == init, the clip is simultaneously
the literature's KL-anchor-to-base. A step-0 calibration probe measures the
serving-vs-training logp gap (zero-init LoRA => current policy == rollout policy,
so any gap is pure cross-stack numerics); a tripwire aborts if mean target logp
collapses. 1 epoch bounds within-round drift; step_weight (1.5 early-session) is
the TCOD-style guard for small-student multi-turn instability.

Memory note: a full-vocab logits forward at 16K x 248K would OOM, so we run the
transformer BODY to hidden states and apply the LM head ONLY at the unmasked (action)
positions — the only positions the loss touches.

Records staged on the checkpoint volume:
    modal volume put kaetram-model-vol \
        dataset/opd_2b/round2/records.jsonl /opd_2b/round2/records.jsonl

Run:  modal run finetune/train_opd_2b.py            # LR 5e-5 (gentle: no IS correction,
                                                    # so don't run tinker's with-IS 1e-4 point)
      modal run finetune/train_opd_2b.py --lr 1e-4  # aggressive variant (tinker's LoRA-OPD
                                                    # operating point, calibrated WITH IS)
"""
import modal

app = modal.App("kaetram-qwen-2b-opd-finetune")

# Self-contained (no cross-module import — that crash-loops in Modal containers /
# DataLoader workers). Image mirrors train_opd_modal.py so Modal reuses the cached build.
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
        "--no-build-isolation",
        # Qwen3.5 is 3:1 Gated DeltaNet (linear attention) : full attention — 18/24
        # layers on the 2B run a ~10x slower pure-torch fallback without these.
        # Triton is pinned BELOW 3.4: fla refuses its GDN backward on Hopper with
        # Triton>=3.4 (fla #640 miscompile) and its prescribed tilelang backend
        # SIGABRTs in this image (tvm::ffi double-registration); 3.3.x runs the
        # correct Triton kernel directly. fla needs triton>=3.3.
        "pip install flash-linear-attention==0.5.0",
        "pip install causal-conv1d==1.6.0 --no-build-isolation",
        "pip install triton==3.3.1",
    )
    .env({
        "HF_HOME": "/model_cache",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_DISABLE_XET": "1",
        "TRITON_CACHE_DIR": "/model_cache/.triton_cache",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    })
    .add_local_python_source("render")
    .add_local_python_source("scripts.opd.guided_opd_contract")
    .add_local_python_source("scripts.opd.guided_opd_schedule")
)

# Round-parametrized via the CLI (see main); these are the round-2 defaults.
# Round 2 inits from the merged r1 checkpoint with a fresh LoRA, so init == the
# policy that generated the round-2 rollouts (run_20260610_140358 + seeded).
OPD_EXPERIMENT = "kaetram-qwen3.5-2b-opd-r2"
RECORDS_PATH = "/checkpoints/opd_2b/round2/records.jsonl"
INIT_MODEL = "/checkpoints/kaetram-qwen3.5-2b-opd-r1/merged"

EPOCHS = 1
BATCH_SIZE = 4
GRAD_ACCUM = 8          # effective 32 -> ~170 steps over ~5.5k records
ADV_CLAMP = 3.0
CLIP_EPS = 0.3          # PPO trust region: per-token prob can move ~30% from behavior
TRIPWIRE_LOGP = -10.0   # abort if mean target logp collapses below this
LOGGING_STEPS = 5
SAVE_STEPS = 30  # Modal preempts by default and restarts the input from scratch;
                 # frequent checkpoints + resume below cap the loss at <=30 steps.

with train_image.imports():
    import unsloth  # noqa: F401 — must import first to apply patches
    import torch
    from unsloth import FastLanguageModel


def _load_records(path, backend_plan_path=""):
    import json
    if backend_plan_path:
        from scripts.opd.guided_opd_contract import load_guided_training_bundle
        load_guided_training_bundle(path, backend_plan_path)
        raise RuntimeError(
            "Guided-OPD bundle validated, but execution is blocked: this offline PPO-style "
            "trainer does not implement live mixed actor turns with reverse KL on student "
            "turns and forward KL on teacher turns"
        )
    recs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    if any(
        record.get("schema_version") == "kaetram.normalized-training-record.v1"
        or record.get("arm_id") == "guided_opd"
        for record in recs
    ):
        raise RuntimeError(
            "normalized Guided-OPD records require --backend-plan-path for fail-closed "
            "schema, provenance, role-schedule, and mixed-trajectory validation"
        )
    return recs


def _opd_collator(features):
    """Pad legacy pre-tokenized OPD records."""
    import torch
    maxlen = max(len(f["input_ids"]) for f in features)

    def pad(key, fill, dtype):
        return torch.tensor(
            [f[key] + [fill] * (maxlen - len(f[key])) for f in features], dtype=dtype)

    input_ids = pad("input_ids", 0, torch.long)
    labels = pad("labels", -100, torch.long)
    advantages = pad("advantages", 0.0, torch.float)
    behavior = pad("behavior_logprobs", 0.0, torch.float)
    step_weight = torch.tensor([f.get("step_weight", 1.0) for f in features], dtype=torch.float)
    attention_mask = torch.tensor(
        [[1] * len(f["input_ids"]) + [0] * (maxlen - len(f["input_ids"])) for f in features],
        dtype=torch.long)
    return {"input_ids": input_ids, "attention_mask": attention_mask,
            "labels": labels, "advantages": advantages, "behavior_logprobs": behavior,
            "step_weight": step_weight}


def _make_trainer_cls():
    """Build the OPD Trainer subclass inside the image (transformers available there)."""
    from transformers import Trainer

    class OPDTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            import torch
            labels = inputs["labels"]
            advantages = inputs["advantages"]
            behavior = inputs["behavior_logprobs"]
            step_weight = inputs["step_weight"]
            input_ids = inputs["input_ids"]
            attention_mask = inputs["attention_mask"]

            # Transformer BODY only (no full-vocab logits) -> hidden states.
            base = model.get_base_model() if hasattr(model, "get_base_model") else model
            body = base.model
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
            logits_act = lm_head(hid_act).float()          # [N_act, V] — only action positions
            logp_act = torch.log_softmax(logits_act, dim=-1)
            tgt = labels_s[b, t].unsqueeze(-1)             # [N_act, 1]
            cur = logp_act.gather(-1, tgt).squeeze(-1)     # [N_act]

            # PPO-clipped IS on the reverse-KL advantage. advantage =
            # -(logp_student - logp_teacher) at build time; behavior is the
            # rollout policy's logp (== base-2B == the init), so the pessimistic
            # clip is a trust region around base: each token's probability moves
            # at most ~CLIP_EPS ratio per round, in either direction. Unclipped
            # REINFORCE is unbounded for adv<0 tokens on a fixed batch — it pays
            # the optimizer to drive their logp to -inf and collapses the policy.
            adv = adv_s[b, t].clamp(-ADV_CLAMP, ADV_CLAMP)
            beh = beh_s[b, t]
            w = step_weight[b]                             # per-record TCOD weight
            ratio = torch.exp(cur - beh)
            clipped = ratio.clamp(1.0 - CLIP_EPS, 1.0 + CLIP_EPS)
            loss = -(w * torch.minimum(ratio * adv, clipped * adv)).mean()

            mean_cur = cur.mean().item()
            step = int(self.state.global_step)
            if step % LOGGING_STEPS == 0:
                clip_frac = ((ratio < 1.0 - CLIP_EPS) | (ratio > 1.0 + CLIP_EPS)).float().mean().item()
                print(f"[opd] step {step} mean_cur {mean_cur:+.3f} "
                      f"mean_ratio {ratio.mean().item():.3f} clip_frac {clip_frac:.3f}", flush=True)
            if mean_cur < TRIPWIRE_LOGP:
                raise RuntimeError(
                    f"OPD tripwire: mean target logp {mean_cur:.1f} < {TRIPWIRE_LOGP} — "
                    f"policy collapsing; aborting")

            if return_outputs:
                metrics = {"adv_mean": adv.mean().detach(), "cur_mean": cur.mean().detach()}
                return loss, metrics
            return loss

    return OPDTrainer


@app.function(
    image=train_image,
    gpu="H100",
    # 174 steps x ~168s (torch DeltaNet fallback) ~= 8.1h end-to-end; a shorter
    # timeout cancels the input mid-epoch (this, not preemption, killed the first
    # two 4h attempts at step ~85).
    timeout=12 * 3600,
    retries=modal.Retries(max_retries=1, backoff_coefficient=1.0, initial_delay=10.0),
    volumes={"/model_cache": model_cache_vol, "/checkpoints": checkpoint_vol},
)
def train_opd(max_steps: int = -1, lr: float = 5e-5,
              init_model: str = INIT_MODEL,
              records_path: str = RECORDS_PATH,
              backend_plan_path: str = "",
              experiment: str = OPD_EXPERIMENT):
    import gc
    import os
    os.environ["UNSLOTH_RETURN_LOGITS"] = "1"
    from transformers import TrainingArguments
    from render import patch_qwen_chat_template

    # Reject any schema, provenance, or rollout-trace drift before loading the
    # model or starting accelerator work.
    recs = _load_records(records_path, backend_plan_path)

    print(f"Loading {init_model} with a FRESH LoRA (init == rollout policy)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=init_model, max_seq_length=MAX_SEQ_LEN, load_in_4bit=False, load_in_16bit=True)
    patch_qwen_chat_template(tokenizer)
    model = FastLanguageModel.get_peft_model(
        model, r=LORA_R, target_modules=LORA_TARGETS, lora_alpha=LORA_ALPHA,
        lora_dropout=0, bias="none", use_rslora=False,
        use_gradient_checkpointing=True, random_state=42)

    print(f"OPD records: {len(recs)}  (lr={lr}, experiment={experiment})")
    import datasets
    ds = datasets.Dataset.from_list(recs)

    output_dir = f"/checkpoints/{experiment}"
    args = TrainingArguments(
        output_dir=output_dir, num_train_epochs=EPOCHS, max_steps=max_steps,
        per_device_train_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=lr, lr_scheduler_type="cosine", warmup_ratio=0.03,
        max_grad_norm=1.0, optim="adamw_8bit", bf16=True, logging_steps=LOGGING_STEPS,
        save_strategy="steps", save_steps=SAVE_STEPS, save_total_limit=2,
        report_to="none", seed=42, remove_unused_columns=False,
        dataloader_num_workers=0,
    )
    OPDTrainer = _make_trainer_cls()
    trainer = OPDTrainer(model=model, args=args, train_dataset=ds, data_collator=_opd_collator)

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"trainable params: {trainable:,}")

    # Step-0 calibration probe. With a zero-init LoRA the current policy IS the
    # rollout policy, so |logp_now - behavior| measures pure serving-vs-training
    # numerics (SGLang bf16 vs this forward). The PPO clip only transmits signal
    # while ratios start near 1 — a large init gap means most tokens begin
    # outside the trust region and training silently no-ops.
    probe = _opd_collator([recs[i] for i in range(min(8, len(recs)))])
    dev = next(model.parameters()).device
    probe = {k: v.to(dev) for k, v in probe.items()}
    with torch.no_grad():
        base_m = model.get_base_model() if hasattr(model, "get_base_model") else model
        h = base_m.model(input_ids=probe["input_ids"],
                         attention_mask=probe["attention_mask"]).last_hidden_state[:, :-1, :]
        lab = probe["labels"][:, 1:]
        bh = probe["behavior_logprobs"][:, 1:]
        pm = (lab != -100) & (bh != 0.0)
        pidx = pm.nonzero(as_tuple=False)
        pb, pt = pidx[:, 0], pidx[:, 1]
        plog = base_m.lm_head(h[pb, pt]).float().log_softmax(-1)
        cur0 = plog.gather(-1, lab[pb, pt].unsqueeze(-1)).squeeze(-1)
        gap = cur0 - bh[pb, pt]
        cf0 = ((gap.exp() < 1.0 - CLIP_EPS) | (gap.exp() > 1.0 + CLIP_EPS)).float().mean().item()
        print(f"[opd-probe] n={gap.numel()} median|gap|={gap.abs().median().item():.4f} "
              f"mean_gap={gap.mean().item():+.4f} init_clip_frac={cf0:.3f} "
              f"(healthy: median<0.1, clip_frac<0.3)", flush=True)
        del h, plog
        torch.cuda.empty_cache()

    # Resume from the latest checkpoint-N/ if one exists — Modal preemption
    # restarts this input from scratch, and the optimizer/scheduler state in the
    # checkpoint makes the resumed run equivalent to an uninterrupted one.
    _has_ckpt = os.path.exists(output_dir) and any(
        d.startswith("checkpoint-") for d in os.listdir(output_dir))
    if _has_ckpt:
        print(f"  Resuming from existing checkpoints in {output_dir}")
    result = trainer.train(resume_from_checkpoint=_has_ckpt)
    print(f"done: {result.metrics}")

    # Save the adapter and commit it before the merge. The merge materializes the
    # full fp16 model on the same GPU and can OOM; the trained adapter is the
    # irreplaceable artifact, so it is made durable first and the merge runs as a
    # best-effort step that cannot take the adapter down with it.
    model.save_pretrained(f"{output_dir}/adapter")
    tokenizer.save_pretrained(f"{output_dir}/adapter")
    checkpoint_vol.commit()
    print(f"Adapter committed: {output_dir}/adapter")

    del trainer
    gc.collect()
    torch.cuda.empty_cache()
    try:
        model.save_pretrained_merged(f"{output_dir}/merged", tokenizer, save_method="merged_16bit")
        checkpoint_vol.commit()
        print(f"Merged committed: {output_dir}/merged")
    except Exception as e:
        print(f"Merge failed ({type(e).__name__}: {e}); adapter is saved — recover the "
              f"merge on a clean GPU.")


@app.local_entrypoint()
def main(max_steps: int = -1, lr: float = 5e-5,
         init_model: str = INIT_MODEL,
         records_path: str = RECORDS_PATH,
         backend_plan_path: str = "",
         experiment: str = OPD_EXPERIMENT):
    train_opd.remote(max_steps=max_steps, lr=lr, init_model=init_model,
                     records_path=records_path, backend_plan_path=backend_plan_path,
                     experiment=experiment)
