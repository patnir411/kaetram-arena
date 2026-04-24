"""Shared async helpers for MCP tool implementations.

All functions take a Playwright page as first argument.  These deduplicate
common patterns (inventory snapshot/diff, adjacency polling) that were
previously copy-pasted across 6+ tools.
"""

from mcp_server.js import INVENTORY_SNAPSHOT


async def inventory_snapshot(page) -> dict:
    """Return {item_key: count} dict of current inventory."""
    return await page.evaluate(INVENTORY_SNAPSHOT)


def inventory_diff(before: dict, after: dict) -> dict:
    """Compute items gained between two inventory snapshots."""
    gained = {}
    for k, v in after.items():
        diff = v - before.get(k, 0)
        if diff > 0:
            gained[k] = diff
    return gained


async def inventory_snapshot_with_gold(page) -> dict:
    """Return {items: {key: count}, gold: int} from current inventory."""
    return await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const items = {};
        let gold = 0;
        for (let i = 0; i < 25; i++) {
            const el = inv.getElement(i);
            if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
            const k = el.dataset.key;
            const c = el.count || parseInt(el.dataset?.count || '0') || 1;
            items[k] = (items[k] || 0) + c;
            if (k === 'gold') gold += c;
        }
        return { items, gold };
    }""")


async def inventory_count(page) -> int:
    """Return count of non-empty inventory slots."""
    return await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return -1;
        let count = 0;
        for (let i = 0; i < 25; i++) {
            const e = inv.getElement(i);
            if (e && e.dataset?.key && !inv.isEmpty(e)) count++;
        }
        return count;
    }""")


async def get_player_pos(page) -> dict:
    """Return {x, y} grid position of the player."""
    return await page.evaluate("""() => {
        const p = window.game && window.game.player;
        return p ? { x: p.gridX, y: p.gridY } : {};
    }""")


def mid_navigation(pos_check: dict) -> bool:
    """True if the player is still in motion toward something after an
    adjacency timeout. Used to distinguish "wall between us" from "agent
    just needs to wait another observe tick."
    """
    if not pos_check:
        return False
    if pos_check.get("player_moving"):
        return True
    if pos_check.get("waypoints_remaining", 0) > 0:
        return True
    if pos_check.get("nav_active") and pos_check.get("nav_status") == "navigating":
        return True
    return False


async def wait_for_adjacency(page, npc_pos: dict, max_checks: int = 8) -> tuple[bool, dict]:
    """Poll player→NPC distance until adjacent (Manhattan < 2) or timeout.

    Returns (arrived: bool, last_pos_check: dict).

    The pos_check dict also carries navigation metadata so the caller can
    distinguish "blocked by terrain" from "still walking":
        - nav_active:   bool — is window.__navState.active true?
        - nav_status:   str  — 'navigating' | 'arrived' | 'stuck' | 'idle'
        - waypoints_remaining: int — unvisited waypoints in current plan
        - player_moving: bool — is the player currently in motion this tick
    """
    pos_check = {}
    for _ in range(max_checks):
        await page.wait_for_timeout(1000)
        pos_check = await page.evaluate("""(npcPos) => {
            const p = window.game && window.game.player;
            if (!p) return { px: 0, py: 0, manhattan: 999 };
            const manhattan = Math.abs(p.gridX - npcPos.x) + Math.abs(p.gridY - npcPos.y);
            const nav = window.__navState || {};
            const waypoints = Array.isArray(nav.waypoints) ? nav.waypoints.length : 0;
            const current = typeof nav.currentWP === 'number' ? nav.currentWP : 0;
            return {
                px: p.gridX, py: p.gridY, manhattan: manhattan,
                nav_active: !!nav.active,
                nav_status: nav.status || 'idle',
                waypoints_remaining: Math.max(0, waypoints - current),
                player_moving: !!(p.moving || p.hasPath && p.hasPath()),
            };
        }""", npc_pos)
        if pos_check.get("manhattan", 999) < 2:
            return True, pos_check
    return False, pos_check
