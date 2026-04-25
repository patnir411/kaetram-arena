"""Per-test debug collector for reachability tests.

Writes a JSONL trace of actions + events + observe snapshots to
`sandbox/<slot>/reachability_logs/<test_name>.jsonl` and prints a compact
summary to stderr. Gated by `KAETRAM_DEBUG=1` — off by default so normal
runs stay quiet.

Integration points:
  - `navigate_long` in conftest.py calls `.hop_start()`, `.hop_end()`,
    `.stall_snapshot()` around each hop.
  - `logged_call_tool(session, name, args, debug=...)` wraps every MCP
    tool call so the args + result preview land in the trace.
  - Tests request the `test_debug` fixture and the collector is finalised
    in the fixture teardown.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[5]
_CURRENT_TEST_DEBUG: contextvars.ContextVar["TestDebugLog | None"] = contextvars.ContextVar(
    "reachability_current_test_debug",
    default=None,
)


def _debug_enabled() -> bool:
    return os.environ.get("KAETRAM_DEBUG", "0").lower() not in ("0", "false", "", "no")


def _slot_logs_dir() -> Path:
    slot = os.environ.get("KAETRAM_SLOT", "niral")
    d = PROJECT_DIR / "sandbox" / slot / "reachability_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class TestDebugLog:
    """Collects structured events for a single test run.

    Cheap no-op when KAETRAM_DEBUG is unset — only the wall-clock counter
    is touched, no file I/O.
    """

    test_name: str
    enabled: bool = field(default_factory=_debug_enabled)
    _fp: Any = None
    _started_at: float = field(default_factory=_time.monotonic)
    _event_count: int = 0
    _tool_counts: dict[str, int] = field(default_factory=dict)
    _tool_errors: dict[str, int] = field(default_factory=dict)
    _last_pos: tuple[int, int] | None = None
    _first_pos: tuple[int, int] | None = None

    def __post_init__(self):
        if not self.enabled:
            return
        path = _slot_logs_dir() / f"{self.test_name}.jsonl"
        try:
            self._fp = open(path, "w")
            self._log_path = path
            self._write({"event": "test_start", "test": self.test_name})
        except OSError as exc:
            print(
                f"[test_debug] could not open {path}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            self.enabled = False

    def _write(self, payload: dict) -> None:
        if not self.enabled or not self._fp:
            return
        self._event_count += 1
        payload.setdefault("t", round(_time.monotonic() - self._started_at, 3))
        try:
            self._fp.write(json.dumps(payload, default=str) + "\n")
            self._fp.flush()
        except (OSError, TypeError, ValueError):
            pass

    # ── Public API ────────────────────────────────────────────────────

    def action(self, tool: str, args: dict | None = None, ok: bool | None = None,
               result_preview: str | None = None, error: str | None = None):
        """Record one MCP tool invocation."""
        self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1
        if ok is False:
            self._tool_errors[tool] = self._tool_errors.get(tool, 0) + 1
        self._write({
            "event": "action",
            "tool": tool,
            "args": args,
            "ok": ok,
            "preview": (result_preview or "")[:240] or None,
            "error": error[:240] if error else None,
        })

    def event(self, kind: str, **fields: Any):
        """Record a free-form diagnostic event (hop boundary, stall, etc.)."""
        payload = {"event": kind, **fields}
        # Extract position hints from hop events so the end-of-test summary
        # can show total travel delta without needing a full snapshot call.
        end = fields.get("end")
        start = fields.get("start")
        if isinstance(start, (list, tuple)) and len(start) == 2:
            try:
                if self._first_pos is None:
                    self._first_pos = (int(start[0]), int(start[1]))
            except (TypeError, ValueError):
                pass
        if isinstance(end, (list, tuple)) and len(end) == 2:
            try:
                self._last_pos = (int(end[0]), int(end[1]))
            except (TypeError, ValueError):
                pass
        self._write(payload)

    def snapshot(self, label: str, obs: dict | None):
        """Capture the most-diagnostic subset of an observe payload."""
        if obs is None:
            self._write({"event": "snapshot", "label": label, "obs": None})
            return
        pos = obs.get("pos") or {}
        nav = obs.get("navigation") or {}
        ui = obs.get("ui_state") or {}
        stats = obs.get("stats") or {}
        compact = {
            "pos": {"x": pos.get("x"), "y": pos.get("y")},
            "nav_status": nav.get("status"),
            "nav_stuck_reason": nav.get("stuckReason") or nav.get("stuck_reason"),
            "nav_waypoints_remaining": nav.get("waypointsRemaining"),
            "hp": stats.get("hp"),
            "max_hp": stats.get("max_hp"),
            "is_dead": ui.get("is_dead"),
            "npc_dialogue": ui.get("npc_dialogue"),
            "entities_nearby": len(obs.get("nearby_entities") or []),
        }
        # Track first + last position so summary can show travel delta
        try:
            x, y = int(pos.get("x")), int(pos.get("y"))
            if self._first_pos is None:
                self._first_pos = (x, y)
            self._last_pos = (x, y)
        except (TypeError, ValueError):
            pass
        self._write({"event": "snapshot", "label": label, **compact})

    def raw_observe(self, label: str, text: str):
        """Capture a full observe response (rare — only on failures)."""
        self._write({
            "event": "raw_observe",
            "label": label,
            "text": text[:2000],
        })

    def close(self, status: str = "?"):
        if not self.enabled:
            return
        elapsed = _time.monotonic() - self._started_at
        summary = {
            "event": "test_end",
            "status": status,
            "elapsed_s": round(elapsed, 2),
            "events": self._event_count,
            "tool_calls": dict(self._tool_counts),
            "tool_errors": dict(self._tool_errors),
            "first_pos": self._first_pos,
            "last_pos": self._last_pos,
        }
        self._write(summary)
        try:
            self._fp.close()
        except OSError:
            pass
        self._fp = None

        # Compact stderr summary so operators don't have to open the file
        # for quick triage.
        errs = sum(self._tool_errors.values())
        tools = ", ".join(f"{k}={v}" for k, v in sorted(self._tool_counts.items(), key=lambda x: -x[1]))
        pos_delta = ""
        if self._first_pos and self._last_pos:
            dx = self._last_pos[0] - self._first_pos[0]
            dy = self._last_pos[1] - self._first_pos[1]
            pos_delta = f" pos={self._first_pos}->{self._last_pos} dΣ={abs(dx)+abs(dy)}"
        print(
            f"[test_debug] {self.test_name} {status} {elapsed:.1f}s "
            f"events={self._event_count} errors={errs} tools=[{tools}]{pos_delta} "
            f"log={self._log_path}",
            file=sys.stderr,
            flush=True,
        )


def set_current_test_debug(debug: TestDebugLog | None):
    return _CURRENT_TEST_DEBUG.set(debug)


def reset_current_test_debug(token) -> None:
    _CURRENT_TEST_DEBUG.reset(token)


def get_current_test_debug() -> TestDebugLog | None:
    return _CURRENT_TEST_DEBUG.get()


async def logged_call_tool(session, debug: TestDebugLog | None, name: str,
                            args: dict | None = None):
    """Wrapper around `session.call_tool` that records the invocation.

    Tests call this instead of `session.call_tool(...)` when they want the
    action to land in the debug trace. The helper is a drop-in replacement —
    it returns the same ToolResult.
    """
    if debug is None:
        debug = get_current_test_debug()
    result = await session.call_tool(name, args or {})
    if debug is not None:
        preview = result.text[:240] if result.text else None
        debug.action(
            tool=name,
            args=args,
            ok=not result.is_error,
            result_preview=preview,
            error=result.text[:240] if result.is_error else None,
        )
    return result
