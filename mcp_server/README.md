# mcp_server/ — Kaetram MCP package

The custom MCP server that exposes the typed game tools to every harness
(Claude Code, Codex, Gemini, OpenCode, `play_qwen.py`). The entry point is
`mcp_game_server.py` at the repo root — it just imports this package and
calls `mcp.run(transport="stdio")`. All real code lives here.

This package was split out from a single 2K-line `mcp_game_server.py` in
commit `267b0ec`. If you remember "the file with all the tools," that's
this directory now.

## Layout

| File | What it owns |
|------|--------------|
| `core.py` | The `FastMCP` instance, the `state` dict (page handle, state dir, agent metadata), and the lifespan — which **yields immediately** so the MCP handshake completes fast. The Chromium launch, `state_extractor.js` injection, and the state/activity heartbeats start **lazily in `_ensure_browser()` on the first tool call**, not at startup. |
| `helpers.py` | Wrappers around `page.evaluate(window.__helperFn)` — the bridge between Python tools and the JS helpers exposed by `state_extractor.js`. |
| `login.py` | One-time login flow: spawn page, fill credentials, dismiss tutorial, warp to Mudwich. Called from `core.py`'s lazy browser init (`_ensure_browser`), not a tool. |
| `state_heartbeat.py` | 300 ms loop that POSTs `window.__latestGameState` to `dashboard:8080/ingest/state`, plus a 1 s tail-the-session-log → `/ingest/activity` loop. Best-effort; silent on failure. |
| `tools/observe.py` (side-effect) | On every `observe()` call, also writes `state/game_state.json` — the latest observe payload, consumed by the dashboard for live state display. |
| `utils.py` | Pure helpers (coord math, name normalization, JSON formatting). No Playwright. `normalize_quest_lists()` derives `active_quests`/`finished_quests` from the raw flat `quests` array — the shared split `query_quest` and `observe` both rely on; `quest_stage_item_progress()` computes needed/have/remaining for a stage. |
| `tools/quest.py` (current_step) | `query_quest` leads its response with `_build_current_step`: canonical facts (`accepted`/`stage`/`needed`/`have`/`remaining`) + an advisory `recommended_action` + `preconditions`, computed against live state (normalized via `utils.normalize_quest_lists`). Advisory, not an oracle — the agent verifies preconditions against `observe`. |
| `tools/observe.py` (items_progress) | `_enrich_active_quests` tags each active quest in the observe payload with `items_progress:{have,remaining}` (parallels `_enrich_mobs`). `KAETRAM_OBSERVE_COMPACT` drops the ASCII map (keeps STUCK_CHECK). |
| `resource_gates.py` | Loads `trees.json` / `rocks.json` / `foraging.json` / `fishing.json` from the Kaetram-Open install (overridable via `KAETRAM_DATA_DIR`) into a `name → {skill, level, item}` lookup. Used by `gather()` to translate "no items collected" into a structured `gate` block so the agent doesn't grind a resource it's not levelled for. |
| `mob_stats.py` | Same pattern, for `mobs.json` → `name → {level, max_hp, aggressive}`. `observe()` enriches each `nearby.mobs[]` entry with `level` + `aggressive` so the agent can compare mob level against `stats.level` directly without recalling the MOB PROGRESSION table from prompt context. |
| `js/*.js` | JavaScript snippets injected via `page.evaluate()` for complex flows (observe, shop UI state, buy packet, inventory snapshot, store nudges). |
| `tools/*.py` | One file per capability cluster — see below. Decorators (`@mcp.tool()`) register the model-visible surface at import time. |

## Tool surface (model-visible, 17 tools)

Decorators register tools when `tools/__init__.py` is imported. Search for
`@mcp.tool()` to confirm. Each file owns one capability axis:

| File | Tools |
|------|-------|
| `tools/observe.py` | `observe` |
| `tools/combat.py` | `attack`, `set_attack_style`, `respawn` |
| `tools/navigation.py` | `navigate`, `warp`, `cancel_nav`, `stuck_reset` |
| `tools/npc.py` | `interact_npc` (read-only by default; pass `accept_quest_offer=True` to commit) |
| `tools/inventory.py` | `eat_food`, `drop_item`, `equip_item` |
| `tools/shop.py` | `buy_item` |
| `tools/gathering.py` | `gather`, `loot` |
| `tools/crafting.py` | `craft_item` |
| `tools/quest.py` | `query_quest` |

`tools/test_lane.py` additionally registers two **test-only** tools —
`__test_login` and `__test_close_session` — conditionally behind
`KAETRAM_TEST_LANE=1` (set only by the e2e test lane). They are not part of the
17 model-visible tools and never reach a production / eval / data-collection agent.

If you add a tool, decorate with `@mcp.tool()` and update `prompts/system.md`
+ the action vocabulary in `dataset/DATA.md` so extraction and training stay
in sync.

## Adding a tool — checklist

1. Pick the right file in `tools/` (or create one if no axis fits).
2. Decorate with `@mcp.tool()`. Keep parameters flat; return a short
   human-readable string (the agent reads these like log lines).
3. Bridge to JS via `helpers.py` — never call `page.evaluate()` from a tool
   directly.
4. Update `prompts/system.md` (model-visible surface) and
   `dataset/DATA.md` (action vocabulary) in the same commit.
5. Add a smoke test under `tests/e2e/` if the tool gates quest progression.

## Gotchas

- `context.add_init_script(script)` in Python Playwright takes a string only —
  no second-argument substitution like Node. Embed values via f-string in
  `core.py`.
- The MCP server runs as a stdio subprocess of the harness. Do not print to
  stdout — use `log()` from `core.py` (writes to stderr).
- The browser is launched once on the first tool call (lazy init in
  `_ensure_browser`) and reused across all subsequent calls. A new `Page`
  per tool would burn 1–2 s per invocation and break
  `window.__latestGameState` continuity.
