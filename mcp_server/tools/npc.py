"""NPC interaction tools: interact_npc, talk_npc, accept_quest."""

import json

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log, log_tool, mcp
from mcp_server.helpers import mid_navigation, wait_for_adjacency
from mcp_server.js import NUDGE_STORE, SHOP_UI_STATE
from mcp_server.utils import check_shop_visibly_open, compact_shop_ui


# ── Dialogue state JS (injected once per read) ──────────────────────────────

_DIALOGUE_STATE_JS = """() => {
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
        text: bubbleText || recentChat || null,
        quest_panel: panelVisible || !!(questBtn && questBtn.offsetParent),
        quest_btn_text: questBtnText,
    };
}"""


# ── Shared dialogue loop ────────────────────────────────────────────────────

async def _run_dialogue_loop(page, instance_id: str) -> dict:
    """Drive NPC dialogue to completion using a read-first, talk-second approach.

    The game server wraps talkIndex around when dialogue is exhausted — the NPC
    will talk forever.  We detect completion by:
      1. Seeing the first dialogue line repeat (cycle detection)
      2. Seeing the same line twice consecutively (stalled NPC)
      3. Getting no bubble text for 2 consecutive reads (dialogue dismissed)
      4. Quest panel appearing (quest offered)

    Returns a dict with dialogue_lines, dialogue_complete, quest_opened, etc.
    """
    quest_opened = False
    dialogue_lines: list[str] = []
    first_line: str | None = None
    empty_count = 0

    # ── Phase 1: Read the response from the initial talk ────────────────
    # __interactNPC (or its auto-talk interval) already sent the first
    # Target.Talk packet. Wait for the server to respond and read it
    # BEFORE sending any more packets.
    await page.wait_for_timeout(900)
    state = await page.evaluate(_DIALOGUE_STATE_JS)
    text = state.get("text") if isinstance(state, dict) else None

    if isinstance(state, dict) and state.get("quest_panel"):
        quest_opened = True
        btn_text = state.get("quest_btn_text", "")
        dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
        await page.evaluate(
            "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
        )
        await page.wait_for_timeout(500)
        return {
            "dialogue_lines": len(dialogue_lines),
            "dialogue": dialogue_lines,
            "dialogue_complete": True,
            "quest_opened": True,
        }

    if text:
        dialogue_lines.append(text)
        first_line = text
        empty_count = 0
    else:
        empty_count = 1

    # ── Phase 2: Talk-read loop until dialogue cycles or empties ─────────
    for i in range(15):
        # Check termination BEFORE sending the next talk packet
        if empty_count >= 2:
            break

        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        await page.wait_for_timeout(800)

        state = await page.evaluate(_DIALOGUE_STATE_JS)
        text = state.get("text") if isinstance(state, dict) else None

        # Quest panel takes priority — accept and stop immediately
        if isinstance(state, dict) and state.get("quest_panel"):
            quest_opened = True
            btn_text = state.get("quest_btn_text", "")
            dialogue_lines.append(f"[Quest panel opened: {btn_text}]")
            await page.evaluate(
                "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
            )
            await page.wait_for_timeout(500)
            break

        if text:
            empty_count = 0

            # Cycle detection: if this text matches the FIRST line we saw
            # and we've collected at least 2 unique lines, the NPC has
            # wrapped around. Stop without adding the duplicate.
            if first_line and text == first_line and len(dialogue_lines) >= 2:
                log(f"[dialogue] cycle detected after {len(dialogue_lines)} lines — stopping")
                break

            if not dialogue_lines or dialogue_lines[-1] != text:
                # New dialogue line
                dialogue_lines.append(text)
                if not first_line:
                    first_line = text
            else:
                # Same text as last read — NPC is stalled/repeating
                log(f"[dialogue] stalled on repeated line — stopping")
                break
        else:
            empty_count += 1

    return {
        "dialogue_lines": len(dialogue_lines),
        "dialogue": dialogue_lines,
        "dialogue_complete": True,
        "quest_opened": quest_opened,
        "last_dialogue": dialogue_lines[-1] if dialogue_lines else None,
    }


# ── Shop interaction resolver (unchanged) ───────────────────────────────────

