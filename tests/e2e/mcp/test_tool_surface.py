"""Guarantee every model-visible tool is still exposed by the MCP server.

Locks in the curated 17-tool surface defined in tool_surface.py so an
accidental rename or deletion shows up here as a red test instead of silent
agent regression.
"""

from __future__ import annotations

import pytest

from tool_surface import MODEL_VISIBLE_TOOL_NAMES

from ..helpers.mcp_client import mcp_session


@pytest.mark.mcp
async def test_mcp_exposes_all_model_visible_tools(seeded_player):
    """Every name in MODEL_VISIBLE_TOOL_NAMES must be listed by the MCP
    server's list_tools. Missing tools break the training data surface."""
    async with mcp_session(username=seeded_player["username"]) as s:
        tools = set(await s.list_tools())
        missing = [t for t in MODEL_VISIBLE_TOOL_NAMES if t not in tools]
        assert not missing, f"MCP does not expose: {missing}"
