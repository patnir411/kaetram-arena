"""State heartbeat — pushes window.__latestGameState to the dashboard WS relay.

The MCP server already calls extractGameState() on every observe(). For the
dashboard we want the same data on a fixed cadence regardless of agent
inference timing — otherwise tiles freeze for 20-60s during long thinking
turns. This task runs in the same event loop as the page and POSTs JSON to
the dashboard's localhost ingest endpoint, which in turn broadcasts to
every connected WebSocket client.

The dashboard is a soft dependency. POST failures are silent so the heartbeat
never interferes with agent gameplay.
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

DASHBOARD_INGEST_HOST = os.environ.get("KAETRAM_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_INGEST_PORT = int(os.environ.get("KAETRAM_DASHBOARD_PORT", "8080"))

# Resolve agent_id once, at heartbeat startup. KAETRAM_SCREENSHOT_DIR has the
# form /tmp/kaetram_agent_N/state — a robust place to read the slot from.
def _resolve_agent_id() -> int | None:
    sd = os.environ.get("KAETRAM_SCREENSHOT_DIR", "")
    for seg in sd.split(os.sep):
        if seg.startswith("kaetram_agent_"):
            try:
                return int(seg.replace("kaetram_agent_", ""))
            except ValueError:
                return None
    return None


def _post_json(path: str, body: dict | list) -> None:
    """Synchronous POST. Called from a thread pool executor so the event loop
    doesn't block on the HTTP round-trip.

    Note: `urllib.request.urlopen` opens a fresh socket per call — fine at our
    current ~10 RPS to localhost (3 agents × 3.3 Hz heartbeat). If we scale to
    >5 agents or move the dashboard off-host, switch to `urllib3.PoolManager`.
    """
    url = f"http://{DASHBOARD_INGEST_HOST}:{DASHBOARD_INGEST_PORT}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except (urllib.error.URLError, OSError, TimeoutError):
        # Dashboard not running, or unreachable — best-effort.
        pass


async def state_heartbeat_loop(state: dict, interval: float = 0.3) -> None:
    """Push window.__latestGameState to the dashboard every `interval` seconds.

    `state` is the same dict managed by mcp_server.core (carries `page`).
    Stops automatically when the page is closed/None for ~30 s
    (100 consecutive ticks at the default 0.3 s interval) — long enough to
    tolerate slow tool calls without giving up on dashboard streaming.
    """
    agent_id = _resolve_agent_id()
    qs = f"?agent={agent_id}" if agent_id is not None else ""
    consecutive_misses = 0

    while True:
        try:
            page = state.get("page")
            if page is None:
                consecutive_misses += 1
                if consecutive_misses > 100:
                    return
                await asyncio.sleep(interval)
                continue
            consecutive_misses = 0

            try:
                payload = await page.evaluate(
                    "window.__latestGameState ? "
                    "JSON.parse(JSON.stringify(window.__latestGameState)) : null"
                )
            except Exception as e:
                log.debug("state_heartbeat evaluate failed: %s", e)
                payload = None

            if payload is not None:
                # Run POST in a thread to avoid blocking the asyncio loop.
                await asyncio.get_event_loop().run_in_executor(
                    None, _post_json, f"/ingest/state{qs}", payload
                )

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.debug("state_heartbeat loop error: %s", e)
            await asyncio.sleep(interval)


async def activity_heartbeat_loop(state: dict, interval: float = 1.0) -> None:
    """Tail the active session log incrementally and push new events.

    KAETRAM session logs are written by the CLI harness (Claude/Codex/etc)
    to dataset/raw/agent_N/logs/session_*.log. We tail the most-recent one
    and emit new tool_use events to the dashboard. This is decoupled from
    the screenshot/state heartbeat so a busy parser doesn't slow down state
    updates.
    """
    agent_id = _resolve_agent_id()
    if agent_id is None:
        return
    qs = f"?agent={agent_id}"

    log_root = os.path.join(
        os.environ.get("KAETRAM_LOG_ROOT", ""),
    ) if os.environ.get("KAETRAM_LOG_ROOT") else None
    if not log_root:
        # Default to the project's dataset/raw layout.
        project_dir = os.environ.get("KAETRAM_PROJECT_DIR", "")
        if not project_dir:
            # KAETRAM_EXTRACTOR ends with state_extractor.js inside the project root.
            ext = os.environ.get("KAETRAM_EXTRACTOR", "")
            project_dir = os.path.dirname(ext) if ext else ""
        log_root = os.path.join(project_dir, "dataset", "raw", f"agent_{agent_id}", "logs")
    if not os.path.isdir(log_root):
        return

    offsets: dict[str, int] = {}
    tick = 0
    while True:
        try:
            try:
                logs = [os.path.join(log_root, f) for f in os.listdir(log_root) if f.endswith(".log")]
            except FileNotFoundError:
                await asyncio.sleep(interval * 4)
                continue
            if not logs:
                await asyncio.sleep(interval * 4)
                continue
            tick += 1
            # Every ~60 ticks (≈1 min at interval=1.0s), drop offsets for log
            # files that have been deleted or rotated out — prevents unbounded
            # growth across long runs.
            if tick % 60 == 0 and offsets:
                offsets = {k: v for k, v in offsets.items() if os.path.exists(k)}
            latest = max(logs, key=os.path.getmtime)
            offset = offsets.get(latest, 0)
            try:
                size = os.path.getsize(latest)
            except OSError:
                await asyncio.sleep(interval)
                continue
            if size < offset:
                # File rotated/truncated — reset.
                offset = 0
            new_events: list[dict] = []
            if size > offset:
                try:
                    with open(latest, "rb") as f:
                        f.seek(offset)
                        chunk = f.read()
                    # Don't advance past a partial line at the chunk boundary —
                    # the harness may be mid-write. Process up to the last \n
                    # only, and keep the rest for the next pass.
                    last_newline = chunk.rfind(b"\n")
                    if last_newline == -1:
                        await asyncio.sleep(interval)
                        continue
                    processable = chunk[:last_newline + 1]
                    offsets[latest] = offset + len(processable)
                    for line in processable.decode("utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line or not line.startswith("{"):
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        # Cheap filter: only forward events likely to interest the UI.
                        t = obj.get("type", "")
                        if t in ("tool_use", "tool_result", "step_finish",
                                 "assistant", "rate_limit_event",
                                 "text", "thinking"):
                            new_events.append(obj)
                except OSError as e:
                    log.debug("activity_heartbeat read failed: %s", e)

            if new_events:
                await asyncio.get_event_loop().run_in_executor(
                    None, _post_json, f"/ingest/activity{qs}", new_events
                )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.debug("activity_heartbeat loop error: %s", e)
            await asyncio.sleep(interval)
