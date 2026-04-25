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

  // ── Door tiles (from world.json areas.doors) ──
  // These tiles are marked as collision in the client map but are walk-on teleports.
  // Pathfinding must treat them as walkable destinations so agents can enter caves/buildings.
  var _doorCoords = new Set([
    "101,851","1053,758","106,160","1082,714","109,426","1090,707","1111,775","1112,775","1115,668","112,183",
    "113,105","1138,800","114,800","1146,681","115,882","123,205","125,733","125,847","126,417","134,569",
    "135,886","136,106","136,170","139,886","140,630","141,624","143,675","144,624","145,630","147,113",
    "154,231","154,675","158,232","160,785","164,113","164,741","169,174","173,98","177,675","179,671",
    "181,157","184,116","187,183","191,840","194,218","197,768","199,149","201,168","220,686","234,662",
    "25,540","260,102","260,229","263,793","265,828","266,824","269,131","269,176","272,846","273,162",
    "273,338","279,113","281,930","283,124","283,296","284,897","285,824","287,178","289,137","289,921",
    "290,349","292,734","301,132","310,264","314,924","315,855","324,931","326,174","326,891","329,898",
    "336,304","337,143","337,344","337,358","342,687","343,119","343,132","345,273","349,847","352,730",
    "355,157","357,687","357,770","358,269","359,123","369,118","369,872","369,893","370,747","372,891",
    "374,866","375,132","375,745","378,740","379,205","379,331","379,388","379,744","383,159","383,929",
    "386,282","386,738","386,745","387,110","389,106","389,170","39,611","391,109","391,891","392,154",
    "393,143","395,157","395,893","396,107","396,131","40,549","406,292","407,874","419,285","421,903",
    "422,920","423,738","425,901","425,905","425,909","426,927","429,903","431,920","433,270","439,887",
    "449,904","453,901","453,904","453,907","455,930","457,904","46,363","472,855","483,275","501,247",
    "509,688","51,415","512,342","513,726","514,769","515,807","518,758","527,252","528,347","531,263",
    "534,811","547,719","554,758","557,681","56,311","562,810","57,600","579,335","587,651","591,805",
    "591,822","591,828","595,776","601,711","607,327","607,756","608,335","625,290","644,602","654,648",
    "665,836","667,685","669,619","67,434","683,844","688,707","688,844","693,836","697,647","700,662",
    "702,554","702,585","702,613","704,632","708,104","724,810","733,105","733,604","741,694","742,636",
    "742,687","745,687","746,724","748,607","748,687","748,733","75,422","752,722","753,688","757,688",
    "757,728","764,640","776,114","776,658","777,598","790,769","792,688","792,696","793,814","794,633",
    "795,768","797,689","798,730","799,816","801,643","801,689","807,688","808,598","810,692","814,792",
    "83,422","848,672","851,750","852,760","852,763","852,765","852,772","855,764","855,780","858,808",
    "859,764","860,785","867,722","867,747","867,751","869,775","870,781","870,784","873,761","874,773",
    "875,757","876,757","878,761","895,718","899,802","90,367","908,712","909,799","920,734","931,806",
    "935,741","939,719","942,708","942,711","944,723","952,722","954,703","955,728","957,722","959,730",
    "960,710","962,728","966,746","971,782","988,700","997,803"
  ]);
  function _isDoor(x, y) { return _doorCoords.has(x + "," + y); }
  // True if tile is walkable OR is a door (teleport on step)
  function _isWalkableOrDoor(map, x, y) {
    if (map.isOutOfBounds(x, y)) return false;
    var walkable = !map.isColliding(x, y);
    var door = _isDoor(x, y);
    if (door) console.log('[debug_npc] _isWalkableOrDoor (' + x + ',' + y + ') IS DOOR');
    return walkable || door;
  }
  // ── Live screenshot hook for dashboard (fires every 1s via console.debug) ──
  if (!window.__liveScreenshotActive) {
    window.__liveScreenshotActive = true;
    setInterval(function () { console.debug('LIVE_SCREENSHOT_TRIGGER'); }, 250);
  }

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

  // ── Persistent state for combat/XP/chat hooks ──
  window.__kaetramState = {
    lastCombat: null,
    lastXpEvent: null,
    combatLog: [],
    xpLog: [],
    chatLog: [],   // { sender, message, timestamp } — rolling buffer, max 50
    overlay: { active: false, since: null },  // tracks indoor/cave overlay state
    lastCombatTime: 0,  // timestamp (ms) of last combat hit (received or dealt)
    warpPending: null,  // { time, preWarpX, preWarpY, targetWarp, confirmed, failed, reason }
  };

  // ── Observe counter + rules reminder ──
  // Incremented each time __extractGameState is called (every 500ms via auto-cache).
  // Every ~90 seconds, a rules reminder is appended to the game state to survive
  // context compaction (the system prompt gets compressed, but fresh tool results don't).
  window.__observeTick = 0;
  window.__lastReminderTick = 0;

  // ── Entity name resolution with fallbacks ──
  // Some entities (e.g., Cactus, certain mobs) have empty ent.name.
  // Fall back to ent.data, ent.key, or instance string to resolve the display name.
  function getEntityName(ent) {
    if (ent.name) return ent.name;
    if (ent.data && ent.data.name) return ent.data.name;
    if (ent.key) {
      // Parse key: "cactus" → "Cactus", "desertscorpion" → "Desertscorpion",
      // "ironogre" → "Iron Ogre" (split on camelCase boundaries)
      return ent.key
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/^./, function (c) { return c.toUpperCase(); });
    }
    return '';
  }

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
      // Skip: projectile(5/6), effect(7/9). Keep tree(10), rock(11) for gathering.
      if (ent.type === 5 || ent.type === 6 || ent.type === 7 || ent.type === 9) continue;

      const dx = ent.gridX - px, dy = ent.gridY - py;
      const dist = Math.abs(dx) + Math.abs(dy);
      const sx = CX + dx * TILE_PX, sy = CY + dy * TILE_PX;
      const canvasLeft = rect ? rect.left : 0;
      const canvasTop = rect ? rect.top : 0;
      const onScreen = sx > canvasLeft + TILE_PX && sx < canvasLeft + VW - TILE_PX &&
                       sy > canvasTop + TILE_PX && sy < canvasTop + VH - TILE_PX;

      // Check if entity's tile is walkable (reachable by pathfinding)
      const tileWalkable = !game.map.isColliding(ent.gridX, ent.gridY)
          || !!_snapToWalkable(ent.gridX, ent.gridY, 3);

      const e = {
        id: inst, type: ent.type, name: getEntityName(ent),
        x: ent.gridX, y: ent.gridY,
        hp: ent.hitPoints || 0, max_hp: ent.maxHitPoints || 0,
        exhausted: [10, 11, 12].includes(ent.type) ? !!ent.exhausted : false,  // use client's ResourceState flag
        has_achievement: !!ent.exclamation, quest_npc: !!ent.blueExclamation,
        distance: dist,
        reachable: tileWalkable,
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

    // Cap: keep all NPCs/players/items/chests, closest 10 mobs, closest 5 harvestables, 3 trees, 3 rocks
    const capped = [];
    let mobCount = 0, harvestCount = 0, treeCount = 0, rockCount = 0;
    for (const e of entities) {
      if (e.type === 3) { // mob
        if (mobCount < 10) { capped.push(e); mobCount++; }
      } else if (e.type === 12) { // harvestable (foraging bush)
        if (harvestCount < 5) { capped.push(e); harvestCount++; }
      } else if (e.type === 10) { // tree
        if (treeCount < 3) { capped.push(e); treeCount++; }
      } else if (e.type === 11) { // rock
        if (rockCount < 3) { capped.push(e); rockCount++; }
      } else {
        capped.push(e); // NPC(1), player(0), item(2), chest(4), lootbag(8), fishspot(13)
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
        name: getEntityName(t), id: t.instance, type: t.type,
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
      // Quest panel detection — check multiple indicators since Kaetram uses
      // CSS transitions/opacity that can fool offsetParent checks.
      const questBtn = document.getElementById('quest-button');
      const questPanel = document.getElementById('quest');
      const questBtnVisible = !!(questBtn && questBtn.offsetParent !== null);
      // Fallback: check if quest panel container is visible via computed style
      let questPanelShown = false;
      if (questPanel) {
        const style = window.getComputedStyle(questPanel);
        questPanelShown = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
      }
      uiState.quest_panel_visible = questBtnVisible || questPanelShown;
      // Also expose the button text so the agent knows if it says "Start Quest" vs "Complete Quest"
      if (questBtn) {
        uiState.quest_button_text = questBtn.textContent.trim().slice(0, 50);
      }

      const dialogBubble = document.querySelector('.bubble');
      uiState.npc_dialogue = dialogBubble ? dialogBubble.textContent.trim().slice(0, 200) : null;

      // Death is toggled via body.classList.add/remove('death') (connection.ts:1039, game.ts:313).
      // The #death element is always display:flex with opacity:0 — CSS computed style checks fail.
      // Body class is the single source of truth.
      const isDead = document.body.classList.contains('death');
      uiState.is_dead = isDead;
      uiState.death_overlay_visible = isDead;
      uiState.respawn_button_visible = isDead;

      // Overlay / indoor detection (captured via hook on game.overlays.update)
      const ovl = window.__kaetramState.overlay;
      uiState.is_indoors = ovl.active;
      if (ovl.active && ovl.since) {
        uiState.indoor_since_seconds = Math.round(Date.now() / 1000 - ovl.since);
      }

      // Return chat messages from the last 30 seconds (captured via hook on chatHandler.add)
      const now = Date.now() / 1000;
      uiState.recent_chat = window.__kaetramState.chatLog
        .filter(m => now - m.timestamp < 30)
        .map(m => ({ sender: m.sender, message: m.message, age_seconds: Math.round(now - m.timestamp) }));
    } catch (e) {}

    var result = {
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
      navigation: window.__navState ? {
        status: window.__navState.status,
        active: window.__navState.active,
        current_wp: window.__navState.currentWP,
        total_wps: window.__navState.waypoints ? window.__navState.waypoints.length : 0,
        target: window.__navState.active ? {x: window.__navState.targetX, y: window.__navState.targetY} : null,
        stuck_reason: window.__navState.status === 'stuck' ? (window.__navState._stuckReason || 'unknown') : null,
        pathfinding_method: window.__navState._pathfindingMethod || null,
      } : null,
      warp_status: window.__kaetramState.warpPending ? {
        pending: !window.__kaetramState.warpPending.confirmed && !window.__kaetramState.warpPending.failed,
        confirmed: !!window.__kaetramState.warpPending.confirmed,
        failed: !!window.__kaetramState.warpPending.failed,
        reason: window.__kaetramState.warpPending.reason || null,
      } : null,
    };

    // Inject rules reminder every ~180 ticks (~90 seconds at 500ms interval)
    window.__observeTick++;
    if (window.__observeTick - window.__lastReminderTick >= 180) {
      window.__lastReminderTick = window.__observeTick;
      result._rules_reminder = '⚠️ RULES REMINDER: (1) Use the EXACT locked OBSERVE template from your system prompt — do NOT write custom state extraction or return summary strings. (2) OBSERVE and ACT are SEPARATE browser_run_code calls — never combine them. (3) ONE action per browser_run_code call — no loops. (4) Max waitForTimeout is 8000ms. (5) If Bronze Axe + Strength>=10, get Iron Axe from Foresting quest ASAP.';
    }

    return result;
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
  // Verifies panel state before sending and checks if quest actually started.
  window.__acceptQuest = function(questKey) {
    var game = window.game;
    if (!game || !game.socket) return { error: 'Game not loaded' };

    // Check if quest panel is actually visible (required for server to accept)
    var questPanel = document.getElementById('quest');
    var panelVisible = false;
    if (questPanel) {
      var style = window.getComputedStyle(questPanel);
      panelVisible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    }

    if (!panelVisible) {
      // Try clicking the quest button if it's visible
      var btn = document.getElementById('quest-button');
      if (btn && btn.offsetParent !== null) {
        btn.click();
        return { sent: false, quest: questKey, warning: 'Quest panel was not visible. Clicked quest-button — observe next turn to check if quest started.' };
      }
      return { sent: false, quest: questKey, error: 'Quest panel not visible — talk to NPC more times to trigger the quest dialogue, or this quest may not be available.' };
    }

    // Panel is visible — click the quest button (this is how the game normally accepts)
    var btn = document.getElementById('quest-button');
    if (btn) btn.click();

    // Also send the raw packet as backup
    // Packets.Quest = 23 (enum index in packets.ts)
    game.socket.send(23, { key: questKey });

    // Check if quest appeared in player's quest list
    var accepted = false;
    if (game.player && game.player.quests && game.player.quests[questKey]) {
      var q = game.player.quests[questKey];
      accepted = (q.stage || 0) > 0;
    }

    return { sent: true, quest: questKey, panel_visible: true, accepted: accepted,
             hint: accepted ? 'Quest accepted!' : 'Packet sent — observe next turn to confirm quest started' };
  };

  // ── Eat food by inventory slot (reliable replacement for selectEdible) ──
  // Auto-closes NPC dialogue/quest panels that block inventory access.
  window.__eatFood = function (slot) {
    var game = window.game;
    if (!game || !game.menu || !game.socket) return { error: 'Game not loaded' };

    // Close any open NPC dialogue/quest panel that blocks inventory access
    var dialogueClosed = false;
    try {
      var closeQuest = document.getElementById('close-quest');
      if (closeQuest && closeQuest.offsetParent !== null) {
        closeQuest.click();
        dialogueClosed = true;
      }
      var bubble = document.querySelector('.bubble');
      if (bubble) {
        // Dismiss dialogue bubble
        try { game.input.chatHandler.clear(); } catch(e2) {}
        dialogueClosed = true;
      }
    } catch(e) {}

    var inv = game.menu.getInventory();
    if (!inv || !inv.getElement) {
      return {
        error: 'Inventory not available' + (dialogueClosed ? ' (tried closing dialogue — retry next turn)' : ''),
        hint: 'Close any open NPC dialogue/quest panel first, then retry next turn',
      };
    }
    var el = inv.getElement(slot);
    if (!el) return { error: 'No item in slot ' + slot };
    if (!el.edible) return { error: 'Item in slot ' + slot + ' is not edible (key: ' + (el.dataset && el.dataset.key || 'unknown') + ')' };
    var itemKey = (el.dataset && el.dataset.key) || 'unknown';
    var hpBefore = game.player ? game.player.hitPoints : 0;
    var maxHp = game.player ? game.player.maxHitPoints : 0;
    if (hpBefore > 0 && maxHp > 0 && hpBefore >= maxHp) {
      return { error: 'HP is full (' + hpBefore + '/' + maxHp + ') — cannot eat. Use drop_item to free inventory space.', slot: slot, item: itemKey };
    }
    // Use the inventory's select method with doubleClick=true which triggers eat for edibles
    inv.select(slot, true);
    // Also try direct packet: Packets.Equipment = 17, Opcodes.Equipment.Eat = 3 (slot index)
    try { game.socket.send(17, [3, slot]); } catch(e) {}
    return { eating: true, slot: slot, item: itemKey, hp_before: hpBefore, dialogue_closed: dialogueClosed };
  };

  // ── Equip item by inventory slot ──
  // Sends Container.Select packet directly — no DOM click fragility.
  window.__equipItem = function (slot) {
    var game = window.game;
    if (!game || !game.menu || !game.socket) return { error: 'Game not loaded' };

    // Close any open dialogue that blocks inventory
    try {
      var closeQuest = document.getElementById('close-quest');
      if (closeQuest && closeQuest.offsetParent !== null) closeQuest.click();
    } catch(e) {}

    var inv = game.menu.getInventory();
    if (!inv || !inv.getElement) return { error: 'Inventory not available' };

    var el = inv.getElement(slot);
    if (!el) return { error: 'No item in slot ' + slot };

    var itemKey = (el.dataset && el.dataset.key) || 'unknown';
    var itemName = el.name || itemKey;

    if (!el.equippable) {
      return { error: 'Item in slot ' + slot + ' (' + itemName + ') is not equippable', slot: slot, item: itemKey };
    }

    // Snapshot equipment before
    var beforeEquip = {};
    try {
      var p = game.player;
      if (p && p.equipments) {
        // p.equipments is an object keyed by Modules.Equipment slot enum,
        // not an array — see __extractGameState() at line ~324.
        for (var slotId in p.equipments) {
          if (!Object.prototype.hasOwnProperty.call(p.equipments, slotId)) continue;
          var eq = p.equipments[slotId];
          beforeEquip[slotId] = eq ? (eq.name || eq.key || 'none') : 'none';
        }
      }
    } catch(e) {}

    // Send Container.Select packet: Packets.Container=21, Opcodes.Container.Select=3, ContainerType.Inventory=1
    game.socket.send(21, { opcode: 3, type: 1, fromIndex: slot, value: 1 });

    return {
      equipping: true, slot: slot, item: itemKey, name: itemName,
      equipment_before: beforeEquip
    };
  };

  window.__inventorySnapshot = function () {
    var game = window.game;
    if (!game || !game.menu) return {};
    try {
      var inv = game.menu.getInventory();
      var items = {};
      if (!inv || !inv.getElement) return items;
      for (var i = 0; i < 25; i++) {
        var el = inv.getElement(i);
        if (!el || !el.dataset || !el.dataset.key || inv.isEmpty(el)) continue;
        var key = el.dataset.key;
        items[key] = (items[key] || 0) + (el.count || parseInt(el.dataset.count || '0', 10) || 1);
      }
      return items;
    } catch (e) {
      return {};
    }
  };

  window.__findInventorySlotByKey = function (itemKey) {
    var game = window.game;
    if (!game || !game.menu) return -1;
    try {
      var inv = game.menu.getInventory();
      if (!inv || !inv.getElement) return -1;
      for (var i = 0; i < 25; i++) {
        var el = inv.getElement(i);
        if (!el || !el.dataset || !el.dataset.key || inv.isEmpty(el)) continue;
        if ((el.dataset.key || '').toLowerCase() === (itemKey || '').toLowerCase()) return i;
      }
    } catch (e) {}
    return -1;
  };

  function _findNearestCursorTile(cursorName) {
    var game = window.game;
    if (!game || !game.player || !game.map) return null;
    var map = game.map;
    var px = game.player.gridX, py = game.player.gridY;
    var wanted = (cursorName || '').toLowerCase();
    var best = null;
    var cursorTiles = map.cursorTiles || {};

    for (var index in cursorTiles) {
      if (!Object.prototype.hasOwnProperty.call(cursorTiles, index)) continue;
      var cursor = (cursorTiles[index] || '').toLowerCase();
      if (cursor !== wanted) continue;
      var coord = map.indexToCoord(parseInt(index, 10));
      var dist = Math.abs(coord.x - px) + Math.abs(coord.y - py);
      if (!best || dist < best.distance) {
        best = { x: coord.x, y: coord.y, distance: dist, cursor: cursor };
      }
    }

    return best;
  }

  window.__debugCursorTiles = function (wantedName) {
    var game = window.game;
    if (!game || !game.player || !game.map) {
      return { error: 'Game not loaded' };
    }
    var cursorTiles = game.map.cursorTiles || {};
    var counts = {};
    var samples = [];
    var wanted = (wantedName || '').toLowerCase();
    var wantedMatches = [];
    var total = 0;

    for (var index in cursorTiles) {
      if (!Object.prototype.hasOwnProperty.call(cursorTiles, index)) continue;
      total += 1;
      var cursor = String(cursorTiles[index] || '').toLowerCase();
      counts[cursor] = (counts[cursor] || 0) + 1;
      if (samples.length < 20) {
        var coord = game.map.indexToCoord(parseInt(index, 10));
        samples.push({ cursor: cursor, x: coord.x, y: coord.y });
      }
      if (wanted && cursor === wanted) {
        var wantedCoord = game.map.indexToCoord(parseInt(index, 10));
        wantedMatches.push({ x: wantedCoord.x, y: wantedCoord.y });
      }
    }

    return {
      player_pos: { x: game.player.gridX, y: game.player.gridY },
      total_cursor_tiles: total,
      distinct_cursors: Object.keys(counts).sort(),
      counts: counts,
      wanted: wanted,
      wanted_matches: wantedMatches,
      sample: samples
    };
  };

  function _currentCraftingSkillName() {
    var game = window.game;
    if (!game || !game.menu || !game.menu.getCrafting) return '';
    var menu = game.menu.getCrafting();
    var type = typeof menu.type === 'number' ? menu.type : -1;
    var names = {
      9: 'cooking',
      10: 'smithing',
      11: 'crafting',
      12: 'chiseling',
      13: 'fletching',
      14: 'smelting',
      18: 'alchemy'
    };
    return names[type] || '';
  }

  window.__getCraftingState = function () {
    var game = window.game;
    if (!game || !game.menu || !game.menu.getCrafting) {
      return { visible: false, skill: '', selected_key: '', selected_name: '' };
    }
    var menu = game.menu.getCrafting();
    var name = document.getElementById('crafting-result-name');
    return {
      visible: !!(menu.isVisible && menu.isVisible()),
      skill: _currentCraftingSkillName(),
      selected_key: menu.selectedKey || '',
      selected_name: name ? (name.textContent || '').trim() : '',
      active_count: menu.craftAmount || 1
    };
  };

  // Close the crafting menu client-side. Useful between chained crafts of the
  // same skill where the server-side state survives but the client interface
  // misses the "fresh" Crafting.Open packet that primes follow-up confirms.
  window.__closeCraftingMenu = function () {
    var game = window.game;
    if (!game || !game.menu || !game.menu.getCrafting) return { closed: false };
    var menu = game.menu.getCrafting();
    if (menu && typeof menu.hide === 'function') menu.hide();
    return { closed: true };
  };

  window.__openProductionInterface = function (skill) {
    var game = window.game;
    if (!game || !game.player || !game.menu) return { error: 'Game not loaded' };
    var normalized = (skill || '').toLowerCase();
    var aliases = {
      cook: 'cooking',
      brew: 'alchemy',
      smith: 'smithing',
      smelt: 'smelting',
      craft: 'crafting',
      fletch: 'fletching',
      chisel: 'chiseling'
    };
    normalized = aliases[normalized] || normalized;

    var current = window.__getCraftingState();
    if (current.visible && current.skill === normalized) {
      return { opened: true, already_open: true, via: 'existing', skill: normalized };
    }

    if (normalized === 'fletching' || normalized === 'chiseling') {
      var openerKey = normalized === 'fletching' ? 'knife' : 'chisel';
      var slot = window.__findInventorySlotByKey(openerKey);
      if (slot < 0) return { error: 'Required opener item not found', skill: normalized, opener: openerKey };
      var inv = game.menu.getInventory();
      if (!inv || !inv.select) return { error: 'Inventory not available', skill: normalized };
      inv.select(slot, true);
      return { opened: false, via: 'inventory_item', skill: normalized, opener: openerKey, slot: slot };
    }

    var target = _findNearestCursorTile(normalized);
    if (!target) {
      return {
        error: 'No station found for skill on this map',
        skill: normalized
      };
    }
    var adjacent = _snapNearWalkable(target.x, target.y, 4);
    if (!adjacent) {
      return {
        error: 'No reachable tile next to station',
        skill: normalized,
        target: target
      };
    }
    var dist = Math.abs(game.player.gridX - adjacent.x) + Math.abs(game.player.gridY - adjacent.y);
    if (dist > 6) {
      return {
        opened: false,
        needs_move: true,
        via: 'station',
        skill: normalized,
        target: target,
        adjacent: adjacent,
        distance: dist
      };
    }

    var clicked = window.__clickTile(target.x, target.y);
    var directTarget = null;
    var player = game.player;
    if (player && Math.abs(player.gridX - target.x) <= 2 && Math.abs(player.gridY - target.y) <= 2) {
      try {
        if (typeof player.lookAtPosition === 'function') player.lookAtPosition(target.x, target.y);
        // Packets.Target = 14, Opcodes.Target.Object = 3 on the current tree.
        game.socket.send(14, [3, target.x + '-' + target.y]);
        directTarget = { sent: true, instance: target.x + '-' + target.y };
      } catch (e) {
        directTarget = { error: String(e), instance: target.x + '-' + target.y };
      }
    }
    return {
      opened: false,
      via: 'station',
      skill: normalized,
      target: target,
      adjacent: adjacent,
      click: clicked,
      direct_target: directTarget
    };
  };

  window.__selectCraftRecipe = function (recipeKey) {
    var game = window.game;
    if (!game || !game.menu || !game.socket) return { error: 'Game not loaded' };
    var key = (recipeKey || '').trim();
    if (!key) return { error: 'Recipe key is empty' };
    try {
      if (typeof game.menu.handleCraftingSelect === 'function') game.menu.handleCraftingSelect(key);
      else game.socket.send(53, { opcode: 1, key: key });
    } catch (e) {
      return { error: 'Failed to select recipe', detail: String(e) };
    }
    return { selected: true, recipe_key: key };
  };

  window.__confirmCraftRecipe = function (recipeKey, count) {
    var game = window.game;
    if (!game || !game.menu || !game.socket) return { error: 'Game not loaded' };
    var key = (recipeKey || '').trim();
    var amount = Math.max(1, Math.min(25, parseInt(count || 1, 10) || 1));
    try {
      if (typeof game.menu.handleCraftingConfirm === 'function') game.menu.handleCraftingConfirm(key, amount);
      else game.socket.send(53, { opcode: 2, key: key, count: amount });
    } catch (e) {
      return { error: 'Failed to craft recipe', detail: String(e) };
    }
    return { crafted: true, recipe_key: key, count: amount };
  };

  // ── Combat state helpers ──
  // Clear target and disableAction so the player can move/warp freely.
  window.__clearCombatState = function () {
    var game = window.game;
    if (!game || !game.player) return { error: 'Game not loaded' };
    var p = game.player;
    var hadTarget = !!p.target;
    p.removeTarget();
    p.disableAction = false;
    return { cleared: true, had_target: hadTarget, player_pos: { x: p.gridX, y: p.gridY } };
  };

  // Warp with combat awareness — checks target, nearby aggro, and server combat cooldown.
  // warpId matches WARP_IDS in convert_to_qwen.py: 0=Mudwich, 1=Aynor, 2=Lakesworld, 3=Crullfield, 4=Patsow, 5=Undersea.
  // Sets warpPending for verification on next OBSERVE (check warp_status in game state).
  window.__safeWarp = function (warpId) {
    var game = window.game;
    if (!game || !game.player || !game.menu) return { error: 'Game not loaded' };
    var p = game.player;
    // Check client-side combat state
    if (p.target) {
      return {
        error: 'In combat — cannot warp. Call __clearCombatState() first, then wait 20+ seconds before retrying.',
        has_target: true, target_name: p.target.name || 'unknown',
      };
    }
    // Check server-side combat cooldown (20s after last hit)
    var timeSinceCombat = Date.now() - window.__kaetramState.lastCombatTime;
    if (timeSinceCombat < 20000 && window.__kaetramState.lastCombatTime > 0) {
      var waitSeconds = Math.ceil((20000 - timeSinceCombat) / 1000);
      return {
        error: 'Server combat cooldown active — wait ' + waitSeconds + ' more seconds before warping.',
        cooldown_remaining_seconds: waitSeconds,
        hint: 'Call __clearCombatState(), then wait ' + waitSeconds + 's (do 3-4 OBSERVE cycles), then retry.',
      };
    }
    // Check if mobs are targeting the player
    var attackerNames = [];
    var allEnts = game.entities.entities || {};
    for (var inst in allEnts) {
      if (!allEnts.hasOwnProperty(inst)) continue;
      var ent = allEnts[inst];
      if (ent && ent.type === 3 && ent.target && ent.target.instance === p.instance) {
        attackerNames.push(ent.name || 'mob');
      }
    }
    if (attackerNames.length > 0) {
      return {
        error: 'Mobs targeting you (' + attackerNames.join(', ') + ') — server blocks warp for 20s after combat. Move away from mobs first.',
        attackers: attackerNames,
      };
    }
    warpId = warpId || 0;
    game.menu.warp.show();
    var warpEl = document.getElementById('warp' + warpId);
    if (warpEl) {
      var preWarpX = p.gridX, preWarpY = p.gridY;
      setTimeout(function () { warpEl.click(); }, 500);
      // Set verification flag — the 500ms auto-cache loop will check if warp succeeded
      window.__kaetramState.warpPending = {
        time: Date.now(), targetWarp: warpId,
        preWarpX: preWarpX, preWarpY: preWarpY,
        confirmed: false, failed: false, reason: null,
      };
      return { warping: true, warp_id: warpId, player_pos: { x: preWarpX, y: preWarpY },
               hint: 'Check warp_status in next OBSERVE to confirm arrival' };
    }
    return { error: 'Warp element not found: warp' + warpId };
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
          symbol = ent.blueExclamation ? 'Q' : (ent.exclamation ? '!' : 'N');
          priority = ent.blueExclamation ? 70 : (ent.exclamation ? 65 : 50);
          break;
        case 2:  symbol = '*'; priority = 25; break; // Item
        case 3:  // Mob — first letter of name
          symbol = (getEntityName(ent) || 'M').charAt(0).toUpperCase();
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
        label: '', symbol, name: getEntityName(ent), type: ent.type, id: inst,
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
          } else if (_isDoor(absX, absY)) {
            ch = 'D';
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
    if (window.game && window.game.player) window.game.player.disableAction = false;
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
    if (window.game && window.game.player) window.game.player.disableAction = false;
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

  // ── Move to tile using game's built-in pathfinder (works off-screen) ──
  // NOTE: The game's A* has a 100-node open list limit. For long/complex paths,
  // it silently returns [] and followPath does nothing. Use __navigateTo for >15 tiles.
  window.__moveTo = function (gridX, gridY) {
    var game = window.game;
    if (!game || !game.player) return { error: 'Game not loaded' };
    var p = game.player;
    var map = game.map;
    var startX = p.gridX, startY = p.gridY;
    if (map.isOutOfBounds(gridX, gridY))
      return { error: 'Out of bounds', target: { x: gridX, y: gridY } };
    var targetIsDoor = _isDoor(gridX, gridY);
    if (map.isColliding(gridX, gridY) && !targetIsDoor)
      return { error: 'Target is a wall', target: { x: gridX, y: gridY }, player_pos: { x: startX, y: startY } };
    var distance = Math.abs(gridX - startX) + Math.abs(gridY - startY);
    p.disableAction = false;
    // Door tiles are flagged colliding in map.grid, so handleRequestPath returns []
    // before pathfinding runs. Temporarily mark the door tile walkable, request the
    // path, then restore. Mirrors how Kaetram's own pathfinder.handleIgnore works.
    var doorPatched = false;
    var prevGridVal, prevDataVal, dataIdx;
    if (targetIsDoor) {
      if (map.grid && map.grid[gridY] && map.grid[gridY][gridX] === 1) {
        prevGridVal = map.grid[gridY][gridX];
        map.grid[gridY][gridX] = 0;
        doorPatched = true;
      }
      if (map.data && typeof map.coordToIndex === 'function') {
        dataIdx = map.coordToIndex(gridX, gridY);
        var dv = map.data[dataIdx];
        if (typeof dv === 'number' && dv < 1) {
          prevDataVal = dv;
          map.data[dataIdx] = 1;
          doorPatched = true;
        }
      }
    }
    try {
      p.go(gridX, gridY);
    } finally {
      if (doorPatched) {
        if (prevGridVal !== undefined) map.grid[gridY][gridX] = prevGridVal;
        if (prevDataVal !== undefined && dataIdx !== undefined) map.data[dataIdx] = prevDataVal;
      }
    }
    // Verify a path was actually generated (A* returns [] for complex/long paths)
    if (!p.hasPath() && !p.moving) {
      return {
        error: 'No path found (too far or terrain too complex). Use __navigateTo() for long distances.',
        target: { x: gridX, y: gridY }, player_pos: { x: startX, y: startY },
        distance: distance, door: targetIsDoor,
      };
    }
    return {
      success: true,
      player_pos: { x: startX, y: startY },
      target: { x: gridX, y: gridY },
      distance: distance,
    };
  };

  // ── Reliable long-distance navigation with auto-waypointing ──
  // Breaks long paths into ~15-tile hops to work around the A* 100-node limit.
  // Returns immediately; the 500ms interval auto-advances waypoints.
  // Check navigation.status in game state to monitor progress.
  window.__navState = {
    active: false, waypoints: [], currentWP: 0,
    targetX: 0, targetY: 0, startTime: 0,
    lastMoveTime: 0, lastPos: null, stuckCount: 0,
    status: 'idle', error: null,
  };

  // Bounded BFS pathfinder — finds a walkable path between two points.
  // Returns the full tile-by-tile path, or null if no path.
  // Bounded to maxRadius tiles from the midpoint to keep it fast.
  function _bfsPath(fromX, fromY, toX, toY, maxRadius) {
    var map = window.game.map;
    if (!map) return null;
    maxRadius = maxRadius || 30;

    // Bounding box centered on midpoint
    var midX = Math.round((fromX + toX) / 2);
    var midY = Math.round((fromY + toY) / 2);
    var minX = midX - maxRadius, maxX = midX + maxRadius;
    var minY = midY - maxRadius, maxY = midY + maxRadius;

    // BFS
    var queue = [{ x: fromX, y: fromY }];
    var visited = {};
    var parent = {};
    var key = function(x, y) { return x + ',' + y; };
    visited[key(fromX, fromY)] = true;
    var dirs = [[0,-1],[0,1],[-1,0],[1,0]]; // N S W E
    var found = false;

    while (queue.length > 0) {
      var cur = queue.shift();
      if (cur.x === toX && cur.y === toY) { found = true; break; }

      for (var d = 0; d < 4; d++) {
        var nx = cur.x + dirs[d][0], ny = cur.y + dirs[d][1];
        if (nx < minX || nx > maxX || ny < minY || ny > maxY) continue;
        var k = key(nx, ny);
        if (visited[k]) continue;
        if (!_isWalkableOrDoor(map, nx, ny)) continue;
        visited[k] = true;
        parent[k] = { x: cur.x, y: cur.y };
        queue.push({ x: nx, y: ny });
      }
    }

    if (!found) return null;

    // Reconstruct path
    var path = [];
    var cx = toX, cy = toY;
    while (cx !== fromX || cy !== fromY) {
      path.unshift({ x: cx, y: cy });
      var p = parent[key(cx, cy)];
      if (!p) break;
      cx = p.x; cy = p.y;
    }

    return path.length > 0 ? path : null;
  }

  function _samplePath(path, sampleInterval) {
    if (!path || path.length === 0) return null;
    sampleInterval = Math.max(1, sampleInterval || 1);
    if (sampleInterval === 1 || path.length <= sampleInterval)
      return [path[path.length - 1]];

    var sampled = [];
    for (var i = sampleInterval - 1; i < path.length; i += sampleInterval) {
      sampled.push(path[i]);
    }

    var last = path[path.length - 1];
    if (
      sampled.length === 0 ||
      sampled[sampled.length - 1].x !== last.x ||
      sampled[sampled.length - 1].y !== last.y
    ) {
      sampled.push(last);
    }
    return sampled;
  }

  // Find nearest walkable tile within radius r of (cx, cy)
  function _snapToWalkable(cx, cy, maxR) {
    var map = window.game.map;
    if (_isWalkableOrDoor(map, cx, cy)) return { x: cx, y: cy };
    for (var r = 1; r <= maxR; r++) {
      for (var dx = -r; dx <= r; dx++) {
        for (var dy = -r; dy <= r; dy++) {
          if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
          var nx = cx + dx, ny = cy + dy;
          if (_isWalkableOrDoor(map, nx, ny)) return { x: nx, y: ny };
        }
      }
    }
    return null;
  }

  // Find a walkable approach tile near a station/object without selecting the
  // station cursor tile itself. Clicking the tile the player is standing on can
  // fail to trigger station interaction.
  function _snapNearWalkable(cx, cy, maxR) {
    var map = window.game.map;
    for (var r = 1; r <= maxR; r++) {
      for (var dx = -r; dx <= r; dx++) {
        for (var dy = -r; dy <= r; dy++) {
          if (dx === 0 && dy === 0) continue;
          if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
          var nx = cx + dx, ny = cy + dy;
          if (_isWalkableOrDoor(map, nx, ny)) return { x: nx, y: ny };
        }
      }
    }
    return null;
  }

  window.__navigateTo = function (gridX, gridY) {
    var game = window.game;
    if (!game || !game.player || !game.map) return { error: 'Game not loaded' };
    var p = game.player;
    var startX = p.gridX, startY = p.gridY;
    var nav = window.__navState;

    if (game.map.isOutOfBounds(gridX, gridY))
      return { error: 'Out of bounds', target: { x: gridX, y: gridY } };

    // Snap target to walkable tile if it's a wall
    var target = _snapToWalkable(gridX, gridY, 25);
    if (!target)
      return { error: 'Target and all nearby tiles are walls', target: { x: gridX, y: gridY } };
    var targetX = target.x, targetY = target.y;
    var totalDist = Math.abs(targetX - startX) + Math.abs(targetY - startY);

    if (totalDist === 0)
      return { status: 'arrived', player_pos: { x: startX, y: startY } };

    // For short distances, just use moveTo directly
    if (totalDist <= 15) {
      p.disableAction = false;
      // Door tiles are flagged colliding in map.grid, so the game's A* won't
      // plan into them without a hint. Mirror __moveTo's grid/data patch so
      // a short navigate onto a door tile actually triggers the teleport.
      var targetIsDoor = _isDoor(targetX, targetY);
      var doorPatched = false;
      var prevGridVal, prevDataVal, dataIdx;
      if (targetIsDoor) {
        var map = game.map;
        if (map.grid && map.grid[targetY] && map.grid[targetY][targetX] === 1) {
          prevGridVal = map.grid[targetY][targetX];
          map.grid[targetY][targetX] = 0;
          doorPatched = true;
        }
        if (map.data && typeof map.coordToIndex === 'function') {
          dataIdx = map.coordToIndex(targetX, targetY);
          var dv = map.data[dataIdx];
          if (typeof dv === 'number' && dv < 1) {
            prevDataVal = dv;
            map.data[dataIdx] = 1;
            doorPatched = true;
          }
        }
      }
      try {
        p.go(targetX, targetY);
      } finally {
        if (doorPatched) {
          if (prevGridVal !== undefined) game.map.grid[targetY][targetX] = prevGridVal;
          if (prevDataVal !== undefined && dataIdx !== undefined) game.map.data[dataIdx] = prevDataVal;
        }
      }
      if (p.hasPath() || p.moving) {
        // Reset nav state to idle (no auto-advance needed)
        nav.active = false; nav.status = 'idle';
        return { status: 'short_path', success: true, player_pos: { x: startX, y: startY },
                 target: { x: targetX, y: targetY }, distance: totalDist, door: targetIsDoor };
      }
      // Fall through to waypoint mode if short path also fails (complex terrain)
    }

    // PRIMARY: Use BFS pathfinding for wall-aware waypoints.
    // Keep waypoints dense enough that player.go() does not have to improvise
    // around single-tile blockers like trees or rocks. Try a tight bounded box
    // first for speed, then progressively widen so detours around mid-route
    // walls (e.g. cliff edges, river bends) still succeed.
    var bfsPath = null;
    var bfsRadiusUsed = 0;
    var radiusTries = [
      Math.min(Math.max(totalDist, 30), 80),
      Math.min(Math.max(totalDist + 30, 60), 150),
      Math.min(Math.max(totalDist + 80, 120), 250),
      Math.min(Math.max(totalDist + 200, 250), 400),
    ];
    for (var ri = 0; ri < radiusTries.length; ri++) {
      bfsPath = _bfsPath(startX, startY, targetX, targetY, radiusTries[ri]);
      if (bfsPath && bfsPath.length > 0) { bfsRadiusUsed = radiusTries[ri]; break; }
    }
    if (!bfsPath || bfsPath.length === 0) {
      nav.active = false;
      nav.status = 'stuck';
      nav.waypoints = [];
      nav.currentWP = 0;
      nav.targetX = targetX;
      nav.targetY = targetY;
      nav.startTime = Date.now();
      nav.lastMoveTime = Date.now();
      nav.lastPos = { x: startX, y: startY };
      nav.stuckCount = 0;
      nav.error = 'No BFS path found within bounded radius (tried up to ' + radiusTries[radiusTries.length - 1] + ')';
      nav._pathfindingMethod = 'bfs_failed';
      nav._stuckReason = 'no_bfs_route';
      return {
        status: 'stuck',
        pathfinding: nav._pathfindingMethod,
        player_pos: { x: startX, y: startY },
        target: { x: targetX, y: targetY },
        total_distance: totalDist,
        error: nav.error,
      };
    }
    var bfsSample = Math.max(2, Math.min(5, Math.round(totalDist / 25)));
    var waypoints = _samplePath(bfsPath, bfsSample);

    // Set up nav state
    nav.active = true;
    nav.waypoints = waypoints;
    nav.currentWP = 0;
    nav.targetX = targetX;
    nav.targetY = targetY;
    nav.startTime = Date.now();
    nav.lastMoveTime = Date.now();
    nav.lastPos = { x: startX, y: startY };
    nav.stuckCount = 0;
    nav.status = 'navigating';
    nav.error = null;
    nav._pathfindingMethod = 'bfs';
    nav._stuckReason = null;

    // Start first hop
    p.disableAction = false;
    p.go(waypoints[0].x, waypoints[0].y);

    return {
      status: 'navigating',
      pathfinding: nav._pathfindingMethod,
      waypoints_count: waypoints.length,
      player_pos: { x: startX, y: startY },
      target: { x: targetX, y: targetY },
      total_distance: totalDist,
      first_waypoint: waypoints[0],
      estimated_seconds: Math.ceil(totalDist * 0.3),
    };
  };

  window.__navStatus = function () {
    var nav = window.__navState;
    if (!nav.active && nav.status === 'idle') return { status: 'idle' };
    var game = window.game;
    var pos = (game && game.player) ? { x: game.player.gridX, y: game.player.gridY } : null;
    return {
      status: nav.status,
      player_pos: pos,
      target: { x: nav.targetX, y: nav.targetY },
      current_waypoint: nav.currentWP,
      total_waypoints: nav.waypoints.length,
      next_waypoint: nav.waypoints[nav.currentWP] || null,
      distance_to_target: pos ? Math.abs(pos.x - nav.targetX) + Math.abs(pos.y - nav.targetY) : null,
      stuck_count: nav.stuckCount,
      elapsed_ms: Date.now() - nav.startTime,
    };
  };

  window.__navCancel = function () {
    var nav = window.__navState;
    nav.active = false;
    nav.status = 'idle';
    nav.waypoints = [];
    return { cancelled: true };
  };

  // ── Attack nearest mob by name (immune to entity label shifting) ──
  window.__attackMob = function (name) {
    var game = window.game;
    if (!game || !game.player || !game.entities) return { error: 'Game not loaded' };
    var mapData = window.__generateAsciiMap();
    if (mapData.error) return { error: mapData.error };
    var nameLower = name.toLowerCase();
    var entity = mapData.legend.find(function (e) {
      return e.type === 3 && e.hp > 0 && (e.name || '').toLowerCase().includes(nameLower);
    });
    if (!entity) return { error: 'No alive mob matching "' + name + '" nearby' };
    var coords = window.__tileToScreenCoords(entity.gridX, entity.gridY);
    if (coords.error) return coords;
    // Check on-screen
    var bg = document.getElementById('background');
    if (bg) {
      var rect = bg.getBoundingClientRect();
      if (coords.click_x < rect.left || coords.click_x > rect.right ||
          coords.click_y < rect.top || coords.click_y > rect.bottom)
        return { error: 'Mob "' + entity.name + '" not on screen (dist=' + entity.distance + '). Use __moveTo(' + entity.gridX + ',' + entity.gridY + ') first.' };
    }
    game.player.disableAction = false;
    document.getElementById('canvas').dispatchEvent(new MouseEvent('click', {
      clientX: coords.click_x, clientY: coords.click_y, bubbles: true, ctrlKey: false,
    }));
    var p = game.player;
    return {
      attacking: entity.name, label: entity.label,
      mob_pos: { x: entity.gridX, y: entity.gridY },
      mob_hp: entity.hp, mob_max_hp: entity.max_hp,
      distance: entity.distance, player_pos: { x: p.gridX, y: p.gridY },
    };
  };

  // ── Interact with NPC by name (walk + talk) ──
  window.__interactNPC = function (name) {
    var game = window.game;
    if (!game || !game.player || !game.entities) return { error: 'Game not loaded' };
    var p = game.player, px = p.gridX, py = p.gridY;
    var nameLower = name.toLowerCase();
    var best = null, bestDist = Infinity;
    var allEnts = game.entities.entities || {};

    for (var inst in allEnts) {
      if (!allEnts.hasOwnProperty(inst)) continue;
      var ent = allEnts[inst];
      if (inst === p.instance || ent.type !== 1) continue;
      if (!getEntityName(ent).toLowerCase().includes(nameLower)) continue;
      var dist = Math.abs(ent.gridX - px) + Math.abs(ent.gridY - py);
      if (dist < bestDist) { bestDist = dist; best = { instance: inst, entity: ent, dist: dist }; }
    }

    if (!best) return { error: 'No NPC matching "' + name + '" found nearby' };

    var npc = best.entity;
    var manhattan = Math.abs(npc.gridX - px) + Math.abs(npc.gridY - py);
    p.disableAction = false;
    if (manhattan < 2) {
      game.socket.send(14, [0, best.instance, npc.gridX, npc.gridY]);
      return {
        talked: true, npc: getEntityName(npc), instance: best.instance,
        npc_pos: { x: npc.gridX, y: npc.gridY }, distance: manhattan,
        player_pos: { x: px, y: py },
      };
    }
    // Not adjacent — walk to nearest ORTHOGONAL neighbor of the NPC (not the NPC tile itself)
    var neighbors = [
      { x: npc.gridX, y: npc.gridY - 1 },  // North
      { x: npc.gridX, y: npc.gridY + 1 },  // South
      { x: npc.gridX - 1, y: npc.gridY },  // West
      { x: npc.gridX + 1, y: npc.gridY },  // East
    ];
    var bestNeighbor = neighbors[0], bestNDist = Infinity;
    for (var n = 0; n < neighbors.length; n++) {
      var nd = Math.abs(neighbors[n].x - px) + Math.abs(neighbors[n].y - py);
      if (nd < bestNDist) { bestNDist = nd; bestNeighbor = neighbors[n]; }
    }
    p.go(bestNeighbor.x, bestNeighbor.y);
    var npcInst = best.instance;
    var npcGX = npc.gridX, npcGY = npc.gridY;
    var retryCount = 0;
    // Clear any previous auto-talk interval to prevent leaks
    if (window.__interactNPCInterval) clearInterval(window.__interactNPCInterval);
    var retryInterval = setInterval(function () {
      retryCount++;
      if (retryCount > 20) { clearInterval(retryInterval); return; }
      var pp = game.player;
      if (!pp) { clearInterval(retryInterval); return; }
      // Server adjacency: Manhattan distance < 2 (orthogonal only)
      var dist = Math.abs(pp.gridX - npcGX) + Math.abs(pp.gridY - npcGY);
      if (dist < 2) {
        clearInterval(retryInterval);
        game.socket.send(14, [0, npcInst, npcGX, npcGY]);
      }
    }, 500);
    window.__interactNPCInterval = retryInterval;
    return {
      talked: false, walking_to: getEntityName(npc), instance: best.instance,
      npc_pos: { x: npc.gridX, y: npc.gridY }, distance: manhattan,
      walk_target: { x: bestNeighbor.x, y: bestNeighbor.y },
      player_pos: { x: px, y: py },
      auto_talk: true,
      hint: 'Walking to orthogonal neighbor of NPC. Auto-talk fires when adjacent (Manhattan < 2).',
    };
  };

  // ── Stuck detection — automatic position tracking + XP awareness ──
  window.__stuckState = { positions: [], maxSize: 10, threshold: 3, radius: 3, lastXP: 0, lastLevel: 0 };

  window.__stuckCheck = function () {
    var game = window.game;
    if (!game || !game.player) return { stuck: false };
    var st = window.__stuckState;
    var px = game.player.gridX, py = game.player.gridY;
    var now = Date.now() / 1000;
    var currentXP = game.player.experience || 0;
    var currentLevel = game.player.level || 1;

    // XP awareness: if XP or level has increased since last check, agent is
    // productively grinding — not stuck. Reset position history.
    var xpGaining = (currentXP > st.lastXP) || (currentLevel > st.lastLevel);
    st.lastXP = currentXP;
    st.lastLevel = currentLevel;
    if (xpGaining) {
      st.positions = [];
      return {
        stuck: false, reason: 'gaining_xp', turns_near: 0, total: 0,
        pos: { x: px, y: py }, xp: currentXP, level: currentLevel,
      };
    }

    st.positions.push({ x: px, y: py, t: now });
    if (st.positions.length > st.maxSize) st.positions.shift();
    var near = 0;
    for (var i = 0; i < st.positions.length; i++) {
      var pos = st.positions[i];
      if (Math.abs(pos.x - px) <= st.radius && Math.abs(pos.y - py) <= st.radius) near++;
    }
    var stuck = near >= st.threshold;
    return {
      stuck: stuck, turns_near: near, total: st.positions.length,
      pos: { x: px, y: py },
      suggestion: stuck ? 'Warp to Mudwich and try a different objective' : null,
    };
  };

  window.__stuckReset = function () {
    window.__stuckState.positions = [];
    return { reset: true };
  };

  // ── Auto-cache: update game state + ASCII map every 500ms ──
  window.__latestGameState = window.__extractGameState();
  window.__latestAsciiMap = window.__generateAsciiMap();
  setInterval(() => {
    window.__latestGameState = window.__extractGameState();
    window.__latestAsciiMap = window.__generateAsciiMap();

    // ── Warp verification ──
    var wpend = window.__kaetramState.warpPending;
    if (wpend && !wpend.confirmed && !wpend.failed) {
      if (Date.now() - wpend.time > 3000) {
        var game = window.game;
        if (game && game.player) {
          var moved = Math.abs(game.player.gridX - wpend.preWarpX) + Math.abs(game.player.gridY - wpend.preWarpY) > 20;
          if (moved) {
            wpend.confirmed = true;
          } else {
            wpend.failed = true;
            wpend.reason = 'Position unchanged after 3s — server likely rejected warp (combat cooldown or blocked)';
          }
        }
      }
    }

    // ── Navigation auto-advance ──
    var nav = window.__navState;
    if (nav.active) {
      var game = window.game;
      if (game && game.player) {
        var p = game.player;

        // Total navigation timeout (120s)
        if (Date.now() - nav.startTime > 120000) {
          nav.active = false;
          nav.status = 'stuck';
          nav._stuckReason = 'timeout';
          nav.error = 'Navigation timed out after 120s';
        }

        // Clear mob targeting during active navigation (prevents aggro oscillation)
        if (nav.active && p.target && p.target.type === 3) {
          nav._aggroClearCount = (nav._aggroClearCount || 0) + 1;
          if (nav._aggroClearCount >= 5) {
            // Too many aggro interrupts — area is too dangerous, abort navigation
            nav.active = false;
            nav.status = 'stuck';
            nav._stuckReason = 'aggro';
            nav.error = 'Too many mob aggro interrupts during navigation — area too dangerous';
            nav._aggroClearCount = 0;
          } else {
            p.removeTarget();
            p.disableAction = false;
            var aggroWP = nav.waypoints[nav.currentWP];
            if (aggroWP) p.go(aggroWP.x, aggroWP.y);
          }
        }

        var wp = nav.waypoints[nav.currentWP];
        if (wp) {
          var distToWP = Math.abs(p.gridX - wp.x) + Math.abs(p.gridY - wp.y);
          var distToTarget = Math.abs(p.gridX - nav.targetX) + Math.abs(p.gridY - nav.targetY);

          // Check if we've arrived at final target (even if we skip waypoints)
          if (distToTarget <= 2) {
            nav.active = false;
            nav.status = 'arrived';
            nav._aggroClearCount = 0;
          }
          // Arrived at current waypoint (within 2 tiles) — advance
          else if (distToWP <= 2) {
            nav.lastMoveTime = Date.now();
            nav.lastPos = { x: p.gridX, y: p.gridY };
            nav.stuckCount = 0;
            nav._aggroClearCount = 0;
            nav.currentWP++;
            if (nav.currentWP >= nav.waypoints.length) {
              nav.active = false;
              nav.status = 'arrived';
            } else {
              p.disableAction = false;
              p.go(nav.waypoints[nav.currentWP].x, nav.waypoints[nav.currentWP].y);
            }
          }
          // Check if stuck (no position change for 3 seconds)
          else if (Date.now() - nav.lastMoveTime > 3000) {
            var moved = nav.lastPos && (p.gridX !== nav.lastPos.x || p.gridY !== nav.lastPos.y);
            if (moved) {
              // Player moved but hasn't reached waypoint — update tracking
              nav.lastMoveTime = Date.now();
              nav.lastPos = { x: p.gridX, y: p.gridY };
              nav.stuckCount = 0;
            } else {
              nav.stuckCount++;
              if (nav.stuckCount === 1) {
                // First stuck: reroute all the way to the final target from the
                // current position instead of nudging the same coarse waypoint.
                nav.lastMoveTime = Date.now();
                var reroutePath = null;
                var rerouteTries = [
                  Math.min(Math.max(distToTarget + 20, 40), 80),
                  Math.min(Math.max(distToTarget + 60, 80), 150),
                  Math.min(Math.max(distToTarget + 120, 150), 250),
                ];
                for (var rri = 0; rri < rerouteTries.length; rri++) {
                  reroutePath = _bfsPath(p.gridX, p.gridY, nav.targetX, nav.targetY, rerouteTries[rri]);
                  if (reroutePath && reroutePath.length > 0) break;
                }
                if (reroutePath && reroutePath.length > 0) {
                  var rerouteSample = Math.max(2, Math.min(4, Math.round(distToTarget / 20)));
                  var rerouteWaypoints = _samplePath(reroutePath, rerouteSample);
                  nav.waypoints = rerouteWaypoints;
                  nav.currentWP = 0;
                  p.disableAction = false;
                  p.go(rerouteWaypoints[0].x, rerouteWaypoints[0].y);
                  nav.stuckCount = 0; // Reset — we have a new route
                } else {
                  nav.active = false;
                  nav.status = 'stuck';
                  nav._stuckReason = 'reroute_failed';
                  nav.error = 'Stuck and could not reroute with BFS at (' + p.gridX + ',' + p.gridY + ')';
                }
              } else if (nav.stuckCount >= 2) {
                nav.active = false;
                nav.status = 'stuck';
                nav._stuckReason = 'wall';
                nav.error = 'Stuck after BFS + retries at (' + p.gridX + ',' + p.gridY + ')';
              } else {
                // Retry: clear state and re-issue go()
                nav.lastMoveTime = Date.now();
                p.disableAction = false;
                if (!p.moving && !p.hasPath()) {
                  p.go(wp.x, wp.y);
                }
              }
            }
          }
        }
      }
    }
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
        window.__kaetramState.lastCombatTime = Date.now();
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

    // ── Chat hook — intercept chatHandler.add() to capture messages with timestamps ──
    const chatHandler = game.input?.chatHandler;
    if (chatHandler && chatHandler.add) {
      const origAdd = chatHandler.add.bind(chatHandler);
      chatHandler.add = function (source, message, colour, notify) {
        window.__kaetramState.chatLog.push({
          sender: source || '',
          message: message || '',
          timestamp: Date.now() / 1000,
        });
        // Keep rolling buffer at max 50 entries
        if (window.__kaetramState.chatLog.length > 50)
          window.__kaetramState.chatLog.splice(0, window.__kaetramState.chatLog.length - 50);
        return origAdd(source, message, colour, notify);
      };
    }

    // ── Overlay hook — intercept game.overlays.update() to track indoor/cave state ──
    const overlays = game.overlays;
    if (overlays && overlays.update) {
      const origUpdate = overlays.update.bind(overlays);
      overlays.update = function (overlay) {
        const entering = !!overlay;
        window.__kaetramState.overlay = {
          active: entering,
          since: entering ? Date.now() / 1000 : null,
        };
        return origUpdate(overlay);
      };
    }

    return true;
  }

  // Install hooks now, retry if game not ready
  if (!installHooks()) {
    const retry = setInterval(() => {
      if (installHooks()) clearInterval(retry);
    }, 500);
  }
})();
