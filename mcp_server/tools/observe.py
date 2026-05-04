"""observe() — Game state observation tool."""

import json as _json
import os

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.js import OBSERVE_SCRIPT
from mcp_server.mob_stats import mob_info


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

    Returns a unified view (~700-900 tokens) optimized for decision-making:
    - Player: pos, stats, equipment, skills
    - Status: dead, stuck, nav, indoors, combat target
    - Nearby: categorized NPCs, mobs, resources, ground items — with
      direction (N/S/E/W) and distance from player
    - Inventory: stacked by item key with counts
    - Quests: active and finished
    - Events: recent chat, combat, XP, NPC dialogue
    - ASCII map: terrain layout with entity symbols
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
            result = _json.dumps(gs_obj) + sep + tail
        else:
            gs_obj = _json.loads(result)
            gs_obj = _enrich_mobs(gs_obj)
            result = _json.dumps(gs_obj)
    except (ValueError, TypeError):
        pass

    # Write game_state.json for dashboard (live state, no log parsing needed)
    # AND a compact quest_resume.json that the orchestrator injects into the
    # next session's prompt — gives the next session a "where I was" anchor
    # so multi-stage quests (Rick's Roll, Herbalist's Desperation) can
    # survive the per-session context reset.
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            gs_path = os.path.join(state_dir, "game_state.json")
            with open(gs_path, "w") as f:
                f.write(gs_json)
            try:
                gs_obj = _json.loads(gs_json)  # already enriched above

                resume = {
                    "level": (gs_obj.get("stats") or {}).get("level"),
                    "pos":   gs_obj.get("pos"),
                    "active_quests":   gs_obj.get("active_quests")   or [],
                    "finished_quests": [
                        q.get("name") for q in (gs_obj.get("finished_quests") or [])
                        if isinstance(q, dict)
                    ],
                    "inventory_summary": gs_obj.get("inventory_summary"),
                    # Last few in-game chat events — surfaces things like
                    # "no space in inventory", "wait N seconds", quest
                    # acknowledgements that the agent may have missed.
                    "recent_chat": [
                        e.get("msg") for e in (gs_obj.get("events") or [])
                        if isinstance(e, dict) and e.get("type") == "chat" and e.get("msg")
                    ][-6:],
                }
                with open(os.path.join(state_dir, "quest_resume.json"), "w") as f:
                    _json.dump(resume, f, indent=2)
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    return result
