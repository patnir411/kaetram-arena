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
    log("[mcp] Starting Kaetram MCP server")
    mcp.run(transport="stdio")
