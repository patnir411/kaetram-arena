"""
Modal vLLM serving endpoint for finetuned Kaetram Qwen3.5-9B.

Serves the SFT-finetuned model as an OpenAI-compatible /v1/chat/completions API.
Merges LoRA adapter into base model on container startup, then runs vLLM for
fast inference.

Usage:
    # Deploy (starts a persistent endpoint with 1 warm container)
    modal deploy finetune/serve_modal.py

    # Stop when done (saves money — $0 while stopped)
    modal app stop kaetram-qwen-serve

    # Test the endpoint
    curl -X POST https://<your-modal-url>/v1/chat/completions \\
      -H "Content-Type: application/json" \\
      -d '{"model":"kaetram","messages":[{"role":"user","content":"test"}]}'

    # Or use with openai Python client:
    from openai import OpenAI
    client = OpenAI(base_url="https://<your-modal-url>/v1", api_key="not-needed")
"""

import os

import modal

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

app = modal.App("kaetram-qwen-serve")

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

# Image with vLLM + model merging deps
serve_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-devel-ubuntu22.04", add_python="3.11")
    .apt_install("libnuma-dev")
    .pip_install(
        "sglang[all]>=0.5.5",
        "peft>=0.16.0",
        "huggingface_hub>=0.34.2",
        "hf-transfer>=0.1.9",
    )
    .env({
        "HF_HOME": "/model_cache",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "SGLANG_DISABLE_CUDNN_CHECK": "1",
        # Bake the deploy-time SFT_EXPERIMENT into the image so the container (which
        # re-imports this module) sees it — local env alone doesn't reach the container.
        "SFT_EXPERIMENT": os.environ.get("SFT_EXPERIMENT", "kaetram-qwen3.5-9b-r10"),
    })
    .add_local_python_source("render")
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_MODEL_ID = "Qwen/Qwen3.5-9B"  # canonical Qwen repo, NOT unsloth/
# Why canonical: SGLang's transformers version can't read Unsloth's
# tokenizer_config.json (TokenizersBackend class — written by tx 5.x, not
# in SGLang's tx 4.x). The merged checkpoint's local tokenizer_config IS
# patched in-place (lines 163-172 below) for SGLang's engine load.
# Train/serve template parity: patch_qwen_chat_template (applied below)
# normalizes the chat_template `last_query_index` block identically for
# both unsloth/ and Qwen/ repos. Any non-patched template fragment delta
# between the two repos is bounded and tested via the chat-template
# byte-level tests in tests/unit/test_chat_template_byte_level.py.
# Override via env: SFT_EXPERIMENT=kaetram-qwen3.5-9b-r11 modal deploy finetune/serve_modal.py
SFT_EXPERIMENT = os.environ.get("SFT_EXPERIMENT", "kaetram-qwen3.5-9b-r10")
GRPO_EXPERIMENT = "kaetram-qwen3.5-9b-grpo"
_RUN_TAG = SFT_EXPERIMENT.rsplit("-", 1)[-1]  # "r10" from "kaetram-qwen3.5-9b-r10"
MERGED_MODEL_DIR = f"/model_cache/kaetram-merged-{_RUN_TAG}"

# vLLM settings
MAX_MODEL_LEN = 32768  # A100 40GB fits 9B bf16 (18GB) + 32k KV cache (~12GB)
GPU_MEMORY_UTILIZATION = 0.92
DTYPE = "bfloat16"

# Qwen3.5-9B thinking mode general defaults (per official model card)
# We serve in thinking mode because training data has <think> blocks on every turn.
# Do NOT enable repetition_penalty / frequency_penalty / DRY — they hurt tool-call JSON.
QWEN_THINK_TEMP = 1.0
QWEN_THINK_TOP_P = 0.95
QWEN_THINK_TOP_K = 20
QWEN_THINK_PRESENCE_PENALTY = 1.5
QWEN_DECODE_MODE = "thinking_general"


# Chat template patch (QwenLM/Qwen3 #1831) lives in finetune/render.py — single
# source of truth shared with train_modal.py, serve_modal_base.py, and the
# convert_to_qwen.py truncation gate.
from render import patch_qwen_chat_template


# ---------------------------------------------------------------------------
# Inference class
# ---------------------------------------------------------------------------

