#!/usr/bin/env python3
"""nim_proxy.py — SSE-rewriting proxy between opencode and NVIDIA NIM.

Two transformations on the way through:

1. Request body: flatten any nested ``extraBody`` (or ``extra_body``) into the
   top-level body. Works around opencode bug #5674 where the openai-compatible
   provider serializes ``extraBody`` as a literal nested key instead of inlining
   its contents — so ``chat_template_kwargs.enable_thinking`` actually reaches NIM.

2. SSE response: NIM streams Qwen thinking models' chain-of-thought via
   ``delta.reasoning_content`` (and a ``delta.reasoning`` mirror), NOT
   ``delta.content``. opencode's adapter only reads ``delta.content``, so
   reasoning tokens are silently dropped. We rewrite each SSE chunk so
   ``reasoning_content`` is copied into ``content`` when ``content`` is empty —
   the model's final-answer ``content`` deltas (which arrive after thinking
   ends) pass through untouched.

Listens on 127.0.0.1:8889 by default. Point opencode's ``baseURL`` at
``http://127.0.0.1:8889/v1`` instead of NIM directly.

Run:
    ./scripts/start-nim-proxy.sh        # daemonize, log to /tmp/nim_proxy.log
    python3 scripts/nim_proxy.py        # foreground for debugging
"""

import json
import logging
import os
import sys

from aiohttp import web, ClientSession, ClientTimeout

NIM_URL = os.environ.get("NIM_PROXY_UPSTREAM", "https://integrate.api.nvidia.com")
HOST = os.environ.get("NIM_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("NIM_PROXY_PORT", "8889"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("nim_proxy")


def _flatten_extra_body(body: dict) -> dict:
    """Pull `extraBody` keys into the top of the request body."""
    for key in ("extraBody", "extra_body"):
        extra = body.pop(key, None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                body[k] = v
    return body


def _rewrite_sse_line(line: bytes, stream_state: dict) -> bytes:
    """Map ``delta.reasoning_content`` → ``delta.content`` and wrap the
    reasoning span in ``<think>...</think>`` so downstream extractors
    (extract_turns.py, Qwen3 chat-template) can split CoT from action.

    State machine (per stream, keyed by ``stream_state`` dict):
      - First reasoning chunk → prepend ``<think>`` to delta.content; set in_think.
      - Content arrives while in_think → prepend ``</think>`` to that content;
        clear in_think.
      - On ``[DONE]`` with in_think still set → caller emits a final
        ``</think>`` frame (see ``_finalize_think``).

    Only rewrites when ``content`` would otherwise be empty for that delta —
    the model's final-answer ``content`` deltas pass through untouched (just
    prefixed with ``</think>`` on the first one if we were still thinking).
    """
    if not line.startswith(b"data: "):
        return line
    payload = line[6:].strip()
    if not payload:
        return line
    if payload == b"[DONE]":
        return line
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return line
    rewritten = False
    for c in obj.get("choices") or []:
        delta = c.get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        existing_content = delta.get("content") or ""
        if reasoning and not existing_content:
            # Reasoning delta. Open <think> exactly once per stream.
            prefix = "<think>" if not stream_state.get("in_think") else ""
            delta["content"] = prefix + reasoning
            stream_state["in_think"] = True
            rewritten = True
        elif existing_content and stream_state.get("in_think"):
            # First post-reasoning content delta — close </think> ahead of it.
            delta["content"] = "</think>" + existing_content
            stream_state["in_think"] = False
            rewritten = True
    if not rewritten:
        return line
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n"


def _finalize_think(stream_state: dict) -> bytes:
    """Emit a synthetic SSE frame closing an open ``<think>`` if the stream
    ended without surfacing any post-reasoning content. Returns b"" if the
    state machine is already closed.
    """
    if not stream_state.get("in_think"):
        return b""
    stream_state["in_think"] = False
    frame = {"choices": [{"index": 0, "delta": {"content": "</think>"}}]}
    return b"data: " + json.dumps(frame, separators=(",", ":")).encode() + b"\n\n"


async def proxy(request: web.Request) -> web.StreamResponse:
    """Catch-all forwarder."""
    path = request.match_info.get("path", "")
    target = f"{NIM_URL.rstrip('/')}/{path}"
    if request.query_string:
        target += f"?{request.query_string}"

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() in ("authorization", "content-type", "accept")
    }
    body_bytes = await request.read()

    # Mutate chat-completions bodies to flatten extraBody.
    if path.endswith("chat/completions") and body_bytes:
        try:
            body = json.loads(body_bytes)
            body = _flatten_extra_body(body)
            body_bytes = json.dumps(body, separators=(",", ":")).encode()
            fwd_headers["Content-Length"] = str(len(body_bytes))
        except (ValueError, TypeError):
            pass

    session: ClientSession = request.app["session"]
    async with session.request(
        request.method, target, headers=fwd_headers, data=body_bytes,
        allow_redirects=False,
    ) as upstream:
        content_type = upstream.headers.get("Content-Type", "")
        is_sse = "text/event-stream" in content_type.lower()

        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in (
                "content-encoding", "content-length", "transfer-encoding",
            )
        }
        resp = web.StreamResponse(status=upstream.status, headers=resp_headers)
        await resp.prepare(request)

        if is_sse:
            stream_state: dict = {"in_think": False}
            buf = b""
            async for chunk in upstream.content.iter_any():
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    out = _rewrite_sse_line(line, stream_state)
                    await resp.write(out + b"\n")
            if buf:
                await resp.write(_rewrite_sse_line(buf, stream_state))
            # If the stream ended mid-thinking (no final-answer content),
            # close the </think> tag so the consumer doesn't see an
            # unclosed CoT block.
            tail = _finalize_think(stream_state)
            if tail:
                await resp.write(tail)
        else:
            data = await upstream.read()
            # Non-streaming chat-completions: copy reasoning_content into
            # content if content is empty, so non-streaming consumers also
            # see the thinking — wrapped in <think>...</think>.
            if path.endswith("chat/completions"):
                try:
                    obj = json.loads(data)
                    for c in obj.get("choices", []):
                        msg = c.get("message", {})
                        rc = msg.get("reasoning_content") or msg.get("reasoning")
                        existing = msg.get("content") or ""
                        if rc and not existing:
                            msg["content"] = f"<think>{rc}</think>"
                        elif rc and existing:
                            msg["content"] = f"<think>{rc}</think>{existing}"
                    data = json.dumps(obj, separators=(",", ":")).encode()
                except (ValueError, TypeError):
                    pass
            await resp.write(data)

        await resp.write_eof()
        return resp


async def _session_ctx(app: web.Application):
    """Create one ClientSession at startup; close it on shutdown.

    A single shared session preserves the aiohttp connector pool across
    requests, which matters under concurrent OpenCode agents — the previous
    per-request ``async with ClientSession(...)`` defeated TLS reuse and
    risked socket exhaustion.
    """
    timeout = ClientTimeout(total=600, sock_connect=15, sock_read=600)
    app["session"] = ClientSession(timeout=timeout)
    try:
        yield
    finally:
        await app["session"].close()


def main():
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.cleanup_ctx.append(_session_ctx)
    app.router.add_route("*", "/{path:.+}", proxy)
    log.info("NIM proxy listening on http://%s:%d → %s", HOST, PORT, NIM_URL)
    try:
        web.run_app(app, host=HOST, port=PORT, access_log=None,
                    print=lambda *a, **kw: None)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
