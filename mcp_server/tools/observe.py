"""observe() — Game state observation tool."""

import json as _json
import os

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.js import OBSERVE_SCRIPT
from mcp_server.mob_stats import mob_info
from mcp_server.utils import (
    load_quest_walkthroughs,
    normalize_quest_name,
    quest_stage_item_progress,
)

_WALKTHROUGH_BY_NAME: dict | None = None


def _walkthrough_by_name() -> dict:
    """Lazily build (and cache) a {normalized_name: quest_data} map.

    observe() runs every turn, so the walkthrough JSON is parsed once and reused.
    """
    global _WALKTHROUGH_BY_NAME
    if _WALKTHROUGH_BY_NAME is None:
        mapping: dict = {}
        try:
            for key, quest in load_quest_walkthroughs().items():
                if not isinstance(quest, dict):
                    continue
                mapping[normalize_quest_name(quest.get("name") or key)] = quest
        except (OSError, ValueError):
            mapping = {}
        _WALKTHROUGH_BY_NAME = mapping
    return _WALKTHROUGH_BY_NAME


def _enrich_active_quests(gs_obj: dict) -> dict:
    """Annotate each active quest with what the current stage still needs.

    Adds `items_progress: {have, remaining}` (computed from the live stage +
    inventory) so the agent can see "have bluelily 1, need 2 more" inline and
    continue rather than re-plan from stage 0. Omitted for stages that name no
    items. Mirrors `_enrich_mobs`: best-effort, never mutates on bad data.
    """
    if not isinstance(gs_obj, dict):
        return gs_obj
    active = gs_obj.get("active_quests")
    if not isinstance(active, list) or not active:
        return gs_obj
    by_name = _walkthrough_by_name()
    if not by_name:
        return gs_obj
    inventory = gs_obj.get("inventory")
    for q in active:
        if not isinstance(q, dict):
            continue
        quest_data = by_name.get(normalize_quest_name(q.get("name") or ""))
        if not quest_data:
            continue
        prog = quest_stage_item_progress(quest_data, q.get("stage", 0), inventory)
        if prog and not prog.get("finished"):
            q["items_progress"] = {"have": prog["have"], "remaining": prog["remaining"]}
    return gs_obj


# Spawn/respawn dungeon box (also the tutorial tile, per system.md). Agents
# respawn here (~328,892), misread it as Mudwich, and try to walk ~600 tiles
# out — but navigate can't path out; only a warp leaves. This box does NOT
# overlap Rick's Roll content (x>=420), so it won't false-positive there.
_DUNGEON_X = (300, 360)
_DUNGEON_Y = (860, 920)


def _enrich_location(gs_obj: dict) -> dict:
    """Flag when the player is in the spawn/respawn dungeon so the agent warps
    out instead of trying to walk (navigate cannot path out)."""
    if not isinstance(gs_obj, dict):
        return gs_obj
    pos = gs_obj.get("pos")
    if isinstance(pos, dict):
        x, y = pos.get("x"), pos.get("y")
        if (isinstance(x, (int, float)) and isinstance(y, (int, float))
                and _DUNGEON_X[0] <= x <= _DUNGEON_X[1]
                and _DUNGEON_Y[0] <= y <= _DUNGEON_Y[1]):
            gs_obj["location_alert"] = (
                "You are in the spawn/respawn dungeon (NOT Mudwich). navigate "
                "cannot path out of here — warp('mudwich') to leave."
            )
    return gs_obj


def _enrich_mobs(gs_obj: dict) -> dict:
    """Add `level` and `aggressive` to each nearby mob entry from mobs.json.

    The browser-side observe payload only carries `name/x/y/dist/dir/hp/max_hp/
    reachable` per mob. Cross-referencing the in-game mob name against the
    bundled stat table gives the agent the level + aggro flag inline — so it
    can compare nearby.mobs[].level against stats.level without recalling
    the MOB PROGRESSION table from prompt context.
    """
    nearby = gs_obj.get("nearby") if isinstance(gs_obj, dict) else None
    if not isinstance(nearby, dict):
        return gs_obj
    mobs = nearby.get("mobs")
    if not isinstance(mobs, list):
        return gs_obj
    for m in mobs:
        if not isinstance(m, dict):
            continue
        info = mob_info(m.get("name"))
        if not info:
            continue
        if "level" not in m:
            m["level"] = info["level"]
        if "aggressive" not in m:
            m["aggressive"] = info["aggressive"]
    return gs_obj


@mcp.tool()
async def observe(ctx: Context) -> str:
    """Observe the current game state.

    Returns a unified view optimized for decision-making:
    - Player: pos, stats, equipment, skills
    - Status: dead, stuck, nav, indoors, combat target
    - Nearby: categorized NPCs, mobs, resources, ground items — with
      direction (N/S/E/W) and distance from player
    - Inventory: stacked by item key with counts
    - Quests: active and finished, each active quest tagged with items_progress
    - Events: recent chat, combat, XP, NPC dialogue
    - ASCII map: terrain layout with entity symbols (dropped when
      KAETRAM_OBSERVE_COMPACT is set; STUCK_CHECK is always kept)
    """
    log_tool("observe")
    page = await get_page(ctx)

    state_dir = os.environ.get("KAETRAM_STATE_DIR", "/tmp")

    result = await page.evaluate(OBSERVE_SCRIPT)

    # Enrich each nearby mob with `level` + `aggressive` from the bundled
    # mob stats table. Done Python-side rather than in JS to avoid coupling
    # observe.js to the data files. Survives a missing/corrupt JSON payload
    # by leaving `result` untouched on any decode error.
    try:
        if "\n\nASCII_MAP:" in result:
            head, sep, tail = result.partition("\n\nASCII_MAP:")
            gs_obj = _json.loads(head)
            gs_obj = _enrich_mobs(gs_obj)
            gs_obj = _enrich_active_quests(gs_obj)
            gs_obj = _enrich_location(gs_obj)
            result = _json.dumps(gs_obj) + sep + tail
        else:
            gs_obj = _json.loads(result)
            gs_obj = _enrich_mobs(gs_obj)
            gs_obj = _enrich_active_quests(gs_obj)
            gs_obj = _enrich_location(gs_obj)
            result = _json.dumps(gs_obj)
    except (ValueError, TypeError):
        pass

    # Write game_state.json for the dashboard (live state, no log parsing).
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            with open(os.path.join(state_dir, "game_state.json"), "w") as f:
                f.write(gs_json)
    except Exception:
        pass

    # Optional compaction: drop the ASCII-map grid (redundant with the
    # structured `nearby` block's coords/dir/dist) while KEEPING STUCK_CHECK.
    # Roughly halves the per-turn payload → more turns per session. Off by
    # default to preserve training/eval parity; base data-collection runs
    # enable it via KAETRAM_OBSERVE_COMPACT=1. Applied after the game_state.json
    # write so the dashboard + cross-session note still see full state.
    if os.environ.get("KAETRAM_OBSERVE_COMPACT") and "\n\nASCII_MAP:" in result:
        head = result.partition("\n\nASCII_MAP:")[0]
        stuck = ""
        if "\n\nSTUCK_CHECK:" in result:
            stuck = "\n\nSTUCK_CHECK:" + result.split("\n\nSTUCK_CHECK:", 1)[1]
        result = head + stuck

    return result