def _interaction_ui_state_script() -> str:
    """Return the JS source for evaluating shop/quest/dialogue UI state."""
    return SHOP_UI_STATE


async def _resolve_shop_interaction(page, npc_name: str, store_key: str | None = None) -> dict:
    async def _nudge_hidden_store_open() -> dict:
        return await page.evaluate(NUDGE_STORE)

    result_raw = await page.evaluate(
        "(name) => JSON.stringify(window.__interactNPC(name))", npc_name
    )
    result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw

    if isinstance(result, dict) and result.get("error"):
        return {
            "interacted": False,
            "npc": npc_name,
            "error": result.get("error"),
            "ui": await page.evaluate(SHOP_UI_STATE),
        }

    instance_id = result.get("instance", "") if isinstance(result, dict) else ""
    talked = result.get("talked", False) if isinstance(result, dict) else False
    npc_pos = result.get("npc_pos", {}) if isinstance(result, dict) else {}
    player_start = result.get("player_pos", {}) if isinstance(result, dict) else {}
    log(f"[interact_npc/shop] initial_result npc={npc_name} result={result}")

    if not talked:
        arrived, pos_check = await wait_for_adjacency(page, npc_pos)
        if not arrived:
            still_moving = mid_navigation(pos_check)
            return {
                "interacted": False,
                "npc": npc_name,
                "error": (
                    f"Still moving toward {npc_name} — observe again in a few seconds and retry."
                    if still_moving
                    else f"Could not reach {npc_name} — pathfinding failed or NPC too far"
                ),
                "instance": instance_id,
                "walked": True,
                "arrived": False,
                "mid_navigation": still_moving,
                "nav_status": pos_check.get("nav_status"),
                "waypoints_remaining": pos_check.get("waypoints_remaining"),
                "player_start": player_start,
                "player_end": {"x": pos_check.get("px"), "y": pos_check.get("py")},
                "npc_pos": npc_pos,
                "final_distance": pos_check.get("manhattan", -1),
                "hint": (
                    "Player is mid-navigation — wait for arrival before retrying, don't call warp or cancel_nav."
                    if still_moving
                    else "NPC is unreachable from current position. Try warping closer or finding a different path."
                ),
                "ui": await page.evaluate(SHOP_UI_STATE),
            }

    if instance_id and not talked:
        await page.evaluate("""() => {
            const p = window.game && window.game.player;
            if (p && p.stop) p.stop(true);
        }""")
        await page.wait_for_timeout(200)
        ui_before_talk = await page.evaluate(SHOP_UI_STATE)
        log(f"[interact_npc/shop] before_manual_talk npc={npc_name} ui={compact_shop_ui(ui_before_talk)}")
        await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
        ui_after_talk = await page.evaluate(SHOP_UI_STATE)
        log(f"[interact_npc/shop] after_manual_talk npc={npc_name} ui={compact_shop_ui(ui_after_talk)}")
        await page.wait_for_timeout(1800)
    elif instance_id and talked:
        ui_after_interact = await page.evaluate(SHOP_UI_STATE)
        log(f"[interact_npc/shop] interact_npc_already_talked npc={npc_name} ui={compact_shop_ui(ui_after_interact)}")

    ui_state = await page.evaluate(SHOP_UI_STATE)
    retried_talk = False
    for attempt in range(12):
        await page.wait_for_timeout(1500 if attempt == 0 else 1200)
        ui_state = await page.evaluate(SHOP_UI_STATE)
        shop = ui_state.get("shop") if isinstance(ui_state, dict) else {}
        shop_debug = shop.get("debug") if isinstance(shop, dict) else {}
        visibly_open = check_shop_visibly_open(ui_state)
        if (
            ui_state.get("type") == "shop"
            and visibly_open
        ):
            log(f"[interact_npc/shop] ready npc={npc_name} attempt={attempt + 1} ui={compact_shop_ui(ui_state)}")
            break
        if (
            isinstance(shop, dict)
            and shop.get("has_store")
            and not visibly_open
            and isinstance(shop_debug, dict)
            and shop_debug.get("dom_store_text")
            and attempt >= 1
        ):
            nudge = await _nudge_hidden_store_open()
            log(f"[interact_npc/shop] nudge_hidden_store_open npc={npc_name} attempt={attempt + 1} result={nudge}")
            await page.wait_for_timeout(800)
            ui_state = await page.evaluate(SHOP_UI_STATE)
            log(f"[interact_npc/shop] post_nudge npc={npc_name} ui={compact_shop_ui(ui_state)}")
            shop = ui_state.get("shop") if isinstance(ui_state, dict) else {}
            shop_debug = shop.get("debug") if isinstance(shop, dict) else {}
            visibly_open = check_shop_visibly_open(ui_state)
            if ui_state.get("type") == "shop" and visibly_open:
                break
        if ui_state.get("type") in {"dialogue", "quest"}:
            break
        if (
            instance_id
            and not retried_talk
            and attempt >= 2
            and ui_state.get("type") == "none"
            and not visibly_open
            and not (isinstance(shop, dict) and shop.get("has_store"))
        ):
            log(f"[interact_npc/shop] retrying_npc_talk_once npc={npc_name} attempt={attempt + 1}")
            await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)
            await page.wait_for_timeout(1800)
            retried_talk = True

    shop = ui_state.get("shop") if isinstance(ui_state, dict) else {}
    matched = (
        ui_state.get("type") == "shop"
        and isinstance(shop, dict)
        and (
            shop.get("ready")
            or shop.get("visible")
            or shop.get("containerVisible")
            or shop.get("storeContainerVisible")
            or (isinstance(shop.get("debug"), dict) and shop["debug"].get("any_visible_dom_storeish"))
        )
        and (store_key is None or shop.get("store_key") in {None, store_key})
    )

    response = {
        "interacted": True,
        "npc": npc_name,
        "instance": instance_id,
        "walk_result": result,
        "expected": "shop",
        "matched_expectation": matched,
        "ui": ui_state,
    }
    if not matched:
        log(f"[interact_npc/shop] unresolved npc={npc_name} ui={compact_shop_ui(ui_state)}")
        visibly_open = bool(
            isinstance(shop, dict)
            and (
                shop.get("visible")
                or shop.get("containerVisible")
                or shop.get("storeContainerVisible")
                or (isinstance(shop.get("debug"), dict) and shop["debug"].get("any_visible_dom_storeish"))
            )
        )
        if ui_state.get("type") in {"dialogue", "quest"}:
            response["error"] = f"Expected shop but {ui_state.get('type')} opened instead"
        elif visibly_open:
            response["error"] = "Shop opened but items never populated"
        elif isinstance(shop, dict) and shop.get("has_store"):
            response["error"] = "Shop object exists but never became visibly open"
        else:
            response["error"] = "Timed out waiting for shop UI"
    return response


