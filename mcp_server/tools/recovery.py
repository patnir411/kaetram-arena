"""Recovery tools: clear_combat, click_tile (legacy/hidden, not exposed via @mcp.tool)."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page


async def clear_combat(ctx: Context) -> str:
    """Clear combat state and cooldown timer so you can warp."""
    page = await get_page(ctx)
    result = await page.evaluate("""() => {
        const r = window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
        window.__kaetramState.lastCombat = null;
        return JSON.stringify(r);
    }""")
    return result


async def click_tile(ctx: Context, x: int, y: int) -> str:
    """Click a specific grid tile (must be on screen). Fallback for edge cases.

    Args:
        x: Grid X coordinate
        y: Grid Y coordinate
    """
    page = await get_page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__clickTile(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result