@app.cls(
    image=serve_image,
    gpu="A100",  # 40GB — fits 9B bf16 comfortably
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
    min_containers=0,  # scale to zero when idle — $0/hr vs ~$1500/month always-on
    max_containers=1,
    scaledown_window=600,  # 10 min idle before scale down
    timeout=300,  # 5 min per request max
)
class Inference:
    @modal.enter()
    def load_model(self):
        """Load the finetuned model and start vLLM engine."""
        import os
        import torch
        from pathlib import Path

        # Check for pre-merged model (Unsloth saves merged safetensors)
        sft_merged = f"/checkpoints/{SFT_EXPERIMENT}/merged"
        grpo_merged = f"/checkpoints/{GRPO_EXPERIMENT}/merged"
        sft_adapter = f"/checkpoints/{SFT_EXPERIMENT}/adapter"
        grpo_adapter = f"/checkpoints/{GRPO_EXPERIMENT}/adapter"

        merged_path = Path(MERGED_MODEL_DIR)

        # Priority: cached merge > GRPO merged > SFT merged > adapter merge > base model
        if merged_path.exists() and (merged_path / "config.json").exists():
            print(f"Using cached merged model at {merged_path}")
        elif os.path.exists(grpo_merged) and os.path.exists(os.path.join(grpo_merged, "config.json")):
            merged_path = Path(grpo_merged)
            print(f"Using GRPO merged model: {merged_path}")
        elif os.path.exists(sft_merged) and os.path.exists(os.path.join(sft_merged, "config.json")):
            merged_path = Path(sft_merged)
            print(f"Using SFT merged model: {merged_path}")
        elif os.path.exists(grpo_adapter) or os.path.exists(sft_adapter):
            # Fall back to merging adapter on startup
            adapter_path = grpo_adapter if os.path.exists(grpo_adapter) else sft_adapter
            print(f"Merging adapter {adapter_path} into base model...")
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID)
            model = AutoModelForCausalLM.from_pretrained(
                BASE_MODEL_ID,
                torch_dtype=torch.bfloat16,
                device_map="cpu",
            )
            model = PeftModel.from_pretrained(model, adapter_path)
            model = model.merge_and_unload()

            merged_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(merged_path)
            tokenizer.save_pretrained(merged_path)
            model_cache_vol.commit()
            print(f"Merged model saved to {merged_path}")
            del model
            torch.cuda.empty_cache()
        else:
            merged_path = Path(BASE_MODEL_ID)
            print(f"WARNING: No finetuned model found, using base {BASE_MODEL_ID}")
        self.loaded_model_path = str(merged_path)

        # Patch tokenizer_config.json if saved by transformers 5.x
        # (SGLang uses transformers 4.x which doesn't have TokenizersBackend)
        tok_config_path = merged_path / "tokenizer_config.json"
        if tok_config_path.exists():
            import json as _json
            tc = _json.loads(tok_config_path.read_text())
            if tc.get("tokenizer_class") == "TokenizersBackend":
                tc["tokenizer_class"] = "PreTrainedTokenizerFast"
                tok_config_path.write_text(_json.dumps(tc, indent=2))
                print("Patched tokenizer_class: TokenizersBackend → PreTrainedTokenizerFast")

        # Start SGLang engine (supports Qwen3.5 natively, unlike vLLM < 0.19)
        print(f"Starting SGLang engine (model={merged_path})...")
        import sglang as sgl

        # SGLang tokenizer_path is encoding-only (we apply the chat template
        # ourselves below via self.tokenizer); vocab/BPE merges are identical
        # between Qwen/ and unsloth/ Qwen3.5-9B repos. Pin to canonical Qwen/
        # so SGLang (transformers 4.x) doesn't trip on the TokenizersBackend
        # tokenizer_class that transformers 5.x writes.
        self.engine = sgl.Engine(
            model_path=str(merged_path),
            tokenizer_path="Qwen/Qwen3.5-9B",
            dtype=DTYPE,
            context_length=MAX_MODEL_LEN,
            mem_fraction_static=GPU_MEMORY_UTILIZATION,
            trust_remote_code=True,
            disable_cuda_graph=True,  # Modal containers don't have nvcc
        )
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID, trust_remote_code=True)
        patch_qwen_chat_template(self.tokenizer)
        print("SGLang engine ready.")

    @modal.asgi_app()
    def serve(self):
        """OpenAI-compatible API with proper /v1/chat/completions routing."""
        from fastapi import FastAPI, Request
        import time
        import uuid

        web_app = FastAPI()

        @web_app.get("/health")
        async def health():
            return {
                "status": "ok",
                "model": BASE_MODEL_ID,
                "variant": "finetuned",
                "sft_experiment": SFT_EXPERIMENT,
                "loaded_model_path": getattr(self, "loaded_model_path", None),
                "decode_mode": QWEN_DECODE_MODE,
                "decode_defaults": {
                    "temperature": QWEN_THINK_TEMP,
                    "top_p": QWEN_THINK_TOP_P,
                    "top_k": QWEN_THINK_TOP_K,
                    "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
                },
                "capabilities": ["chat", "score"],
            }

        @web_app.get("/v1/models")
        async def list_models():
            return {"data": [{"id": "kaetram", "object": "model"}]}

        def _adapt_for_template(messages):
            """Coerce assistant tool_calls.arguments from JSON strings to dicts
            so the Qwen chat template's `.items()` call doesn't crash. Applied
            unconditionally on /v1/score: reconstructed messages from session
            logs always carry tool_calls with string-encoded arguments."""
            import json as _json
            import re as _re_local
            out = []
            for m in messages:
                if m.get("role") != "assistant":
                    out.append(m)
                    continue
                new_m = dict(m)
                tcs = new_m.get("tool_calls") or []
                if tcs:
                    fixed = []
                    for tc in tcs:
                        fn = (tc.get("function") or {})
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args_obj = _json.loads(args) if args.strip() else {}
                            except _json.JSONDecodeError:
                                args_obj = {}
                        else:
                            args_obj = args or {}
                        fixed.append({**tc, "function": {**fn, "arguments": args_obj}})
                    new_m["tool_calls"] = fixed
                    content = new_m.get("content") or ""
                    if isinstance(content, str) and "<tool_call>" in content:
                        new_m["content"] = _re_local.split(r"<tool_call>", content, maxsplit=1)[0].rstrip()
                out.append(new_m)
            return out

        @web_app.post("/v1/score")
        async def score(request: Request):
            """Teacher-forcing logprob computation for a fixed (context, target).

            Request: {"messages": [...]}; the LAST message must be role=assistant
            and is the scoring target. All earlier messages form the context.

            Response: per-token logprobs for the target tokens. Index 0 is None
            (SGLang's standard convention — no preceding token to condition on at
            logprob_start_len). Caller drops index 0 and sums/means the rest.
            """
            body = await request.json()
            messages = body.get("messages", [])
            if not messages or messages[-1].get("role") != "assistant":
                from fastapi import HTTPException
                raise HTTPException(400, "last message must be role=assistant (the scoring target)")

            # Same template path as /v1/chat/completions: no tools= (SFT
            # training/serve parity — system prompt embeds the markdown tool
            # table). Pass through _adapt_for_template so tool_calls.arguments
            # are dict-shaped (Qwen template requires `.items()`).
            messages_adapted = _adapt_for_template(messages)
            context_text = self.tokenizer.apply_chat_template(
                messages_adapted[:-1], tokenize=False, add_generation_prompt=True,
            )
            context_ids = self.tokenizer(context_text, add_special_tokens=False).input_ids
            full_text = self.tokenizer.apply_chat_template(
                messages_adapted, tokenize=False, add_generation_prompt=False,
            )
            full_ids = self.tokenizer(full_text, add_special_tokens=False).input_ids
            if len(full_ids) <= len(context_ids):
                from fastapi import HTTPException
                raise HTTPException(400, f"target empty after tokenization (full={len(full_ids)}, ctx={len(context_ids)})")
            target_ids = full_ids[len(context_ids):]

            # One dummy generation step (cheapest scoring mode). top_k=1 + temp=1
            # picks greedy; we only need the prompt-side logprobs.
            output = await self.engine.async_generate(
                input_ids=full_ids,
                sampling_params={"max_new_tokens": 1, "temperature": 1.0, "top_k": 1},
                return_logprob=True,
                logprob_start_len=len(context_ids),
                top_logprobs_num=0,
            )
            raw = output.get("meta_info", {}).get("input_token_logprobs") or []
            # SGLang shape: list of (logp, token_id, token_str|None)
            logprobs = [None if (t is None or t[0] is None) else float(t[0]) for t in raw]
            # The per-token logprobs must line up 1:1 with target_ids — callers
            # index them together. A mismatch means SGLang returned a different
            # input_token_logprobs span than logprob_start_len implies (e.g. a
            # tokenization re-split); fail loudly rather than silently return a
            # misaligned score.
            if len(logprobs) != len(target_ids):
                from fastapi import HTTPException
                raise HTTPException(
                    500,
                    f"logprob/target length mismatch (logprobs={len(logprobs)}, "
                    f"target={len(target_ids)})",
                )
            return {
                "target_token_ids": target_ids,
                "target_token_strs": [self.tokenizer.decode([t]) for t in target_ids],
                "target_logprobs": logprobs,
                "n_context_tokens": len(context_ids),
                "n_target_tokens": len(target_ids),
                "model": body.get("model", "kaetram"),
            }

        @web_app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            import asyncio
            import json
            import re as _re
            body = await request.json()
            messages = body.get("messages", [])
            # Qwen3.5-9B thinking-general defaults per model card; caller may override.
            temperature = body.get("temperature", QWEN_THINK_TEMP)
            max_tokens = body.get("max_tokens", 512)
            top_p = body.get("top_p", QWEN_THINK_TOP_P)
            top_k = body.get("top_k", QWEN_THINK_TOP_K)
            presence_penalty = body.get("presence_penalty", QWEN_THINK_PRESENCE_PENALTY)

            # No tools= kwarg — train and serve both embed the tool table in
            # the system prompt (markdown). Passing tools= here would emit a
            # second JSON-schema tool block the model was never trained on.
            # Source of truth: finetune/render.render_record.
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

            # Use async generate to avoid event loop conflict
            output = await self.engine.async_generate(
                prompt,
                sampling_params={
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "presence_penalty": presence_penalty,
                    "max_new_tokens": max_tokens,
                },
            )
            generated_text = output["text"]
            prompt_tokens = output.get("meta_info", {}).get("prompt_tokens", 0)
            completion_tokens = output.get("meta_info", {}).get("completion_tokens", 0)

            # Try to parse Qwen3.5 Coder XML tool calls from generated text
            # Format: <tool_call><function=name><parameter=key>val</parameter></function></tool_call>
            parsed_tool_calls = []
            for m in _re.finditer(
                r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
                generated_text, _re.DOTALL
            ):
                fn_name = m.group(1)
                params_text = m.group(2)
                args = {}
                for pm in _re.finditer(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", params_text, _re.DOTALL):
                    args[pm.group(1)] = pm.group(2).strip()
                parsed_tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": json.dumps(args),
                    },
                })

            # Also try JSON-in-tool_call format as fallback
            if not parsed_tool_calls:
                for m in _re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", generated_text, _re.DOTALL):
                    try:
                        tc = json.loads(m.group(1))
                        parsed_tool_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": json.dumps(tc.get("arguments", {})),
                            },
                        })
                    except json.JSONDecodeError:
                        pass

            # Build response message
            msg = {"role": "assistant", "content": generated_text}
            if parsed_tool_calls:
                msg["tool_calls"] = parsed_tool_calls
                finish_reason = "tool_calls"
            else:
                finish_reason = "stop"

            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "kaetram"),
                "choices": [
                    {
                        "index": 0,
                        "message": msg,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        return web_app


# ---------------------------------------------------------------------------
# Local test
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main():
    """Quick test of the deployed endpoint using the canonical bootstrap."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bootstrap import build_orchestrate_bootstrap

    inference = Inference()
    result = inference.v1_chat_completions.remote({
        "model": "kaetram",
        "messages": [
            {"role": "system", "content": "You are an AI agent playing Kaetram."},
            {"role": "user", "content": build_orchestrate_bootstrap("completionist", 1)},
        ],
        "temperature": 0.7,
        "max_tokens": 256,
    })
    print(f"Response: {result['choices'][0]['message']['content'][:200]}")
    print(f"Tokens: {result['usage']}")