# ── MCP Tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def interact_npc(ctx: Context, npc_name: str, expect: str = "dialogue", include_ui_state: bool = True) -> str:
    """Walk to an NPC, talk through ALL dialogue lines, and auto-accept quest if offered.

    This handles the full NPC interaction flow:
    1. Walk to NPC if not adjacent (targets orthogonal neighbor tile)
    2. Verify adjacency (Manhattan distance < 2, server requirement)
    3. Read all dialogue lines until the NPC cycles or stops
    4. Click quest-button if quest panel opens

    Returns dialogue_complete=true when all unique dialogue has been exhausted.
    Do NOT call interact_npc again on the same NPC after dialogue_complete=true
    unless you need to trigger a different quest stage or shop.

    Args:
        npc_name: Name of the NPC (e.g. 'Forester', 'Blacksmith', 'Village Girl')
        expect: Expected interaction result: 'dialogue' (default), 'shop', or 'any'
        include_ui_state: Include a best-effort snapshot of the visible UI state
    """
    log_tool("interact_npc", args={"npc_name": npc_name, "expect": expect})
    page = await get_page(ctx)

    if expect == "shop":
        response = await _resolve_shop_interaction(page, npc_name)
        if not include_ui_state:
            response.pop("ui", None)
        return json.dumps(response)

    # Snapshot quests BEFORE any interaction
    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    # Clear any leftover auto-talk interval from a previous interaction
    await page.evaluate("() => { if (window.__interactNPCInterval) { clearInterval(window.__interactNPCInterval); window.__interactNPCInterval = null; } }")

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

    if not talked:
        arrived, pos_check = await wait_for_adjacency(page, npc_pos)
        if not arrived:
            final_pos = pos_check or {}
            still_moving = mid_navigation(final_pos)
            return json.dumps({
                "npc": npc_name,
                "error": (
                    f"Still moving toward {npc_name} — observe again in a few seconds and retry."
                    if still_moving
                    else f"Could not reach {npc_name} — pathfinding failed or NPC too far"
                ),
                "instance": instance_id,
                "walked": True,
                "arrived": False,
                "mid_navigation": still_moving,
                "nav_status": final_pos.get("nav_status"),
                "waypoints_remaining": final_pos.get("waypoints_remaining"),
                "player_start": player_start,
                "player_end": {"x": final_pos.get("px"), "y": final_pos.get("py")},
                "npc_pos": npc_pos,
                "final_distance": final_pos.get("manhattan", -1),
                "dialogue_lines": 0,
                "dialogue_complete": False,
                "quest_opened": False,
                "hint": (
                    "Player is mid-navigation — wait for arrival before retrying, don't call warp or cancel_nav."
                    if still_moving
                    else "NPC is unreachable from current position. Try warping closer or finding a different path."
                ),
            })
        # The auto-talk interval already sent the first talk packet when
        # the player arrived. Give it a moment to fire.
        await page.wait_for_timeout(300)

    # Drive dialogue to completion
    dialogue_result = await _run_dialogue_loop(page, instance_id)

    # Get final player position
    player_end = await page.evaluate("""() => {
        const p = window.game && window.game.player;
        return p ? { x: p.gridX, y: p.gridY } : {};
    }""")

    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after
    quest_opened = dialogue_result.get("quest_opened", False)

    response = {
        "npc": npc_name,
        "instance": instance_id,
        "walked": not talked,
        "arrived": True,
        "player_start": player_start,
        "player_end": player_end,
        "npc_pos": npc_pos,
        "dialogue_lines": dialogue_result["dialogue_lines"],
        "dialogue": dialogue_result["dialogue"],
        "dialogue_complete": True,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_result.get("last_dialogue"),
    }
    if include_ui_state:
        response["ui"] = await page.evaluate(SHOP_UI_STATE)
    return json.dumps(response)


