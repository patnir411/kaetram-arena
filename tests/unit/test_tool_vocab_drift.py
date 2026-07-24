from __future__ import annotations

import ast
import copy
import re
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from convert_to_qwen import TOOL_DEFINITIONS  # noqa: E402
from tool_surface import (  # noqa: E402
    MODEL_VISIBLE_TOOL_NAMES,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
    TOOL_SCHEMA_VERSION,
    tool_schema_sha256,
    validate_live_tool_compatibility,
    validate_tool_definitions,
)

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


def _exported_mcp_parameter_schemas() -> dict[str, dict]:
    """Derive the model-visible parameter contract from decorated functions."""
    schemas = {}
    python_to_json = {"str": "string", "int": "integer", "bool": "boolean"}
    for path in sorted(MCP_TOOLS_DIR.glob("*.py")):
        if path.name in ("__init__.py", "test_lane.py"):
            continue
        module = ast.parse(path.read_text())
        for node in module.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            is_tool = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "mcp"
                and dec.func.attr == "tool"
                for dec in node.decorator_list
            )
            if not is_tool:
                continue
            args = [arg for arg in node.args.args if arg.arg != "ctx"]
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            properties = {}
            required = []
            for arg, default in zip(args, defaults):
                assert isinstance(arg.annotation, ast.Name), f"complex annotation for {node.name}.{arg.arg}"
                properties[arg.arg] = {"type": python_to_json[arg.annotation.id]}
                if default is None:
                    required.append(arg.arg)
                else:
                    properties[arg.arg]["default"] = ast.literal_eval(default)
            schemas[node.name] = {
                "type": "object",
                "properties": properties,
                "required": required,
            }
    return schemas


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
    assert "MODEL_VISIBLE_TOOL_DEFINITIONS" in source
    assert "MODEL_VISIBLE_TOOL_NAMES" in source
    assert "validate_live_tool_compatibility" in source
    assert "if name not in MODEL_VISIBLE_TOOL_NAMES:" in source
    assert "return [n for n in self._tools if n in MODEL_VISIBLE_TOOL_NAMES]" in source
    assert 'default=os.environ.get("KAETRAM_TOOL_SCHEMA_SOURCE", "live")' in source
    assert 'if schema_source == "canonical":' in source


def test_live_mcp_server_exports_exact_curated_surface():
    exported = _exported_mcp_tool_names()
    assert set(exported) == set(MODEL_VISIBLE_TOOL_NAMES)


def test_frozen_schema_parameters_match_every_live_tool_signature_and_default():
    exported = _exported_mcp_parameter_schemas()
    frozen = {}
    for tool in TOOL_DEFINITIONS:
        parameters = tool["function"]["parameters"]
        frozen[tool["function"]["name"]] = {
            "type": parameters["type"],
            "properties": {
                name: {
                    key: value
                    for key, value in schema.items()
                    if key in {"type", "default"}
                }
                for name, schema in parameters["properties"].items()
            },
            "required": parameters["required"],
        }
    assert frozen == exported


def test_schema_hash_is_frozen_and_validates_all_model_visible_bytes():
    assert TOOL_SCHEMA_VERSION == "kaetram_mcp_v1"
    assert tool_schema_sha256(TOOL_DEFINITIONS) == MODEL_VISIBLE_TOOL_SCHEMA_SHA256
    validate_tool_definitions(TOOL_DEFINITIONS)

    drifted = copy.deepcopy(TOOL_DEFINITIONS)
    drifted[-1]["function"]["parameters"]["properties"]["count"]["default"] = 2
    with pytest.raises(ValueError, match="schema drift"):
        validate_tool_definitions(drifted)


def test_canonical_runtime_handshake_rejects_live_functional_drift_only():
    live = copy.deepcopy(TOOL_DEFINITIONS)
    # FastMCP/Pydantic may add non-functional titles and owns live descriptions.
    live.reverse()
    live[0]["function"]["description"] = "live server prose may differ"
    live[0]["function"]["parameters"]["title"] = "CraftItemArguments"
    for schema in live[0]["function"]["parameters"]["properties"].values():
        schema["title"] = "Pydantic title"
    validate_live_tool_compatibility(live)

    drifted = copy.deepcopy(live)
    craft = next(t for t in drifted if t["function"]["name"] == "craft_item")
    craft["function"]["parameters"]["properties"]["count"]["default"] = 2
    with pytest.raises(ValueError, match="live MCP functional schema drift"):
        validate_live_tool_compatibility(drifted)

    missing = [t for t in live if t["function"]["name"] != "observe"]
    with pytest.raises(ValueError, match="live MCP tool-name drift"):
        validate_live_tool_compatibility(missing)
