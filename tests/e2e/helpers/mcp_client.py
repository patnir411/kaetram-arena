"""Spawn `mcp_game_server.py` as a subprocess and expose `call_tool`.

One MCP subprocess per test. The server lazy-launches its own Playwright
browser on the first tool call, so Layer B tests should seed their player
via the REST helper first, then call any tool (usually `observe`) to
trigger browser + login.

Environment variables honored by `mcp_game_server.py`:
  KAETRAM_PORT          — game server websocket port (19101 in the isolated lane)
  KAETRAM_CLIENT_URL    — client URL (http://127.0.0.1:9000)
  KAETRAM_USERNAME      — seeded username this MCP instance should log in as
  KAETRAM_SCREENSHOT_DIR — optional; default /tmp

Usage:
    async with mcp_session(username="e2e_foo_1234") as session:
        result = await session.call_tool("observe", {})
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# Resolve relative to this file so the tests work from any box (GCP VM,
# local GPU runner, laptop, etc). `KAETRAM_ARENA_VENV_PYTHON` env overrides
# the interpreter path for cases where the venv lives elsewhere (e.g. the
# QwenPlays runner reusing its own venv).
import os as _os
_ARENA_ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER_PATH = _ARENA_ROOT / "mcp_game_server.py"
PYTHON_INTERPRETER = Path(
    _os.environ.get(
        "KAETRAM_ARENA_VENV_PYTHON",
        str(_ARENA_ROOT / ".venv" / "bin" / "python"),
    )
)


def _server_params(
    *,
    username: str,
    port: int | None = None,
    client_url: str = "http://127.0.0.1:9000",
    screenshot_dir: str = "/tmp/mcp",
    password: str = "test",
    extra_env: dict[str, str] | None = None,
) -> StdioServerParameters:
    env: dict[str, str] = {
        "KAETRAM_CLIENT_URL": client_url,
        "KAETRAM_USERNAME": username,
        "KAETRAM_PASSWORD": password,
        "KAETRAM_SCREENSHOT_DIR": screenshot_dir,
    }
    # Resolve port: explicit arg > KAETRAM_PORT env (set by conftest from
    # KAETRAM_WS_PORT). Without this fallback the MCP subprocess starts
    # with no port hint and the browser's WS URL stays at the default
    # :9001 — which is agent_0's port, not the test lane.
    resolved_port = port if port is not None else _os.environ.get("KAETRAM_PORT")
    if resolved_port is not None:
        env["KAETRAM_PORT"] = str(resolved_port)
    if extra_env:
        env.update(extra_env)
    return StdioServerParameters(
        command=str(PYTHON_INTERPRETER),
        args=[str(MCP_SERVER_PATH)],
        env=env,
    )


class McpToolResult:
    def __init__(self, raw: types.CallToolResult):
        self.raw = raw
        self.is_error: bool = bool(getattr(raw, "isError", False))
        content_blocks = list(raw.content or [])
        self.text: str = "\n".join(
            block.text
            for block in content_blocks
            if hasattr(block, "text") and block.text is not None
        )

    def __repr__(self) -> str:
        return f"McpToolResult(is_error={self.is_error}, text={self.text!r})"

    def json(self) -> dict[str, Any] | list[Any]:
        # Many tools prefix output with "tool_name: " and/or append trailing
        # sections like "\n\nASCII_MAP:...", "\n\nDIGEST:...", "\n\nSTUCK_CHECK:...".
        # Strip them so json.loads gets just the JSON payload.
        text = re.sub(r"^[a-zA-Z_][a-zA-Z0-9_]*:\s+", "", self.text, count=1)
        for marker in ("\n\nASCII_MAP:", "\n\nDIGEST:", "\n\nSTUCK_CHECK:"):
            idx = text.find(marker)
            if idx != -1:
                text = text[:idx]
        return json.loads(text)

    def observe_state(self) -> dict[str, Any]:
        state_text = self.text.split("\n\nASCII_MAP:", 1)[0]
        return json.loads(state_text)

    def observe_stuck_check(self) -> dict[str, Any]:
        marker = "\n\nSTUCK_CHECK:\n"
        if marker not in self.text:
            return {}
        return json.loads(self.text.split(marker, 1)[1])


@asynccontextmanager
async def mcp_session(
    *,
    username: str,
    port: int | None = None,
    client_url: str = "http://127.0.0.1:9000",
    screenshot_dir: str = "/tmp/mcp",
    password: str = "test",
    extra_env: dict[str, str] | None = None,
) -> AsyncIterator["McpSession"]:
    params = _server_params(
        username=username,
        port=port,
        client_url=client_url,
        screenshot_dir=screenshot_dir,
        password=password,
        extra_env=extra_env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield McpSession(session)


class McpSession:
    def __init__(self, session: ClientSession):
        self._session = session

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> McpToolResult:
        raw = await self._session.call_tool(name, arguments or {})
        return McpToolResult(raw)

    async def list_tools(self) -> list[str]:
        response = await self._session.list_tools()
        return [tool.name for tool in response.tools]
