"""Test-run lifecycle for the Tests tab.

Owns the in-flight pytest subprocess plus its sibling Xvfb + ffmpeg
when `headed=True`. Single in-flight run; second `start()` while a
run is alive raises `RuntimeError("run-in-flight")` so `api.py` can
translate to HTTP 409.

Run history persists at `/tmp/test_runs/<id>/{progress.json, junit.xml,
log.txt, meta.json}` — `dashboard_progress_plugin` writes progress.json,
this module writes meta.json. Last 20 retained, older pruned LRU.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# orchestrate.py is at the project root. Import lazily inside start() so
# importing test_runner doesn't pull in orchestrate's heavy deps at boot.

logger = logging.getLogger(__name__)

# Project root: kaetram-arena/. Computed from this file's location so we
# don't depend on dashboard.constants.PROJECT_DIR (which is stale).
PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV_PY = PROJECT_DIR / ".venv" / "bin" / "python3"

TEST_RUNS_DIR = Path("/tmp/test_runs")
TEST_AGENT_ID = 99   # MAX_AGENTS=3 in dashboard.constants; real agents are 0–2.
TEST_DISPLAY = 198   # Xvfb display number for headed test runs (well clear of agents).

# Live test-run video uses MJPEG: ffmpeg writes a single JPEG to this path,
# overwriting it every frame. handler.send_mjpeg_stream polls the file's
# mtime and boundary-streams it to the <img> on the Tests tab. No segments,
# no playlist, no HLS live-edge race. See plan: yes-i-like-option-foamy-babbage.md.
MJPEG_FRAME_DIR  = Path("/tmp/test_run")
MJPEG_FRAME_PATH = MJPEG_FRAME_DIR / "frame.jpg"
MJPEG_FPS        = 5

# Single-run guard.
_lock = threading.Lock()
_current_run: TestRun | None = None

# Suite-tree cache: 60 s TTL, invalidated when a run finishes.
_tree_cache: dict | None = None
_tree_cache_ts: float = 0.0
_TREE_TTL = 60.0


def _has_xdist() -> bool:
    """pytest-xdist provides the `-n` flag. Without it, `-n 0` is a fatal arg error."""
    try:
        import xdist  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class TestRun:
    run_id: str
    run_dir: Path
    headed: bool
    suite: str | None
    markers: str | None
    xvfb: object | None = None              # XvfbProcess from orchestrate
    ffmpeg: subprocess.Popen | None = None  # raw MJPEG ffmpeg process
    pytest_proc: subprocess.Popen | None = None
    started_at: float = 0.0
    finished_at: float | None = None
    exit_code: int | None = None
    cancelled: bool = False
    _reaper: threading.Thread | None = field(default=None, repr=False)

    def to_meta(self) -> dict:
        return {
            "run_id": self.run_id,
            "headed": self.headed,
            "suite": self.suite,
            "markers": self.markers,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "cancelled": self.cancelled,
        }


def _clean_env() -> dict[str, str]:
    """os.environ minus DASHBOARD_TEST_RUN_DIR.

    Used for `collect_tree()` so an inherited env var from a prior run
    doesn't make a stray progress.json land in the wrong place during
    `pytest --collect-only`.
    """
    env = dict(os.environ)
    env.pop("DASHBOARD_TEST_RUN_DIR", None)
    return env


def _broadcast(event: str, payload: dict, run_id: str | None = None) -> None:
    """Best-effort notify_test_event via the WS relay."""
    try:
        from dashboard.server import get_relay
        relay = get_relay()
        if relay is None:
            return
        relay.notify_test_event(run_id or "", event, payload)
    except Exception as e:
        logger.debug("test_event broadcast failed: %s", e)


def _kill_display_orphans(display: int) -> int:
    """SIGKILL any Xvfb or ffmpeg attached to the test display.

    Belt-and-suspenders against orphans from a prior dashboard, reaper, or
    crashed run that didn't clean up.

    Walks /proc directly instead of pgrep -f because pgrep matches its
    own argv (the search pattern is in its argv), polluting the result
    set with bogus pids. /proc gives us per-process `comm` (executable
    name) and `cmdline` (full args) — both reliable.
    """
    killed = 0
    display_marker = f":{display}"
    proc_root = Path("/proc")
    self_pid = os.getpid()

    if not proc_root.exists():
        return 0

    def _matches_display(cmdline: str) -> bool:
        # Find :<display> not followed by another digit, so the test
        # display :198 doesn't false-match :1980 / :1981 / etc.
        idx = 0
        while True:
            idx = cmdline.find(display_marker, idx)
            if idx == -1:
                return False
            tail = idx + len(display_marker)
            if tail >= len(cmdline) or not cmdline[tail].isdigit():
                return True
            idx = tail

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == self_pid:
            continue
        try:
            # `comm` is the process basename — exact match avoids picking
            # up shells whose argv happens to mention "Xvfb" / "ffmpeg".
            comm = (entry / "comm").read_text(errors="replace").strip()
            if comm not in ("Xvfb", "ffmpeg"):
                continue
            # cmdline is NUL-separated argv; "join" with spaces for grep.
            raw = (entry / "cmdline").read_bytes()
            cmdline = raw.replace(b"\x00", b" ").decode(errors="replace")
            if not _matches_display(cmdline):
                continue
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
            logger.info("killed orphan %s pid=%d cmdline=%r",
                        comm, pid, cmdline[:120])
        except OSError as e:
            logger.debug("kill %d failed: %s", pid, e)
    if killed:
        # Give the OS a moment to reap the zombies.
        time.sleep(0.3)
    return killed


def _spawn_mjpeg_ffmpeg(display: int, log_path: Path) -> subprocess.Popen:
    """Spawn an ffmpeg that x11grabs the display and overwrites a single
    JPEG every frame. The MJPEG handler polls the file's mtime and
    boundary-streams it. Atomic file replacement avoids any "torn" reads.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "x11grab",
        "-framerate", str(MJPEG_FPS),
        "-video_size", "1280x810",
        "-i", f":{display}",
        "-vf", f"crop=1280:720:0:90,fps={MJPEG_FPS}",
        "-q:v", "5",      # JPEG quality (1=best, 31=worst); ~50–80 KB/frame
        "-update", "1",   # overwrite the same file each frame
        "-f", "image2",
        str(MJPEG_FRAME_PATH),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "a")
    logf.write(f"\n--- mjpeg ffmpeg start at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    logf.flush()
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=logf, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )


