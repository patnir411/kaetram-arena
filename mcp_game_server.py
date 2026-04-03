#!/usr/bin/env python3
"""
mcp_game_server.py — Custom MCP server for Kaetram game automation.

Exposes structured game tools (observe, attack, navigate, etc.) so the AI agent
calls typed functions instead of writing raw JavaScript.  Internally manages a
Playwright browser, injects state_extractor.js, and handles login.

Environment variables (set via .mcp.json env block):
    KAETRAM_PORT          — Game server WebSocket port (9001, 9011, etc.)
    KAETRAM_USERNAME      — Login username (ClaudeBot0, ClaudeBot1, etc.)
    KAETRAM_EXTRACTOR     — Absolute path to state_extractor.js
    KAETRAM_SCREENSHOT_DIR — Directory for live screenshots
    KAETRAM_CLIENT_URL    — Game client URL (default: http://localhost:9000)
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from playwright.async_api import async_playwright

# All debug output to stderr (stdout reserved for MCP JSON-RPC)
def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


# ── Browser lifespan (lazy — yields immediately, launches browser on first use) ─

@asynccontextmanager
async def game_lifespan(server: FastMCP):
    """Yield immediately so MCP handshake completes fast. Browser launches lazily."""
    state = {
        "page": None, "browser": None, "pw": None,
        "logged_in": False, "_lock": asyncio.Lock(),
    }
    log("[mcp] Server ready (browser will launch on first tool call)")
    try:
        yield state
    finally:
        if state["browser"]:
            log("[mcp] Shutting down browser")
            await state["browser"].close()
        if state["pw"]:
            await state["pw"].stop()


async def _ensure_browser(state: dict):
    """Launch browser if not yet started. Thread-safe via asyncio.Lock."""
    if state["page"] is not None:
        return state["page"]

    async with state["_lock"]:
        # Double-check after acquiring lock
        if state["page"] is not None:
            return state["page"]

        log("[mcp] Launching browser...")
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})

        # Inject state_extractor.js (survives page reloads/navigation)
        extractor_path = os.environ.get("KAETRAM_EXTRACTOR", "state_extractor.js")
        if os.path.exists(extractor_path):
            await context.add_init_script(path=extractor_path)
            log(f"[mcp] Injected {extractor_path}")

        # WebSocket port override for multi-agent isolation
        port = os.environ.get("KAETRAM_PORT", "")
        if port:
            await context.add_init_script(f"""(() => {{
                const PORT = '{port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    url = url.replace(/\\/\\/[^:/]+/, '//localhost');
                    url = url.replace(/:9001(?=\\/|$)/, ':' + PORT);
                    return protocols ? new _WS(url, protocols) : new _WS(url);
                }};
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            }})()""")
            log(f"[mcp] WebSocket port override: {port}")

        page = await context.new_page()

        # Live screenshot hook (dashboard reads these)
        screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
        os.makedirs(screenshot_dir, exist_ok=True)
        screenshot_path = os.path.join(screenshot_dir, "live_screen.png")

        async def on_console(msg):
            if msg.text == "LIVE_SCREENSHOT_TRIGGER":
                try:
                    await page.screenshot(path=screenshot_path, type="png")
                except Exception:
                    pass

        page.on("console", on_console)

        state["page"] = page
        state["browser"] = browser
        state["pw"] = pw
        log("[mcp] Browser ready")
        return page


# ── Server ────────────────────────────────────────────────────────────────────

mcp = FastMCP("kaetram", lifespan=game_lifespan)


async def _page(ctx: Context):
    """Get the Playwright page, launching browser if needed."""
    state = ctx.request_context.lifespan_context
    return await _ensure_browser(state)


# ── Login ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def login(ctx: Context) -> str:
    """Log into Kaetram. Call this FIRST before any other tool."""
    page = await _page(ctx)
    username = os.environ.get("KAETRAM_USERNAME", "ClaudeBot")
    client_url = os.environ.get("KAETRAM_CLIENT_URL", "http://localhost:9000")

    await page.goto(client_url)
    await page.wait_for_timeout(3000)
    await page.locator("#login-name-input").fill(username)
    await page.locator("#login-password-input").fill("password123")
    await page.locator("#login").click()
    await page.wait_for_timeout(4000)

    # Check if we need to register (account doesn't exist)
    still_on_login = await page.evaluate("""() => {
        const el = document.getElementById('load-character');
        if (!el) return false;
        const s = window.getComputedStyle(el);
        return s.display !== 'none' && s.opacity !== '0';
    }""")

    if still_on_login:
        await page.evaluate("""(username) => {
            document.getElementById('new-account').click();
            setTimeout(() => {
                const set = (el, val) => {
                    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                        .set.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                };
                set(document.getElementById('register-name-input'), username);
                set(document.getElementById('register-password-input'), 'password123');
                set(document.getElementById('register-password-confirmation-input'), 'password123');
                set(document.getElementById('register-email-input'), username + '@test.com');
                setTimeout(() => document.getElementById('play').click(), 300);
            }, 500);
        }""", username)
        await page.wait_for_timeout(8000)

    await page.wait_for_timeout(2000)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1000)

    # Verify the game actually loaded (retry up to 3 times)
    # Note: gridX can be 0 at spawn, so just check player object exists
    game_ready = False
    for _attempt in range(3):
        game_ready = await page.evaluate(
            "() => !!(window.game && window.game.player && typeof window.game.player.gridX === 'number')"
        )
        if game_ready:
            break
        await page.wait_for_timeout(3000)

    if not game_ready:
        log(f"[mcp] Login failed for {username} — game did not load")
        return "Login FAILED — game did not load. The game client may not be connected to the server. Try login() again."

    ctx.request_context.lifespan_context["logged_in"] = True
    log(f"[mcp] Logged in as {username}")
    return f"Logged in as {username}"


# ── Observe ───────────────────────────────────────────────────────────────────

@mcp.tool()
async def observe(ctx: Context) -> str:
    """Observe the current game state.

    Returns game state JSON + ASCII map + stuck check. Call this before every
    decision and after every action.  Always returns the full, consistent state.
    """
    page = await _page(ctx)

    # Take screenshot for dashboard
    screenshot_dir = os.environ.get("KAETRAM_SCREENSHOT_DIR", "/tmp")
    try:
        await page.screenshot(
            path=os.path.join(screenshot_dir, "live_screen.png"), type="png"
        )
    except Exception:
        pass

    result = await page.evaluate("""() => {
        if (typeof window.__extractGameState !== 'function') {
            return 'ERROR: State extractor not loaded. Call login() first.';
        }
        // Always extract FRESH state — never use stale cache
        const gs = window.__extractGameState();
        const am = window.__generateAsciiMap();
        const sc = window.__stuckCheck ? window.__stuckCheck() : {};

        // Check freshness — warn if game object seems stale
        const age_ms = gs.timestamp ? (Date.now() / 1000 - gs.timestamp) * 1000 : 0;
        if (gs.error) {
            return 'ERROR: ' + gs.error + ' (game may not be loaded — try login() again)';
        }

        const asciiText = (am && !am.error) ? (am.ascii + '\\n\\n' + am.legendText) : '';
        const ps = gs.player_stats || {};
        const ents = gs.nearby_entities || [];
        const quests = gs.quests || [];
        const digest = {
            hp_pct: ps.max_hp ? Math.round(100 * ps.hp / ps.max_hp) : 0,
            threats: ents.filter(e => e.type === 3 && e.distance <= 3).length,
            nearest_mob: (ents.find(e => e.type === 3 && e.hp > 0) || {}).name || null,
            quest_active: quests.some(q => q.started && !q.finished),
            quest_npc_near: ents.some(e => e.quest_npc && e.distance <= 10),
            stuck: sc.stuck || false,
            nav_status: (gs.navigation || {}).status || 'idle',
        };
        return JSON.stringify(gs) + '\\n\\nASCII_MAP:\\n' + asciiText
               + '\\n\\nDIGEST:\\n' + JSON.stringify(digest)
               + '\\n\\nSTUCK_CHECK:\\n' + JSON.stringify(sc);
    }""")

    return result


# ── Combat ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def attack(ctx: Context, mob_name: str) -> str:
    """Attack the nearest alive mob matching the given name.

    Args:
        mob_name: Name of mob to attack (e.g. 'Rat', 'Snek', 'Goblin')
    """
    page = await _page(ctx)

    # Snapshot mob HP before attacking
    hp_before = await page.evaluate("""(name) => {
        const g = window.game;
        if (!g || !g.player) return null;
        const nl = name.toLowerCase();
        for (const e of Object.values(g.entities.entities || {})) {
            if (e.type === 3 && (e.hitPoints || 0) > 0 &&
                (e.name || '').toLowerCase().includes(nl))
                return e.hitPoints;
        }
        return null;
    }""", mob_name)

    result = await page.evaluate(
        "(name) => JSON.stringify(window.__attackMob(name))", mob_name
    )
    await page.wait_for_timeout(2500)

    # Post-attack state: check if mob died, damage dealt, player HP
    post = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p) return {};
        const t = p.target;
        return {
            killed: !t || (t.hitPoints !== undefined && t.hitPoints <= 0),
            mob_hp: t ? (t.hitPoints || 0) : 0,
            mob_name: t ? (t.name || '') : null,
            player_hp: p.hitPoints || 0,
            player_max_hp: p.maxHitPoints || 0,
        };
    }""")
    # Add damage tracking
    if isinstance(post, dict) and hp_before is not None:
        post["hp_before"] = hp_before
        hp_after = post.get("mob_hp", 0)
        post["damage_dealt"] = max(0, hp_before - hp_after)
        if post["damage_dealt"] == 0 and not post.get("killed"):
            post["note"] = "Attack landed but game tick has not updated HP yet. Keep attacking — do not move."

    # Merge post-attack state into result
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict):
            parsed["post_attack"] = post
            return json.dumps(parsed)
    except Exception:
        pass
    return result


