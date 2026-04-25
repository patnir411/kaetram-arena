"""Server infrastructure: ThreadedHTTPServer, WebSocketRelay, ScreenshotWatcher."""

import asyncio
import glob
import http.server
import json
import logging
import os
import threading
import time

import websockets

from dashboard.constants import STATE_DIR, MAX_AGENTS, WS_PORT, SCREENSHOT_POLL_INTERVAL
from dashboard.handler import DashboardHandler

logger = logging.getLogger(__name__)


class ThreadedHTTPServer(http.server.HTTPServer):
    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address))
        thread.daemon = True
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


class ScreenshotWatcher:
    """Polls screenshot file mtimes and calls a callback on change.

    Auto-restarts its inner loop on exceptions so a single bad callback
    can't kill the watcher thread for the lifetime of the process.
    """

    def __init__(self, on_change):
        """on_change(agent_id, filepath, mtime) — agent_id is None for single-agent, int for multi."""
        self.on_change = on_change
        self._mtimes = {}
        self._running = False
        self._active_paths = []
        self._path_refresh_counter = 0

    def _refresh_watch_paths(self):
        """Glob all live screenshot files (agent count is dynamic, not capped at MAX_AGENTS)."""
        paths = []
        # Single-agent fallback path (only used when no /tmp/kaetram_agent_* dir exists).
        for name in ("live_screen.jpg", "screenshot.png"):
            p = os.path.join(STATE_DIR, name)
            if os.path.isfile(p):
                paths.append((None, p))
        # Multi-agent: glob discovers slots dynamically — survives agent count
        # changes and eval sandboxes added at runtime.
        for jpg in glob.glob("/tmp/kaetram_agent_*/state/live_screen.jpg"):
            try:
                # /tmp/kaetram_agent_N/state/live_screen.jpg → N
                segs = jpg.split(os.sep)
                slot_dir = next(s for s in segs if s.startswith("kaetram_agent_"))
                agent_id = int(slot_dir.replace("kaetram_agent_", ""))
                paths.append((agent_id, jpg))
            except (ValueError, StopIteration):
                continue
        self._active_paths = paths

    def _tick(self):
        """One polling tick. Returns nothing; raises on unrecoverable error."""
        # Re-discover paths every ~2s (8 cycles at 0.25s) so new agents are
        # picked up quickly and stale entries drop out without a long wait.
        self._path_refresh_counter += 1
        if self._path_refresh_counter >= 8:
            self._refresh_watch_paths()
            self._path_refresh_counter = 0

        for agent_id, filepath in self._active_paths:
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                # File disappeared (agent stopped) — drop the cached mtime so
                # the next mtime change is treated as a real update, not a no-op.
                self._mtimes.pop(filepath, None)
                continue
            prev = self._mtimes.get(filepath)
            if prev is None:
                self._mtimes[filepath] = mtime
            elif mtime != prev:
                self._mtimes[filepath] = mtime
                try:
                    self.on_change(agent_id, filepath, mtime)
                except Exception as e:
                    logger.warning("ScreenshotWatcher on_change failed: %s", e)

    def run(self):
        self._running = True
        while self._running:
            try:
                self._refresh_watch_paths()
                while self._running:
                    self._tick()
                    time.sleep(SCREENSHOT_POLL_INTERVAL)
            except Exception as e:
                # Outer auto-restart: catch anything the inner tick guard
                # missed (e.g., the refresh itself raising) and recover.
                logger.warning("ScreenshotWatcher inner loop crashed: %s — restarting in 1s", e)
                time.sleep(1)

    def stop(self):
        self._running = False


