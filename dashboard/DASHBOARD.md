# DASHBOARD.md — Developer Reference

> Read this before modifying any dashboard code. This is the single source of truth for managing, extending, and debugging the dashboard subsystem of the Kaetram AI Agent project — a live observability surface for autonomous game-playing agents and the SFT data pipeline.

## What it is

A Python `http.server`-based dashboard (no framework) serving HTTP on `:8080` and a WebSocket relay on `:8081`. It surfaces:

- Live agent state (HP, level, position, quests, inventory, skills) merged from MongoDB + the agent's `observe()` writes.
- Per-agent HLS livestream of the game browser.
- Activity feed (tool calls, reasoning, errors) parsed incrementally from session logs.
- Past sessions with cost/turns/duration, SFT pipeline stats, eval comparison.
- A Tests tab for launching pytest runs from the UI with optional headed-browser MJPEG video.
- Restart-run controls.

Everything lives in `dashboard/` plus `dashboard.py` (one-line stub at the project root).

## Layout

```
dashboard/
├── server.py        # Entry point: ThreadedHTTPServer (:8080), WebSocketRelay (:8081, permessage-deflate)
├── handler.py       # HTTP routing (HTTP/1.1 keep-alive); HLS, /static/, /ingest/, /stream/test_run endpoints
├── api.py           # APIMixin — all /api/* endpoint methods (mixed into DashboardHandler)
├── parsers.py       # Session log parsers (Claude/Codex/Gemini/OpenCode); incremental, offset-tracked. OpenCode handler emits `talk` for plain `text` parts, splits `<think>...</think>` wrappers (CoT injected by `nim_proxy.py` for providers without `delta.reasoning_content` support) into `thinking` events with per-turn dedupe, and renders `reasoning` part-types as `thinking` directly for providers whose CoT comes through native (e.g. opencode `interleaved.field` config when honored).
├── _log_tail.py     # Offset-tracking JSONL tail helper used by parsers
├── game_state.py    # Game-state extraction: MongoDB (authoritative) + game_state.json (live, 30 s window) + log fallback
├── db.py            # MongoDB queries: player_info, skills, equipment, inventory, quests, achievements
├── test_runner.py   # Tests tab backend: pytest run lifecycle, Xvfb :198 + ffmpeg MJPEG, single-in-flight guard, persistent run history
├── constants.py     # Shared config: ports, paths, TTL constants, GZIP_MIN_BYTES, sanitize(), process checks
├── static/          # Vendored frontend assets (e.g. hls.min.js)
└── templates/
    └── index.html   # Single-page dashboard (all tabs, JS, CSS in one file)
```

## Data planes

There are three planes per agent, each with its own cadence and consumer.

### 1. HLS livestream
Each agent runs Xvfb + `ffmpeg x11grab` (managed by `orchestrate.py`) writing segments to `/tmp/hls/agent_N/{stream.m3u8, seg_*.ts}`. The dashboard serves these under `/hls/agent_N/*` (allowlisted file types only). Decoupled from `observe()` cadence — tiles keep streaming during long thinking turns. Per-agent card thumbnails use independent hls.js instances; the currently-selected agent's card video is paused (its stream plays in the hero `<video>`) so we never double-decode the same URL on the client.

### 2. WebSocket state push (:8081, permessage-deflate)
`mcp_server.state_heartbeat` POSTs a slim projection of `window.__latestGameState` (`{player_stats, player_position, current_target, nearby_count, last_xp_event}`) to `/ingest/state` every 300 ms, and tails the active session log to `/ingest/activity` every 1 s. The relay rebroadcasts these as typed messages:

| Type | Producer | Consumer effect |
|------|----------|-----------------|
| `state` | `/ingest/state` ← state_heartbeat | In-place HP/level/position update; no DOM rebuild |
| `activity` | `/ingest/activity` ← state_heartbeat | Debounced (200 ms) `/api/activity` re-fetch for the active agent |
| `heartbeat` | Relay every 30 s | Keep-alive, no UI effect; agent-slot enumeration glob-discovers `/tmp/kaetram_agent_*/state/game_state.json` |
| `restart` | `/api/restart-run` | Tabs clear local state, drop hero HLS, force-refresh `/api/agents` + `/api/live` |
| `test_event` | `dashboard/test_runner.py` | Per-test status pills, run-finish refresh, MJPEG `<img>` rebind/teardown |
| `connected` | Relay on socket open | Sets connection indicator |

