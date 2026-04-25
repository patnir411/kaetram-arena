"""Core MCP server setup: FastMCP instance, lifespan, browser management, logging.

This module owns the singleton ``mcp`` instance that tool modules decorate.
"""

import asyncio
import json
import os
import sys
import time as _time
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from playwright.async_api import async_playwright

# ── Logging ──────────────────────────────────────────────────────────────────

_MCP_START = _time.time()
_MCP_TOOL_COUNTS: dict[str, int] = {}
_MCP_ERROR_COUNTS: dict[str, int] = {}
_MCP_LOG_FILE = None


def _init_log_file():
    """Open a persistent log file for MCP diagnostics."""
    global _MCP_LOG_FILE
    screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
    log_path = os.path.join(screenshot_dir, "mcp_server.log")
    try:
        os.makedirs(screenshot_dir, exist_ok=True)
        _MCP_LOG_FILE = open(log_path, "a")
    except OSError:
        pass


def log(msg: str):
    elapsed = _time.time() - _MCP_START
    m, s = divmod(int(elapsed), 60)
    ts = _time.strftime("%H:%M:%S")
    line = f"[{ts} +{m:02d}:{s:02d}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _MCP_LOG_FILE:
        try:
            _MCP_LOG_FILE.write(line + "\n")
            _MCP_LOG_FILE.flush()
        except OSError:
            pass


def _debug_enabled() -> bool:
    """Enable verbose per-call tool logging when KAETRAM_DEBUG=1. Temporary
    diagnostic aid for reachability tests — keep OFF in production."""
    return os.environ.get("KAETRAM_DEBUG", "0").lower() not in ("0", "false", "", "no")


def log_tool(name: str, success: bool = True, error: str = "", args: dict | None = None):
    _MCP_TOOL_COUNTS[name] = _MCP_TOOL_COUNTS.get(name, 0) + 1
    if not success:
        _MCP_ERROR_COUNTS[name] = _MCP_ERROR_COUNTS.get(name, 0) + 1
        log(f"[tool] {name} FAILED ({_MCP_ERROR_COUNTS[name]} errors): {error[:200]}")
    elif _debug_enabled():
        # KAETRAM_DEBUG=1 → log every call with args preview
        args_str = ""
        if args:
            try:
                args_str = " " + json.dumps(args, default=str)[:200]
            except (TypeError, ValueError):
                args_str = f" {args!r}"[:200]
        log(f"[tool] {name} #{_MCP_TOOL_COUNTS[name]}{args_str}")
    elif _MCP_TOOL_COUNTS[name] <= 3 or _MCP_TOOL_COUNTS[name] % 25 == 0:
        # Default: log first 3 calls of each tool + every 25th as heartbeat
        log(f"[tool] {name} #{_MCP_TOOL_COUNTS[name]}")
    # Periodic stats dump every 50 total calls
    total = sum(_MCP_TOOL_COUNTS.values())
    if total % 50 == 0:
        log_stats()


def log_tool_result(name: str, result: str | dict | None, *, max_preview: int = 300):
    """Log a tool's return payload when KAETRAM_DEBUG=1. Preview-truncates
    long payloads."""
    if not _debug_enabled() or result is None:
        return
    if isinstance(result, str):
        preview = result.replace("\n", " ")[:max_preview]
    else:
        try:
            preview = json.dumps(result, default=str)[:max_preview]
        except (TypeError, ValueError):
            preview = str(result)[:max_preview]
    log(f"[tool] {name} -> {preview}")


def log_stats():
    total = sum(_MCP_TOOL_COUNTS.values())
    errors = sum(_MCP_ERROR_COUNTS.values())
    top5 = sorted(_MCP_TOOL_COUNTS.items(), key=lambda x: -x[1])[:5]
    top5_str = ", ".join(f"{k}={v}" for k, v in top5)
    err_str = ""
    if _MCP_ERROR_COUNTS:
        err_detail = ", ".join(f"{k}={v}" for k, v in sorted(_MCP_ERROR_COUNTS.items(), key=lambda x: -x[1]))
        err_str = f" | errors: {err_detail}"
    log(f"[stats] {total} total calls, {errors} errors | top: {top5_str}{err_str}")


# Initialize log file on import
_init_log_file()


# ── Browser lifespan (lazy — yields immediately, launches browser on first use) ─

@asynccontextmanager
async def game_lifespan(server: FastMCP):
    """Yield immediately so MCP handshake completes fast. Browser launches lazily."""
    state = {
        "page": None, "browser": None, "pw": None,
        "logged_in": False, "_lock": asyncio.Lock(),
        "_heartbeat_tasks": [],
    }
    log("[mcp] Server ready (browser will launch on first tool call)")
    try:
        yield state
    finally:
        log_stats()
        # Cancel heartbeat tasks first so they stop poking the (about-to-die) page.
        tasks = state.get("_heartbeat_tasks") or []
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if state["browser"]:
            log("[mcp] Shutting down browser")
            await state["browser"].close()
        if state["pw"]:
            await state["pw"].stop()
        log("[mcp] Server shutdown complete")


