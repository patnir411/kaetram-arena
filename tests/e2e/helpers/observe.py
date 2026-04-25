"""Observe game state, either from the running browser or from Mongo via the REST helper.

Two observers:

- `observe_via_browser(page)` — rich, real-time snapshot from `window.game`.
  Preferred for state-delta assertions since packets fire before the server
  commits to DB.

- `observe_via_db(username)` — pulls every collection for a user via the E2E
  helper at `:19300`. Useful for authoritative state after a save boundary
  (login, respawn, logout, save tick).

`observe_via_browser` returns a flat dict with the following top-level keys:

  player      — pose/hp/mana/target + attack-style/combat flags
  inventory   — {slotKey: count} collapsed across stacks
  inventorySlots — raw per-index slot info
  equipment   — {slotName: {key, count}}
  skills      — {skillName: {level, experience, percentage}}
  quests      — {questKey: {stage, subStage, stageCount, started, finished}}
  achievements — {achievementKey: {stage, stageCount}}
  movement    — {moving, following, hasPath, hasNextStep, destination, path}
  combat      — {target, attackers, inCombat, attackStyle, stunned, effects}
  entities    — {mobs: [...], npcs: [...], items: [...]}
  menu        — {active, dialogueActive, storeOpen, bankOpen, craftingOpen}
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

_SNAPSHOT_JS = r"""
() => {
    const game = window.game;
    if (!game || !game.player) return { error: 'game-not-loaded' };

    const player = game.player;
    const entities = (game.entities && game.entities.entities) || {};
    const menu = game.menu || {};

    const entityName = (e) => e && (e.name || e.key) || 'Unknown';
    const distance = (e) =>
        Math.abs((e.gridX || 0) - (player.gridX || 0)) +
        Math.abs((e.gridY || 0) - (player.gridY || 0));

    // Inventory via DOM container. Same shape as the retired Cypress helper.
    const inventorySlots = [];
    const inventoryCounts = {};
    try {
        const inv = menu.getInventory && menu.getInventory();
        if (inv && inv.getElement) {
            for (let i = 0; i < 25; i++) {
                const el = inv.getElement(i);
                const key = (el && el.dataset && el.dataset.key) || '';
                const count = el ? (el.count || parseInt((el.dataset && el.dataset.count) || '0') || (key ? 1 : 0)) : 0;
                inventorySlots.push({ index: i, key, count });
                if (key) inventoryCounts[key] = (inventoryCounts[key] || 0) + count;
            }
        }
    } catch (_e) {}

    // Equipment map keyed by slot name.
    const equipment = {};
    try {
        const equipmentEnum = {
            0: 'helmet', 1: 'pendant', 2: 'arrows', 3: 'chestplate',
            4: 'weapon', 5: 'shield', 6: 'ring', 7: 'armourSkin',
            8: 'weaponSkin', 9: 'legplates', 10: 'cape', 11: 'boots'
        };
        for (const [type, name] of Object.entries(equipmentEnum)) {
            const e = player.equipments && player.equipments[type];
            if (e && e.key) {
                equipment[name] = { key: e.key, count: e.count || 1 };
            }
        }
    } catch (_e) {}

    // Skills.
    const skills = {};
    try {
        for (const [type, skill] of Object.entries(player.skills || {})) {
            if (skill && skill.name) {
                skills[skill.name] = {
                    type: Number(type),
                    level: skill.level || 1,
                    experience: skill.experience || 0,
                    percentage: skill.percentage || 0,
                };
            }
        }
    } catch (_e) {}

    const quests = {};
    try {
        for (const [key, q] of Object.entries(player.quests || {})) {
            const stage = q.stage || 0;
            const stageCount = q.stageCount || 1;
            quests[key] = {
                stage,
                subStage: q.subStage || 0,
                stageCount,
                started: stage > 0,
                finished: stage >= stageCount,
            };
        }
    } catch (_e) {}

    const achievements = {};
    try {
        for (const [key, a] of Object.entries(player.achievements || {})) {
            achievements[key] = { stage: a.stage || 0, stageCount: a.stageCount || 1 };
        }
    } catch (_e) {}

    const mobs = [];
    const npcs = [];
    const items = [];
    try {
        for (const [instance, entity] of Object.entries(entities)) {
            const summary = {
                instance,
                name: entityName(entity),
                x: entity.gridX,
                y: entity.gridY,
                distance: distance(entity),
                hitPoints: entity.hitPoints || 0,
                maxHitPoints: entity.maxHitPoints || 0,
            };
            if (entity.type === 3) mobs.push(summary);
            else if (entity.type === 1) npcs.push(summary);
            else if (entity.type === 2) items.push(summary);
        }
        mobs.sort((a, b) => a.distance - b.distance);
        npcs.sort((a, b) => a.distance - b.distance);
        items.sort((a, b) => a.distance - b.distance);
    } catch (_e) {}

    // Dialogue / menu surface (best effort; not all menus expose is-open state).
    let dialogueActive = false;
    try {
        const bubble = game.bubble;
        if (bubble && bubble.bubbles) dialogueActive = Object.keys(bubble.bubbles).length > 0;
    } catch (_e) {}
    const isMenuOpen = (m) => {
        try {
            if (!m) return false;
            if (typeof m.isVisible === 'function') return !!m.isVisible();
            return !!(m.body && (m.body.style.display || '').toLowerCase() !== 'none');
        } catch (_e) { return false; }
    };

    const menuFlags = {
        storeOpen: isMenuOpen(menu.getStore && menu.getStore()),
        bankOpen: isMenuOpen(menu.getBank && menu.getBank()),
        craftingOpen: isMenuOpen(menu.getCrafting && menu.getCrafting()),
        warpOpen: isMenuOpen(menu.getWarp && menu.getWarp()),
    };

    const getAttackStyle = () => {
        try { return typeof player.getAttackStyle === 'function' ? player.getAttackStyle() : null; }
        catch (_e) { return null; }
    };
    const hasAttackers = () => {
        try { return typeof player.hasAttackers === 'function' ? !!player.hasAttackers() : Object.keys(player.attackers || {}).length > 0; }
        catch (_e) { return false; }
    };

    return {
        player: {
            name: player.name,
            instance: player.instance,
            x: player.gridX,
            y: player.gridY,
            hitPoints: player.hitPoints || 0,
            maxHitPoints: player.maxHitPoints || 0,
            mana: player.mana || 0,
            maxMana: player.maxMana || 0,
            orientation: player.orientation,
            level: player.level || 0,
        },
        movement: {
            moving: !!player.moving,
            following: !!player.following,
            hasPath: (() => { try { return typeof player.hasPath === 'function' ? !!player.hasPath() : !!player.path; } catch (_e) { return false; } })(),
            hasNextStep: (() => { try { return !!(player.path && typeof player.hasNextStep === 'function' && player.hasNextStep()); } catch (_e) { return false; } })(),
            destination: player.destination || null,
            path: player.path || null,
        },
        combat: {
            target: player.target ? { instance: player.target.instance, name: entityName(player.target), hitPoints: player.target.hitPoints || 0 } : null,
            attackers: Object.keys(player.attackers || {}).length,
            inCombat: hasAttackers(),
            attackStyle: getAttackStyle(),
            stunned: !!player.stunned,
            effects: Array.from(player.statusEffects || []),
        },
        inventory: inventoryCounts,
        inventorySlots,
        equipment,
        skills,
        quests,
        achievements,
        entities: { mobs, npcs, items },
        menu: { dialogueActive, ...menuFlags },
    };
}
"""


async def observe_via_browser(page: Page) -> dict[str, Any]:
    """Evaluate the snapshot JS in the page and return the decoded dict."""
    return await page.evaluate(_SNAPSHOT_JS)
