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
    })
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_MODEL_ID = "Qwen/Qwen3.5-9B"  # HF model ID (not Unsloth wrapper)
SFT_EXPERIMENT = "kaetram-qwen3.5-9b-r4-multiturn"
GRPO_EXPERIMENT = "kaetram-qwen3.5-9b-grpo"
MERGED_MODEL_DIR = "/model_cache/kaetram-merged-r4"

# vLLM settings
MAX_MODEL_LEN = 32768  # A100 40GB fits 9B bf16 (18GB) + 32k KV cache (~12GB)
GPU_MEMORY_UTILIZATION = 0.92
DTYPE = "bfloat16"


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
    min_containers=1,
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

        # Check for pre-merged model (Unsloth saves merged safetensors in gguf dir)
        sft_merged = f"/checkpoints/{SFT_EXPERIMENT}/gguf"
        grpo_merged = f"/checkpoints/{GRPO_EXPERIMENT}/gguf"
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

        self.engine = sgl.Engine(
            model_path=str(merged_path),
            tokenizer_path=BASE_MODEL_ID,  # Use original Qwen tokenizer (avoids transformers 5.x compat issue)
            dtype=DTYPE,
            context_length=MAX_MODEL_LEN,
            mem_fraction_static=GPU_MEMORY_UTILIZATION,
            trust_remote_code=True,
            disable_cuda_graph=True,  # Modal containers don't have nvcc
        )
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID, trust_remote_code=True)
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
            return {"status": "ok", "model": BASE_MODEL_ID}

        @web_app.get("/v1/models")
        async def list_models():
            return {"data": [{"id": "kaetram", "object": "model"}]}

        @web_app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            import asyncio
            body = await request.json()
            messages = body.get("messages", [])
            temperature = body.get("temperature", 0.7)
            max_tokens = body.get("max_tokens", 512)
            top_p = body.get("top_p", 0.9)

            prompt = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # Use async generate to avoid event loop conflict
            output = await self.engine.async_generate(
                prompt,
                sampling_params={
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_new_tokens": max_tokens,
                },
            )
            generated_text = output["text"]
            prompt_tokens = output.get("meta_info", {}).get("prompt_tokens", 0)
            completion_tokens = output.get("meta_info", {}).get("completion_tokens", 0)

            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "kaetram"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": generated_text,
                        },
                        "finish_reason": "stop",
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
    """Quick test of the deployed endpoint."""
    inference = Inference()
    result = inference.v1_chat_completions.remote({
        "model": "kaetram",
        "messages": [
            {"role": "system", "content": "You are an AI agent playing Kaetram."},
            {"role": "user", "content": '<game_state>\n{"player_position":{"x":188,"y":157},"player_stats":{"hp":100,"max_hp":100,"level":1,"experience":0}}\n</game_state>\n\nWhat should you do?'},
        ],
        "temperature": 0.7,
        "max_tokens": 256,
    })
    print(f"Response: {result['choices'][0]['message']['content'][:200]}")
    print(f"Tokens: {result['usage']}")