@mcp.tool()
async def set_attack_style(ctx: Context, style: str = "hack") -> str:
    """Set combat attack style.

    Args:
        style: 'hack' (strength+defense), 'chop' (strength), or 'defensive' (defense)
    """
    style_ids = {"hack": 6, "chop": 7, "defensive": 3}
    sid = style_ids.get(style.lower(), 6)
    page = await _page(ctx)
    await page.evaluate(f"() => window.game.player.setAttackStyle({sid})")
    return f"Set attack style to {style} (id={sid})"


# ── Navigation ────────────────────────────────────────────────────────────────

@mcp.tool()
async def navigate(ctx: Context, x: int, y: int) -> str:
    """Navigate to grid coordinates using BFS pathfinding.

    Auto-advances waypoints in background. Call observe() to check navigation.status.
    For distances > 100 tiles, warp to nearest town first.

    Args:
        x: Target grid X coordinate
        y: Target grid Y coordinate
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__navigateTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(4000)

    # Warn if BFS failed and linear fallback is being used
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict) and parsed.get("pathfinding") == "linear_fallback":
            parsed["warning"] = (
                "BFS pathfinding failed — using approximate straight-line route. "
                "High chance of getting stuck on walls. Consider warping closer first, "
                "or navigating in shorter hops (< 80 tiles)."
            )
            return json.dumps(parsed)
    except Exception:
        pass
    return result


@mcp.tool()
async def move(ctx: Context, x: int, y: int) -> str:
    """Move to a nearby tile (< 15 tiles). For longer distances use navigate().

    Args:
        x: Target grid X
        y: Target grid Y
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__moveTo(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result


@mcp.tool()
async def warp(ctx: Context, location: str = "mudwich") -> str:
    """Fast travel to a town. Auto-waits up to 25s if combat cooldown is active.

    Args:
        location: 'mudwich', 'crossroads', or 'lakesworld'
    """
    warp_ids = {"mudwich": 0, "crossroads": 1, "lakesworld": 2}
    warp_id = warp_ids.get(location.lower(), 0)
    page = await _page(ctx)

    # Clear combat state + zero the cooldown timer so incoming hits don't keep resetting it
    await page.evaluate("""() => {
        window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
    }""")

    # Poll until cooldown expires (max ~25s) instead of failing immediately.
    # Handles: cooldown_remaining_seconds, has_target, and attackers cases.
    max_attempts = 6  # 6 attempts * ~5s sleep = 30s max wait
    for attempt in range(max_attempts):
        result_raw = await page.evaluate(
            "(id) => JSON.stringify(window.__safeWarp(id))", warp_id
        )
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        is_combat_block = isinstance(result, dict) and (
            result.get("cooldown_remaining_seconds")
            or result.get("has_target")
            or result.get("attackers")
        )
        if is_combat_block:
            wait_secs = result.get("cooldown_remaining_seconds", 5)
            wait_ms = min(wait_secs * 1000 + 1000, 6000)
            await page.wait_for_timeout(wait_ms)
            # Re-clear combat + timer in case mobs re-engaged during wait
            await page.evaluate("""() => {
                window.__clearCombatState();
                window.__kaetramState.lastCombatTime = 0;
            }""")
            continue
        # Success or non-combat error — return immediately
        break

    await page.wait_for_timeout(3000)
    return result_raw


@mcp.tool()
async def cancel_nav(ctx: Context) -> str:
    """Cancel active navigation."""
    page = await _page(ctx)
    await page.evaluate("() => window.__navCancel()")
    return "Navigation cancelled"


# ── NPC / Quests ──────────────────────────────────────────────────────────────

@mcp.tool()
async def interact_npc(ctx: Context, npc_name: str) -> str:
    """Walk to an NPC, talk through ALL dialogue lines, and auto-accept quest if offered.

    This handles the full NPC interaction flow:
    1. Walk to NPC if not adjacent (targets orthogonal neighbor tile)
    2. Verify adjacency (Manhattan distance < 2, server requirement)
    3. Send talk packets repeatedly (NPCs have 1-10+ dialogue lines)
    4. Click quest-button if quest panel opens

    Args:
        npc_name: Name of the NPC (e.g. 'Forester', 'Blacksmith', 'Village Girl')
    """
    page = await _page(ctx)

    # Snapshot quests BEFORE any interaction (to detect changes later)
    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    # Step 1: Walk to NPC and get initial talk result
    result_raw = await page.evaluate(
        "(name) => JSON.stringify(window.__interactNPC(name))", npc_name
    )
    result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw

    if isinstance(result, dict) and result.get("error"):
        return result_raw

    instance_id = result.get("instance", "") if isinstance(result, dict) else ""
    talked = result.get("talked", False) if isinstance(result, dict) else False
    npc_pos = result.get("npc_pos", {}) if isinstance(result, dict) else {}
    player_start = result.get("player_pos", {}) if isinstance(result, dict) else {}

    # Step 2: If not adjacent, wait for walk + verify arrival
    if not talked:
        # Wait for pathfinding walk (check every 1s, up to 8s)
        arrived = False
        for wait_i in range(8):
            await page.wait_for_timeout(1000)
            pos_check = await page.evaluate("""(npcPos) => {
                const p = window.game && window.game.player;
                if (!p) return { px: 0, py: 0, manhattan: 999 };
                const manhattan = Math.abs(p.gridX - npcPos.x) + Math.abs(p.gridY - npcPos.y);
                return { px: p.gridX, py: p.gridY, manhattan: manhattan };
            }""", npc_pos)
            if pos_check.get("manhattan", 999) < 2:
                arrived = True
                break
        if not arrived:
            # Player never reached the NPC — return clear error
            final_pos = pos_check or {}
            return json.dumps({
                "npc": npc_name,
                "error": f"Could not reach {npc_name} — pathfinding failed or NPC too far",
                "instance": instance_id,
                "walked": True,
                "arrived": False,
                "player_start": player_start,
                "player_end": {"x": final_pos.get("px"), "y": final_pos.get("py")},
                "npc_pos": npc_pos,
                "final_distance": final_pos.get("manhattan", -1),
                "dialogue_lines": 0,
                "quest_opened": False,
                "hint": "NPC is unreachable from current position. Try warping closer or finding a different path.",
            })

    # Step 3: Click through all dialogue lines
    # Player is now adjacent — send talk packets and collect dialogue
    quest_opened = False
    dialogue_lines = []
    empty_count = 0
    for i in range(20):
        # Send talk packet using the JS helper (includes proper coordinates)
        await page.evaluate(
            "(id) => window.__talkToNPC(id)", instance_id
        )
        # Short wait for server response + bubble render
        await page.wait_for_timeout(800)

        # Check for dialogue bubble, quest panel, and chat messages
        panel_state = await page.evaluate("""() => {
            // Check speech bubbles
            const bubbles = document.querySelectorAll('.bubble');
            let bubbleText = null;
            for (const b of bubbles) {
                const t = b.textContent.trim();
                if (t) { bubbleText = t.slice(0, 200); break; }
            }
            // Check quest panel visibility
            const questBtn = document.getElementById('quest-button');
            const questPanel = document.getElementById('quest');
            let panelVisible = false;
            let questBtnText = null;
            if (questPanel) {
                const s = window.getComputedStyle(questPanel);
                panelVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            if (questBtn) questBtnText = questBtn.textContent.trim().slice(0, 50);
            // Check recent chat for NPC speech (fallback if bubble missed)
            let recentChat = null;
            const chatLog = (window.__kaetramState || {}).chatLog || [];
            if (chatLog.length > 0) {
                const last = chatLog[chatLog.length - 1];
                if (last && (Date.now() / 1000 - (last.time || 0)) < 3) {
                    recentChat = last.text;
                }
            }
            return {
                bubble_text: bubbleText,
                chat_text: recentChat,
                quest_panel: panelVisible || !!(questBtn && questBtn.offsetParent),
                quest_btn_text: questBtnText,
            };
        }""")

        dialogue_text = panel_state.get("bubble_text") or panel_state.get("chat_text")
        if dialogue_text:
            # Avoid duplicate consecutive lines
            if not dialogue_lines or dialogue_lines[-1] != dialogue_text:
                dialogue_lines.append(dialogue_text)
            empty_count = 0
        else:
            empty_count += 1

        if panel_state.get("quest_panel"):
            quest_opened = True
            btn_text = panel_state.get("quest_btn_text", "")
            dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
            # Click accept/complete button
            await page.evaluate(
                "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
            )
            await page.wait_for_timeout(500)
            break

        # Stop after 4 consecutive empty responses (dialogue exhausted)
        if empty_count >= 4 and i >= 3:
            break

    # Get final player position
    player_end = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        return p ? { x: p.gridX, y: p.gridY } : {};
    }""")

    # Final check: did quests change even if we didn't see the panel?
    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after

    return json.dumps({
        "npc": npc_name,
        "instance": instance_id,
        "walked": not talked,
        "arrived": True,
        "player_start": player_start,
        "player_end": player_end,
        "npc_pos": npc_pos,
        "dialogue_lines": len(dialogue_lines),
        "dialogue": dialogue_lines,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_lines[-1] if dialogue_lines else None,
    })


@mcp.tool()
async def talk_npc(ctx: Context, instance_id: str) -> str:
    """Click through ALL remaining NPC dialogue lines until quest panel opens or dialogue ends.

    Player must be adjacent (Manhattan distance < 2) to the NPC.
    Auto-accepts quest if quest panel opens.

    Args:
        instance_id: NPC instance ID from game state (e.g. '1-33362128')
    """
    page = await _page(ctx)

    # Verify player is adjacent before sending any packets
    adjacency = await page.evaluate("""(id) => {
        const game = window.game;
        if (!game || !game.player) return { error: 'Game not loaded' };
        const entity = game.entities && game.entities.get ? game.entities.get(id) : null;
        if (!entity && game.entities && game.entities.entities) {
            // Fallback: search entities dict directly
            for (const inst in game.entities.entities) {
                if (inst === id) { entity = game.entities.entities[inst]; break; }
            }
        }
        if (!entity) return { error: 'NPC not found with instance ' + id };
        const p = game.player;
        const manhattan = Math.abs(p.gridX - entity.gridX) + Math.abs(p.gridY - entity.gridY);
        return {
            npc_name: entity.name || 'Unknown',
            npc_pos: { x: entity.gridX, y: entity.gridY },
            player_pos: { x: p.gridX, y: p.gridY },
            manhattan: manhattan,
            adjacent: manhattan < 2,
        };
    }""", instance_id)

    if isinstance(adjacency, dict) and adjacency.get("error"):
        return json.dumps(adjacency)

    if not adjacency.get("adjacent"):
        return json.dumps({
            "instance": instance_id,
            "error": f"Not adjacent to NPC (distance={adjacency.get('manhattan')}). Walk closer first.",
            "npc_name": adjacency.get("npc_name"),
            "npc_pos": adjacency.get("npc_pos"),
            "player_pos": adjacency.get("player_pos"),
            "dialogue_lines": 0,
            "quest_opened": False,
        })

    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    quest_opened = False
    dialogue_lines = []
    empty_count = 0
    for i in range(20):
        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        await page.wait_for_timeout(800)

        panel_state = await page.evaluate("""() => {
            const bubbles = document.querySelectorAll('.bubble');
            let bubbleText = null;
            for (const b of bubbles) {
                const t = b.textContent.trim();
                if (t) { bubbleText = t.slice(0, 200); break; }
            }
            const questBtn = document.getElementById('quest-button');
            const questPanel = document.getElementById('quest');
            let panelVisible = false;
            let questBtnText = null;
            if (questPanel) {
                const s = window.getComputedStyle(questPanel);
                panelVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            if (questBtn) questBtnText = questBtn.textContent.trim().slice(0, 50);
            let recentChat = null;
            const chatLog = (window.__kaetramState || {}).chatLog || [];
            if (chatLog.length > 0) {
                const last = chatLog[chatLog.length - 1];
                if (last && (Date.now() / 1000 - (last.time || 0)) < 3) {
                    recentChat = last.text;
                }
            }
            return {
                bubble_text: bubbleText,
                chat_text: recentChat,
                quest_panel: panelVisible || !!(questBtn && questBtn.offsetParent),
                quest_btn_text: questBtnText,
            };
        }""")

        dialogue_text = panel_state.get("bubble_text") or panel_state.get("chat_text")
        if dialogue_text:
            if not dialogue_lines or dialogue_lines[-1] != dialogue_text:
                dialogue_lines.append(dialogue_text)
            empty_count = 0
        else:
            empty_count += 1

        if panel_state.get("quest_panel"):
            quest_opened = True
            btn_text = panel_state.get("quest_btn_text", "")
            dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
            await page.evaluate(
                "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
            )
            await page.wait_for_timeout(500)
            break

        if empty_count >= 4 and i >= 3:
            break

    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after

    return json.dumps({
        "instance": instance_id,
        "npc_name": adjacency.get("npc_name"),
        "dialogue_lines": len(dialogue_lines),
        "dialogue": dialogue_lines,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_lines[-1] if dialogue_lines else None,
    })


@mcp.tool()
async def accept_quest(ctx: Context) -> str:
    """Accept the quest shown in the quest panel. Usually not needed — interact_npc auto-accepts."""
    page = await _page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(1500)
    return "Quest accept clicked"


# ── Inventory ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def eat_food(ctx: Context, slot: int) -> str:
    """Eat food from inventory to heal HP.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "(s) => JSON.stringify(window.__eatFood(s))", slot
    )
    await page.wait_for_timeout(1000)
    return result


