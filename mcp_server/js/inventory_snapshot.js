// inventory_snapshot.js — Unified inventory snapshot used across multiple tools.
// Replaces 8+ copy-pasted variants in mcp_game_server.py.
// Called as: page.evaluate(INVENTORY_SNAPSHOT)
() => {
    try {
        const inv = window.game.menu.getInventory();
        const items = {};
        if (inv && inv.getElement) {
            for (let i = 0; i < 25; i++) {
                const el = inv.getElement(i);
                if (!el || !el.dataset?.key || inv.isEmpty(el)) continue;
                const k = el.dataset.key;
                items[k] = (items[k] || 0) + (el.count || parseInt(el.dataset?.count || '0') || 1);
            }
        }
        return items;
    } catch(e) { return {}; }
}
