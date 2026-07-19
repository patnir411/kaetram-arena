"""
Modal serving endpoint for the non-SFT Qwen3.5-2B (the smallest-model scaffold lane).

Serves `Qwen/Qwen3.5-2B` instruct (non-SFT) — the size-ladder probe: how far down the
Qwen3.5 family the R11 scaffold still carries a competent agent. Same serving stack as
serve_modal_base.py (/v1/chat for rollouts + /v1/score for a later OPD data-build); kept
as a SEPARATE app so the 9B base (teacher) and 4B endpoints stay available.

Usage:
    modal deploy finetune/serve_modal_2b.py
    # Endpoint: https://workspace--kaetram-qwen-2b-inference-serve.modal.run/v1
"""

import modal

app = modal.App("kaetram-qwen-2b")

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)

serve_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-devel-ubuntu22.04", add_python="3.11")
    .apt_install("libnuma-dev")
    .pip_install(
        "sglang[all]>=0.5.5",
        "huggingface_hub>=0.34.2",
        "hf-transfer>=0.1.9",
    )
    .env({
        "HF_HOME": "/model_cache",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "SGLANG_DISABLE_CUDNN_CHECK": "1",
    })
    .add_local_python_source("render")
    .add_local_python_source("inference_seed")
)

BASE_MODEL_ID = "Qwen/Qwen3.5-2B"
MAX_MODEL_LEN = 32768
GPU_MEMORY_UTILIZATION = 0.85  # headroom for concurrent prefill spikes (0.92 OOMd under load)
DTYPE = "bfloat16"

# Qwen3.5 thinking-mode SAMPLING preset (per the official model card) — sampling values
# only; the generation prompt below is non-thinking (closed-empty `<think></think>`).
# Matched to serve_modal.py so base vs finetuned comparison uses identical decode config.
# Do NOT enable repetition_penalty / frequency_penalty / DRY — they hurt tool-call JSON.
QWEN_THINK_TEMP = 1.0
QWEN_THINK_TOP_P = 0.95
QWEN_THINK_TOP_K = 20
QWEN_THINK_PRESENCE_PENALTY = 1.5
QWEN_DECODE_MODE = "thinking_general"


# Chat template patch (QwenLM/Qwen3 #1831) lives in finetune/render.py — single
# source of truth shared with train_modal.py, serve_modal.py, and the
# convert_to_qwen.py truncation gate.
from render import patch_qwen_chat_template
from inference_seed import validate_inference_seed


