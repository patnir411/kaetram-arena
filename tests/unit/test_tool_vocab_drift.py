from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from convert_to_qwen import TOOL_DEFINITIONS  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_NAMES  # noqa: E402

SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.md"
# mcp_game_server.py is now a 19-line stub; @mcp.tool() decorators live inside
# the modular package mcp_server/tools/*.py — scan all files there.
MCP_TOOLS_DIR = REPO_ROOT / "mcp_server" / "tools"


def _system_prompt_tools_block() -> str:
    text = SYSTEM_PROMPT.read_text()
    return text.split("<tools>", 1)[1].split("</tools>", 1)[0]


def _exported_mcp_tool_names() -> tuple[str, ...]:
    """Collect every @mcp.tool()-decorated async function across mcp_server/tools/.

    Skips `test_lane.py` — its tools (`__test_login`, `__test_close_session`)
    are conditionally registered only when `KAETRAM_TEST_LANE=1` is set in
    the MCP subprocess environment, so they never reach the model-visible
    surface in production agent runs.
    """
    names: list[str] = []
    for path in sorted(MCP_TOOLS_DIR.glob("*.py")):
        if path.name in ("__init__.py", "test_lane.py"):
            continue
        text = path.read_text()
        names.extend(
            re.findall(r"@mcp\.tool\(\)\s+async def ([a-zA-Z_][a-zA-Z0-9_]*)\(", text)
        )
    return tuple(names)


def test_system_prompt_matches_curated_model_visible_surface():
    # Post-shrink (r11 teacher prompt) the <tools> block is compact prose
    # notes, not an exhaustive ordered markdown table — the full call schemas
    # reach the model via the native tools= spec. Invariant kept: every curated
    # model-visible tool is documented (as `name`) in the <tools> block, so a
    # removed/renamed tool still trips this test. Exact surface parity (no
    # extras, right order) is enforced against the live MCP server and the
    # convert_to_qwen metadata by the other tests in this file.
    block = _system_prompt_tools_block()
    missing = [name for name in MODEL_VISIBLE_TOOL_NAMES if f"`{name}" not in block]
    assert not missing, f"tools missing from system.md <tools> block: {missing}"


def test_convert_to_qwen_metadata_matches_curated_surface():
    metadata_tools = tuple(tool["function"]["name"] for tool in TOOL_DEFINITIONS)
    assert metadata_tools == MODEL_VISIBLE_TOOL_NAMES


def test_play_qwen_filters_to_curated_surface():
    source = (REPO_ROOT / "play_qwen.py").read_text()
    assert "from tool_surface import MODEL_VISIBLE_TOOL_NAMES" in source
    assert "if name not in MODEL_VISIBLE_TOOL_NAMES:" in source
    assert "return [n for n in self._tools if n in MODEL_VISIBLE_TOOL_NAMES]" in source


def test_live_mcp_server_exports_exact_curated_surface():
    exported = _exported_mcp_tool_names()
    assert set(exported) == set(MODEL_VISIBLE_TOOL_NAMES)
