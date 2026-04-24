// nudge_store.js — Extracted from mcp_game_server.py _nudge_hidden_store_open().
// Called as: page.evaluate(NUDGE_STORE)
() => {
    try {
        const store = window.game && window.game.menu && window.game.menu.store;
        if (!store) return { nudged: false, reason: 'no_store' };

        const actions = [];
        const tryCall = (label, fn) => {
            try {
                if (typeof fn === 'function') {
                    fn();
                    actions.push(label);
                    return true;
                }
            } catch (e) {
                actions.push(label + ':error:' + String(e));
            }
            return false;
        };

        tryCall('showCallback', store.showCallback);
        tryCall('container.show', store.container && store.container.show);
        tryCall('storeContainer.show', store.storeContainer && store.storeContainer.show);
        tryCall('inventory.show', store.inventory && store.inventory.show);
        tryCall('buyDialog.hide', store.buyDialog && store.buyDialog.hide);

        if (store.container && typeof store.container.visible === 'boolean') {
            store.container.visible = true;
            actions.push('container.visible=true');
        }
        if (store.storeContainer && typeof store.storeContainer.visible === 'boolean') {
            store.storeContainer.visible = true;
            actions.push('storeContainer.visible=true');
        }
        if (store.inventory && typeof store.inventory.visible === 'boolean') {
            store.inventory.visible = true;
            actions.push('inventory.visible=true');
        }
        if (typeof store.visible === 'boolean') {
            store.visible = true;
            actions.push('store.visible=true');
        }

        return { nudged: actions.length > 0, actions };
    } catch (e) {
        return { nudged: false, error: String(e) };
    }
}