class WebSocketRelay:
    """Manages WebSocket connections and broadcasts typed messages.

    Auto-restarts the asyncio server if it dies (port-bind failure, asyncio
    exception). A bad message or a client disconnect must not kill the relay
    for the lifetime of the dashboard.
    """

    def __init__(self, host="0.0.0.0", port=WS_PORT):
        self.host = host
        self.port = port
        self.connections = set()
        self._loop = None

    async def handler(self, websocket):
        self.connections.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "connected", "ws_version": 2}))
            async for _ in websocket:
                pass  # drain client messages
        except websockets.ConnectionClosed:
            pass
        finally:
            self.connections.discard(websocket)

    # ── Typed broadcast primitive ──

    def _broadcast_typed(self, msg_type, payload):
        """Schedule a broadcast on the relay's event loop. Safe to call from
        any thread (uses run_coroutine_threadsafe). No-op if the loop is down."""
        if self._loop is None or self._loop.is_closed():
            return
        msg = json.dumps({"type": msg_type, **payload})
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        except RuntimeError as e:
            logger.debug("WS broadcast scheduling failed: %s", e)

    # ── Public, typed convenience methods ──

    def notify_screenshot(self, agent_id, mtime):
        self._broadcast_typed("screenshot", {"agent": agent_id, "ts": mtime})

    def notify_state(self, agent_id, payload, ts=None):
        self._broadcast_typed(
            "state",
            {"agent": agent_id, "payload": payload, "ts": ts if ts is not None else time.time()},
        )

    def notify_activity(self, agent_id, events, ts=None):
        self._broadcast_typed(
            "activity",
            {"agent": agent_id, "events": events, "ts": ts if ts is not None else time.time()},
        )

    def notify_heartbeat(self, agents, ts=None):
        self._broadcast_typed(
            "heartbeat",
            {"agents": agents, "ts": ts if ts is not None else time.time()},
        )

    async def _broadcast(self, message):
        if self.connections:
            websockets.broadcast(self.connections, message)

    async def _heartbeat_loop(self):
        """Periodically advertise WS health + currently-watched agent slots.

        Lets the frontend distinguish "watcher alive but agent gone" from
        "WS dead" — the latter shows no heartbeats at all.
        """
        while True:
            try:
                slots = sorted({
                    int(p.split(os.sep)[-3].replace("kaetram_agent_", ""))
                    for p in glob.glob("/tmp/kaetram_agent_*/state/live_screen.jpg")
                })
            except Exception:
                slots = []
            self.notify_heartbeat(slots)
            await asyncio.sleep(5)

    async def _run(self):
        async with websockets.serve(
            self.handler, self.host, self.port,
            ping_interval=30, ping_timeout=10, compression=None,
        ):
            # Heartbeat task lives for the lifetime of the server.
            asyncio.create_task(self._heartbeat_loop())
            await asyncio.Future()  # run forever

    def run_in_thread(self):
        def _thread_main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Outer auto-restart: if _run raises (port bind failure, asyncio
            # exception), wait 1s and retry. Today this thread silently dies
            # and the dashboard falls back to HTTP polling forever.
            while True:
                try:
                    self._loop.run_until_complete(self._run())
                except Exception as e:
                    logger.warning("WebSocketRelay crashed: %s — restarting in 1s", e)
                    time.sleep(1)
        t = threading.Thread(target=_thread_main, daemon=True, name="ws-relay")
        t.start()
        return t


# ── Module-level singleton wiring ──
# Exposed so other dashboard modules (handler.py ingest endpoints) can push
# typed messages to all connected clients without reaching into start_dashboard.

_relay_singleton: WebSocketRelay | None = None


def get_relay() -> WebSocketRelay | None:
    """Return the running WebSocketRelay (or None before start_dashboard runs)."""
    return _relay_singleton


def start_dashboard():
    """Main entry point — start the dashboard server."""
    global _relay_singleton
    ws_relay = WebSocketRelay()
    _relay_singleton = ws_relay
    ws_relay.run_in_thread()

    watcher = ScreenshotWatcher(
        on_change=lambda aid, fp, mt: ws_relay.notify_screenshot(aid, mt)
    )
    watcher_thread = threading.Thread(target=watcher.run, daemon=True, name="screenshot-watcher")
    watcher_thread.start()

    print(f"Dashboard running at http://0.0.0.0:8080")
    print(f"WebSocket relay on ws://0.0.0.0:{WS_PORT}")
    server = ThreadedHTTPServer(("0.0.0.0", 8080), DashboardHandler)
    server.serve_forever()
