"""Gathering and looting tools: gather, loot."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.helpers import inventory_count, inventory_diff, inventory_snapshot
from mcp_server.resource_gates import gate_for_resource


@mcp.tool()
async def gather(ctx: Context, resource_name: str) -> str:
    """Gather from a nearby resource (tree, rock, bush, fish spot).

    Args:
        resource_name: Name of resource (e.g. 'Oak', 'Nisoc Rock', 'Tomato', 'Blueberry Bush')
    """
    log_tool("gather", args={"resource_name": resource_name})
    page = await get_page(ctx)

    inv_before = await inventory_snapshot(page)

    resource = await page.evaluate("""(name) => {
        const gs = window.__extractGameState();
        if (!gs) return { error: 'Game not loaded' };
        const nameLower = name.toLowerCase();
        const resources = (gs.nearby_entities || []).filter(e =>
            [10, 11, 12, 13].includes(e.type) &&
            !e.exhausted &&
            (e.name || '').toLowerCase().includes(nameLower)
        );
        if (resources.length === 0) return { error: 'No resource matching "' + name + '" nearby.' };
        resources.sort((a, b) => a.distance - b.distance);
        return resources[0];
    }""", resource_name)

    if isinstance(resource, str):
        resource = json.loads(resource)
    if resource.get("error"):
        return json.dumps(resource)

    await page.evaluate(
        "([x,y]) => JSON.stringify(window.__clickTile(x, y))", [resource["x"], resource["y"]]
    )

    resource_type = resource.get("type", 0)
    wait_ms = 4000 if resource_type == 12 else 7000
    await page.wait_for_timeout(wait_ms)

    inv_after = await inventory_snapshot(page)
    gained = inventory_diff(inv_before, inv_after)

    response = {
        "resource": resource.get("name", resource_name),
        "position": {"x": resource["x"], "y": resource["y"]},
        "type": resource_type,
        "items_gained": gained if gained else "none",
    }

    # When nothing was gained, surface the most likely cause: a skill-level
    # gate. Look up the resource's required skill+level from bundled Kaetram
    # data, fetch the player's current level for that skill, and report a
    # structured `gate` block so the agent can decide (grind vs pivot)
    # without having to guess from a vague error string.
    if not gained:
        # A full inventory silently blocks every gather — surface it as the
        # cause so the agent drops items instead of looping on "RNG miss".
        slots_used = await inventory_count(page)
        inv_full = isinstance(slots_used, int) and slots_used >= 25
        gate = gate_for_resource(resource.get("name", resource_name))
        if gate and gate.get("level", 1) > 1:
            current = await page.evaluate("""(skill) => {
                const gs = window.__extractGameState && window.__extractGameState();
                const sk = gs && gs.skills && gs.skills[skill];
                return sk ? (sk.level || 1) : 1;
            }""", gate["skill"])
            current_level = int(current) if isinstance(current, (int, float)) else 1
            gated = current_level < gate["level"]
            response["gate"] = {
                "skill": gate["skill"],
                "required_level": gate["level"],
                "current_level": current_level,
                "gated": gated,
            }
            if gated:
                response["why_no_items"] = (
                    f"{gate['skill']} L{gate['level']} required, you are L{current_level}. "
                    f"Either grind {gate['skill']} XP or pick a different resource/quest."
                )
            elif inv_full:
                response["why_no_items"] = (
                    f"Inventory full ({slots_used}/25) — drop low-value items "
                    "(e.g. surplus blueberries) before gathering."
                )
            else:
                response["why_no_items"] = (
                    "No items gained, though your skill level is sufficient. "
                    "Re-observe and check your distance to the node and whether it "
                    "is still ready before retrying."
                )
        elif inv_full:
            response["why_no_items"] = (
                f"Inventory full ({slots_used}/25) — drop low-value items "
                "(e.g. surplus blueberries) before gathering."
            )
        else:
            response["why_no_items"] = (
                "No items gained. Re-observe and check your distance to the node, "
                "its state, and your skill level before retrying."
            )

    return json.dumps(response)


@mcp.tool()
async def loot(ctx: Context) -> str:
    """Pick up nearby ground items and lootbag contents after combat."""
    log_tool("loot")
    page = await get_page(ctx)

    inv_before = await inventory_snapshot(page)

    open_lootbag = await page.evaluate("""() => {
        try {
            const game = window.game;
            const player = game && game.player;
            const menu = game && game.menu;
            const lootBag = menu && menu.getLootBag ? menu.getLootBag() : null;
            const visible = !!(
                lootBag && (
                    (typeof lootBag.isVisible === 'function' && lootBag.isVisible()) ||
                    lootBag.visible === true
                )
            );
            const itemList = lootBag && lootBag.itemList;
            const itemEntries = [];
            if (itemList && itemList.children) {
                for (const child of itemList.children) {
                    const slot = child.querySelector ? child.querySelector('.item-slot') : null;
                    const idx = Number(child.index ?? child.dataset?.index ?? slot?.dataset?.index);
                    if (!Number.isNaN(idx)) {
                        itemEntries.push({ index: idx, has_slot: !!slot, key: slot?.dataset?.key || child.dataset?.key || null });
                    }
                }
            }
            return {
                active_instance: player ? (player.activeLootBag || '') : '',
                visible, item_count: itemEntries.length, item_entries: itemEntries,
                player_pos: player ? { x: player.gridX, y: player.gridY } : null,
            };
        } catch (e) { return { error: String(e) }; }
    }""")

    if isinstance(open_lootbag, dict) and open_lootbag.get("visible") and int(open_lootbag.get("item_count", 0) or 0) > 0:
        result = {
            "found": 1, "targeting": {"instance": open_lootbag.get("active_instance") or "", "type": 8, "name": "Open Lootbag", "distance": 0},
            "player_pos": open_lootbag.get("player_pos"), "candidates": [],
        }
    else:
        result_raw = await page.evaluate("""() => {
            const game = window.game;
            if (!game || !game.player) return JSON.stringify({ error: 'Game not loaded' });
            const player = game.player;
            const allEnts = game.entities.entities || {};
            const lootable = [];
            for (const [inst, ent] of Object.entries(allEnts)) {
                if (ent.type !== 2 && ent.type !== 8) continue;
                const dist = Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY);
                if (dist <= 15) lootable.push({ instance: inst, type: ent.type, name: ent.name || 'Unknown', x: ent.gridX, y: ent.gridY, distance: dist });
            }
            const playerPos = { x: player.gridX, y: player.gridY };
            if (lootable.length === 0) return JSON.stringify({ found: 0, player_pos: playerPos, candidates: [] });
            lootable.sort((a, b) => { const ap = a.type === 8 ? 0 : 1; const bp = b.type === 8 ? 0 : 1; if (ap !== bp) return ap - bp; return a.distance - b.distance; });
            const target = lootable[0];
            const coords = window.__tileToScreenCoords(target.x, target.y);
            if (coords && !coords.error) { game.player.disableAction = false; document.getElementById('canvas').dispatchEvent(new MouseEvent('click', { clientX: coords.click_x, clientY: coords.click_y, bubbles: true })); }
            return JSON.stringify({ found: lootable.length, targeting: target, player_pos: playerPos, candidates: lootable });
        }""")
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw

    if result.get("found", 0) == 0:
        return json.dumps({"message": "No items or lootbags nearby to pick up"})

    targeting = result.get("targeting", {})
    lootbag_ready = open_lootbag if targeting.get("type") == 8 else None
    lootbag_opened = bool(isinstance(lootbag_ready, dict) and lootbag_ready.get("visible") and int(lootbag_ready.get("item_count", 0) or 0) > 0)

    if not lootbag_opened:
        for _ in range(12):
            await page.wait_for_timeout(500)
            lootbag_ready = await page.evaluate("""(instanceId) => {
                try {
                    const game = window.game; const player = game && game.player; const menu = game && game.menu;
                    const lootBag = menu && menu.getLootBag ? menu.getLootBag() : null;
                    const visible = !!(lootBag && ((typeof lootBag.isVisible === 'function' && lootBag.isVisible()) || lootBag.visible === true));
                    const itemList = lootBag && lootBag.itemList; const itemEntries = [];
                    if (itemList && itemList.children) { for (const child of itemList.children) { const slot = child.querySelector ? child.querySelector('.item-slot') : null; const idx = Number(child.index ?? child.dataset?.index ?? slot?.dataset?.index); if (!Number.isNaN(idx)) itemEntries.push({ index: idx, has_slot: !!slot, key: slot?.dataset?.key || child.dataset?.key || null }); } }
                    const ent = game && game.entities && game.entities.entities ? game.entities.entities[instanceId] : null;
                    const distance = (player && ent) ? Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY) : null;
                    return { active_instance: player ? (player.activeLootBag || '') : '', visible, item_count: itemEntries.length, item_entries: itemEntries, distance };
                } catch (e) { return { error: String(e) }; }
            }""", targeting.get("instance"))
            if isinstance(lootbag_ready, dict) and lootbag_ready.get("visible") and (lootbag_ready.get("active_instance") == targeting.get("instance") or (lootbag_ready.get("item_count") or 0) > 0):
                lootbag_opened = True
                break

    if targeting.get("type") == 8 or lootbag_opened:
        if isinstance(lootbag_ready, dict) and lootbag_ready.get("visible"):
            for _ in range(10):
                await page.evaluate("""async () => {
                    const game = window.game; const list = document.querySelector('#lootbag-items > ul');
                    const entries = [];
                    if (list && list.children) { for (const child of list.children) { const slot = child.querySelector ? child.querySelector('.item-slot') : null; const idx = Number(child.index ?? child.dataset?.index ?? slot?.dataset?.index); if (!Number.isNaN(idx)) entries.push({ index: idx, has_slot: !!slot }); } }
                    if (entries.length === 0) return { clicked: null };
                    const next = entries[0]; let slot = null;
                    for (const child of list.children) { const c = child.querySelector ? child.querySelector('.item-slot') : null; const idx = Number(child.index ?? child.dataset?.index ?? c?.dataset?.index); if (idx === next.index) { slot = c; break; } }
                    if (game.socket) { try { game.socket.send(58, { opcode: 1, index: next.index }); } catch(e) {} }
                    if (slot) { slot.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); slot.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })); slot.dispatchEvent(new MouseEvent('click', { bubbles: true })); }
                    await new Promise(r => setTimeout(r, 250));
                    return { clicked: next.index };
                }""")
                await page.wait_for_timeout(350)
                lootbag_ready = await page.evaluate("""() => {
                    try {
                        const game = window.game; const menu = game && game.menu;
                        const lootBag = menu && menu.getLootBag ? menu.getLootBag() : null;
                        const visible = !!(lootBag && ((typeof lootBag.isVisible === 'function' && lootBag.isVisible()) || lootBag.visible === true));
                        const itemList = lootBag && lootBag.itemList; const itemEntries = [];
                        if (itemList && itemList.children) { for (const child of itemList.children) { const slot = child.querySelector ? child.querySelector('.item-slot') : null; const idx = Number(child.index ?? child.dataset?.index ?? slot?.dataset?.index); if (!Number.isNaN(idx)) itemEntries.push({ index: idx }); } }
                        return { visible, item_count: itemEntries.length };
                    } catch (e) { return { error: String(e) }; }
                }""")
                if not isinstance(lootbag_ready, dict) or not lootbag_ready.get("visible") or int(lootbag_ready.get("item_count", 0) or 0) == 0:
                    break
            await page.wait_for_timeout(1000)
    else:
        for _ in range(20):
            await page.wait_for_timeout(500)
            gws = await page.evaluate("""([instanceId, invBefore]) => {
                try {
                    const game = window.game; const player = game && game.player;
                    const entities = game && game.entities && game.entities.entities ? game.entities.entities : {};
                    const ent = entities[instanceId] || null;
                    const inv = game && game.menu ? game.menu.getInventory() : null;
                    const invNow = {};
                    if (inv && inv.getElement) { for (let i = 0; i < 25; i++) { const el = inv.getElement(i); if (!el || !el.dataset?.key || inv.isEmpty(el)) continue; const k = el.dataset.key; invNow[k] = (invNow[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1); } }
                    const changed = JSON.stringify(invNow) !== JSON.stringify(invBefore || {});
                    return { target_exists: !!ent, inventory_changed: changed };
                } catch (e) { return { error: String(e) }; }
            }""", [targeting.get("instance"), inv_before])
            if isinstance(gws, dict) and (gws.get("inventory_changed") or not gws.get("target_exists", True)):
                break

    inv_after = await inventory_snapshot(page)
    gained = inventory_diff(inv_before, inv_after)

    return json.dumps({
        "target": targeting.get("name", "unknown"),
        "target_type": "lootbag" if (targeting.get("type") == 8 or lootbag_opened) else "ground_item",
        "items_collected": gained if gained else "none (item may have despawned or inventory full)",
        "other_nearby": result.get("found", 0) - 1,
    })
