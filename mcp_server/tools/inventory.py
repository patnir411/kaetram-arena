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

    # Capture both per-slot count (for stackable items like logs/ores where
    # dropping 1 of 10 leaves the slot occupied) AND total occupied count
    # (for non-stackable items where the slot empties out). Either signal
    # alone misses cases — we need both to confirm a real drop.
    before = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const el = inv.getElement(idx);
        if (!el || inv.isEmpty(el)) return { error: 'No item in slot ' + idx };
        const key = (el.dataset && el.dataset.key) || 'unknown';
        const slotCount = Number(el.count ?? el.dataset?.count ?? 1) || 1;
        let occupied = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) occupied++;
        }
        return { key: key, slot_count: slotCount, occupied_count: occupied };
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

    # Snapshot the same slot's state plus total occupied so we can detect
    # both stack-decrement and full-slot-clear drops.
    after = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const el = inv.getElement(idx);
        const empty = !el || inv.isEmpty(el);
        const slotCount = empty ? 0 : (Number(el.count ?? el.dataset?.count ?? 1) || 1);
        let occupied = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) occupied++;
        }
        return { empty: empty, slot_count: slotCount, occupied_count: occupied };
    }""", slot)

    item_key = before.get("key", "unknown") if isinstance(before, dict) else "unknown"
    count_before = before.get("occupied_count", -1) if isinstance(before, dict) else -1
    slot_count_before = before.get("slot_count", 0) if isinstance(before, dict) else 0
    count_after = after.get("occupied_count", -1) if isinstance(after, dict) else -1
    slot_count_after = after.get("slot_count", 0) if isinstance(after, dict) else 0

    # Either signal proves a successful drop:
    #   - non-stackable: slot becomes empty → occupied_count drops
    #   - stackable: slot stays occupied but slot_count decrements
    dropped = (
        (isinstance(count_after, int) and count_after < count_before)
        or (slot_count_after < slot_count_before)
    )
    body = {
        "item": item_key, "slot": slot,
        "inventory_before": count_before, "inventory_after": count_after,
        "slot_count_before": slot_count_before, "slot_count_after": slot_count_after,
    }
    if dropped:
        return json.dumps({"dropped": True, **body})
    return json.dumps({
        "dropped": False, **body,
        "error": "Drop may have failed — neither slot count nor inventory occupancy decreased",
    })


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

    # Client-side `player.equipments` is `{ [key: number]: Equipment }` (a
    # map keyed by slot type), NOT an array — see Kaetram-Open
    # packages/client/src/entity/character/player/player.ts:60. The previous
    # `for (i < .length)` loop never iterated (length undefined), so the
    # after-snapshot was always {} and the diff at the Python layer always
    # reported `equipped: true`. Object.entries matches the before-snapshot
    # in state_extractor.js __equipItem.
    after = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p || !p.equipments) return {};
        const slots = {};
        for (const [slotId, eq] of Object.entries(p.equipments)) {
            slots[slotId] = eq ? (eq.name || eq.key || 'none') : 'none';
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
