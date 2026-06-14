#!/usr/bin/env python3
"""
mcp_game_server.py — Entry point for the Kaetram MCP server.

Remains at the project root so all external references (opencode.json,
play_qwen.py, tests, dashboard pkill patterns, ecosystem.config.js)
continue to work unchanged.

The actual implementation lives in the mcp_server/ package.
"""

from mcp_server.core import log, mcp

# Import tool modules — their @mcp.tool() decorators register everything
import mcp_server.tools  # noqa: F401

if __name__ == "__main__":
    import signal

    # Route SIGTERM through the same path as Ctrl-C (SIGINT) so a `kill -TERM`
    # unwinds the asyncio loop and runs the lifespan `finally` in core.py —
    # which calls browser.close(), tearing down Chromium's full process tree.
    # Without this, Python's default SIGTERM kills the process before that
    # cleanup runs, orphaning the headed Chromium the teardown scripts can't
    # match by name. Teardown sends SIGTERM first (then SIGKILL after a grace
    # window), so this lets the graceful path win in the common case.
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    log("[mcp] Starting Kaetram MCP server")
    mcp.run(transport="stdio")
