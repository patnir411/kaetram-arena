"""Dashboard pytest progress hooks."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


RUN_DIR = os.environ.get("DASHBOARD_TEST_RUN_DIR")


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


class DashboardProgressPlugin:
    def __init__(self) -> None:
        self.total = 0
        self.completed = 0
        self.counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        self.current_test: str | None = None
        self.started_at = int(time.time() * 1000)
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
        self._write()

    def pytest_runtest_logstart(self, nodeid: str, location) -> None:
        self.current_test = nodeid
        self._write()
        # Explicitly print to stdout so the dashboard log stream sees it immediately
        print(f"\n{nodeid}", flush=True)

    def pytest_runtest_logreport(self, report) -> None:
        if report.when != "call":
            return
        self.completed += 1
        if report.outcome in self.counts:
            self.counts[report.outcome] += 1
        self.current_test = None
        self._write()

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self.current_test = None
        self._write()


def pytest_configure(config) -> None:
    config.pluginmanager.register(DashboardProgressPlugin(), "dashboard-progress-plugin")
