/**
 * state_extractor.js — Injected into the Kaetram browser page during login
 * via page.addScriptTag({ path: '.../state_extractor.js' }).
 *
 * Exposes on `window`:
 *   window.__extractGameState()  — returns full game state object (call on demand)
 *   window.__latestGameState     — auto-updated every 500ms (read-only cache)
 *   window.__kaetramState        — persistent combat/XP event log
 *
 * The agent's observe step is just 2 lines:
 *   await page.screenshot({ path: '.../screenshot.png', type: 'png' });
 *   return await page.evaluate(() => JSON.stringify(window.__latestGameState));
 */

(function () {
  // Guard against double-injection
  if (window.__extractGameState) return;

  // ── Dynamic canvas metrics (computed per extraction) ──
  function getCanvasMetrics() {
    const canvas = document.getElementById('canvas') || document.querySelector('canvas');
    if (!canvas) return { CX: 408, CY: 264, TILE_PX: 48, rect: null };
    const rect = canvas.getBoundingClientRect();
    const CX = rect.left + rect.width / 2;
    const CY = rect.top + rect.height / 2;
    // Get actual tile size from the game renderer/camera
    const game = window.game;
    let TILE_PX = 48; // safe default for ~816x528 canvas
    try {
      if (game && game.renderer) TILE_PX = game.renderer.actualTileSize || TILE_PX;
      else if (game && game.camera) TILE_PX = (game.camera.tileSize || 16) * (game.camera.zoomFactor || 3);
    } catch (e) {}
    return { CX, CY, TILE_PX, rect };
  }

  // ── Persistent state for combat/XP hooks ──
  window.__kaetramState = {
    lastCombat: null,
    lastXpEvent: null,
    combatLog: [],
    xpLog: [],
  };

  // ── Main extraction function ──
  window.__extractGameState = function () {
    const game = window.game;
    if (!game || !game.player || !game.entities) {
      return {
        error: 'Game not loaded',
        timestamp: Date.now() / 1000,
        nearby_entities: [],
        player_position: null,
        nearest_mob: null,
        current_target: null,
        player_stats: null,
        player_count_nearby: 0,
        last_combat: null,
        last_xp_event: null,
        quests: [],
        achievements: [],
        inventory: [],
      };
    }

    const player = game.player;
    const px = player.gridX, py = player.gridY;
    const { CX, CY, TILE_PX, rect } = getCanvasMetrics();
    const VW = rect ? rect.width : 816;
    const VH = rect ? rect.height : 528;

    // ── Collect entities ──
    const allEnts = game.entities.entities || {};
    const entities = [];
    let playerCount = 0;

    for (const [inst, ent] of Object.entries(allEnts)) {
      if (inst === player.instance) continue;
      if (ent.type === 6 || ent.type === 7) continue; // projectile/effect

      const dx = ent.gridX - px, dy = ent.gridY - py;
      const dist = Math.abs(dx) + Math.abs(dy);
      const sx = CX + dx * TILE_PX, sy = CY + dy * TILE_PX;
      const canvasLeft = rect ? rect.left : 0;
      const canvasTop = rect ? rect.top : 0;
      const onScreen = sx >= canvasLeft && sx <= canvasLeft + VW && sy >= canvasTop && sy <= canvasTop + VH;

      const e = {
        id: inst, type: ent.type, name: ent.name || '',
        x: ent.gridX, y: ent.gridY,
        hp: ent.hitPoints || 0, max_hp: ent.maxHitPoints || 0,
        has_achievement: !!ent.exclamation, quest_npc: !!ent.blueExclamation,
        distance: dist,
      };
      if (onScreen) {
        e.click_x = Math.round(sx);
        e.click_y = Math.round(sy);
        e.on_screen = true;
      } else {
        e.on_screen = false;
      }

      if (ent.type === 0) playerCount++;
      entities.push(e);
    }
    entities.sort((a, b) => a.distance - b.distance);

    // ── Nearest alive mob ──
    let nearestMob = null;
    for (const e of entities) {
      if (e.type === 3 && e.hp > 0) {
        nearestMob = {
          name: e.name, id: e.id, distance: e.distance,
          click_x: e.click_x || null, click_y: e.click_y || null,
          on_screen: e.on_screen, hp: e.hp, max_hp: e.max_hp,
        };
        break;
      }
    }

    // ── Current target ──
    let currentTarget = null;
    if (player.target) {
      const t = player.target;
      const tdx = t.gridX - px, tdy = t.gridY - py;
      const tsx = CX + tdx * TILE_PX, tsy = CY + tdy * TILE_PX;
      const cLeft = rect ? rect.left : 0;
      const cTop = rect ? rect.top : 0;
      const tOn = tsx >= cLeft && tsx <= cLeft + VW && tsy >= cTop && tsy <= cTop + VH;
      currentTarget = {
        name: t.name || '', id: t.instance, type: t.type,
        x: t.gridX, y: t.gridY,
        hp: t.hitPoints || 0, max_hp: t.maxHitPoints || 0,
        distance: Math.abs(tdx) + Math.abs(tdy),
        click_x: tOn ? Math.round(tsx) : null, click_y: tOn ? Math.round(tsy) : null,
        on_screen: tOn,
      };
    }

    // ── Quests ──
    const quests = [];
    if (player.quests) {
      for (const [key, q] of Object.entries(player.quests)) {
        if (key === 'tutorial') continue;
        quests.push({
          key, name: q.name, description: (q.description || '').split('|')[0],
          stage: q.stage, stageCount: q.stageCount,
          started: q.isStarted(), finished: q.isFinished(),
        });
      }
    }

    // ── Achievements ──
    const achievements = [];
    if (player.achievements) {
      for (const [key, a] of Object.entries(player.achievements)) {
        if (a.secret && !a.isStarted()) continue;
        achievements.push({
          key, name: a.name,
          stage: a.stage, stageCount: a.stageCount,
          started: a.isStarted(), finished: a.isFinished(),
        });
      }
    }

    // ── Inventory (non-empty slots only) ──
    const inventory = [];
    try {
      const inv = game.menu.getInventory();
      for (let i = 0; i < 25; i++) {
        const el = inv.getElement(i);
        if (!el || inv.isEmpty(el)) continue;
        inventory.push({
          slot: i, key: el.dataset?.key || '',
          name: el.name || '', count: el.count || parseInt(el.dataset?.count || '0') || 0,
          edible: !!el.edible, equippable: !!el.equippable,
        });
      }
    } catch (e) { /* inventory not yet loaded */ }

    return {
      timestamp: Date.now() / 1000,
      nearby_entities: entities,
      last_combat: window.__kaetramState.lastCombat,
      last_xp_event: window.__kaetramState.lastXpEvent,
      player_count_nearby: playerCount,
      player_position: { x: px, y: py },
      nearest_mob: nearestMob,
      current_target: currentTarget,
      player_stats: {
        hp: player.hitPoints || 0, max_hp: player.maxHitPoints || 0,
        mana: player.mana || 0, max_mana: player.maxMana || 0,
        level: player.level || 1, experience: player.experience || 0,
      },
      quests: quests,
      achievements: achievements,
      inventory: inventory,
    };
  };

  // ── Auto-cache: update window.__latestGameState every 500ms ──
  window.__latestGameState = window.__extractGameState();
  setInterval(() => {
    window.__latestGameState = window.__extractGameState();
  }, 500);

  // ── Install combat/XP hooks ──
  function installHooks() {
    const game = window.game;
    if (!game || !game.info) return false;

    const origCreate = game.info.create.bind(game.info);
    game.info.create = function (type, damage, x, y, isPlayer, ...rest) {
      if (damage !== undefined && damage !== 0) {
        window.__kaetramState.lastCombat = {
          attacker: isPlayer ? 'target' : (game.player?.name || 'ClaudeBot'),
          target: isPlayer ? (game.player?.name || 'ClaudeBot') : 'target',
          damage: damage,
        };
        window.__kaetramState.combatLog.push({
          damage, isPlayer, timestamp: Date.now() / 1000,
        });
        if (window.__kaetramState.combatLog.length > 20)
          window.__kaetramState.combatLog.shift();
      }
      return origCreate(type, damage, x, y, isPlayer, ...rest);
    };

    let lastXp = game.player?.experience || 0;
    let lastLevel = game.player?.level || 1;
    setInterval(() => {
      const xp = game.player?.experience || 0;
      const lvl = game.player?.level || 1;
      if (xp > lastXp) {
        const event = {
          amount: xp - lastXp, skill: 'experience',
          level: lvl !== lastLevel ? lvl : null,
        };
        window.__kaetramState.lastXpEvent = event;
        window.__kaetramState.xpLog.push({ ...event, timestamp: Date.now() / 1000 });
        if (window.__kaetramState.xpLog.length > 20) window.__kaetramState.xpLog.shift();
      }
      lastXp = xp;
      lastLevel = lvl;
    }, 1000);

    return true;
  }

  // Install hooks now, retry if game not ready
  if (!installHooks()) {
    const retry = setInterval(() => {
      if (installHooks()) clearInterval(retry);
    }, 500);
  }
})();
