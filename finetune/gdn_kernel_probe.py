"""GDN kernel parity + speed probe for the Qwen3.5-2B train image.

Round 1 trained on the pure-torch Gated-DeltaNet fallback (~168 s/step). The
image now pins triton==3.3.1 + flash-linear-attention==0.5.0, which SHOULD give
the fast Triton GDN path — but that combination has never run, and fla #640 was
a BACKWARD miscompile, so speed alone is not enough: gradients must match the
fallback before any training run uses the fast path.

Two H100 calls on the exact training stack (unsloth FastLanguageModel, 16-bit,
same LoRA init, gradient checkpointing):
  1. block_fla=False — fla importable, fast kernels if the install is sound
  2. block_fla=True  — a meta-path hook refuses every `fla` import before
     transformers loads, forcing the proven torch fallback

Each call runs the same seeded batch: forward (per-token logprobs on fixed
targets) + backward (LoRA grad fingerprint), then times N train-shaped steps.
The entrypoint compares: forward max|Δlogp|, grad cosine per sampled module,
and s/step. Adopt the fast path only if logprobs agree to bf16 noise and grad
cosines are ~1.

Run:  modal run finetune/gdn_kernel_probe.py
"""
import modal

app = modal.App("kaetram-gdn-kernel-probe")

MODEL_ID = "unsloth/Qwen3.5-2B"
MAX_SEQ_LEN = 16384
LORA_R = 64
LORA_ALPHA = 64
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

PROBE_SEQ = 4096      # representative of the p50 record length regime
PROBE_BATCH = 2
TIMED_STEPS = 4

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)
RECORDS_PATH = "/checkpoints/opd_2b/round1/records.jsonl"  # real-text parity inputs

# Identical to train_opd_2b.py's image so the verdict transfers 1:1.
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
)