@mcp.tool()
async def drop_item(ctx: Context, slot: int) -> str:
    """Drop an item from inventory to free space.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)

    # Get item info and inventory count before drop
    before = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        if (!inv) return { error: 'Inventory not loaded' };
        const el = inv.getElement(idx);
        if (!el) return { error: 'No item in slot ' + idx };
        const key = (el.dataset && el.dataset.key) || 'unknown';
        const count = inv.getList().filter(e => e).length;
        return { key: key, count: count };
    }""", slot)

    if isinstance(before, dict) and before.get("error"):
        return json.dumps(before)

    # Send container remove packet: Packets.Container=16, Opcodes.Container.Remove=2
    # The slot index tells the server which item to drop
    result = await page.evaluate("""(idx) => {
        try {
            // Method 1: Direct packet (most reliable)
            window.game.socket.send(16, [2, idx, 1]);
            return { sent: true };
        } catch(e) {
            return { error: 'Failed to send drop packet: ' + e.message };
        }
    }""", slot)

    await page.wait_for_timeout(1000)

    # Verify item was dropped
    after = await page.evaluate("""() => {
        const inv = window.game && window.game.menu && window.game.menu.getInventory();
        return inv ? inv.getList().filter(e => e).length : -1;
    }""")

    item_key = before.get("key", "unknown") if isinstance(before, dict) else "unknown"
    count_before = before.get("count", -1) if isinstance(before, dict) else -1

    if isinstance(after, int) and after < count_before:
        return json.dumps({"dropped": True, "item": item_key, "slot": slot,
                           "inventory_before": count_before, "inventory_after": after})
    else:
        return json.dumps({"dropped": False, "item": item_key, "slot": slot,
                           "error": "Drop may have failed — inventory count unchanged",
                           "inventory_before": count_before, "inventory_after": after})


