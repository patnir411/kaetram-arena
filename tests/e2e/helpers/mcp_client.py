"""Minimal MCP client for tests.

Spawns `mcp_game_server.py` as a stdio subprocess (same way play_qwen.py does)
and exposes `.call_tool(name, args)` + helpers to parse results. Designed for
direct-assertion tool tests — no LLM in the loop.

Parameters are wired to match the arena helper of the same name so test code
is portable if we ever unify.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python3"
MCP_SERVER = PROJECT_DIR / "mcp_game_server.py"
STATE_EXTRACTOR = PROJECT_DIR / "state_extractor.js"


def _timing_enabled() -> bool:
    return os.environ.get("KAETRAM_TEST_TIMING", "1").lower() not in {"0", "false", "no"}


def _harness_log(message: str) -> None:
    if _timing_enabled():
        print(f"[harness] {message}", file=sys.stderr, flush=True)


async def _wait_for_tcp(host: str, port: int, *, timeout_s: float = 20.0, poll_s: float = 0.5, label: str = "endpoint") -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_err: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_err = exc
            await asyncio.sleep(poll_s)
    raise RuntimeError(f"{label} {host}:{port} not reachable within {timeout_s:.1f}s: {last_err}")


async def _wait_for_client_url(client_url: str, *, timeout_s: float = 20.0) -> None:
    parsed = urlparse(client_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"Unsupported KAETRAM_CLIENT_URL for readiness check: {client_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    await _wait_for_tcp(parsed.hostname, port, timeout_s=timeout_s, label="Game client")


async def send_chat_command_via_browser(
    *,
    username: str,
    message: str,
    password: str = "test",
    client_url: str = "http://localhost:9000",
) -> None:
    """Temporary browser session for harness-only setup actions.

    Used by tests that need to trigger an existing server command path without
    widening the MCP tool surface.
    """
    from playwright.async_api import async_playwright

    game_ws_host = os.environ.get("GAME_WS_HOST", "localhost")
    game_ws_port_raw = os.environ.get("GAME_WS_PORT", "9001")
    try:
        game_ws_port = int(game_ws_port_raw)
    except ValueError:
        game_ws_port = 9001

    await _wait_for_tcp(game_ws_host, game_ws_port, label="Game server")
    await _wait_for_client_url(client_url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        try:
            await page.goto(client_url)
            try:
                await page.wait_for_function(
                    """() => (
                        document.body?.className === 'game' ||
                        !!document.getElementById('login-name-input')
                    )""",
                    timeout=10000,
                )
            except Exception:
                pass
            await page.locator("#login-name-input").fill(username)
            await page.locator("#login-password-input").fill(password)
            await page.locator("#login").click()

            game_ready = False
            for _ in range(18):
                await page.wait_for_timeout(1000)
                result = await page.evaluate("""() => ({
                    game: !!(document.body && document.body.className === 'game'),
                    loginVisible: !!document.getElementById('load-character'),
                })""")
                if result.get("game"):
                    game_ready = True
                    break

            if not game_ready:
                raise RuntimeError(
                    f"Temporary browser session failed to log in as {username} "
                    f"before sending chat command {message!r}"
                )

            await page.wait_for_timeout(1000)
            send_result = await page.evaluate(
                """(text) => {
                    try {
                        const game = window.game;
                        if (!game || !game.socket) return { error: 'Game socket not ready' };
                        // Packets.Chat = 19 on the current tree.
                        game.socket.send(19, [text]);
                        return { sent: true, text };
                    } catch (e) {
                        return { error: String(e) };
                    }
                }""",
                message,
            )
            if isinstance(send_result, dict) and send_result.get("error"):
                raise RuntimeError(
                    f"Temporary browser session failed to send chat command {message!r}: {send_result}"
                )

            await page.wait_for_timeout(1200)
        finally:
            await context.close()
            await browser.close()


@dataclass
class ToolResult:
    """Parsed CallToolResult. Mirrors arena's helper — `.text` for raw body,
    `.json()` for the trailing JSON blob (after the leading `tool_name: ` prefix).
    """
    is_error: bool
    text: str

    def json(self) -> dict | None:
        """Parse the tool's JSON payload.

        Tool results come in one of three shapes:
          1. `tool_name: {json}`              — attack, observe, most tools
          2. `tool_name: {json}\\n\\nSTUCK_CHECK:\\n{json}` — observe
          3. `{json}`                         — some tools (errors, warp)

        The old logic split on the first `": "` and grabbed everything after,
        which broke when the JSON itself contained `": "` before the first
        brace (e.g. `{"error":"..."}`). Only strip the prefix when it looks
        like an identifier followed by `: {`.
        """
        import re
        body = self.text
        m = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*:\s+", body)
        if m:
            body = body[m.end():]
        for sep in ("\n\nASCII_MAP:", "\n\nDIGEST:", "\n\nSTUCK_CHECK:"):
            if sep in body:
                body = body.split(sep)[0]
                break
        try:
            return json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return None

    def observe_stuck_check(self) -> dict | None:
        """Parse the STUCK_CHECK trailer that observe() emits."""
        marker = "\n\nSTUCK_CHECK:\n"
        if marker not in self.text:
            return None
        try:
            return json.loads(self.text.split(marker, 1)[1])
        except (ValueError, json.JSONDecodeError):
            return None


@asynccontextmanager
async def mcp_session(
    *,
    username: str,
    password: str = "test",
    client_url: str = "http://localhost:9000",
    server_port: str = "",
    headed: bool = False,
    screenshot_dir: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    """Spawn mcp_game_server as a stdio MCP subprocess scoped to `username`.

    Yields an object with `.call_tool(name, args) -> ToolResult` and
    `.list_tools() -> list[str]`. Cleanup closes the MCP session + kills
    the browser.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    started_at = time.perf_counter()
    phase_at = started_at

    def mark(phase: str) -> None:
        nonlocal phase_at
        now = time.perf_counter()
        _harness_log(
            f"mcp_session[{username}] {phase}: +{now - phase_at:.2f}s "
            f"(total {now - started_at:.2f}s)"
        )
        phase_at = now

    game_ws_host = os.environ.get("GAME_WS_HOST", "localhost")
    game_ws_port_raw = os.environ.get("GAME_WS_PORT", "9001")
    try:
        game_ws_port = int(game_ws_port_raw)
    except ValueError:
        game_ws_port = 9001

    # Block before any test code runs until both the game socket and the web
    # client URL are reachable.
    await _wait_for_tcp(game_ws_host, game_ws_port, label="Game server")
    await _wait_for_client_url(client_url)
    mark("endpoints_ready")

    if screenshot_dir is None:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "single")
        screenshot_dir = f"/tmp/kaetram_test_screens/{worker}/{username}"

    env = {
        **os.environ,
        "KAETRAM_USERNAME": username,
        "KAETRAM_PASSWORD": password,
        "KAETRAM_CLIENT_URL": client_url,
        "KAETRAM_PORT": server_port,
        "KAETRAM_EXTRACTOR": str(STATE_EXTRACTOR),
        "KAETRAM_SCREENSHOT_DIR": screenshot_dir,
        # Respect KAETRAM_HEADED from the environment (set by server.js when
        # tests are launched from the dashboard with headed=true) unless the
        # caller explicitly requested headless.
        "KAETRAM_HEADED": "1" if headed else os.environ.get("KAETRAM_HEADED", "0"),
        **(extra_env or {}),
    }

    params = StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[str(MCP_SERVER)],
        env=env,
    )
    transport = stdio_client(params)
    read, write = await transport.__aenter__()
    session = ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120))
    await session.__aenter__()
    await session.initialize()
    mark("mcp_initialized")

    class _Handle:
        async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
            result = await session.call_tool(name, args or {})
            parts: list[str] = []
            for block in result.content or []:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return ToolResult(is_error=bool(result.isError), text="\n".join(parts))

        async def list_tools(self) -> list[str]:
            res = await session.list_tools()
            return [t.name for t in res.tools]

    handle = _Handle()

    # Warm the browser + login path and require a usable observe result before
    # yielding to the test body. This prevents tests from racing the initial
    # MCP/browser/game bootstrap and failing in the first few seconds.
    last_observe: ToolResult | None = None
    last_payload: dict | None = None
    warmup_attempts = 0
    for _ in range(30):
        warmup_attempts += 1
        last_observe = await handle.call_tool("observe", {})
        last_payload = last_observe.json()
        if (
            not last_observe.is_error
            and isinstance(last_payload, dict)
            and isinstance(last_payload.get("pos"), dict)
            and "x" in last_payload["pos"]
            and "y" in last_payload["pos"]
            and isinstance(last_payload.get("inventory"), list)
        ):
            break
        await asyncio.sleep(0.5)
    else:
        text = last_observe.text[:500] if last_observe else "no observe response"
        raise RuntimeError(
            f"MCP session did not become ready after warmup observe retries. "
            f"last_observe={text!r} payload={last_payload!r}"
        )
    mark(f"warmup_ready attempts={warmup_attempts}")

    try:
        yield handle
    finally:
        try: await session.__aexit__(None, None, None)
        except Exception: pass
        try: await transport.__aexit__(None, None, None)
        except Exception: pass
