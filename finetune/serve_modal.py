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
    })
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_MODEL_ID = "Qwen/Qwen3.5-9B"  # HF model ID (not Unsloth wrapper)
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


# ---------------------------------------------------------------------------
# Chat template fix (QwenLM/Qwen3#1831)
# ---------------------------------------------------------------------------

def _patch_qwen_chat_template(tokenizer):
    """Patch Qwen 3.5 chat template to preserve <think> in all turns."""
    template = tokenizer.chat_template
    if template is None:
        return
    old = (
        "{%- if loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    new = (
        "{%- if reasoning_content %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- elif loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    if old not in template:
        raise RuntimeError(
            "Qwen 3.5 chat template patch target not found — tokenizer revision has changed "
            "the reasoning_content stripping block. Inspect tokenizer.chat_template, update the "
            "`old` pattern, and re-verify <think> survives in multi-turn apply_chat_template output."
        )
    tokenizer.chat_template = template.replace(old, new)
    print("  Patched Qwen 3.5 chat template: <think> now preserved in all turns")


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
        _patch_qwen_chat_template(self.tokenizer)
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
            }

        @web_app.get("/v1/models")
        async def list_models():
            return {"data": [{"id": "kaetram", "object": "model"}]}

        @web_app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            import asyncio
            import json
            import re as _re
            body = await request.json()
            messages = body.get("messages", [])
            tools = body.get("tools")
            # Qwen3.5-9B thinking-general defaults per model card; caller may override.
            temperature = body.get("temperature", QWEN_THINK_TEMP)
            max_tokens = body.get("max_tokens", 512)
            top_p = body.get("top_p", QWEN_THINK_TOP_P)
            top_k = body.get("top_k", QWEN_THINK_TOP_K)
            presence_penalty = body.get("presence_penalty", QWEN_THINK_PRESENCE_PENALTY)

            prompt = self.tokenizer.apply_chat_template(
                messages,
                tools=tools,
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