async def talk_npc(ctx: Context, instance_id: str) -> str:
    """Click through ALL remaining NPC dialogue lines until quest panel opens or dialogue ends.

    Player must be adjacent (Manhattan distance < 2) to the NPC.
    Auto-accepts quest if quest panel opens.

    Args:
        instance_id: NPC instance ID from game state (e.g. '1-33362128')
    """
    page = await get_page(ctx)

    adjacency = await page.evaluate("""(id) => {
        const game = window.game;
        if (!game || !game.player) return { error: 'Game not loaded' };
        let entity = game.entities && game.entities.get ? game.entities.get(id) : null;
        if (!entity && game.entities && game.entities.entities) {
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
            "dialogue_complete": False,
            "quest_opened": False,
        })

    quests_before = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )

    # Send the initial talk packet — talk_npc doesn't go through __interactNPC
    await page.evaluate("(id) => window.__talkToNPC(id)", instance_id)

    # Drive dialogue to completion
    dialogue_result = await _run_dialogue_loop(page, instance_id)

    quests_after = await page.evaluate(
        "() => JSON.stringify((window.__extractGameState() || {}).quests || [])"
    )
    quest_changed = quests_before != quests_after
    quest_opened = dialogue_result.get("quest_opened", False)

    return json.dumps({
        "instance": instance_id,
        "npc_name": adjacency.get("npc_name"),
        "dialogue_lines": dialogue_result["dialogue_lines"],
        "dialogue": dialogue_result["dialogue"],
        "dialogue_complete": True,
        "quest_opened": quest_opened or quest_changed,
        "quest_accepted": quest_opened or quest_changed,
        "last_dialogue": dialogue_result.get("last_dialogue"),
    })


async def accept_quest(ctx: Context) -> str:
    """Accept the quest shown in the quest panel. Usually not needed — interact_npc auto-accepts."""
    page = await get_page(ctx)
    await page.evaluate(
        "() => { const btn = document.getElementById('quest-button'); if (btn) btn.click(); }"
    )
    await page.wait_for_timeout(1500)
    return "Quest accept clicked"
