"""Combat tools: attack, set_attack_style, respawn."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.helpers import inventory_diff, inventory_snapshot
from mcp_server.tools.navigation import _warp_impl


@mcp.tool()
async def attack(ctx: Context, mob_name: str) -> str:
    """Attack the nearest alive mob matching the given name.

    Args:
        mob_name: Name of mob to attack (e.g. 'Rat', 'Snek', 'Goblin')
    """
    log_tool("attack", args={"mob_name": mob_name})
    page = await get_page(ctx)

    # Snapshot mob HP before attacking
    hp_before = await page.evaluate("""(name) => {
        const g = window.game;
        if (!g || !g.player) return null;
        const nl = name.toLowerCase();
        for (const e of Object.values(g.entities.entities || {})) {
            if (e.type === 3 && (e.hitPoints || 0) > 0 &&
                (e.name || '').toLowerCase().includes(nl))
                return e.hitPoints;
        }
        return null;
    }""", mob_name)

    result = await page.evaluate(
        "(name) => JSON.stringify(window.__attackMob(name))", mob_name
    )
    # 2.5 s settle window matches the original monolithic mcp_game_server.py;
    # the 3000 ms value introduced in the modular refactor was an inadvertent
    # regression that added ~500 ms latency to every combat turn.
    await page.wait_for_timeout(2500)

    # Post-attack state: check if mob died, damage dealt, player HP
    post = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p) return {};
        const t = p.target;
        return {
            killed: !t || (t.hitPoints !== undefined && t.hitPoints <= 0),
            mob_hp: t ? (t.hitPoints || 0) : 0,
            mob_name: t ? (t.name || '') : null,
            player_hp: p.hitPoints || 0,
            player_max_hp: p.maxHitPoints || 0,
        };
    }""")
    if isinstance(post, dict):
        if hp_before is not None:
            post["hp_before"] = hp_before
            hp_after = post.get("mob_hp", 0)
            post["damage_dealt"] = max(0, hp_before - hp_after)
            post["killed"] = bool(hp_before > 0 and hp_after <= 0)
            if post["damage_dealt"] == 0 and not post["killed"]:
                post["note"] = "Attack landed but game tick has not updated HP yet. Keep attacking — do not move."
        else:
            post["killed"] = False
            post["damage_dealt"] = 0
            post["note"] = "No target acquired — attack did nothing. Move closer or pick a different mob."

    # Auto-loot on kill: scan for nearby items and walk to them
    auto_looted = {}
    if isinstance(post, dict) and post.get("killed"):
        await page.wait_for_timeout(500)
        inv_before = await inventory_snapshot(page)
        auto_looted = await page.evaluate("""() => {
            const game = window.game;
            if (!game || !game.player) return {};
            const player = game.player;
            const allEnts = game.entities.entities || {};
            let nearest = null;
            let minDist = 999;
            for (const [inst, ent] of Object.entries(allEnts)) {
                if (ent.type !== 2 && ent.type !== 8) continue;
                const dist = Math.abs(ent.gridX - player.gridX) + Math.abs(ent.gridY - player.gridY);
                if (dist < minDist && dist <= 10) {
                    minDist = dist;
                    nearest = { instance: inst, type: ent.type, name: ent.name || 'Unknown', x: ent.gridX, y: ent.gridY, distance: dist };
                }
            }
            if (!nearest) return { no_drops: true };
            const coords = window.__tileToScreenCoords(nearest.x, nearest.y);
            if (coords && !coords.error) {
                player.disableAction = false;
                document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
                    clientX: coords.click_x, clientY: coords.click_y, bubbles: true
                }));
            }
            return { targeting: nearest };
        }""")

        if isinstance(auto_looted, dict) and auto_looted.get("targeting"):
            dist = auto_looted["targeting"].get("distance", 3)
            wait_ms = min(max(1500, dist * 300), 5000)
            await page.wait_for_timeout(wait_ms)

            # Lootbags are a two-step flow (walk to bag → bag UI opens →
            # take items by index). The careful implementation lives in
            # `loot()` (gathering.py) which polls for the bag UI to become
            # visible and only sends Take packets for indices it can see.
            # Brute-forcing 10 packets blindly here either fires before the
            # server has registered the open, or hits already-taken indices
            # — works sometimes, silent-fails often. Hint the agent to call
            # loot() explicitly instead.
            if auto_looted["targeting"].get("type") == 8:
                inv_after = await inventory_snapshot(page)
                gained = inventory_diff(inv_before, inv_after)
                auto_looted = {
                    "lootbag_pending": True,
                    "target": auto_looted["targeting"].get("name", "?"),
                    "instance": auto_looted["targeting"].get("instance"),
                    "looted": gained if gained else "none",
                    "hint": "Lootbag spawned — call loot() to take its contents.",
                }
            else:
                # Ground items: instant pickup on click; just diff inventory.
                inv_after = await inventory_snapshot(page)
                gained = inventory_diff(inv_before, inv_after)
                auto_looted = {"looted": gained if gained else "none", "target": auto_looted["targeting"].get("name", "?")}

    # Merge post-attack state into result
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict):
            parsed["post_attack"] = post
            if auto_looted and not auto_looted.get("no_drops"):
                parsed["auto_loot"] = auto_looted
            return json.dumps(parsed)
    except Exception:
        pass
    return result


@mcp.tool()
async def set_attack_style(ctx: Context, style: str = "hack") -> str:
    """Set combat attack style.

    Args:
        style: 'hack' (strength+defense), 'chop' (accuracy+defense), or 'defensive' (defense)
    """
    style_ids = {"hack": 6, "chop": 7, "defensive": 3}
    sid = style_ids.get(style.lower(), 6)
    page = await get_page(ctx)
    await page.evaluate(f"() => window.game.player.setAttackStyle({sid})")
    return f"Set attack style to {style} (id={sid})"


@mcp.tool()
async def respawn(ctx: Context) -> str:
    """Respawn after death, clear all combat state, and warp to Mudwich."""
    page = await get_page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('respawn'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(2000)
    result = await _warp_impl(page, 0)
    return "Respawned and combat cleared. " + result
