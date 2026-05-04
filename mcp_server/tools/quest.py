"""Quest query tool: query_quest."""

import json
import re

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, mcp
from mcp_server.utils import build_quest_query_response, load_quest_walkthroughs, resolve_quest_name


_SKILL_REQ_RE = re.compile(r"([A-Za-z]+)\s+(\d+)")

# Production skills the agent might need to find a station for. Lowercase
# matches Kaetram's cursorTiles namespace (see state_extractor.js
# _currentCraftingSkillName).
_PRODUCTION_SKILLS = ("cooking", "smithing", "crafting", "chiseling",
                      "fletching", "smelting", "alchemy")


def _detect_production_skills(quest: dict) -> list[str]:
    """Scan the quest's text fields for mentions of production skills.

    Returns a list of skill names the agent will likely need a station for
    (e.g. Rick's Roll → ['cooking']). Skills mentioned outside crafting
    context (e.g. "Foraging 25") are also caught — over-inclusion is
    cheap, the payload caps at 3 stations per skill anyway.
    """
    haystack = " ".join(
        str(quest.get(k) or "")
        for k in ("walkthrough", "items_needed", "tips")
    )
    for v in (quest.get("walkthrough_steps") or []):
        haystack += " " + str(v)
    for k, v in (quest.get("crafting_chain") or {}).items():
        haystack += f" {k} {v}"
    haystack_lower = haystack.lower()
    return [s for s in _PRODUCTION_SKILLS if s in haystack_lower]


def _compute_live_gate_status(requirements: dict, live: dict) -> dict:
    """Compare static `requirements` against the live `__extractGameState()`
    snapshot. Returns `{gated, blockers}` so the agent can decide-by-data:
      - gated=true  → skip this quest, pick another
      - gated=false → safe to accept

    `requirements` shape (from quest_walkthroughs.json):
      {
        "skills":           ["Foraging 25", ...],
        "quests_finished":  ["Royal Drama", ...],
        "quests_started":   [...],
        "achievements":     [...],
        "practical":        [...]            # human-readable, not auto-checked
      }
    """
    blockers: list[dict] = []
    if not isinstance(requirements, dict) or not isinstance(live, dict):
        return {"gated": False, "blockers": []}

    # Skills: "Foraging 25" → check live.skills["Foraging"].level >= 25
    live_skills = (live.get("skills") or {}) if isinstance(live, dict) else {}
    for req in requirements.get("skills", []) or []:
        if not isinstance(req, str):
            continue
        m = _SKILL_REQ_RE.search(req)
        if not m:
            continue
        skill, lvl = m.group(1), int(m.group(2))
        cur = (live_skills.get(skill) or {}).get("level", 1) if isinstance(live_skills, dict) else 1
        try:
            cur = int(cur)
        except (TypeError, ValueError):
            cur = 1
        if cur < lvl:
            blockers.append({
                "type":     "skill",
                "skill":    skill,
                "required": lvl,
                "current":  cur,
            })

    # Finished-quest prereqs.
    finished_names = {
        (q.get("name") or "").lower()
        for q in (live.get("finished_quests") or [])
        if isinstance(q, dict)
    }
    for req in requirements.get("quests_finished", []) or []:
        if isinstance(req, str) and req.lower() not in finished_names:
            blockers.append({"type": "quest_finished", "name": req})

    # Started-quest prereqs.
    started_names = {
        (q.get("name") or "").lower()
        for q in (live.get("active_quests") or [])
        if isinstance(q, dict)
    } | finished_names
    for req in requirements.get("quests_started", []) or []:
        if isinstance(req, str) and req.lower() not in started_names:
            blockers.append({"type": "quest_started", "name": req})

    # Achievements: extract a flat set of finished-achievement keys/names.
    live_achievements = set()
    for ach in (live.get("achievements") or []):
        if isinstance(ach, dict):
            if ach.get("finished") or ach.get("status") == "finished":
                k = ach.get("key") or ach.get("name")
                if k:
                    live_achievements.add(str(k).lower())
        elif isinstance(ach, str):
            live_achievements.add(ach.lower())
    for req in requirements.get("achievements", []) or []:
        if isinstance(req, str) and req.lower() not in live_achievements:
            blockers.append({"type": "achievement", "key": req})

    return {"gated": len(blockers) > 0, "blockers": blockers}


@mcp.tool()
async def query_quest(ctx: Context, quest_name: str) -> str:
    """Look up detailed walkthrough + live gate status for a specific quest.

    Returns quest status, requirements, unlocks, reward caveats, walkthrough,
    boss/recipe notes, AND a `live_gate_status` block computed against your
    current player state. **Call this BEFORE `interact_npc(accept_quest_offer=
    true)` for any Core-5 candidate** — if `live_gate_status.gated` is true,
    pick a different quest instead of accepting an unfinishable one.

    Args:
        quest_name: Exact or near-exact quest name (e.g. 'Sorcery and Stuff',
            'Scavenger', 'Royal Drama').
    """
    try:
        data = load_quest_walkthroughs()
    except FileNotFoundError:
        return json.dumps({"error": "Quest walkthrough data not found"})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Quest walkthrough data is invalid JSON: {exc}"})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    matched_name, err = resolve_quest_name(quest_name, data)
    if err:
        return json.dumps(err, indent=2)

    quest = data.get(matched_name)
    if not isinstance(quest, dict):
        return json.dumps({"error": f"Quest data for '{matched_name}' is malformed"})

    response = build_quest_query_response(matched_name, quest)

    # Compute live gate status + nearby station coords for any production
    # skills this quest needs. Best-effort: if the browser isn't reachable
    # for any reason, omit both blocks rather than fail.
    try:
        page = await get_page(ctx)
        live = await page.evaluate(
            "() => (window.__latestGameState || (window.__extractGameState && window.__extractGameState()) || null)"
        )
        if isinstance(live, dict):
            response["live_gate_status"] = _compute_live_gate_status(
                response.get("requirements") or {}, live
            )

        # Surface nearby crafting-station tiles for every production skill
        # the quest mentions. This unblocks the "I have raw shrimp but
        # craft_item('cooking', ...) returns 'No station found / Could not
        # reach station'" failure mode that has stalled Rick's Roll forever.
        skills_needed = _detect_production_skills(quest)
        if skills_needed:
            stations: dict[str, list] = {}
            for skill in skills_needed:
                tiles = await page.evaluate(
                    "(s) => window.__debugCursorTiles ? window.__debugCursorTiles(s) : null",
                    skill,
                )
                if not isinstance(tiles, dict):
                    continue
                player = tiles.get("player_pos") or {}
                px, py = player.get("x", 0), player.get("y", 0)
                matches = tiles.get("wanted_matches") or []
                ranked = sorted(
                    ({"x": m.get("x"), "y": m.get("y"),
                      "dist": abs(m.get("x", 0) - px) + abs(m.get("y", 0) - py)}
                     for m in matches if isinstance(m, dict)),
                    key=lambda t: t["dist"],
                )[:3]
                if ranked:
                    stations[skill] = ranked
            if stations:
                response["station_locations"] = stations
    except Exception:
        pass

    return json.dumps(response, indent=2)
