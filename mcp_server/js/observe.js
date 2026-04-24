// observe.js — Unified game observation for LLM agent decision-making.
// Called as: page.evaluate(OBSERVE_SCRIPT)
() => {
    if (typeof window.__extractGameState !== 'function') {
        return 'ERROR: State extractor not loaded. Session is not ready yet.';
    }
    const gs = window.__extractGameState();
    const sc = window.__stuckCheck ? window.__stuckCheck() : {};

    if (gs.error) {
        return 'ERROR: ' + gs.error + ' (game may not be ready yet — retry observe)';
    }

    const ps = gs.player_stats || {};
    const ents = gs.nearby_entities || [];
    const quests = gs.quests || [];
    const px = (gs.player_position || {}).x || 0;
    const py = (gs.player_position || {}).y || 0;

    // ── Direction helper ──────────────────────────────────────────────
    function dir(dx, dy) {
        if (dx === 0 && dy === 0) return 'HERE';
        let d = '';
        if (dy < 0) d += 'N';
        if (dy > 0) d += 'S';
        if (dx > 0) d += 'E';
        if (dx < 0) d += 'W';
        return d;
    }

    // ── Categorize entities ───────────────────────────────────────────
    // Caps: 10 NPCs, 10 mobs, 10 resources, 5 ground items
    const npcs = [], mobs = [], resources = [], groundItems = [];
    const resourceKinds = {10: 'tree', 11: 'rock', 12: 'bush', 13: 'fishspot'};

    for (const e of ents) {
        const dx = e.x - px, dy = e.y - py;
        const d = dir(dx, dy);

        if (e.type === 1) {
            // NPC — name, position, direction, quest marker
            if (npcs.length < 10) {
                npcs.push({
                    name: e.name, x: e.x, y: e.y,
                    dist: e.distance, dir: d,
                    quest: !!e.quest_npc,
                });
            }
        } else if (e.type === 3) {
            // Mob — only alive, with HP for combat decisions
            if (mobs.length < 10 && e.hp > 0) {
                mobs.push({
                    name: e.name, x: e.x, y: e.y,
                    dist: e.distance, dir: d,
                    hp: e.hp, max_hp: e.max_hp,
                    reachable: !!e.reachable,
                });
            }
        } else if (e.type >= 10 && e.type <= 13) {
            // Resource — tree(10), rock(11), bush(12), fishspot(13)
            if (resources.length < 10) {
                resources.push({
                    name: e.name, x: e.x, y: e.y,
                    dist: e.distance, dir: d,
                    kind: resourceKinds[e.type] || 'resource',
                    ready: !e.exhausted,
                });
            }
        } else if (e.type === 2 || e.type === 4 || e.type === 8) {
            // Ground item(2), chest(4), lootbag(8)
            if (groundItems.length < 5) {
                groundItems.push({
                    name: e.name || '?', x: e.x, y: e.y,
                    dist: e.distance, dir: d,
                });
            }
        }
    }

    // ── Stack inventory by key ────────────────────────────────────────
    const rawInv = gs.inventory || [];
    const stackMap = {};
    for (const item of rawInv) {
        if (!item.key) continue;
        if (stackMap[item.key]) {
            stackMap[item.key].count += (item.count || 1);
        } else {
            stackMap[item.key] = {
                key: item.key,
                name: item.name || item.key,
                count: item.count || 1,
                slot: item.slot,
                edible: !!item.edible,
                equippable: !!item.equippable,
            };
        }
    }
    const inventory = Object.values(stackMap);

    // ── Quests ────────────────────────────────────────────────────────
    const activeQuests = quests
        .filter(q => q.started && !q.finished)
        .map(q => ({
            name: q.name, stage: q.stage, stage_count: q.stageCount,
            description: (q.description || '').slice(0, 200),
        }));
    const finishedQuests = quests
        .filter(q => q.finished)
        .map(q => ({ name: q.name }));

    // ── Combat target ─────────────────────────────────────────────────
    let combat = null;
    if (gs.current_target) {
        const ct = gs.current_target;
        combat = {
            target: ct.name,
            target_hp: ct.hp + '/' + ct.max_hp,
            dist: ct.distance,
        };
    }

    // ── Events (recent chat, combat, XP, NPC dialogue) ────────────────
    const events = [];
    const uiState = gs.ui_state || {};
    if (uiState.npc_dialogue) {
        events.push({type: 'npc', msg: uiState.npc_dialogue});
    }
    if (Array.isArray(uiState.recent_chat)) {
        for (const chat of uiState.recent_chat.slice(0, 3)) {
            events.push({type: 'chat', msg: chat.message, age: chat.age_seconds});
        }
    }
    if (gs.last_combat) {
        events.push({type: 'combat', data: gs.last_combat});
    }
    if (gs.last_xp_event) {
        events.push({type: 'xp', data: gs.last_xp_event});
    }

    // ── Build unified view ────────────────────────────────────────────
    const view = {
        // Player
        pos: gs.player_position,
        stats: { hp: ps.hp, max_hp: ps.max_hp, level: ps.level, xp: ps.experience },
        equipment: gs.equipment || {},
        skills: gs.skills || {},

        // Status
        status: {
            dead: uiState.is_dead || false,
            stuck: sc.stuck || false,
            stuck_suggestion: sc.suggestion || null,
            nav: (gs.navigation && gs.navigation.active) ? gs.navigation.status : 'idle',
            indoors: uiState.is_indoors || false,
            combat: combat,
        },

        // Nearby — categorized, capped, with direction
        nearby: {
            npcs: npcs,
            mobs: mobs,
            resources: resources,
            ground_items: groundItems,
        },

        // Inventory — stacked by key
        inventory: inventory,

        // Quests
        active_quests: activeQuests,
        finished_quests: finishedQuests,

        // Events (omit if empty to save tokens)
        events: events.length > 0 ? events : undefined,

        // Backward compat — tests check these at top level
        is_dead: uiState.is_dead || false,
        indoors: uiState.is_indoors || false,
    };

    // ── ASCII map ─────────────────────────────────────────────────────
    const am = window.__generateAsciiMap();
    const asciiText = (am && !am.error) ? (am.ascii + '\n\n' + am.legendText) : '';

    return JSON.stringify(view)
         + '\n\nASCII_MAP:\n' + asciiText
         + '\n\nSTUCK_CHECK:\n' + JSON.stringify(sc);
}