async def _ensure_browser(state: dict):
    """Launch browser if not yet started. Thread-safe via asyncio.Lock."""
    if state["page"] is not None:
        return state["page"]

    async with state["_lock"]:
        # Double-check after acquiring lock
        if state["page"] is not None:
            return state["page"]

        log("[mcp] Launching browser...")
        pw = await async_playwright().start()
        headed = os.environ.get("KAETRAM_HEADED", "").lower() in ("1", "true", "yes")
        # Frame the Xvfb capture on the game canvas, not on Chrome's chrome.
        # The strategy is to make Chrome fill the full Xvfb display (1280x810),
        # then have ffmpeg crop the top 90px of browser chrome back out when
        # building the HLS stream. Net visible frame = 1280x720 of pure game.
        # - (0,0) position + 1280x810 size → no off-screen overflow, no black padding.
        # - --disable-infobars + --hide-scrollbars kills the in-page UI noise.
        chrome_args = [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1280,810",
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",
            "--disable-infobars",
            "--hide-scrollbars",
        ]
        # Pass DISPLAY through so headed Chromium can attach to the per-agent
        # Xvfb display when orchestrate.py sets DISPLAY=:99+N. In pure
        # headless mode DISPLAY is ignored.
        launch_env = {**os.environ}
        if headed and "DISPLAY" not in launch_env:
            launch_env["DISPLAY"] = ":0"
        browser = await pw.chromium.launch(
            headless=not headed,
            args=chrome_args,
            env=launch_env,
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})

        # Inject state_extractor.js (survives page reloads/navigation)
        extractor_path = os.environ.get("KAETRAM_EXTRACTOR", "state_extractor.js")
        if os.path.exists(extractor_path):
            await context.add_init_script(path=extractor_path)
            log(f"[mcp] Injected {extractor_path}")

        # WebSocket port override for multi-agent isolation
        port = os.environ.get("KAETRAM_PORT", "")
        if port:
            await context.add_init_script(f"""(() => {{
                const PORT = '{port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    // Rewrite whatever port the client emits (today always
                    // :9001 from .env.defaults; defensive against future
                    // Kaetram default-port changes). Hostname is preserved so
                    // dual-VM setups (game server on one host, agent browser
                    // on another) connect correctly.
                    url = url.replace(/:\\d+(?=\\/|$)/, ':' + PORT);
                    return protocols ? new _WS(url, protocols) : new _WS(url);
                }};
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            }})()""")
            log(f"[mcp] WebSocket port override: {port}")

        page = await context.new_page()

        async def on_console(msg):
            if "[debug_test]" in msg.text or "[debug_npc]" in msg.text:
                log(f"[browser] {msg.text}")

        page.on("console", on_console)

        # Log page crashes and WebSocket closures
        page.on("crash", lambda: log("[mcp] PAGE CRASHED — browser tab died"))
        page.on("close", lambda: log("[mcp] PAGE CLOSED — browser tab was closed"))

        state["page"] = page
        state["browser"] = browser
        state["pw"] = pw

        # Start the dashboard heartbeats once. They run for the lifetime of
        # the MCP server and are best-effort — never crash the agent.
        # Handles are tracked in state["_heartbeat_tasks"] so the lifespan
        # finally block can cancel them cleanly on shutdown.
        if not state.get("_heartbeats_started"):
            try:
                from mcp_server.state_heartbeat import (
                    state_heartbeat_loop, activity_heartbeat_loop,
                )
                state["_heartbeat_tasks"].extend([
                    asyncio.create_task(state_heartbeat_loop(state)),
                    asyncio.create_task(activity_heartbeat_loop(state)),
                ])
                state["_heartbeats_started"] = True
                log("[mcp] Dashboard heartbeats started")
            except Exception as e:
                log(f"[mcp] heartbeat start failed: {e}")

        log("[mcp] Browser ready")
        return page


async def _page_in_game(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => (
                    document.body &&
                    document.body.className === 'game' &&
                    typeof window.__extractGameState === 'function' &&
                    !!(window.game && window.game.player)
                )"""
            )
        )
    except Exception:
        return False


async def get_page(ctx: Context, ensure_logged_in: bool = True):
    """Get the Playwright page, launching browser if needed."""
    from mcp_server.login import login_impl

    state = ctx.request_context.lifespan_context
    page = await _ensure_browser(state)
    if not ensure_logged_in:
        return page

    if state.get("logged_in") and await _page_in_game(page):
        return page

    login_result = await login_impl(ctx, page)
    if "FAILED" in login_result.upper():
        raise RuntimeError(login_result)
    return page


# ── FastMCP instance ─────────────────────────────────────────────────────────

mcp = FastMCP("kaetram", lifespan=game_lifespan)