@app.cls(
    image=serve_image,
    gpu="L4",  # light eval serving; flip to A100 for any batch /v1/score build (L4 OOMs at 16K-ctx concurrency)
    volumes={"/model_cache": model_cache_vol},
    min_containers=0,  # scale to zero when idle — matches serve_modal.py
    max_containers=1,
    scaledown_window=600,
    timeout=300,
)
@modal.concurrent(max_inputs=16)  # serve concurrent requests; SGLang batches them.
# Without this Modal serializes inputs per container, so a multi-slot scoring
# client stacks a queue that outlives its own timeouts and wedges the endpoint
# (every retry re-enqueues while the original is still being processed).
class Inference:
    @modal.enter()
    def load_model(self):
        """Load the non-SFT Qwen3.5-9B instruct model (no SFT adapter)."""
        print(f"Loading BASE model {BASE_MODEL_ID}...")
        self.loaded_model_path = BASE_MODEL_ID
        import sglang as sgl

        self.engine = sgl.Engine(
            model_path=BASE_MODEL_ID,
            tokenizer_path=BASE_MODEL_ID,
            dtype=DTYPE,
            context_length=MAX_MODEL_LEN,
            mem_fraction_static=GPU_MEMORY_UTILIZATION,
            chunked_prefill_size=8192,   # bound prefill batch tokens — 10x16K concurrent prefills OOMd the L4
            max_running_requests=8,
            trust_remote_code=True,
            disable_cuda_graph=True,
        )
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID, trust_remote_code=True)
        patch_qwen_chat_template(self.tokenizer)
        print("SGLang engine ready (BASE model).")

    @staticmethod
    def _adapt_messages_for_qwen_template(messages, tools):
        """Make OpenAI-style messages safe for Qwen3.5's chat template.

        Two adjustments needed only when tools= is in play:
        - assistant.tool_calls[*].function.arguments: parse JSON string → dict
          (Qwen template does `arguments | items`).
        - assistant.content: strip rendered `<tool_call>...</tool_call>` XML,
          since the template re-emits it from the structured tool_calls field.
        """
        import json
        import re as _re
        if not tools:
            return messages
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
                            args_obj = json.loads(args) if args.strip() else {}
                        except json.JSONDecodeError:
                            args_obj = {}
                    else:
                        args_obj = args or {}
                    fixed.append({
                        **tc,
                        "function": {**fn, "arguments": args_obj},
                    })
                new_m["tool_calls"] = fixed
                content = new_m.get("content") or ""
                if isinstance(content, str) and "<tool_call>" in content:
                    # Keep only the reasoning prefix before the XML.
                    new_m["content"] = _re.split(r"<tool_call>", content, maxsplit=1)[0].rstrip()
            out.append(new_m)
        return out

    @modal.asgi_app()
    def serve(self):
        from fastapi import FastAPI, HTTPException, Request
        import time
        import uuid

        web_app = FastAPI()

        @web_app.get("/health")
        async def health():
            return {
                "status": "ok",
                "model": BASE_MODEL_ID,
                "variant": "base",
                "loaded_model_path": getattr(self, "loaded_model_path", None),
                "decode_mode": QWEN_DECODE_MODE,
                "decode_defaults": {
                    "temperature": QWEN_THINK_TEMP,
                    "top_p": QWEN_THINK_TOP_P,
                    "top_k": QWEN_THINK_TOP_K,
                    "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
                },
                "capabilities": ["chat", "score"],
                "supports_system_prefix": True,
                "supports_seed": True,
            }

        def _apply_system_prefix(messages, system_prefix):
            """Prepend system_prefix to the first system message, separated by
            a blank line. Synthesizes a system message if none exists.

            Returns a new list — does not mutate input.
            """
            if not system_prefix:
                return messages
            if messages and messages[0].get("role") == "system":
                head = dict(messages[0])
                head["content"] = f"{system_prefix}\n\n{head.get('content', '')}"
                return [head] + list(messages[1:])
            return [{"role": "system", "content": system_prefix}] + list(messages)

        @web_app.get("/v1/models")
        async def list_models():
            return {"data": [{"id": "kaetram-base", "object": "model"}]}

        @web_app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            import json
            import re as _re
            body = await request.json()
            messages = body.get("messages", [])
            # Qwen3.5 thinking-mode sampling defaults per model card; caller may override.
            temperature = body.get("temperature", QWEN_THINK_TEMP)
            max_tokens = body.get("max_tokens", 512)
            top_p = body.get("top_p", QWEN_THINK_TOP_P)
            top_k = body.get("top_k", QWEN_THINK_TOP_K)
            presence_penalty = body.get("presence_penalty", QWEN_THINK_PRESENCE_PENALTY)
            seed = body.get("seed")
            if seed is not None:
                try:
                    seed = validate_inference_seed(seed, label="seed")
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Honor tools= from the request body so the chat template
            # injects Qwen3.5's native tool spec block. The base model was
            # never finetuned on our XML format, so without this it falls back
            # to free-text JSON (markdown-fenced) and `tool_calls` is always
            # empty. The native template reminds the model to emit
            # `<tool_call><function=NAME><parameter=...>...</parameter></function></tool_call>`,
            # which is exactly what the regex parser below already matches.
            #
            # SFT (serve_modal.py) intentionally drops tools= to preserve
            # training/serve parity — it learned the format from training,
            # not from the chat template.
            tools = body.get("tools") or None
            # Teacher-prompt prefix: prepended to the first system message
            # so the same deployed container can serve Teacher A (no prefix)
            # and Teacher B (expert hint) without a second cold start.
            system_prefix = body.get("system_prefix")
            messages = _apply_system_prefix(messages, system_prefix)
            # Adapt OpenAI-style messages to what Qwen's chat template
            # expects when tools= is set:
            #
            #   1. assistant.tool_calls[*].function.arguments is a JSON
            #      STRING in OpenAI's spec, but Qwen's template iterates
            #      it with `.items()` and crashes ("Can only get item pairs
            #      from a mapping") if it's a string. Parse to dict.
            #   2. assistant.content from a tool-calling turn already
            #      contains the rendered `<tool_call>...</tool_call>` XML.
            #      Qwen's template renders tool_calls separately, so leaving
            #      the XML inline would double-emit it. Strip the XML from
            #      content, keep only the reasoning prefix.
            messages = self._adapt_messages_for_qwen_template(messages, tools)
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=True,
            )

            sampling_params = {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "presence_penalty": presence_penalty,
                "max_new_tokens": max_tokens,
            }
            if seed is not None:
                sampling_params["sampling_seed"] = seed
            output = await self.engine.async_generate(prompt, sampling_params=sampling_params)
            generated_text = output["text"]
            prompt_tokens = output.get("meta_info", {}).get("prompt_tokens", 0)
            completion_tokens = output.get("meta_info", {}).get("completion_tokens", 0)

            # Parse tool calls (same as finetuned endpoint)
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
                    "function": {"name": fn_name, "arguments": json.dumps(args)},
                })

            if not parsed_tool_calls:
                for m in _re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", generated_text, _re.DOTALL):
                    try:
                        tc = json.loads(m.group(1))
                        parsed_tool_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("arguments", {}))},
                        })
                    except json.JSONDecodeError:
                        pass

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
                "model": body.get("model", "kaetram-base"),
                "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        @web_app.post("/v1/score")
        async def score(request: Request):
            """Teacher-forcing logprob computation for a fixed (context, target).

            Request: {"messages": [...], "system_prefix": "..." (optional)},
            OR {"context_text": "...", "full_text": "..."} with pre-rendered
            chat-template strings (raw-text path). With messages, the LAST one
            must be role=assistant — the scoring target; all earlier messages
            form the context.

            CRITICAL: this endpoint does NOT pass tools= to apply_chat_template.
            Offline scoring needs the prompt structure to match the r10
            training/inference path byte-for-byte; if we let the base template
            inject the <tools>...</tools> JSON prefix, the token boundary would
            shift and the score would not be comparable across endpoints.

            Response: per-token logprobs for the target tokens. Index 0 is None.
            """
            body = await request.json()
            context_text = body.get("context_text")
            full_text = body.get("full_text")
            if context_text is None or full_text is None:
                messages = body.get("messages", [])
                if not messages or messages[-1].get("role") != "assistant":
                    from fastapi import HTTPException
                    raise HTTPException(400, "last message must be role=assistant (the scoring target)")

                system_prefix = body.get("system_prefix")
                messages_adapted = _apply_system_prefix(messages, system_prefix)
                # tools=True here only gates the arg-coercion path (tool_calls.arguments
                # string -> dict); no tools= list is ever passed to apply_chat_template for
                # scoring, so the Qwen chat template doesn't crash on the score request.
                messages_adapted = self._adapt_messages_for_qwen_template(messages_adapted, tools=True)
                context_text = self.tokenizer.apply_chat_template(
                    messages_adapted[:-1], tokenize=False, add_generation_prompt=True,
                )
                full_text = self.tokenizer.apply_chat_template(
                    messages_adapted, tokenize=False, add_generation_prompt=False,
                )
            # Raw-text path: the caller rendered the chat template itself. The OPD
            # data build uses this so the student and teacher endpoints score
            # IDENTICAL token ids even though the Qwen3.5 repos ship different
            # chat-template revisions — the sizes share one vocab, so tokenizing
            # the same string yields the same ids on every endpoint.
            context_ids = self.tokenizer(context_text, add_special_tokens=False).input_ids
            full_ids = self.tokenizer(full_text, add_special_tokens=False).input_ids
            if len(full_ids) <= len(context_ids):
                from fastapi import HTTPException
                raise HTTPException(400, f"target empty after tokenization (full={len(full_ids)}, ctx={len(context_ids)})")
            target_ids = full_ids[len(context_ids):]

            output = await self.engine.async_generate(
                input_ids=full_ids,
                sampling_params={"max_new_tokens": 1, "temperature": 1.0, "top_k": 1},
                return_logprob=True,
                logprob_start_len=len(context_ids),
                top_logprobs_num=0,
            )
            raw = output.get("meta_info", {}).get("input_token_logprobs") or []
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
                "model": body.get("model", "kaetram-base"),
            }

        return web_app
