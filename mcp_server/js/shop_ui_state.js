// shop_ui_state.js — Extracted from mcp_game_server.py _interaction_ui_state_script().
// Called as: page.evaluate(SHOP_UI_STATE)
() => {
    try {
        const game = window.game;
        const menu = game && game.menu;
        const store = menu && menu.store;

        const bubbles = document.querySelectorAll('.bubble');
        let bubbleText = null;
        for (const b of bubbles) {
            const t = (b.textContent || '').trim();
            if (t) { bubbleText = t.slice(0, 200); break; }
        }

        let recentChat = null;
        const chatLog = (window.__kaetramState || {}).chatLog || [];
        if (chatLog.length > 0) {
            const last = chatLog[chatLog.length - 1];
            if (last && (Date.now() / 1000 - (last.time || 0)) < 3) {
                recentChat = last.text;
            }
        }

        const questBtn = document.getElementById('quest-button');
        const questPanel = document.getElementById('quest');
        let questPanelVisible = false;
        let questBtnText = null;
        if (questPanel) {
            const s = window.getComputedStyle(questPanel);
            questPanelVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        if (questBtn) questBtnText = (questBtn.textContent || '').trim().slice(0, 80);

        const containerVisible = !!(store && store.container && store.container.visible === true);
        const storeContainerVisible = !!(store && store.storeContainer && store.storeContainer.visible === true);
        const buyDialogVisible = !!(store && store.buyDialog && store.buyDialog.visible === true);
        const inventoryVisible = !!(store && store.inventory && store.inventory.visible === true);
        const visible = !!(store && (
            store.visible === true ||
            containerVisible ||
            storeContainerVisible ||
            buyDialogVisible ||
            inventoryVisible ||
            (typeof store.isVisible === 'function' && store.isVisible())
        ));

        const pickText = (value) => {
            if (value === null || value === undefined) return null;
            if (typeof value === 'string') return value.trim() || null;
            if (typeof value === 'number') return String(value);
            return null;
        };

        const summarizeEntry = (entry, idx, source) => {
            if (!entry) return null;
            const fields = [
                entry.key, entry.name, entry.string, entry.label, entry.text,
                entry.title, entry.caption, entry.containerName,
            ];
            let text = null;
            for (const field of fields) {
                const picked = pickText(field);
                if (picked) { text = picked; break; }
            }
            const count = entry.count ?? entry.amount ?? entry.quantity ?? entry.buyCount ?? null;
            const price = entry.price ?? entry.cost ?? entry.buyPrice ?? entry.value ?? null;
            if (!text && count === null && price === null && !entry.key && !entry.name) {
                return null;
            }
            return {
                index: idx,
                source,
                key: entry.key ?? null,
                name: entry.name ?? null,
                text,
                count,
                price,
            };
        };

        const itemEntries = [];
        const appendEntries = (collection, source) => {
            if (!collection) return;
            if (Array.isArray(collection)) {
                for (let i = 0; i < collection.length && itemEntries.length < 12; i++) {
                    const entry = summarizeEntry(collection[i], i, source);
                    if (entry) itemEntries.push(entry);
                }
                return;
            }
            if (Array.isArray(collection.items)) {
                for (let i = 0; i < collection.items.length && itemEntries.length < 12; i++) {
                    const entry = summarizeEntry(collection.items[i], i, `${source}.items`);
                    if (entry) itemEntries.push(entry);
                }
            }
            if (Array.isArray(collection.buttons)) {
                for (let i = 0; i < collection.buttons.length && itemEntries.length < 12; i++) {
                    const entry = summarizeEntry(collection.buttons[i], i, `${source}.buttons`);
                    if (entry) itemEntries.push(entry);
                }
            }
        };

        const summarizeDom = (selector, limit = 12) => {
            const nodes = Array.from(document.querySelectorAll(selector));
            return nodes
                .map((node, idx) => {
                    const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
                    if (!text) return null;
                    const style = window.getComputedStyle(node);
                    const visible = style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                    return {
                        index: idx,
                        text: text.slice(0, 120),
                        className: typeof node.className === 'string' ? node.className.slice(0, 120) : '',
                        id: node.id || null,
                        visible,
                    };
                })
                .filter(Boolean)
                .slice(0, limit);
        };

        const domStoreish = summarizeDom(
            '#store, #shop, .store, .shop, .menu, .menu-button, .slot, .inventory-slot, .list, .list-button'
        );
        const anyVisibleDomStoreish = domStoreish.some((entry) => entry && entry.visible);
        const domStoreText = domStoreish.map((entry) => entry && entry.text).filter(Boolean).join(' | ').slice(0, 400);

        const keys = [];
        let itemCount = null;
        let storeListCount = null;
        let inventoryListCount = null;
        let selectedBuyIndex = null;
        if (store) {
            for (const k of Object.keys(store)) {
                if (typeof store[k] !== 'function') keys.push(k);
            }
            if (Array.isArray(store.items)) itemCount = store.items.length;
            else if (store.inventory && Array.isArray(store.inventory.items)) itemCount = store.inventory.items.length;
            else if (Array.isArray(store.slots)) itemCount = store.slots.length;
            if (store.storeList && Array.isArray(store.storeList)) storeListCount = store.storeList.length;
            else if (store.storeList && Array.isArray(store.storeList.items)) storeListCount = store.storeList.items.length;
            else if (store.storeList && Array.isArray(store.storeList.buttons)) storeListCount = store.storeList.buttons.length;
            if (store.inventoryList && Array.isArray(store.inventoryList)) inventoryListCount = store.inventoryList.length;
            else if (store.inventoryList && Array.isArray(store.inventoryList.items)) inventoryListCount = store.inventoryList.items.length;
            else if (store.inventoryList && Array.isArray(store.inventoryList.buttons)) inventoryListCount = store.inventoryList.buttons.length;
            selectedBuyIndex = store.selectedBuyIndex ?? null;
            appendEntries(store.items, 'store.items');
            appendEntries(store.storeList, 'store.storeList');
            appendEntries(store.inventory?.items, 'store.inventory.items');
        }

        const hasEntries = (
            (itemCount !== null && itemCount > 0) ||
            (storeListCount !== null && storeListCount > 0) ||
            itemEntries.length > 0
        );
        const shopReady = !!(visible && hasEntries);

        let uiType = 'none';
        if (shopReady || visible || store) uiType = 'shop';
        else if (questPanelVisible || (questBtn && questBtn.offsetParent)) uiType = 'quest';
        else if (bubbleText || recentChat) uiType = 'dialogue';

        return {
            type: uiType,
            bubble_text: bubbleText,
            chat_text: recentChat,
            quest_panel: questPanelVisible || !!(questBtn && questBtn.offsetParent),
            quest_btn_text: questBtnText,
            shop: {
                ready: shopReady,
                hasEntries,
                visible,
                containerVisible,
                storeContainerVisible,
                buyDialogVisible,
                inventoryVisible,
                keys,
                itemCount,
                storeListCount,
                inventoryListCount,
                selectedBuyIndex,
                store_key: store && (store.key || store.storeKey || store.activeKey || null),
                npc_key: store && (store.npcKey || store.npc_key || null),
                has_store: !!store,
                item_entries: itemEntries,
                debug: {
                    dom_store_text: domStoreText,
                    any_visible_dom_storeish: anyVisibleDomStoreish,
                },
            },
        };
    } catch (e) {
        return { type: 'error', error: String(e) };
    }
}