@mcp.tool()
async def equip_item(ctx: Context, slot: int) -> str:
    """Equip an item from inventory.

    Args:
        slot: Inventory slot number (0-24)
    """
    page = await _page(ctx)

    # Snapshot weapon before equip
    before = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p || !p.equipments) return { weapon: 'unknown' };
        const wep = p.equipments[4];  // slot 4 = weapon
        return { weapon: wep ? (wep.name || wep.key || 'unknown') : 'none' };
    }""")

    # Get item info from inventory slot
    item_info = await page.evaluate("""(idx) => {
        const inv = window.game && window.game.menu && window.game.menu.inventory;
        if (!inv) return { error: 'Inventory not loaded' };
        const slots = document.querySelectorAll('.item-slot');
        const el = slots[idx];
        if (!el) return { error: 'No item in slot ' + idx };
        return { key: el.dataset && el.dataset.key || 'unknown', slot: idx };
    }""", slot)

    if isinstance(item_info, dict) and item_info.get("error"):
        return json.dumps(item_info)

    # Click the slot and equip button
    await page.evaluate("""(idx) => {
        document.getElementById('inventory-button').click();
        setTimeout(() => {
            const slots = document.querySelectorAll('.item-slot');
            if (slots[idx]) slots[idx].click();
            setTimeout(() => {
                const btn = document.querySelector('.action-equip');
                if (btn) btn.click();
                setTimeout(() => document.getElementById('inventory-button').click(), 500);
            }, 800);
        }, 800);
    }""", slot)
    await page.wait_for_timeout(2500)

    # Verify: did weapon actually change?
    after = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        if (!p || !p.equipments) return { weapon: 'unknown' };
        const wep = p.equipments[4];
        return { weapon: wep ? (wep.name || wep.key || 'unknown') : 'none' };
    }""")

    weapon_before = before.get("weapon", "unknown") if isinstance(before, dict) else "unknown"
    weapon_after = after.get("weapon", "unknown") if isinstance(after, dict) else "unknown"
    item_key = item_info.get("key", "unknown") if isinstance(item_info, dict) else "unknown"

    if weapon_after != weapon_before:
        return json.dumps({
            "equipped": True, "slot": slot, "item": item_key,
            "weapon_before": weapon_before, "weapon_now": weapon_after,
        })
    else:
        return json.dumps({
            "equipped": False, "slot": slot, "item": item_key,
            "weapon_before": weapon_before, "weapon_now": weapon_after,
            "error": f"Equip failed — weapon unchanged ({weapon_after}). Possible cause: stat requirement not met (e.g., Strength too low for Iron Axe).",
        })