def _probe(block_fla: bool):
    import sys
    import time
    import warnings

    if block_fla:
        # Halt every `import fla` (and transitively fla.*) — the documented
        # sys.modules sentinel. Must run before unsloth/transformers import
        # anything; the fast/fallback arms are SEPARATE Modal functions so a
        # warm container can never carry an already-imported fla across arms.
        assert "fla" not in sys.modules, "fla already imported — wrong container"
        sys.modules["fla"] = None

    import unsloth  # noqa: F401 — must import first to apply patches
    import torch
    from unsloth import FastLanguageModel

    try:
        import fla  # noqa: F401
        fla_available = True
    except ImportError:
        fla_available = False
    print(f"[probe] block_fla={block_fla} fla_importable={fla_available}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model, _tok = FastLanguageModel.from_pretrained(
            model_name=MODEL_ID, max_seq_length=MAX_SEQ_LEN,
            load_in_4bit=False, load_in_16bit=True)
        model = FastLanguageModel.get_peft_model(
            model, r=LORA_R, target_modules=LORA_TARGETS, lora_alpha=LORA_ALPHA,
            lora_dropout=0, bias="none", use_rslora=False,
            use_gradient_checkpointing=True, random_state=42)
    fallback_warnings = [str(w.message) for w in caught
                         if "fallback" in str(w.message).lower()
                         or "fla" in str(w.message).lower()
                         or "slow" in str(w.message).lower()]
    for w in fallback_warnings[:5]:
        print(f"[probe] load warning: {w[:160]}")

    dev = next(model.parameters()).device
    # Real corpus tokens, not random ids: random inputs give near-uniform
    # logits where bf16 kernel-order noise looks alarmingly large; parity must
    # be judged on the low-entropy natural text the trainer actually sees.
    import json as _json
    rows = []
    with open(RECORDS_PATH) as f:
        for line in f:
            rows.append(_json.loads(line))
            if len(rows) >= PROBE_BATCH:
                break
    ids = [r["input_ids"][:PROBE_SEQ] for r in rows]
    width = max(len(x) for x in ids)
    pad = model.config.get_text_config().pad_token_id or 0
    input_ids = torch.tensor([x + [pad] * (width - len(x)) for x in ids]).to(dev)
    attention_mask = torch.tensor(
        [[1] * len(x) + [0] * (width - len(x)) for x in ids]).to(dev)

    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    body, lm_head = base.model, base.lm_head

    min_len = min(len(x) for x in ids)

    def forward_logp():
        hidden = body(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        hidden = hidden[:, :-1, :]
        tgt = input_ids[:, 1:]
        # Sampled non-pad positions only — full-vocab logits everywhere would OOM.
        pos = torch.arange(0, min_len - 1, 7, device=dev)
        h = hidden[:, pos, :]
        logp = lm_head(h).float().log_softmax(-1)
        return logp.gather(-1, tgt[:, pos].unsqueeze(-1)).squeeze(-1)  # [B, P]

    # Forward parity sample + backward fingerprint.
    model.train()
    logp = forward_logp()
    loss = -logp.mean()
    loss.backward()
    grads = {}
    for name, p in model.named_parameters():
        # lora_A grads are structurally zero at zero-init (chain rule through
        # the zero lora_B) — only lora_B carries a comparable fingerprint.
        if (p.requires_grad and p.grad is not None and "lora_B" in name
                and ("layers.0." in name or "layers.5." in name)):
            grads[name] = p.grad.detach().float().flatten()[:512].cpu().tolist()
    model.zero_grad(set_to_none=True)

    # Timed train-shaped steps (forward+backward, no optimizer).
    torch.cuda.synchronize()
    t0 = time.monotonic()
    for _ in range(TIMED_STEPS):
        l = -forward_logp().mean()
        l.backward()
        model.zero_grad(set_to_none=True)
    torch.cuda.synchronize()
    s_per_step = (time.monotonic() - t0) / TIMED_STEPS

    print(f"[probe] block_fla={block_fla}  {s_per_step:.2f}s/step "
          f"(B={PROBE_BATCH} x S={PROBE_SEQ})")
    return {
        "block_fla": block_fla,
        "fla_importable": fla_available,
        "fallback_warnings": fallback_warnings[:5],
        "s_per_step": s_per_step,
        "logp_sample": logp.detach().float().cpu().flatten().tolist(),
        "grads": grads,
        "loss": float(loss.item()),
    }


@app.function(image=train_image, gpu="H100", timeout=1800,
              volumes={"/model_cache": model_cache_vol, "/checkpoints": checkpoint_vol})
def probe_fast():
    return _probe(block_fla=False)


@app.function(image=train_image, gpu="H100", timeout=1800,
              volumes={"/model_cache": model_cache_vol, "/checkpoints": checkpoint_vol})
def probe_fallback():
    return _probe(block_fla=True)


@app.function(image=train_image, gpu="H100", timeout=1800,
              volumes={"/model_cache": model_cache_vol, "/checkpoints": checkpoint_vol})
def probe_ref_fp32():
    """fp32 torch-fallback forward — the numeric reference both bf16 arms are
    judged against. Plain transformers load (no unsloth/LoRA: forward-only)."""
    import sys
    assert "fla" not in sys.modules
    sys.modules["fla"] = None

    import json as _json
    import torch
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, torch_dtype=torch.float32, device_map="cuda")
    model.eval()

    rows = []
    with open(RECORDS_PATH) as f:
        for line in f:
            rows.append(_json.loads(line))
            if len(rows) >= PROBE_BATCH:
                break
    ids = [r["input_ids"][:PROBE_SEQ] for r in rows]
    width = max(len(x) for x in ids)
    pad = model.config.get_text_config().pad_token_id or 0
    input_ids = torch.tensor([x + [pad] * (width - len(x)) for x in ids]).cuda()
    attention_mask = torch.tensor(
        [[1] * len(x) + [0] * (width - len(x)) for x in ids]).cuda()
    min_len = min(len(x) for x in ids)

    with torch.no_grad():
        hidden = model.model(input_ids=input_ids,
                             attention_mask=attention_mask).last_hidden_state[:, :-1, :]
        tgt = input_ids[:, 1:]
        pos = torch.arange(0, min_len - 1, 7, device=input_ids.device)
        logp = model.lm_head(hidden[:, pos, :]).float().log_softmax(-1)
        logp = logp.gather(-1, tgt[:, pos].unsqueeze(-1)).squeeze(-1)
    return {"logp_sample": logp.cpu().flatten().tolist()}