### 3. Persistent merge (MongoDB + game_state.json)
`game_state.json` (live, written by `observe()`) is merged with MongoDB (authoritative for quests/skills/equipment/inventory). If `game_state.json` is stale (>30 s), falls back to DB-only, then log parsing as last resort. The 30 s window means restarts surface in the UI within seconds rather than holding pre-restart state.

## Process model

Python `http.server` with HTTP/1.1 keep-alive and threaded request handling. No framework. The WebSocket relay auto-restarts on crash and runs in its own thread.

**Restart UX is instant.** `POST /api/restart-run` invalidates every server cache that could mask the new run (`_agents_cache`, `_dataset_stats_cache`, `_sft_stats_cache`, `_eval_cache`, `_eval_live_cache`, `_ss_cache`) and broadcasts `{type:"restart"}` over the WS relay. Every connected tab clears local state, drops the hero HLS, and re-fetches `/api/agents` + `/api/live` within one RTT.

## Tabs

| Tab | Endpoint(s) | What it shows |
|-----|-------------|---------------|
| Overview | `/api/game-state`, `/api/live`, `/api/agents` | HLS hero video; **3-column row** (Quests · Inventory · Skills) for the selected agent; activity summary; HP/level/run-timer status bar |
| Activity | `/api/activity` (initial + on agent-switch) + WS push | Full event log — tool calls, reasoning, results (expandable). WS push triggers a debounced re-fetch on each new event burst. |
| Sessions | `/api/sessions`, `/api/dataset-stats`, `/api/sft-stats` | Session history, cost/turns/duration, SFT pipeline stats |
| Prompt | `/api/prompt`, `/api/session-log` | System prompt viewer, personality grid, game knowledge, CLAUDE.md |
| Eval | `/api/eval/latest`, `/api/eval/live` | Eval comparison: r10-sft vs base, live split-screen + results (Glass's delta, action distributions) |
| Tests | `/api/test/{tree,runs,run,current,cancel,reach_log}`, `/stream/test_run`, WS `{type:"test_event"}` | Pytest run launcher: collapsible suite tree, Run/Cancel/Headed toggle, live counts + per-test pills, streaming pytest stdout tail, run history with per-case status chips inline (slim cases preloaded with each run row), JSONL reach-log tail viewer on expand for reachability tests, MJPEG live video for headed runs. `KAETRAM_DEBUG=1` is the default for dashboard-launched runs (so the JSONL traces always exist). |

## API endpoints

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/game-state` | GET | `?agent=N` | Merged MongoDB + live game state JSON |
| `/api/state` | GET | — | Legacy stub — returns `{}`; live state is served by `/api/game-state` |
| `/api/agents` | GET | — | All active agents: harness, model, personality, HLS age, session stats. Username comes from `metadata.json` and varies by harness — `ClaudeBot{N}` / `CodexBot{N}` / `GeminiBot{N}`; the in-house Qwen harness uses personality-based names `QwenGrinder` / `QwenCompletionist` / `QwenExplorer`; opencode splits by model family — `BigQwenBot{N}` / `GrokBot{N}` / `DeepSeekBot{N}` / `OpenCodeBot{N}`. See `cli_adapter.opencode_bot_prefix` and `orchestrate.Orchestrator.setup` (qwen_username_map). |
| `/api/activity` | GET | `?agent=N` | Latest session activity feed (incremental parser; `{events, turn, cost, tokens, model}`) |
| `/api/sessions` | GET | `?agent=N` | Past session list with cost, turns, model, duration |
| `/api/session-detail` | GET | `?name=X&log_dir=Y` | Full parsed session log (events, thinking, tool calls) |
| `/api/live` | GET | — | Mode (single/multi/none), agent count, port status, run elapsed/remaining |
| `/api/dataset-stats` | GET | — | Raw session count + total size |
| `/api/sft-stats` | GET | — | Extracted turns count + Qwen SFT train/val record counts |
| `/api/prompt` | GET | — | System prompt, game knowledge, personality files |
| `/api/quest-walkthroughs` | GET | — | `prompts/quest_walkthroughs.json` (mtime-cached) — used for dashboard hover tooltips |
| `/api/session-log` | GET | — | session_log.md content |
| `/api/eval/latest` | GET | — | Eval comparison results (models vs base) |
| `/api/eval/live` | GET | — | Live eval sandbox status (TTL fast-path: skips fingerprint glob inside cache window) |
| `/api/raw` | GET | `?file=X` | Raw file viewer (game_state, session_log, claude_md, state_extractor, orchestrate) |
| `/api/restart-run` | POST | body: `{hours, grinder, completionist, explorer_tinkerer, harness}` | Kicks off restart-agent.sh; invalidates server caches and WS-broadcasts `{type:"restart"}` |
| `/api/test/tree` | GET | — | Cached `pytest --collect-only` tree (60 s TTL, invalidated on run finish) |
| `/api/test/runs` | GET | — | All persisted test runs newest-first (LRU 20 under `/tmp/test_runs/<id>/`) |
| `/api/test/run` | GET | `?id=<run_id>` | Full detail for one run: meta, junit summary, log_tail |
| `/api/test/run` | POST | body: `{suite, markers, headed}` | Start a pytest run. 409 if another run is in flight |
| `/api/test/cancel` | POST | — | Cancel the in-flight run, if any |
| `/api/test/current` | GET | — | Meta of the in-flight run, or `{current: null}` |
| `/api/test/reach_log` | GET | `?test=<node_name>` | Tail (~200 KB) of `sandbox/<slot>/reachability_logs/<test_name>.jsonl` for post-hoc per-test debug. Surfaced in the Tests tab when a reachability case is expanded. |
| `/stream/test_run` | GET | `?run=<id>` | MJPEG stream of `/tmp/test_run/frame.jpg` (only populated during a headed run) |
| `/hls/agent_N/stream.m3u8` | GET | — | Per-agent HLS playlist served from `/tmp/hls/agent_N/` (allowlisted segments only) |
| `/hls/agent_N/seg_*.ts` | GET | — | HLS segment files |
| `/static/*` | GET | — | Vendored frontend assets (e.g. `hls.min.js`) |
| `/ingest/state` | POST | `?agent=N` (body: slim state JSON) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:state}` |
| `/ingest/activity` | POST | `?agent=N` (body: events list) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:activity}` |
| `/ingest/test_event` | POST | body: `{run_id, event, payload}` | CLI shim hello — terminal-launched pytest runs surface in the Tests tab |
| `/report.json` | GET | — | Auto-generated export report (regenerates if >5 min stale) |

## Caching strategy

Every dashboard worker is single-process; caches keep the UI responsive on the 8-vCPU VM.

| Cache | TTL | Why |
|-------|-----|-----|
| `_agents_cache` | 15 s (`AGENTS_CACHE_TTL`) | Avoids re-parsing logs + port probing on every poll. Includes `hls_age` and `hls_available`. **Filters out agents whose game-server port isn't currently listening** (kills stale-sandbox UI ghosts after a partial resume — e.g. `--opencode 1` leaves agent_1/2 metadata on disk but only agent_0's port is bound). `/api/live` applies the same filter for the agent count. Invalidated on restart. |
| `_ss_cache` (ss -tlnp) | 5 s | Avoids forking subprocess per request. Invalidated on restart. |
| MongoDB player state | 3 s | DB only saves on autosave/logout — more frequent queries waste cycles. |
| Session log parser | offset-tracked, persistent across calls (LRU 25 files) | Incremental tail: O(new bytes), not O(file size). On a 4 MB log, cold ≈ 250 ms, warm ≈ 0.3 ms. |
| `live_session_stats` | offset-tracked, persistent (LRU 25 files) | Same incremental pattern — used by the heavy `/api/agents` path. |
| Dataset / SFT stats | 30 s mtime-bucketed | Avoids walking `dataset/extracted/` on every poll. Invalidated on restart. |
| Eval-live | 1 s TTL fast-path; fingerprint glob only past TTL | Eval tab polls 2 s. |
| Eval results | mtime-keyed | Invalidated on restart. |
| Test collect tree | 60 s, invalidated on run finish | Pytest collect is the slowest non-IO step in the Tests tab. |
| HTML template | import-time | Loaded once, never re-read (restart dashboard after template edits). |
| Gzip threshold | `GZIP_MIN_BYTES` (4 KB) | Small payloads skip gzip overhead. |

## Frontend refresh model

Two intervals + WS push:

- **`refreshFast` every 3 s** — `/api/live`, `/api/game-state`. In-place updates of mission/inventory/skills/status. Run timer is anchor-and-tick (1 s local interval, re-anchors on fresher server data; see `_runAnchor` in index.html). Skipped while `document.hidden`.
- **`refreshSlow` every 8 s** — `/api/agents` (rebuilds `agentList`), `/api/sessions`, eval-tab indicator, then `refreshActivity()`. Skipped while `document.hidden`.
- **`refreshActivity` (WS-driven)** — On each `{type:"activity"}` push the frontend debounces a single `/api/activity` fetch (200 ms). The 8 s steady poll is a safety net.
- **`onStateNotification`** — In-place HP/level overlay update; no DOM destruction.
- **`onRestartNotification`** — Clears `agentList`, `liveState`, feed counts; tears down hero HLS; force-runs `refreshFast()` + `refreshSlow()`.
- **`onTestEvent`** — Drives the Tests tab: per-test status pills, run-finish refresh of the history list, MJPEG `<img>` rebind/teardown on `session_start`/`session_finish`. Tests-tab polling (`/api/test/runs`, `/api/test/current`) only runs while that tab is the active one.

On `visibilitychange` to visible, both refresh loops fire once immediately so a returning user doesn't see a stale snapshot.

## Skills panel (Overview tab, 3rd column)

Lives alongside Quests + Inventory in the `.grid-3` row (responsive: 3 cols desktop, 2 cols ≤1200px, 1 col ≤800px). Renders for the selected agent only — same pattern as Quests/Inventory. Data source: `gs.skills` from `/api/game-state`, no extra endpoint.

Per-skill row shows: name, current level (`L##`), an XP bar to the next level, and `current / next xp (NN%)` underneath. The per-level XP table is a one-time JS-side mirror of Kaetram's server formula (`window._kaetramLevelExp`, defined in `index.html` near `loadQuestWalkthroughs`); same RuneScape-style cumulative curve as `Kaetram-Open/packages/server/src/info/loader.ts:44`. No backend XP-table endpoint needed.

**Gated-skill highlighting** is the non-obvious part. For each *active, unfinished* quest in `gs.quests`, the panel looks up its walkthrough in `questWalkthroughs` (already loaded for tooltips) and parses `requirements.skills` strings like `"Foraging 25"`. If the player's level for that skill is below the requirement, the skill is marked gated:

- Sorted to the top of the list (most-blocking gate first if multiple).
- Amber `NEED L25` chip beside the name; amber XP bar fill.
- Background tint (`.skill-row.gated`) for at-a-glance distinction.

This means the moment an agent accepts Herbalist's Desperation, Foraging jumps to the top of the panel with a visible gap-to-target — surfacing the same gate the agent should be reading via `query_quest`'s `live_gate_status` block (see `mcp_server/tools/quest.py`).

## Tests-tab subsystem (`dashboard/test_runner.py`)

Self-contained pytest run lifecycle managed by the dashboard. Independent of the agent runtime — separate display, separate frame file, separate WS event channel.

**Lifecycle of one run:**

1. `POST /api/test/run` → `test_runner.start(suite, markers, headed)`. Single in-flight guard returns 409 if another run is active.
2. **Orphan sweep** before each run: walks `/proc`, kills stray `Xvfb`/`ffmpeg` whose `comm` + `cmdline` match the test display. Substring matcher requires the leading colon (`":198"`) so it won't false-match `:1980`.
3. If `headed=true`, spawns:
   - `Xvfb :198` (separate display from the agent fleet)
   - `ffmpeg x11grab` writing a single overwriting JPEG to `/tmp/test_run/frame.jpg` (MJPEG, not HLS — see gotcha below)
4. Spawns `pytest -p tests.dashboard_progress_plugin --junit-xml=…` with `DASHBOARD_TEST_RUN_DIR` and `DISPLAY=:198` set when headed.
5. **Reaper thread** waits on the pytest process; on exit, tears down ffmpeg + Xvfb and emits a single `session_finish` event over WS. Single source of truth — survives hard pytest crashes.
6. Run dir at `/tmp/test_runs/<id>/` persists `progress.json`, `junit.xml`, `log.txt`, `meta.json`. LRU pruned past 20 runs.
7. `pytest --collect-only` tree cached 60 s; invalidated on run finish so newly-added tests show up next time.

**Two run sources:**
- **Dashboard "Run" button** — full lifecycle above. Only path that supports headed video.
- **`scripts/run-tests-with-dashboard.sh`** (CLI shim) — terminal-launched pytest. POSTs `run_started` to `/ingest/test_event` so the run appears in the tab; uses the `dashboard_progress_plugin` for streaming progress; **headless only**.

**Frame file (`/tmp/test_run/frame.jpg`):** lockstep updated by ffmpeg, served via the same `send_mjpeg_stream` handler used for legacy agent thumbnails. The frontend `<img src="/stream/test_run?run=…">` is rebound per-run id so cached connections from a previous run don't get the new run's frames.

**Test lane prerequisites** (must be running before a Tests-tab run):
- Kaetram game server on `:9191` with `NODE_ENV=e2e` → `kaetram_e2e` MongoDB database. Start with `scripts/start-test-kaetram.sh`.
- Static client on `:9000` (shared with the data-collection lane).
- MongoDB on `:27017` (`kaetram-mongo` Docker container).

`tests/e2e/conftest.py` exports `KAETRAM_PORT=9191` and `KAETRAM_MONGO_DB=kaetram_e2e` into the env so the MCP subprocess and `seed.py` direct-pymongo writes both target the test lane. If a test fails with "wrong username or password" at login, the MCP subprocess is talking to the wrong server — see the test-lane gotcha below.

## Management scripts

```bash
./scripts/start-dashboard.sh       # Kill existing + start on :8080 (nohup, logs to /tmp/dashboard.log)
./scripts/stop-dashboard.sh        # Graceful stop (SIGTERM then SIGKILL)
./scripts/restart-dashboard.sh     # Stop + start (use after template/code changes)
```

The orchestrator (`orchestrate.py`) auto-starts the dashboard if not running. Manual scripts are for dev iteration.

## Editing guide

### Changing the HTML template
1. Edit `templates/index.html`.
2. Run `./scripts/restart-dashboard.sh` — template is cached at import time, not re-read at runtime.

### Adding a new API endpoint
1. Add the handler method to `api.py` in the `APIMixin` class.
2. Add the route to `handler.py` in `do_GET()` / `do_POST()` path dispatch.
3. Use `self._send_json(data)` for JSON responses (auto-gzips >4 KB, sets CORS + `Content-Length` headers).
4. Sanitize any user-visible text through `sanitize()` from `constants.py` to strip API keys.

### Adding a new dashboard tab
1. Add tab button + content div in `templates/index.html`.
2. Add JS fetch logic in the `<script>` section of the same file.
3. Wire up API endpoint if new data is needed.
4. Restart dashboard.

### Adding a new WS message type
1. Add `notify_<type>(...)` to `WebSocketRelay` in `server.py` (mirror existing `notify_state/activity/heartbeat/restart/test_event`).
2. Call it from wherever the event originates (handler, ingest, watcher, test_runner).
3. Add a `case '<type>'` in the frontend `ws.onmessage` switch in `templates/index.html`.

### Parsing a new harness log format
1. Add detection logic in `parsers.py` → `parse_session_log()` (auto-detect by reading first lines).
2. Write `_init_<harness>_acc()`, `_consume_<harness>_obj(state, obj)`, `_finalize_<harness>_acc(state)` matching the Claude/Codex pattern. The `_incremental_parse` driver handles offset tracking + rotation.
3. Add tool summary mappings in `_kaetram_tool_summary()` if tool call format differs.

## Gotchas

- **Template is cached at import time.** After editing `templates/index.html`, you MUST restart the dashboard. Hot reload does not exist.
- **MongoDB typo is intentional.** The data-collection database is `kaetram_devlopment` (missing 'e') — that's how Kaetram-Open ships it. The test lane uses `kaetram_e2e`. Don't "fix" the typo.
- **Username lookup uses metadata.json.** Each agent sandbox has `/tmp/kaetram_agent_N/metadata.json` with the actual username (supports Codex/Gemini agents that may not be `claudebotN`).
- **HTTP/1.1 keep-alive requires `Content-Length` on every body-bearing response.** If you add a new response path, set `Content-Length` (or use 204) — otherwise the persistent connection stalls. The MJPEG streaming path opts out via `Connection: close`.
- **The state heartbeat ships only a slim projection.** If you add a UI panel that needs `inventory`/`quests`/`achievements`/`nearby_entities`/ASCII map at near-real-time, fetch via `/api/game-state` (already on the 3 s `refreshFast` loop). Don't re-fatten the WS payload — it goes to every connected tab × 3.3 Hz.
- **Activity events go through `/api/activity`, not parsed in the frontend.** The WS `{type:"activity"}` push is a *trigger*: the frontend debounces a single fetch. Raw JSONL on the wire would require duplicating the parser in JS.
- **No framework.** This is raw `http.server` + `BaseHTTPRequestHandler`. No Flask, no FastAPI, no middleware.
- **All state is file-based or MongoDB.** No in-memory session state, no Redis. Any dashboard instance can serve any request.
- **Sanitize before serving.** `constants.sanitize()` redacts API keys, tokens, and long secret-like strings. Always wrap user-visible text content through it. Log content, prompt content, raw files — all must be sanitized.
- **MJPEG streams block the thread.** Each `/stream/agent_N` or `/stream/test_run` request holds a thread for the duration of the stream. The threaded server handles this, but don't add synchronous work to the stream loop.
- **HLS segment serving is allowlisted.** `send_hls_file` only serves `stream.m3u8` and `seg_*.ts` from `/tmp/hls/agent_N/`. Adding new file types requires editing the allowlist in `handler.py`.
- **`/ingest/*` is loopback-only.** Endpoints accept POSTs from `127.0.0.1` only; the heartbeat best-effort POSTs and silently drops on failure (the dashboard is a soft dependency for the agent). 2 MB body cap (`INGEST_MAX_BYTES`).
- **Agent discovery is glob-based.** `MAX_AGENTS` is consulted only by `/api/live`; adding a 4th agent doesn't require dashboard config changes.
- **Selected-agent card video is paused.** `_bindCardVideo` early-outs when `agentId === selectedAgent` and hides that card's `<video>`. The stream plays in the hero. Don't accidentally remove the early-out — both decoders running is wasted client CPU.
- **Grid rebuild destroys all `_cardHls` instances.** Before `container.innerHTML = html` we tear down every cached hls.js instance because innerHTML detaches the `<video>` elements they reference. The MutationObserver-driven `bindAllCardVideos` rebinds ~200 ms later.
- **Tests tab uses MJPEG, not HLS.** Short test runs (10–60 s) race the HLS segment writer vs the browser's segment fetcher, flipping the playlist URL between 200 and 404 in millisecond windows. MJPEG (single overwriting JPEG via `multipart/x-mixed-replace`) is lockstep reliable. Don't migrate the Tests tab to HLS.
- **Test display is `:198`, substring matched as `":198"`.** With the leading colon so the orphan sweep doesn't kill `:1980+`. If you ever need a second test display, pick something that won't collide with this prefix rule (e.g. `:298`, not `:1980`).
- **Single in-flight test run.** Concurrent `POST /api/test/run` returns 409. The dashboard "Run" button disables itself; the CLI shim doesn't enforce this — running both at once will collide on `/tmp/test_run/frame.jpg` and `:198`.
- **Reaper thread is the only source of `session_finish`.** Don't emit it from anywhere else — frontend assumes exactly one. Pytest crashing mid-run still produces it because the reaper waits on the process, not on plugin events.
- **`/tmp/test_run/frame.jpg` lifetime.** Persists between runs (last frame of the previous headed run remains until the next `ffmpeg x11grab` overwrites it). The frontend rebinds the `<img>` per-run id so a stale frame can't carry over visually, but the bytes are still on disk until the next headed run starts.
- **Tests-tab polling is tab-gated.** `/api/test/runs` and `/api/test/current` only poll while the Tests tab is active — `pollTimer` starts on tab activation, stops on tab switch. Don't add unconditional polling here.
- **Test-lane port mismatch causes "wrong username or password" at login.** `tests/e2e/conftest.py` sets `KAETRAM_PORT=9191` and `KAETRAM_MONGO_DB=kaetram_e2e`. If `mcp_client.py` reads the wrong env var name (canonical: `KAETRAM_PORT`; legacy fallback: `GAME_WS_PORT`), the MCP subprocess connects to the data-collection lane (`:9001` → `kaetram_devlopment`) where seeded test players don't exist, and login fails. Always preserve `KAETRAM_PORT` when building the MCP subprocess env.

## Port reference

| Port | Service | Notes |
|------|---------|-------|
| 8080 | Dashboard HTTP | HTTP/1.1 keep-alive. UI + `/hls/agent_N/*` + `/ingest/{state,activity,test_event}` + `/static/*` + `/stream/test_run` |
| 8081 | Dashboard WebSocket | permessage-deflate. State, activity, heartbeat, restart, test_event broadcast (typed messages) |
| 9000 | Kaetram game client | Static files (shared by all lanes) |
| 9001 + N×10 | Data-collection game-server WS | Per-agent (agent 0–8); db `kaetram_devlopment` |
| 9061, 9071 | Eval game servers | r10-sft / base |
| 9191 | Test-lane game server | db `kaetram_e2e`. Required by the Tests tab. Start with `scripts/start-test-kaetram.sh` |
| 27017 | MongoDB | `kaetram-mongo` Docker container; per-lane isolation by db name |

> **Display reference**: Tests tab uses Xvfb `:198` (substring matched as `":198"` to avoid colliding with `:1980+`).
