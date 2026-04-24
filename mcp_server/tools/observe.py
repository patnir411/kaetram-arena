"""observe() — Game state observation tool."""

import os

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.js import OBSERVE_SCRIPT


@mcp.tool()
async def observe(ctx: Context) -> str:
    """Observe the current game state.

    Returns a unified view (~700-900 tokens) optimized for decision-making:
    - Player: pos, stats, equipment, skills
    - Status: dead, stuck, nav, indoors, combat target
    - Nearby: categorized NPCs, mobs, resources, ground items — with
      direction (N/S/E/W) and distance from player
    - Inventory: stacked by item key with counts
    - Quests: active and finished
    - Events: recent chat, combat, XP, NPC dialogue
    - ASCII map: terrain layout with entity symbols
    """
    log_tool("observe")
    page = await get_page(ctx)

    screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")

    result = await page.evaluate(OBSERVE_SCRIPT)

    # Write game_state.json for dashboard (live state, no log parsing needed)
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            gs_path = os.path.join(screenshot_dir, "game_state.json")
            with open(gs_path, "w") as f:
                f.write(gs_json)
    except Exception:
        pass

    return result