# ── Recovery ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def clear_combat(ctx: Context) -> str:
    """Clear combat state and cooldown timer so you can warp."""
    page = await _page(ctx)
    result = await page.evaluate("""() => {
        const r = window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
        window.__kaetramState.lastCombat = null;
        return JSON.stringify(r);
    }""")
    return result


@mcp.tool()
async def stuck_reset(ctx: Context) -> str:
    """Reset stuck detection. Use when stuck check shows stuck=true."""
    page = await _page(ctx)
    await page.evaluate("() => window.__stuckReset()")
    return "Stuck state reset"



@mcp.tool()
async def click_tile(ctx: Context, x: int, y: int) -> str:
    """Click a specific grid tile (must be on screen). Fallback for edge cases.

    Args:
        x: Grid X coordinate
        y: Grid Y coordinate
    """
    page = await _page(ctx)
    result = await page.evaluate(
        "([x,y]) => JSON.stringify(window.__clickTile(x, y))", [x, y]
    )
    await page.wait_for_timeout(2000)
    return result


@mcp.tool()
async def respawn(ctx: Context) -> str:
    """Respawn after death, clear all combat state, and warp to Mudwich."""
    page = await _page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('respawn'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(2000)
    # Clear stale combat state from before death (prevents warp cooldown trap)
    await page.evaluate("""() => {
        window.__clearCombatState();
        window.__kaetramState.lastCombatTime = 0;
        window.__kaetramState.lastCombat = null;
    }""")
    await page.wait_for_timeout(1000)
    result = await page.evaluate(
        "(id) => JSON.stringify(window.__safeWarp(id))", 0
    )
    await page.wait_for_timeout(3000)
    return "Respawned and combat cleared. " + result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("[mcp] Starting Kaetram MCP server")
    mcp.run(transport="stdio")