@app.local_entrypoint()
def main():
    import math

    fast = probe_fast.remote()
    slow = probe_fallback.remote()
    ref = probe_ref_fp32.remote()

    lp_f, lp_s, lp_r = fast["logp_sample"], slow["logp_sample"], ref["logp_sample"]
    n = min(len(lp_f), len(lp_s), len(lp_r))
    max_dlogp = max(abs(a - b) for a, b in zip(lp_f[:n], lp_s[:n]))
    mean_dlogp = sum(abs(a - b) for a, b in zip(lp_f[:n], lp_s[:n])) / n

    def vs_ref(lp):
        d = [abs(a - r) for a, r in zip(lp[:n], lp_r[:n])]
        return sum(d) / n, max(d)

    fast_mean, fast_max = vs_ref(lp_f)
    slow_mean, slow_max = vs_ref(lp_s)

    def cosine(u, v):
        du = math.sqrt(sum(x * x for x in u)); dv = math.sqrt(sum(x * x for x in v))
        if du == 0 or dv == 0:
            return float("nan")
        return sum(a * b for a, b in zip(u, v)) / (du * dv)

    print("\n=== GDN kernel probe verdict ===")
    print(f"fla importable: fast={fast['fla_importable']} blocked={slow['fla_importable']}")
    print(f"s/step: fast={fast['s_per_step']:.2f}  fallback={slow['s_per_step']:.2f} "
          f"(speedup {slow['s_per_step'] / max(fast['s_per_step'], 1e-9):.1f}x)")
    print(f"loss: fast={fast['loss']:.5f}  fallback={slow['loss']:.5f}")
    print(f"forward parity over {n} sampled tokens: max|dlogp|={max_dlogp:.4f} "
          f"mean|dlogp|={mean_dlogp:.5f}")
    print(f"vs fp32 reference: fast mean|d|={fast_mean:.5f} max={fast_max:.4f}   "
          f"fallback mean|d|={slow_mean:.5f} max={slow_max:.4f}")
    print("  (equidistant => symmetric bf16 noise, fast path safe; "
          "fast >> fallback => kernel defect)")
    print("grad cosine by module:")
    worst = 1.0
    for name in sorted(set(fast["grads"]) & set(slow["grads"])):
        c = cosine(fast["grads"][name], slow["grads"][name])
        worst = min(worst, c)
        print(f"  {c:+.5f}  {name}")
    if fast["fallback_warnings"]:
        print(f"fast-path load warnings: {fast['fallback_warnings']}")

    speedup = slow["s_per_step"] / max(fast["s_per_step"], 1e-9)
    # Adopt when the fast path is no farther from fp32 truth than the proven
    # fallback is (1.5x slack for distribution noise), gradients agree, and
    # the speedup is real. Raw fast-vs-fallback |dlogp| is NOT the criterion —
    # both arms are bf16 approximations of the fp32 reference.
    ok = (fast["fla_importable"] and worst > 0.99 and speedup > 2.0
          and fast_mean <= 1.5 * slow_mean)
    print(f"\nVERDICT: {'ADOPT fast kernels' if ok else 'KEEP torch fallback'} "
          f"(speedup {speedup:.1f}x, fast-vs-ref {fast_mean:.5f} vs fallback-vs-ref "
          f"{slow_mean:.5f}, worst grad cos {worst:.4f})")
