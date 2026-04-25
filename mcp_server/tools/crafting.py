"""Crafting tools: craft_item."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log, log_tool, mcp
from mcp_server.helpers import inventory_snapshot
from mcp_server.utils import PRODUCTION_SKILL_ALIASES, normalize_production_skill


@mcp.tool()
async def craft_item(ctx: Context, skill: str, recipe_key: str, count: int = 1) -> str:
    """Open the relevant production interface and craft a recipe by key.

    Supports Crafting, Cooking, Smithing, Smelting, Alchemy, Fletching, and
    Chiseling. Station-based skills auto-walk to the nearest matching station.
    """
    page = await get_page(ctx)
    skill_name = normalize_production_skill(skill)
    if not skill_name:
        return json.dumps({"error": f"Unknown production skill '{skill}'", "allowed": sorted(set(PRODUCTION_SKILL_ALIASES.values()))})

    key = (recipe_key or "").strip().lower()
    if not key:
        return json.dumps({"error": "Recipe key is empty"})

    craft_count = max(1, min(int(count or 1), 25))
    inv_before = await page.evaluate("() => window.__inventorySnapshot ? window.__inventorySnapshot() : {}")
    open_result = await page.evaluate("(skillName) => window.__openProductionInterface(skillName)", skill_name)
    if isinstance(open_result, str):
        open_result = json.loads(open_result)
    if open_result.get("error"):
        if open_result.get("error") == "No station found for skill on this map":
            try:
                cursor_debug = await page.evaluate("(skillName) => window.__debugCursorTiles ? window.__debugCursorTiles(skillName) : null", skill_name)
            except Exception as e:
                cursor_debug = {"error": f"cursor debug failed: {e}"}
            log(f"[craft_item] cursor_debug skill={skill_name} recipe={key} detail={cursor_debug}")
            open_result["cursor_debug"] = cursor_debug
        log_tool("craft_item", success=False, error=json.dumps(open_result))
        return json.dumps(open_result)

    if open_result.get("needs_move"):
        adjacent = open_result.get("adjacent") or {}
        target = open_result.get("target") or {}
        await page.evaluate("([x,y]) => window.__navigateTo(x, y)", [adjacent.get("x"), adjacent.get("y")])
        arrived = False
        final_pos = {}
        for _ in range(15):
            await page.wait_for_timeout(1000)
            final_pos = await page.evaluate("""([x,y]) => {
                const p = window.game && window.game.player;
                if (!p) return { distance: 999, player_pos: null };
                return { distance: Math.abs(p.gridX - x) + Math.abs(p.gridY - y), player_pos: { x: p.gridX, y: p.gridY } };
            }""", [adjacent.get("x"), adjacent.get("y")])
            if final_pos.get("distance", 999) <= 1:
                arrived = True
                break
        if not arrived:
            err = {"error": f"Could not reach {skill_name} station", "skill": skill_name, "target": target, "adjacent": adjacent, "player": final_pos.get("player_pos")}
            log_tool("craft_item", success=False, error=json.dumps(err))
            return json.dumps(err)
        open_result = await page.evaluate("(skillName) => window.__openProductionInterface(skillName)", skill_name)
        if isinstance(open_result, str):
            open_result = json.loads(open_result)
        if open_result.get("error"):
            log_tool("craft_item", success=False, error=json.dumps(open_result))
            return json.dumps(open_result)

    crafting_state = {}
    visible = False
    inventory_opener = open_result.get("via") == "inventory_item"
    # For inventory-opener skills (fletching/chiseling), the open round-trip
    # is asynchronous: client sends Container.Select -> server fires the
    # knife/chisel plugin -> server sends Crafting.Open back -> client makes
    # the menu visible. Without this leading wait the visible-check below
    # passes on a stale `selected_key` from a previous open, but the server's
    # `activeCraftingInterface` isn't set yet, so the follow-up Craft packet
    # is rejected at incoming.ts:794.
    if inventory_opener:
        await page.wait_for_timeout(1500)
    for _ in range(10):
        await page.wait_for_timeout(500)
        crafting_state = await page.evaluate("() => window.__getCraftingState ? window.__getCraftingState() : ({ visible: false })")
        if crafting_state.get("visible") and crafting_state.get("skill") == skill_name:
            visible = True
            break
        if inventory_opener and skill_name in {"fletching", "chiseling"} and crafting_state.get("skill") == skill_name and crafting_state.get("selected_key"):
            visible = True
            break

    if not visible:
        err = {"error": f"Could not open {skill_name} interface", "skill": skill_name, "open_result": open_result, "state": crafting_state}
        log_tool("craft_item", success=False, error=json.dumps(err))
        return json.dumps(err)

    select_result = await page.evaluate("(recipe) => window.__selectCraftRecipe(recipe)", key)
    if isinstance(select_result, str):
        select_result = json.loads(select_result)
    if select_result.get("error"):
        return json.dumps(select_result)

    await page.wait_for_timeout(700)
    selected_state = await page.evaluate("() => window.__getCraftingState ? window.__getCraftingState() : ({ visible: false })")
    if selected_state.get("selected_key") != key:
        err = {"error": f"Recipe '{key}' is not available in the open {skill_name} interface", "skill": skill_name, "selected_state": selected_state}
        log_tool("craft_item", success=False, error=json.dumps(err))
        return json.dumps(err)

    craft_result = await page.evaluate("([recipe, amount]) => window.__confirmCraftRecipe(recipe, amount)", [key, craft_count])
    if isinstance(craft_result, str):
        craft_result = json.loads(craft_result)
    if craft_result.get("error"):
        log_tool("craft_item", success=False, error=json.dumps(craft_result))
        return json.dumps(craft_result)

    await page.wait_for_timeout(2500)
    inv_after = await page.evaluate("() => window.__inventorySnapshot ? window.__inventorySnapshot() : {}")
    inventory_delta = {}
    keys = set(inv_before) | set(inv_after)
    for item_key in keys:
        diff = inv_after.get(item_key, 0) - inv_before.get(item_key, 0)
        if diff != 0:
            inventory_delta[item_key] = diff

    # Workaround for chained crafts on the same already-open interface (e.g.
    # fletching: 1 logs -> 4 sticks, then 4 sticks -> 1 bowlmedium). The server
    # accepts Crafting.Select but a follow-up Craft sometimes lands as a no-op.
    # If the confirm produced no inventory change, close the menu, re-open it
    # via the standard path, re-select, and re-confirm once.
    if not inventory_delta and open_result.get("via") == "existing":
        log(f"[craft_item] empty inventory_delta on existing menu — retrying with fresh open for {skill_name}/{key}")
        await page.evaluate("() => window.__closeCraftingMenu && window.__closeCraftingMenu()")
        await page.wait_for_timeout(500)
        reopen = await page.evaluate("(skillName) => window.__openProductionInterface(skillName)", skill_name)
        if isinstance(reopen, str):
            reopen = json.loads(reopen)
        if not reopen.get("error"):
            for _ in range(10):
                await page.wait_for_timeout(500)
                cs = await page.evaluate("() => window.__getCraftingState ? window.__getCraftingState() : ({ visible: false })")
                if cs.get("visible") or (cs.get("skill") == skill_name and cs.get("selected_key")):
                    break
            sel2 = await page.evaluate("(recipe) => window.__selectCraftRecipe(recipe)", key)
            if isinstance(sel2, str):
                sel2 = json.loads(sel2)
            if not sel2.get("error"):
                await page.wait_for_timeout(700)
                conf2 = await page.evaluate("([recipe, amount]) => window.__confirmCraftRecipe(recipe, amount)", [key, craft_count])
                if isinstance(conf2, str):
                    conf2 = json.loads(conf2)
                if not conf2.get("error"):
                    await page.wait_for_timeout(2500)
                    inv_retry = await page.evaluate("() => window.__inventorySnapshot ? window.__inventorySnapshot() : {}")
                    inventory_delta = {}
                    keys = set(inv_before) | set(inv_retry)
                    for item_key in keys:
                        diff = inv_retry.get(item_key, 0) - inv_before.get(item_key, 0)
                        if diff != 0:
                            inventory_delta[item_key] = diff

    result = {"crafted": True, "skill": skill_name, "recipe_key": key, "count": craft_count,
              "opened_via": open_result.get("via"), "target": open_result.get("target"),
              "selected_name": selected_state.get("selected_name"), "inventory_delta": inventory_delta}
    log_tool("craft_item", args={"skill": skill_name, "recipe_key": key, "count": craft_count})
    return json.dumps(result)
