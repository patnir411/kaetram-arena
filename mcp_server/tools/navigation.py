"""Navigation tools: navigate, warp, cancel_nav, stuck_reset."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log, log_tool, log_tool_result, mcp


async def _warp_impl(page, warp_id: int) -> str:
    """Internal helper to handle robust warping with retries and combat awareness."""
    await page.evaluate("""() => {
        window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
    }""")

    max_attempts = 6
    result_raw = "{}"
    for attempt in range(max_attempts):
        result_raw = await page.evaluate(
            "(id) => JSON.stringify(window.__safeWarp(id))", warp_id
        )
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        is_combat_block = isinstance(result, dict) and (
            result.get("cooldown_remaining_seconds")
            or result.get("has_target")
            or result.get("attackers")
        )
        if is_combat_block:
            wait_secs = result.get("cooldown_remaining_seconds", 5)
            wait_ms = min(wait_secs * 1000 + 1000, 6000)
            await page.wait_for_timeout(wait_ms)
            await page.evaluate("""() => {
                window.__clearCombatState();
                window.__kaetramState.lastCombatTime = 0;
            }""")
            continue
        break

    await page.wait_for_timeout(2000)
    return result_raw


@mcp.tool()
async def navigate(ctx: Context, x: int, y: int) -> str:
    """Navigate to grid coordinates using BFS pathfinding.

    Auto-advances waypoints in background. Call observe() to check navigation.status.
    For distances > 100 tiles, warp to nearest town first.

    Args:
        x: Target grid X coordinate
        y: Target grid Y coordinate
    """
    log_tool("navigate", args={"x": x, "y": y})
    page = await get_page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__navigateTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(1000)

    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        # Surface a compact navigate-outcome summary under KAETRAM_DEBUG=1
        # so operators can tell at a glance whether BFS found a path, fell
        # back to linear, or errored. This is the single highest-signal log
        # line for debugging reachability walks.
        if isinstance(parsed, dict):
            compact = {
                "status": parsed.get("status"),
                "pathfinding": parsed.get("pathfinding"),
                "waypoints_count": parsed.get("waypoints_count"),
                "total_distance": parsed.get("total_distance"),
                "target": parsed.get("target"),
                "error": parsed.get("error"),
            }
            log_tool_result("navigate", {k: v for k, v in compact.items() if v is not None})
            if parsed.get("pathfinding") == "linear_fallback":
                parsed["warning"] = (
                    "BFS pathfinding failed — using approximate straight-line route. "
                    "High chance of getting stuck on walls. Consider warping closer first, "
                    "or navigating in shorter hops (< 80 tiles)."
                )
                return json.dumps(parsed)
    except Exception:
        pass
    return result


async def move(ctx: Context, x: int, y: int) -> str:
    """Move to a nearby tile (< 15 tiles). For longer distances use navigate().

    Args:
        x: Target grid X
        y: Target grid Y
    """
    page = await get_page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__moveTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result


@mcp.tool()
async def warp(ctx: Context, location: str = "mudwich") -> str:
    """Fast travel to a town. Auto-waits up to 25s if combat cooldown is active.

    Args:
        location: 'mudwich', 'aynor', 'lakesworld', 'patsow', 'crullfield', or 'undersea'
    """
    log_tool("warp", args={"location": location})
    warp_ids = {"mudwich": 0, "aynor": 1, "lakesworld": 2, "patsow": 3, "crullfield": 4, "undersea": 5}
    normalized = (location or "").lower()
    if normalized not in warp_ids:
        return json.dumps({
            "error": f"Unknown warp location '{location}'",
            "allowed": sorted(warp_ids),
        })
    warp_id = warp_ids[normalized]
    page = await get_page(ctx)
    return await _warp_impl(page, warp_id)


@mcp.tool()
async def cancel_nav(ctx: Context) -> str:
    """Cancel active navigation."""
    page = await get_page(ctx)
    await page.evaluate("() => window.__navCancel()")
    return "Navigation cancelled"


@mcp.tool()
async def stuck_reset(ctx: Context) -> str:
    """Reset stuck detection. Use when stuck check shows stuck=true."""
    page = await get_page(ctx)
    await page.evaluate("() => window.__stuckReset()")
    return "Stuck state reset"
