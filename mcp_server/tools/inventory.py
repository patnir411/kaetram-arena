"""Inventory tools: eat_food, drop_item, equip_item."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, mcp
from mcp_server.helpers import inventory_count


@mcp.tool()
async def eat_food(ctx: Context, slot: int) -> str:
    """Eat food from inventory to heal HP.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await get_page(ctx)
    result = await page.evaluate(
        "(s) => JSON.stringify(window.__eatFood(s))", slot
    )
    await page.wait_for_timeout(1000)
    return result


@mcp.tool()
async def drop_item(ctx: Context, slot: int) -> str:
    """Drop an item from inventory to free space.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await get_page(ctx)

    before = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const el = inv.getElement(idx);
        if (!el) return { error: 'No item in slot ' + idx };
        const key = (el.dataset && el.dataset.key) || 'unknown';
        let count = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) count++;
        }
        return { key: key, count: count };
    }""", slot)

    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    result = await page.evaluate("""(idx) => {
        try {
            window.game.socket.send(21, { opcode: 2, type: 1, fromIndex: idx, value: 1 });
            return { sent: true };
        } catch(e) {
            return { error: 'Failed to send drop packet: ' + e.message };
        }
    }""", slot)

    await page.wait_for_timeout(1000)
    after = await inventory_count(page)

    item_key = before.get("key", "unknown") if isinstance(before, dict) else "unknown"
    count_before = before.get("count", -1) if isinstance(before, dict) else -1

    if isinstance(after, int) and after < count_before:
        return json.dumps({"dropped": True, "item": item_key, "slot": slot,
                           "inventory_before": count_before, "inventory_after": after})
    else:
        return json.dumps({"dropped": False, "item": item_key, "slot": slot,
                           "error": "Drop may have failed — inventory count unchanged",
                           "inventory_before": count_before, "inventory_after": after})


@mcp.tool()
async def equip_item(ctx: Context, slot: int) -> str:
    """Equip an item from inventory.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await get_page(ctx)
    result = await page.evaluate("(s) => window.__equipItem(s)", slot)

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result)

    await page.wait_for_timeout(1500)

    after = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p || !p.equipments) return {};
        const slots = {};
        for (let i = 0; i < p.equipments.length; i++) {
            const eq = p.equipments[i];
            slots[i] = eq ? (eq.name || eq.key || 'none') : 'none';
        }
        return slots;
    }""")

    before_eq = result.get("equipment_before", {}) if isinstance(result, dict) else {}
    item_key = result.get("item", "unknown") if isinstance(result, dict) else "unknown"

    before_norm = {str(k): str(v) for k, v in before_eq.items()} if isinstance(before_eq, dict) else {}
    after_norm = {str(k): str(v) for k, v in after.items()} if isinstance(after, dict) else {}
    changed_slots = {}
    for k in set(list(before_norm.keys()) + list(after_norm.keys())):
        if before_norm.get(k) != after_norm.get(k):
            changed_slots[k] = {"before": before_norm.get(k, "none"), "after": after_norm.get(k, "none")}

    if changed_slots:
        return json.dumps({"equipped": True, "slot": slot, "item": item_key, "changes": changed_slots})
    else:
        return json.dumps({"equipped": False, "slot": slot, "item": item_key,
                           "error": "Equip failed — no equipment slot changed."})
