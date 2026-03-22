/**
 * state_extractor.js — Injected into the Kaetram browser page during login
 * via page.addScriptTag({ path: '.../state_extractor.js' }).
 *
 * Exposes on `window`:
 *   window.__extractGameState()  — returns full game state object (call on demand)
 *   window.__latestGameState     — auto-updated every 500ms (read-only cache)
 *   window.__kaetramState        — persistent combat/XP event log
 *
 * The agent's observe step reads text state only:
 *   const state = await page.evaluate(() => JSON.stringify(window.__latestGameState));
 *   const asciiMap = await page.evaluate(() => window.__latestAsciiMap);
 */

(function () {
  // Guard against double-injection
  if (window.__extractGameState) return;

  // ── Dynamic canvas metrics (computed per extraction) ──
  // IMPORTANT: `document.getElementById('canvas')` returns a <div> wrapper, NOT an actual
  // <canvas> element. Its children are position:absolute so the div has height=0, which
  // would make CY=0 and break all click_y coordinates. Use `#background` (a real canvas).
  function getCanvasMetrics() {
    const canvas = document.getElementById('background') || document.querySelector('canvas');
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
      // Skip: projectile(5/6), effect(7/9), tree(10), rock(11)
      if (ent.type === 5 || ent.type === 6 || ent.type === 7 || ent.type === 9
          || ent.type === 10 || ent.type === 11) continue;

      const dx = ent.gridX - px, dy = ent.gridY - py;
      const dist = Math.abs(dx) + Math.abs(dy);
      const sx = CX + dx * TILE_PX, sy = CY + dy * TILE_PX;
      const canvasLeft = rect ? rect.left : 0;
      const canvasTop = rect ? rect.top : 0;
      const onScreen = sx > canvasLeft + TILE_PX && sx < canvasLeft + VW - TILE_PX &&
                       sy > canvasTop + TILE_PX && sy < canvasTop + VH - TILE_PX;

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

    // Cap: keep all NPCs/players/items/chests, closest 10 mobs, closest 5 harvestables
    const capped = [];
    let mobCount = 0, harvestCount = 0;
    for (const e of entities) {
      if (e.type === 3) { // mob
        if (mobCount < 10) { capped.push(e); mobCount++; }
      } else if (e.type === 12) { // harvestable
        if (harvestCount < 5) { capped.push(e); harvestCount++; }
      } else {
        capped.push(e); // NPC(1), player(0), item(2), chest(4), lootbag(8)
      }
    }

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

    // ── Quests (only started or finished — skip unstarted to save space) ──
    const quests = [];
    try {
      if (player.quests) {
        for (const [key, q] of Object.entries(player.quests)) {
          if (key === 'tutorial') continue;
          // Access stage directly — isStarted()/isFinished() may fail on compiled TS prototypes
          const stage = q.stage || 0;
          const stageCount = q.stageCount || 1;
          const started = stage > 0;
          const finished = stage >= stageCount;
          if (!started && !finished) continue;
          quests.push({
            key, name: q.name || key, description: (q.description || '').split('|')[0],
            stage, stageCount, started, finished,
          });
        }
      }
    } catch (e) {}

    // ── Achievements (only started or finished) ──
    const achievements = [];
    try {
      if (player.achievements) {
        for (const [key, a] of Object.entries(player.achievements)) {
          const stage = a.stage || 0;
          const stageCount = a.stageCount || 1;
          const started = stage > 0;
          const finished = stage >= stageCount;
          if (!started && !finished) continue;
          achievements.push({
            key, name: a.name || key,
            stage, stageCount, started, finished,
          });
        }
      }
    } catch (e) {}

    // ── Inventory (non-empty slots only) ──
    const inventory = [];
    try {
      const inv = game.menu.getInventory();
      if (inv && inv.getElement) {
        for (let i = 0; i < 25; i++) {
          const el = inv.getElement(i);
          if (!el) continue;
          // Check dataset.key (set by setSlot) as primary indicator of a filled slot
          const key = el.dataset?.key || '';
          if (!key || inv.isEmpty(el)) continue;
          inventory.push({
            slot: i, key: key,
            name: el.name || key, count: el.count || parseInt(el.dataset?.count || '0') || 0,
            edible: !!el.edible, equippable: !!el.equippable,
          });
        }
      }
    } catch (e) { /* inventory not yet loaded */ }

    // ── Skills ──
    const skills = {};
    try {
      if (player.skills) {
        for (const [id, skill] of Object.entries(player.skills || {})) {
          if (skill && (skill.level > 1 || skill.experience > 0)) {
            skills[skill.name || id] = { level: skill.level, experience: skill.experience };
          }
        }
      }
    } catch (e) {}

    // ── Equipment ──
    // player.equipments is keyed by numeric Modules.Equipment enum values:
    // 0=Helmet, 1=Pendant, 2=Arrows, 3=Chestplate, 4=Weapon, 5=Shield,
    // 6=Ring, 7=ArmourSkin, 8=WeaponSkin, 9=Legplates, 10=Cape, 11=Boots
    let equipment = {};
    const equipNames = {
      0: 'helmet', 1: 'pendant', 2: 'arrows', 3: 'chestplate',
      4: 'weapon', 5: 'shield', 6: 'ring', 7: 'armour_skin',
      8: 'weapon_skin', 9: 'legplates', 10: 'cape', 11: 'boots',
    };
    try {
      if (player.equipments) {
        for (const [id, item] of Object.entries(player.equipments)) {
          if (item && item.key) {
            const slotName = equipNames[id] || 'slot_' + id;
            equipment[slotName] = { key: item.key, name: item.name || item.key };
          }
        }
      }
    } catch (e) {}

    // ── UI state (replaces screenshot for dialog detection) ──
    let uiState = {};
    try {
      const questBtn = document.getElementById('quest-button');
      uiState.quest_panel_visible = !!(questBtn && questBtn.offsetParent !== null);

      const dialogBubble = document.querySelector('.bubble');
      uiState.npc_dialogue = dialogBubble ? dialogBubble.textContent.trim().slice(0, 200) : null;

      // Death is toggled via body.classList.add/remove('death') (connection.ts:1039, game.ts:313).
      // The #death element is always display:flex with opacity:0 — CSS computed style checks fail.
      // Body class is the single source of truth.
      const isDead = document.body.classList.contains('death');
      uiState.is_dead = isDead;
      uiState.death_overlay_visible = isDead;
      uiState.respawn_button_visible = isDead;

      const chatMsgs = [];
      document.querySelectorAll('#chat-log p').forEach(el => {
        const t = el.textContent.trim();
        if (t) chatMsgs.push(t);
      });
      uiState.recent_chat = chatMsgs.slice(-5);
    } catch (e) {}

    return {
      timestamp: Date.now() / 1000,
      nearby_entities: capped,
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
      skills: skills,
      equipment: equipment,
      quests: quests,
      achievements: achievements,
      inventory: inventory,
      ui_state: uiState,
    };
  };

  // ── NPC interaction helpers ──
  // Talk to an NPC by sending a Target.Talk packet. Player must be adjacent.
  window.__talkToNPC = function(instanceId) {
    const game = window.game;
    if (!game || !game.socket) return { error: 'Game not loaded' };
    const entity = game.entities?.get(instanceId);
    if (!entity) return { error: 'Entity not found: ' + instanceId };
    // Packets.Target = 14 (enum index in packets.ts), Opcodes.Target.Talk = 0
    game.socket.send(14, [0, instanceId, entity.gridX, entity.gridY]);
    return { sent: true, npc: entity.name, instance: instanceId };
  };

  // Accept a quest after dialogue is complete and quest panel is visible.
  window.__acceptQuest = function(questKey) {
    const game = window.game;
    if (!game || !game.socket) return { error: 'Game not loaded' };
    // Packets.Quest = 23 (enum index in packets.ts)
    game.socket.send(23, { key: questKey });
    return { sent: true, quest: questKey };
  };

  // ── ASCII map generator ──
  // Returns a text grid of the visible viewport (~16x12 tiles) with entity legend.
  // Claude reasons about this grid precisely (no pixel guessing) then clicks by reference.
  window.__generateAsciiMap = function () {
    const game = window.game;
    if (!game || !game.player || !game.camera || !game.map) {
      return { error: 'Game not loaded', ascii: '', legend: [], legendText: '', meta: {} };
    }

    const player = game.player;
    const camera = game.camera;
    const map = game.map;
    const { CX, CY, TILE_PX } = getCanvasMetrics();

    const px = player.gridX, py = player.gridY;
    const startX = camera.gridX;
    const startY = camera.gridY;
    const width = camera.gridWidth;
    const height = camera.gridHeight;

    // Build entity lookup: "y,x" -> highest-priority entity
    const entityMap = {};
    const legend = [];
    const targetInst = player.target ? player.target.instance : null;
    const allEnts = game.entities.entities || {};

    for (const [inst, ent] of Object.entries(allEnts)) {
      if (inst === player.instance) continue;
      if (ent.type === 5 || ent.type === 9) continue; // Projectile, Effect

      const ex = ent.gridX, ey = ent.gridY;
      if (ex < startX || ex >= startX + width || ey < startY || ey >= startY + height) continue;

      const isTarget = (inst === targetInst);
      let symbol, priority;

      switch (ent.type) {
        case 0:  symbol = 'P'; priority = 30; break; // Other player
        case 1:  // NPC
          symbol = ent.blueExclamation ? '?' : (ent.exclamation ? '!' : 'N');
          priority = ent.blueExclamation ? 70 : (ent.exclamation ? 65 : 50);
          break;
        case 2:  symbol = '*'; priority = 25; break; // Item
        case 3:  // Mob — first letter of name
          symbol = (ent.name || 'M').charAt(0).toUpperCase();
          priority = 40;
          break;
        case 4:  symbol = '$'; priority = 20; break; // Chest
        case 8:  symbol = '*'; priority = 25; break; // LootBag
        case 10: symbol = '^'; priority = 10; break; // Tree
        case 11: symbol = 'o'; priority = 10; break; // Rock
        default: symbol = '~'; priority = 5; break;
      }

      if (isTarget) { symbol = 'T'; priority = 80; }

      const dist = Math.abs(ex - px) + Math.abs(ey - py);
      const entry = {
        label: '', symbol, name: ent.name || '', type: ent.type, id: inst,
        gridX: ex, gridY: ey,
        hp: ent.hitPoints || 0, max_hp: ent.maxHitPoints || 0,
        distance: dist, isTarget,
        quest_npc: !!ent.blueExclamation, has_achievement: !!ent.exclamation,
        priority,
      };

      legend.push(entry);
      const key = ey + ',' + ex;
      if (!entityMap[key] || priority > entityMap[key].priority) {
        entityMap[key] = entry;
      }
    }

    // Sort legend by distance, assign labels
    legend.sort((a, b) => a.distance - b.distance);
    legend.forEach((e, i) => { e.label = 'E' + i; });
    // Update entityMap labels to match sorted order
    const idToLabel = {};
    for (const e of legend) idToLabel[e.id] = e.label;
    for (const key of Object.keys(entityMap)) {
      entityMap[key].label = idToLabel[entityMap[key].id] || entityMap[key].label;
    }

    // Build ASCII grid
    // Column header: absolute X coords (mod 100, zero-padded)
    let colHeader = '      ';
    for (let c = 0; c < width; c++) {
      colHeader += String((startX + c) % 100).padStart(2, '0') + ' ';
    }

    const rows = [colHeader];
    for (let r = 0; r < height; r++) {
      const absY = startY + r;
      let row = String(absY).padStart(5, ' ') + ' ';
      for (let c = 0; c < width; c++) {
        const absX = startX + c;
        let ch;
        if (absX === px && absY === py) {
          ch = '@';
        } else {
          const key = absY + ',' + absX;
          if (entityMap[key]) {
            ch = entityMap[key].symbol;
          } else if (map.isColliding(absX, absY)) {
            ch = '#';
          } else {
            ch = '.';
          }
        }
        row += ' ' + ch + ' ';
      }
      rows.push(row);
    }

    const ascii = rows.join('\n');

    // Build legend text
    let legendText = 'SYMBOLS: @=you  .=walkable  #=wall  T=target\n';
    legendText += 'ENTITIES:\n';
    for (const e of legend) {
      let line = '  ' + e.label + ' [' + e.symbol + '] ' + e.name;
      if (e.type === 3 && e.max_hp > 0) line += ' (HP:' + e.hp + '/' + e.max_hp + ')';
      line += ' at (' + e.gridX + ',' + e.gridY + ') dist=' + e.distance;
      if (e.isTarget) line += ' *TARGET*';
      if (e.quest_npc) line += ' [QUEST]';
      if (e.has_achievement) line += ' [ACHV]';
      legendText += line + '\n';
    }

    return {
      ascii, legend, legendText,
      meta: {
        viewportStartX: startX, viewportStartY: startY,
        viewportWidth: width, viewportHeight: height,
        playerGridX: px, playerGridY: py,
        tilePx: TILE_PX, canvasCenterX: CX, canvasCenterY: CY,
      },
    };
  };

  // ── Convert absolute grid coords to screen click coords ──
  window.__tileToScreenCoords = function (absGridX, absGridY) {
    const game = window.game;
    if (!game || !game.player) return { error: 'Game not loaded' };
    const { CX, CY, TILE_PX } = getCanvasMetrics();
    const px = game.player.gridX, py = game.player.gridY;
    return {
      click_x: Math.round(CX + (absGridX - px) * TILE_PX),
      click_y: Math.round(CY + (absGridY - py) * TILE_PX),
      gridX: absGridX, gridY: absGridY,
    };
  };

  // ── Click an entity by ASCII map label (e.g. "E0") ──
  window.__clickEntity = function (entityLabel) {
    const mapData = window.__generateAsciiMap();
    if (mapData.error) return { error: mapData.error };
    const entity = mapData.legend.find(function (e) { return e.label === entityLabel; });
    if (!entity) return { error: 'Entity not found: ' + entityLabel };
    const coords = window.__tileToScreenCoords(entity.gridX, entity.gridY);
    if (coords.error) return coords;
    document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
      clientX: coords.click_x, clientY: coords.click_y, bubbles: true, ctrlKey: false,
    }));
    const p = window.game.player;
    return {
      clicked: entityLabel, name: entity.name,
      click_x: coords.click_x, click_y: coords.click_y,
      gridX: entity.gridX, gridY: entity.gridY,
      player_pos: { x: p.gridX, y: p.gridY },
    };
  };

  // ── Click a tile by absolute grid coords (walk there) ──
  window.__clickTile = function (absGridX, absGridY) {
    const coords = window.__tileToScreenCoords(absGridX, absGridY);
    if (coords.error) return coords;
    document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
      clientX: coords.click_x, clientY: coords.click_y, bubbles: true, ctrlKey: false,
    }));
    const p = window.game.player;
    return {
      walked_to: { gridX: absGridX, gridY: absGridY },
      player_pos: { x: p.gridX, y: p.gridY },
      click_x: coords.click_x, click_y: coords.click_y,
    };
  };

  // ── Auto-cache: update game state + ASCII map every 500ms ──
  window.__latestGameState = window.__extractGameState();
  window.__latestAsciiMap = window.__generateAsciiMap();
  setInterval(() => {
    window.__latestGameState = window.__extractGameState();
    window.__latestAsciiMap = window.__generateAsciiMap();
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
