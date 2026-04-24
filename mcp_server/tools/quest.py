"""Quest query tool: query_quest."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import mcp
from mcp_server.utils import build_quest_query_response, load_quest_walkthroughs, resolve_quest_name


@mcp.tool()
async def query_quest(ctx: Context, quest_name: str) -> str:
    """Look up detailed walkthrough for a specific quest.

    Returns quest status, requirements, unlocks, reward caveats, walkthrough,
    and boss or recipe notes for the requested quest.

    Args:
        quest_name: Exact or near-exact quest name (e.g. 'Sorcery and Stuff',
            'Scavenger', 'Royal Drama').
    """
    try:
        data = load_quest_walkthroughs()
    except FileNotFoundError:
        return json.dumps({"error": "Quest walkthrough data not found"})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Quest walkthrough data is invalid JSON: {exc}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    matched_name, err = resolve_quest_name(quest_name, data)
    if err:
        return json.dumps(err, indent=2)

    quest = data.get(matched_name)
    if not isinstance(quest, dict):
        return json.dumps({"error": f"Quest data for '{matched_name}' is malformed"})

    return json.dumps(build_quest_query_response(matched_name, quest), indent=2)
