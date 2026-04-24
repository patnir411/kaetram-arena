// buy_packet.js — Extracted from mcp_game_server.py buy_item() tool.
// Called as: page.evaluate(BUY_PACKET, [storeKey, itemIndex, count])
([key, index, count]) => {
    try {
        const player = window.game && window.game.player;
        const menu = window.game && window.game.menu;
        const store = menu && menu.store;
        const before = {
            storeOpen: player && typeof player.storeOpen === 'string' ? player.storeOpen : null,
            selectedBuyIndex: store ? (store.selectedBuyIndex ?? null) : null,
            visible: !!(store && store.visible === true),
            buyDialogVisible: !!(store && store.buyDialog && store.buyDialog.visible === true),
        };

        window.game.socket.send(40, {
            opcode: 2,
            key: key,
            index: index,
            count: count
        });

        const after = {
            storeOpen: player && typeof player.storeOpen === 'string' ? player.storeOpen : null,
            selectedBuyIndex: store ? (store.selectedBuyIndex ?? null) : null,
            visible: !!(store && store.visible === true),
            buyDialogVisible: !!(store && store.buyDialog && store.buyDialog.visible === true),
        };

        return { sent: true, packet: 40, before, after };
    } catch(e) {
        return { error: 'Failed to send buy packet: ' + e.message };
    }
}
