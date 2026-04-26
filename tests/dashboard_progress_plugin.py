"""Dashboard pytest progress hooks.

Loaded explicitly via `-p tests.dashboard_progress_plugin` from the
dashboard's test_runner or the CLI shim — NOT auto-loaded by pytest.ini.

Two outputs:
  1. progress.json file written to $DASHBOARD_TEST_RUN_DIR (offline use,
     run history).
  2. Best-effort HTTP POST to the dashboard's /ingest/test_event so the
     Tests tab updates live. Silent on failure (dashboard is a soft dep).

Reaper-only session_finish: this plugin emits collection + per-test
events, but does NOT post `session_finish`. The dashboard's reaper
thread fires that exactly once after pytest exits, so it survives even
a hard pytest crash.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path


RUN_DIR = os.environ.get("DASHBOARD_TEST_RUN_DIR")
_PORT = os.environ.get("DASHBOARD_HTTP_PORT", "8080")
INGEST_URL = f"http://127.0.0.1:{_PORT}/ingest/test_event"
POST_TIMEOUT = 0.25  # seconds — must never block tests


def _progress_path() -> Path | None:
    if not RUN_DIR:
        return None
    return Path(RUN_DIR) / "progress.json"


def _write_progress(payload: dict) -> None:
    path = _progress_path()
    if path is None:
        return
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _post(event: str, payload: dict) -> None:
    """Best-effort POST to the dashboard. Swallows everything."""
    if not RUN_DIR:
        return
    run_id = Path(RUN_DIR).name
    body = json.dumps({"run_id": run_id, "event": event, "payload": payload}).encode()
    req = urllib.request.Request(
        INGEST_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=POST_TIMEOUT).read()
    except Exception:
        pass


class DashboardProgressPlugin:
    def __init__(self) -> None:
        self.total = 0
        self.completed = 0
        self.counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        self.current_test: str | None = None
        self.started_at = int(time.time() * 1000)
        self._test_started_at: dict[str, float] = {}
        self._write()

    def _write(self) -> None:
        _write_progress(
            {
                "started_at": self.started_at,
                "total": self.total,
                "completed": self.completed,
                "counts": self.counts,
                "current_test": self.current_test,
                "updated_at": int(time.time() * 1000),
            }
        )

    def pytest_collection_finish(self, session) -> None:
        self.total = len(session.items)
        nodeids = [item.nodeid for item in session.items]
        self._write()
        _post("collection_finish", {"total": self.total, "nodeids": nodeids})

    def pytest_runtest_logstart(self, nodeid: str, location) -> None:
        self.current_test = nodeid
        self._test_started_at[nodeid] = time.time()
        self._write()
        # Echo to stdout so log streamers / log.txt see it immediately.
        print(f"\n{nodeid}", flush=True)
        _post("runtest_start", {"nodeid": nodeid})

    def pytest_runtest_logreport(self, report) -> None:
        # Skipped tests fire a setup-phase report and never reach `call`.
        # Failed setup also matters. Otherwise we only count the call phase.
        if report.when == "setup":
            if report.outcome == "skipped":
                outcome = "skipped"
            elif report.outcome == "failed":
                outcome = "error"  # treat setup failures as errors
            else:
                return  # successful setup — wait for the call report
        elif report.when == "call":
            outcome = report.outcome
        else:
            return  # teardown — ignore
        self.completed += 1
        if outcome in self.counts:
            self.counts[outcome] += 1
        nodeid = report.nodeid
        started = self._test_started_at.pop(nodeid, None)
        duration = (time.time() - started) if started is not None else getattr(report, "duration", 0.0)
        self.current_test = None
        self._write()
        _post(
            f"runtest_{outcome}",
            {
                "nodeid": nodeid,
                "duration": duration,
                "completed": self.completed,
                "total": self.total,
                "counts": dict(self.counts),
            },
        )

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        # Reaper thread in dashboard.test_runner is the single source of
        # truth for session_finish — do NOT post it here. Just flush
        # the final progress.json so an offline tail picks up the totals.
        self.current_test = None
        self._write()


def pytest_configure(config) -> None:
    config.pluginmanager.register(DashboardProgressPlugin(), "dashboard-progress-plugin")