def _wait_for_first_frame(timeout: float = 5.0) -> bool:
    """Poll MJPEG_FRAME_PATH until it exists and has nonzero size.

    Returns True once the first frame has been written, False on timeout.
    Callers should proceed regardless — better to start the test than
    block the API.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if MJPEG_FRAME_PATH.exists() and MJPEG_FRAME_PATH.stat().st_size > 0:
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def start(suite: str | None, markers: str | None, headed: bool) -> str:
    """Start a new pytest run. Returns run_id.

    Raises RuntimeError("run-in-flight") if a run is already alive.
    """
    global _current_run

    with _lock:
        if _current_run is not None and _is_alive(_current_run):
            raise RuntimeError("run-in-flight")

        run_id = time.strftime("%Y%m%d_%H%M%S")
        run_dir = TEST_RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        run = TestRun(
            run_id=run_id,
            run_dir=run_dir,
            headed=headed,
            suite=suite,
            markers=markers,
            started_at=time.time(),
        )

        # Headed pipeline (Xvfb + MJPEG ffmpeg). Failures degrade to
        # headless rather than refuse the run.
        if headed:
            # Nuke any orphans bound to :198 from a prior run/dashboard/
            # crash. Same sweep covers both Xvfb and ffmpeg.
            orphans = _kill_display_orphans(TEST_DISPLAY)
            if orphans:
                logger.info("test_runner: killed %d orphan(s) on :%d", orphans, TEST_DISPLAY)
            # Reset MJPEG frame dir so the <img> doesn't see a stale
            # frame from the prior run.
            if MJPEG_FRAME_DIR.exists():
                shutil.rmtree(MJPEG_FRAME_DIR, ignore_errors=True)
            MJPEG_FRAME_DIR.mkdir(parents=True, exist_ok=True)

            try:
                from orchestrate import XvfbProcess
                xv = XvfbProcess(agent_id=TEST_AGENT_ID, log_dir=run_dir)
                if xv.start():
                    run.xvfb = xv
                    run.ffmpeg = _spawn_mjpeg_ffmpeg(
                        TEST_DISPLAY,
                        run_dir / f"ffmpeg_{TEST_DISPLAY}.log",
                    )
                    # Block until the first JPEG hits disk so the
                    # frontend's first <img> request sees a valid frame.
                    if not _wait_for_first_frame(timeout=5.0):
                        logger.warning(
                            "first MJPEG frame not seen within 5s; "
                            "proceeding (image may take a beat to appear)"
                        )
                else:
                    logger.warning("Xvfb failed to start; degrading to headless")
                    run.headed = False
            except Exception as e:
                logger.warning("headed pipeline init failed: %s; degrading to headless", e)
                if run.xvfb is not None:
                    try: run.xvfb.stop()
                    except Exception: pass
                run.xvfb = None
                run.ffmpeg = None
                run.headed = False

        # Persist starting meta AFTER the headed pipeline branch so the
        # on-disk `headed` flag matches actual state (in case the headed
        # pipeline degraded to headless above). list_runs/get_current
        # readers won't see a stale `headed: true` mid-run.
        _write_meta(run)

        # Build pytest invocation.
        cmd = [
            str(VENV_PY), "-m", "pytest",
            "-p", "tests.dashboard_progress_plugin",
            "--junit-xml", str(run_dir / "junit.xml"),
        ]
        if markers:
            cmd.extend(["-m", markers])
        # `-n 0` only makes sense when pytest-xdist is installed (it forces
        # serial). Without xdist, the flag is unrecognized and pytest errors
        # out before collection. Default behavior is serial anyway.
        if run.headed and _has_xdist():
            cmd.extend(["-n", "0"])
        cmd.append(suite or "tests/")

        env = dict(os.environ)
        env["DASHBOARD_TEST_RUN_DIR"] = str(run_dir)
        if run.headed:
            env["KAETRAM_HEADED"] = "1"
            env["DISPLAY"] = f":{TEST_DISPLAY}"

        log_path = run_dir / "log.txt"
        log_fh = open(log_path, "w")
        run.pytest_proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        run._reaper = threading.Thread(
            target=_reaper_loop,
            args=(run, log_fh),
            daemon=True,
            name=f"test-runner-reaper-{run_id}",
        )
        run._reaper.start()

        _current_run = run

        _broadcast("run_started", run.to_meta(), run_id=run_id)
        return run_id


def stop() -> bool:
    """Cancel the in-flight run. Returns True if a run was active."""
    global _current_run
    with _lock:
        run = _current_run
        if run is None or run.pytest_proc is None:
            return False
        run.cancelled = True

    proc = run.pytest_proc
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    return True


def get_current() -> dict | None:
    """Return current run's meta, or None."""
    run = _current_run
    if run is None:
        return None
    return run.to_meta()


