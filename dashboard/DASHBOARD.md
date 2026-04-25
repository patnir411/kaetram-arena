# DASHBOARD.md — Developer Reference

> Read this before modifying any dashboard code. This is the dashboard subsystem of the Kaetram AI Agent project — a live observability dashboard for monitoring autonomous game-playing agents and SFT data collection.

## Architecture

```
dashboard/
├── server.py        # Entry point: ThreadedHTTPServer (:8080), WebSocketRelay (:8081, auto-restart), ScreenshotWatcher
├── handler.py       # HTTP routing (do_GET / do_POST → path dispatch), template, /hls/, /static/, /ingest/ endpoints
├── api.py           # APIMixin — all /api/* endpoints (mixed into DashboardHandler)
├── parsers.py       # Session log parsers (Claude/Codex/Gemini/OpenCode); mtime-keyed LRU cache
├── game_state.py    # Game state extraction: MongoDB (authoritative) + game_state.json (live) + log fallback
├── db.py            # MongoDB queries: player_info, skills, equipment, inventory, quests, achievements
├── constants.py     # Shared config: ports, paths, TTL constants, GZIP_MIN_BYTES, sanitize(), process checks
├── static/          # Vendored static assets (e.g. hls.min.js)
└── templates/
    └── index.html   # Single-page dashboard (all tabs, JS, CSS in one file)
```

**Two data planes per agent:**

1. **HLS livestream** — Each agent runs Xvfb + `ffmpeg x11grab` (managed by
   `orchestrate.py`) writing segments to `/tmp/hls/agent_N/{stream.m3u8,
   seg_*.ts}`. The dashboard serves these under `/hls/agent_N/*`. Decoupled
   from `observe()` cadence — tiles keep streaming during 60 s+ thinking
   turns. The frontend uses `hls.js` per card.

2. **WebSocket state push (:8081)** — `mcp_server.state_heartbeat` POSTs
   `window.__latestGameState` to `/ingest/state` (300 ms) and tails the
   session log to `/ingest/activity` (1 s). The relay rebroadcasts both to
   every connected browser as typed messages (`{type: state | activity |
   screenshot | heartbeat}`). `ScreenshotWatcher` glob-discovers agents and
   sends `notify_screenshot` on file-mtime change as a fallback channel.

**Persistent merge:** `game_state.json` (live, written by `observe()`) +
MongoDB (authoritative for quests/skills/equipment/inventory). If
`game_state.json` is stale (>2min), falls back to DB-only, then log parsing
as last resort.

**Process model:** Python `http.server` with threaded request handling. No
framework. The WebSocket relay auto-restarts on crash; `ScreenshotWatcher`
recovers from callback exceptions. Both run in the relay thread.

## Tabs

