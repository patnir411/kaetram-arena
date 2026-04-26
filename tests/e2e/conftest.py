"""Shared e2e test fixtures — runs against an ambient Kaetram server.

Default lane is the **isolated test lane** (separate from data-collection
agents): game server on :9191, mongo db `kaetram_e2e`. Override per-run
via env vars below if you want to point at single-agent dev (:9001 +
`kaetram_devlopment`) or any other server.

  KAETRAM_HOST          (default 127.0.0.1)
  KAETRAM_WS_PORT       (default 9191)        — game server websocket
  KAETRAM_CLIENT_PORT   (default 9000)        — static client (shared)
  KAETRAM_MONGO_HOST    (default 127.0.0.1)
  KAETRAM_MONGO_PORT    (default 27017)
  KAETRAM_MONGO_DB      (default kaetram_e2e) — also exported into the env
                                                 so seed.py + MCP subprocesses
                                                 see the same value

Prerequisites (all assumed running before pytest invocation):
  - Kaetram server on $KAETRAM_WS_PORT (start with scripts/start-test-kaetram.sh)
  - Static client on $KAETRAM_CLIENT_PORT (the regular :9000 client is fine)
  - MongoDB on $KAETRAM_MONGO_HOST:$KAETRAM_MONGO_PORT

Per-test isolation via unique usernames — no two tests touch the same
player row. The QwenPlays autonomous `pm2 agent` is paused for the test
session so it doesn't fight with test MCP subprocesses over browser
state. Data-collection agents (`orchestrate.py` in tmux `datacol`) are
NOT paused — the test lane is on its own port + db so they don't collide.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass

import pytest


KAETRAM_HOST = os.environ.get("KAETRAM_HOST", "127.0.0.1")
KAETRAM_WS_PORT = int(os.environ.get("KAETRAM_WS_PORT", "9191"))
KAETRAM_CLIENT_PORT = int(os.environ.get("KAETRAM_CLIENT_PORT", "9000"))
MONGO_HOST = os.environ.get("KAETRAM_MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.environ.get("KAETRAM_MONGO_PORT", "27017"))
MONGO_DB = os.environ.get("KAETRAM_MONGO_DB", "kaetram_e2e")

# Export so seed.py (pymongo direct writes) and any MCP subprocess that
# inherits env land on the same db. seed.py reads KAETRAM_MONGO_DB at
# import-time, so set it before that module is imported anywhere.
os.environ["KAETRAM_MONGO_DB"] = MONGO_DB
# KAETRAM_PORT is what mcp_game_server.py reads to rewrite the browser's
# WS URL. mcp_client._server_params builds a fresh env dict per subprocess
# but falls through to this when callers don't pass `port=` explicitly.
os.environ.setdefault("KAETRAM_PORT", str(KAETRAM_WS_PORT))


def _tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class AmbientKaetram:
    """Back-compat shim — existing arena tests read `.client_url` and
    `.db_helper_url` off this. `db_helper_url` is now empty (pymongo-direct
    seed ignores it)."""
    client_url: str = f"http://{KAETRAM_HOST}:{KAETRAM_CLIENT_PORT}"
    server_ws_url: str = f"ws://{KAETRAM_HOST}:{KAETRAM_WS_PORT}"
    db_helper_url: str = ""


@pytest.fixture(scope="session")
def isolated_lane():
    """Return the ambient Kaetram connection. Skips the session if Kaetram
    or Mongo aren't running — don't fail mysteriously inside tests."""
    if not _tcp_open(KAETRAM_HOST, KAETRAM_CLIENT_PORT):
        pytest.skip(f"Kaetram client not reachable at :{KAETRAM_CLIENT_PORT}")
    if not _tcp_open(KAETRAM_HOST, KAETRAM_WS_PORT):
        pytest.skip(f"Kaetram server not reachable at :{KAETRAM_WS_PORT}")
    if not _tcp_open(MONGO_HOST, MONGO_PORT):
        pytest.skip(f"MongoDB not reachable at :{MONGO_PORT}")
    return AmbientKaetram()


def _pm2_jlist() -> list[dict]:
    try:
        r = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=10,
        )
        return json.loads(r.stdout or "[]")
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


@pytest.fixture(scope="session", autouse=True)
def pause_autonomous_agent():
    """If QwenPlays' pm2 `agent` is running, pause it for the test session
    so it doesn't compete over browser/MCP state with individual tests."""
    procs = _pm2_jlist()
    agent = next((p for p in procs if p.get("name") == "agent"), None)
    was_online = bool(
        agent and (agent.get("pm2_env") or {}).get("status") == "online"
    )
    if was_online:
        subprocess.run(["pm2", "stop", "agent"], capture_output=True, timeout=15)
        time.sleep(3)
    yield
    if was_online:
        subprocess.run(["pm2", "start", "agent", "--update-env"],
                       capture_output=True, timeout=15)


@pytest.fixture
def unique_username(request) -> str:
    """Unique username per test. Kaetram allows 16 chars max and A-Za-z0-9_.
    UUID4 hex[:6] keeps well under that."""
    slug = uuid.uuid4().hex[:6]
    return f"E2EBot_{slug}"


@pytest.fixture
def test_username(request) -> str:
    """Alias used by tests ported from KaetramGPU. Same contract as
    `unique_username` — unique per test, A-Za-z0-9_, <= 16 chars."""
    slug = uuid.uuid4().hex[:6]
    return f"TestBot_{slug}"


@pytest.fixture
def seeded_player(test_username):
    """Seed a minimal player at Mudwich. Tests that need more state should
    call ctx['reseed'](**overrides) inside the test body.

    Yields a dict with `username`, `seeded` (raw seed result), `base_kwargs`,
    and `reseed` callable. Cleanup runs unconditionally on teardown.
    """
    from tests.e2e.helpers.seed import seed_player, cleanup_player

    base = dict(
        position=(188, 157),
        hit_points=69,
        mana=44,
        inventory=[{"index": 0, "key": "bronzeaxe", "count": 1}],
    )
    cleanup_player(test_username)
    seeded = seed_player(test_username, **base)
    ctx = {"username": test_username, "seeded": seeded, "base_kwargs": base}

    def _reseed(**overrides):
        cleanup_player(test_username)
        merged = {**base, **overrides}
        ctx["seeded"] = seed_player(test_username, **merged)
        return ctx["seeded"]

    ctx["reseed"] = _reseed
    try:
        yield ctx
    finally:
        cleanup_player(test_username)