def list_runs() -> list[dict]:
    """Walk /tmp/test_runs/, return run summaries newest-first."""
    if not TEST_RUNS_DIR.exists():
        return []
    runs = []
    for d in sorted(TEST_RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta = _read_meta(d)
        if meta is None:
            continue
        meta["progress"] = _read_progress(d)
        meta["junit"] = _read_junit_summary(d)
        runs.append(meta)
    return runs


def get_run(run_id: str) -> dict | None:
    d = TEST_RUNS_DIR / run_id
    if not d.is_dir():
        return None
    meta = _read_meta(d)
    if meta is None:
        return None
    meta["progress"] = _read_progress(d)
    meta["junit"] = _read_junit_summary(d)
    log_path = d / "log.txt"
    if log_path.exists():
        try:
            meta["log_tail"] = log_path.read_text(errors="replace")[-50_000:]
        except Exception:
            meta["log_tail"] = ""
    return meta


def collect_tree() -> dict:
    """Return cached pytest --collect-only tree.

    Cached for 60 s; invalidated on run finish.
    """
    global _tree_cache, _tree_cache_ts
    now = time.time()
    if _tree_cache is not None and (now - _tree_cache_ts) < _TREE_TTL:
        return _tree_cache
    try:
        result = subprocess.run(
            [str(VENV_PY), "-m", "pytest", "--collect-only", "-q"],
            cwd=str(PROJECT_DIR),
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        nodeids: list[str] = []
        errors: list[str] = []
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            # pytest --collect-only -q emits one nodeid per line, plus a
            # trailing "N tests collected" footer and per-file ERROR lines
            # for collection failures. Filter those out.
            if line.startswith("ERROR ") or line.startswith("===") or line.startswith("____ "):
                errors.append(line)
                continue
            # Footer/info lines.
            low = line.lower()
            if "collected" in low or "warning" in low or "errors during collection" in low:
                continue
            # A real nodeid is either "path/file.py::Class::method[param]" or
            # just "path/file.py" (rare with -q, but possible for empty files).
            if "::" not in line and not line.endswith(".py"):
                continue
            nodeids.append(line)
        tree: dict = {}
        for nid in nodeids:
            file_part = nid.split("::", 1)[0]
            tree.setdefault(file_part, []).append(nid)
        _tree_cache = {
            "nodeids": nodeids,
            "by_file": tree,
            "count": len(nodeids),
            "errors": errors,
        }
        _tree_cache_ts = now
        return _tree_cache
    except Exception as e:
        logger.warning("collect_tree failed: %s", e)
        return {"nodeids": [], "by_file": {}, "count": 0, "error": str(e)}


# ─────────────────────── internals ───────────────────────


def _is_alive(run: TestRun) -> bool:
    return run.pytest_proc is not None and run.pytest_proc.poll() is None


def _reaper_loop(run: TestRun, log_fh) -> None:
    """Wait for pytest, tear down infra, fire session_finish, prune."""
    global _current_run, _tree_cache
    proc = run.pytest_proc
    assert proc is not None
    while proc.poll() is None:
        time.sleep(0.5)
    run.exit_code = proc.returncode
    run.finished_at = time.time()
    try:
        log_fh.close()
    except Exception:
        pass

    # Teardown infra. Stop in reverse-start order: ffmpeg first (it
    # depends on the X socket), then Xvfb. Then sweep any survivors —
    # if either stop failed silently, an orphan would block the next
    # run's pipeline.
    if run.ffmpeg is not None and run.ffmpeg.poll() is None:
        try:
            os.killpg(os.getpgid(run.ffmpeg.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError) as e:
            logger.debug("ffmpeg SIGTERM: %s", e)
        try:
            run.ffmpeg.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try: os.killpg(os.getpgid(run.ffmpeg.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError): pass
    if run.xvfb is not None:
        try: run.xvfb.stop()
        except Exception as e: logger.debug("xvfb.stop: %s", e)
    if run.headed:
        survivors = _kill_display_orphans(TEST_DISPLAY)
        if survivors:
            logger.warning("test_runner: %d orphan(s) survived stop() on :%d",
                           survivors, TEST_DISPLAY)

    _write_meta(run)

    # Single source of truth for run completion.
    payload = {
        "exit_code": run.exit_code,
        "cancelled": run.cancelled,
        "finished_at": run.finished_at,
        "progress": _read_progress(run.run_dir),
        "junit": _read_junit_summary(run.run_dir),
    }
    _broadcast("session_finish", payload, run_id=run.run_id)

    prune_old_runs()
    _tree_cache = None  # tests may have changed

    with _lock:
        if _current_run is run:
            _current_run = None


def _write_meta(run: TestRun) -> None:
    try:
        (run.run_dir / "meta.json").write_text(json.dumps(run.to_meta(), indent=2))
    except Exception as e:
        logger.warning("write_meta failed: %s", e)


def _read_meta(d: Path) -> dict | None:
    p = d / "meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _read_progress(d: Path) -> dict | None:
    p = d / "progress.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _read_junit_summary(d: Path) -> dict | None:
    p = d / "junit.xml"
    if not p.exists():
        return None
    try:
        root = ET.parse(p).getroot()
        # Junit XML root may be <testsuite> or <testsuites>; aggregate.
        suites = root.findall(".//testsuite") or [root]
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
        for s in suites:
            for k in ("tests", "failures", "errors", "skipped"):
                try: totals[k] += int(s.attrib.get(k, "0"))
                except ValueError: pass
            try: totals["time"] += float(s.attrib.get("time", "0"))
            except ValueError: pass
        return totals
    except Exception:
        return None


def prune_old_runs(keep: int = 20) -> int:
    if not TEST_RUNS_DIR.exists():
        return 0
    dirs = [d for d in TEST_RUNS_DIR.iterdir() if d.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    pruned = 0
    for d in dirs[keep:]:
        try:
            shutil.rmtree(d)
            pruned += 1
        except OSError as e:
            logger.debug("prune %s: %s", d, e)
    return pruned