| Tab | Endpoint(s) | What it shows |
|-----|-------------|---------------|
| Overview | `/api/game-state`, `/api/live`, `/api/agents` | Live screenshot, quest progress, inventory, HP/level, activity summary |
| Activity | `/api/activity` | Full event log — tool calls, reasoning, results (expandable) |
| World | `/api/game-state` | Nearby entities, screenshot gallery |
| Sessions | `/api/sessions`, `/api/dataset-stats`, `/api/sft-stats` | Session history, cost/turns/duration, SFT pipeline stats |
| Prompt | `/api/prompt`, `/api/session-log` | System prompt viewer, personality grid, game knowledge, CLAUDE.md |
| Eval | `/api/eval/latest`, `/api/eval/live` | Eval comparison: r9-sft vs base, live split-screen + results (Glass's delta, action distributions) |

## API Endpoints

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/game-state` | GET | `?agent=N` | Merged MongoDB + live game state JSON |
| `/api/agents` | GET | — | All active agents: harness, model, personality, screenshot age, session stats |
| `/api/activity` | GET | `?agent=N` | Latest session activity feed (tool calls with human-readable summaries) |
| `/api/sessions` | GET | `?agent=N` | Past session list with cost, turns, model, duration |
| `/api/session-detail` | GET | `?name=X&log_dir=Y` | Full parsed session log (events, thinking, tool calls) |
| `/api/live` | GET | — | Mode (single/multi/none), agent count, port status, game server health |
| `/api/dataset-stats` | GET | — | Raw session count + total size |
| `/api/sft-stats` | GET | — | Extracted turns count + Qwen SFT train/val record counts |
| `/api/prompt` | GET | — | System prompt, game knowledge, personality files |
| `/api/session-log` | GET | — | session_log.md content |
| `/api/eval/latest` | GET | — | Eval comparison results (models vs base) |
| `/api/eval/live` | GET | — | Live eval sandbox status |
| `/api/raw` | GET | `?file=X` | Raw file viewer (game_state, session_log, claude_md, state_extractor, orchestrate) |
| `/api/screenshots` | GET | — | Screenshot gallery (50 most recent) |
| `/hls/agent_N/stream.m3u8` | GET | — | Per-agent HLS playlist served from `/tmp/hls/agent_N/` (allowlisted segments only) |
| `/hls/agent_N/seg_*.ts` | GET | — | HLS segment files |
| `/static/*` | GET | — | Vendored frontend assets (e.g. `hls.min.js`) |
| `/stream/agent_N` | GET | — | MJPEG fallback stream of agent's `live_screen.jpg` |
| `/ingest/state` | POST | `?agent=N` (body: state JSON) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:state}` |
| `/ingest/activity` | POST | `?agent=N` (body: events list) | Loopback from `mcp_server.state_heartbeat`; rebroadcast as `{type:activity}` |
| `/report.json` | GET | — | Auto-generated export report (regenerates if >5min stale) |

## Caching Strategy

Everything is cached to avoid redundant work on the 8-vCPU VM:

| Cache | TTL | Why |
|-------|-----|-----|
| `_agents_cache` | 5s (`AGENTS_TTL`) | Avoids re-parsing logs + port probing on every dashboard poll. Now includes `hls_age` and `hls_available`. |
| `_ss_cache` (ss -tlnp) | 5s | Avoids forking subprocess per request |
| MongoDB player state | 3s | DB only saves on autosave/logout — more frequent queries waste cycles |
| Session log parser | mtime-keyed LRU (25 files) | Re-parses only when log mtime advances. |
| Dataset / SFT stats | mtime-keyed | Avoids walking `dataset/extracted/` on every poll |
| Eval live | 1s fingerprint | `/api/eval/live` returns 304-equivalent payload when nothing changed |
| HTML template | import-time | Loaded once, never re-read (restart dashboard after template edits) |
| Gzip threshold | `GZIP_MIN_BYTES` (4KB) | Up from 1KB — small responses skip gzip overhead |

## Management Scripts

```bash
./scripts/start-dashboard.sh       # Kill existing + start on :8080 (nohup, logs to /tmp/dashboard.log)
./scripts/stop-dashboard.sh        # Graceful stop (SIGTERM then SIGKILL)
./scripts/restart-dashboard.sh     # Stop + start (use after template/code changes)
```

The orchestrator (`orchestrate.py`) auto-starts the dashboard if not running. Manual scripts are for dev iteration.

## Editing Guide

### Changing the HTML template
1. Edit `templates/index.html`
2. Run `./scripts/restart-dashboard.sh` — template is cached at import time, not re-read at runtime

### Adding a new API endpoint
1. Add the handler method to `api.py` in the `APIMixin` class
2. Add the route to `handler.py` in `do_GET()` path dispatch
3. Use `self._send_json(data)` for JSON responses (auto-gzips >1KB, sets CORS headers)
4. Sanitize any user-visible text through `sanitize()` from constants.py to strip API keys

### Adding a new dashboard tab
1. Add tab button + content div in `templates/index.html`
2. Add JS fetch logic in the `<script>` section of the same file
3. Wire up API endpoint if new data is needed (see above)
4. Restart dashboard

### Parsing a new harness log format
1. Add detection logic in `parsers.py` → `parse_session_log()` (auto-detect by reading first lines)
2. Write a `_parse_<harness>_session_log()` function following Claude/Codex patterns
3. Add tool summary mappings in `_kaetram_tool_summary()` if tool call format differs

## Gotchas

- **Template is cached at import time.** After editing `templates/index.html`, you MUST restart the dashboard. Hot reload does not exist.
- **MongoDB typo is intentional.** The database is named `kaetram_devlopment` (missing 'e') — this is how Kaetram-Open ships it. Do not "fix" it.
- **Username lookup uses metadata.json.** Each agent sandbox has `/tmp/kaetram_agent_N/metadata.json` with the actual username (supports Codex/Gemini agents that may not be "claudebotN").
- **Exit code 144 from zsh on kill is normal.** The stop script sends SIGTERM — zsh reports 128+16=144. Not an error.
- **No framework.** This is raw `http.server` + `BaseHTTPRequestHandler`. No Flask, no FastAPI, no middleware. Adding one would be overengineering for a dev-only dashboard.
- **All state is file-based or MongoDB.** No in-memory session state, no Redis. Dashboard is stateless — any instance can serve any request. This is intentional for simplicity.
- **Sanitize before serving.** `constants.sanitize()` redacts API keys, tokens, and long secret-like strings. Always wrap user-visible text content through it. Log content, prompt content, raw files — all must be sanitized.
- **MJPEG streams block the thread.** Each `/stream/agent_N` request holds a thread for the duration of the stream. The threaded server handles this fine, but don't add synchronous work to the stream loop. (HLS is preferred for new code — MJPEG is a fallback.)
- **HLS segment serving is allowlisted.** `send_hls_file` only serves `stream.m3u8` and `seg_*.ts` from `/tmp/hls/agent_N/`. Adding new file types requires editing the allowlist in `handler.py`.
- **`/ingest/*` is loopback-only.** Endpoints accept POSTs from `127.0.0.1` only; the heartbeat best-effort POSTs and silently drops on failure (the dashboard is a soft dependency for the agent).
- **No hardcoded `MAX_AGENTS`.** Agent discovery is glob-based — adding a 4th agent doesn't require dashboard config changes.

## Port Reference

| Port | Service | Notes |
|------|---------|-------|
| 8080 | Dashboard HTTP | Main UI + `/hls/agent_N/*` + `/ingest/{state,activity}` + `/static/*` |
| 8081 | Dashboard WebSocket | State, activity, screenshot, heartbeat broadcast (typed messages) |
| 9000 | Kaetram game client | Shared static files (all agents) |
| 9001 + N×10 | Game server WS | Per-agent (agent 0-8); today 9001 / 9011 / 9021 |
| 9061, 9071 | Eval game servers | r9-sft / base |
| 9191 | E2E test-lane game server | db `kaetram_e2e` |
