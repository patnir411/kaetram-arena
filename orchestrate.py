#!/usr/bin/env python3
"""
orchestrate.py — Multi-agent launcher and monitor for Kaetram agent runs.

Launches N independent (Kaetram server + AI agent) pairs, monitors health,
auto-restarts on crash, and collects logs for post-processing. Only Claude
runs feed the SFT training pipeline (extract_turns.py); the other harnesses
(Codex / Gemini / OpenCode) are for live observation and ad-hoc evaluation.

Usage:
    python3 orchestrate.py --agents 4                     # 4 Claude agents (default)
    python3 orchestrate.py --agents 2 --hours 8           # auto-stop after 8h
    python3 orchestrate.py --codex                        # all agents use Codex (not for training)
    python3 orchestrate.py --claude 2 --codex 2           # mixed: 2 Claude + 2 Codex
    python3 orchestrate.py --claude 2 --codex 2 --grinder 2 --explorer 2
"""

import argparse
import functools
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

# Force unbuffered output so tee/tmux see it immediately
print = functools.partial(print, flush=True)
from dataclasses import dataclass
from pathlib import Path

from cli_adapter import CLIAdapter, get_adapter, opencode_bot_prefix
from notifications import format_notification, send_email_notification

PROJECT_DIR = Path(__file__).parent


def detect_auth_mode() -> str:
    """Detect Claude Code auth mode via ``claude auth status``.

    Returns ``"api_key"`` when an API key is active (env var, helper, or token),
    ``"subscription"`` for OAuth/subscription login.
    """
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            if info.get("apiKeySource"):
                return "api_key"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        pass
    return "subscription"
KAETRAM_DIR = Path.home() / "projects" / "Kaetram-Open"
KAETRAM_SERVER_DIR = KAETRAM_DIR / "packages" / "server"
NVM_SH = Path.home() / ".nvm" / "nvm.sh"
SYSTEM_PROMPT_FILE = PROJECT_DIR / "prompts" / "system.md"
GAME_KNOWLEDGE_FILE = PROJECT_DIR / "prompts" / "game_knowledge.md"
PERSONALITY_DIR = PROJECT_DIR / "prompts" / "personalities"
VALID_PERSONALITIES = ("grinder", "completionist", "explorer_tinkerer")

# Port allocation: agent N gets server WS on 9001 + N*10
BASE_SERVER_PORT = 9001
PORT_STRIDE = 10
CLIENT_PORT = 9000  # shared static client

# Reasoning-capture proxies (SSE-rewriting bridges for OpenCode CoT capture).
# - NIM (:8889): NVIDIA NIM upstream for Qwen thinking models.
# - DeepSeek (:8890): api.deepseek.com upstream — required because opencode
#   1.14.29 doesn't read DeepSeek's delta.reasoning_content directly.
NIM_PROXY_HOST = "127.0.0.1"
NIM_PROXY_PORT = 8889
NIM_PROXY_SCRIPT = PROJECT_DIR / "scripts" / "start-nim-proxy.sh"
DEEPSEEK_PROXY_PORT = 8890
DEEPSEEK_PROXY_SCRIPT = PROJECT_DIR / "scripts" / "start-deepseek-proxy.sh"


@dataclass
class GameServer:
    agent_id: int
    port: int
    process: subprocess.Popen | None = None
    restart_count: int = 0
    last_restart: float = 0.0
    cooldown: float = 10.0

    def start(self):
        """Start the Kaetram game server on the assigned port."""
        # CWD must be packages/server/ so dotenv resolves ../../.env correctly.
        # Use --port CLI arg to override (see packages/server/src/args.ts).
        log_path = Path(f"/tmp/kaetram_agent_{self.agent_id}/gameserver_{self.port}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = (
            f'source "{NVM_SH}" && nvm use 20 --silent && '
            f'exec node --enable-source-maps dist/main.js --port {self.port}'
        )
        self._server_log = open(log_path, "a")
        self._server_log.write(f"\n--- Server start at {time.strftime('%Y-%m-%d %H:%M:%S')} (restart #{self.restart_count + 1}) ---\n")
        self._server_log.flush()
        self.process = subprocess.Popen(
            ["bash", "-c", cmd],
            cwd=str(KAETRAM_SERVER_DIR),
            stdout=self._server_log,
            stderr=self._server_log,
            preexec_fn=os.setsid,
        )
        self.last_restart = time.time()
        self.restart_count += 1

    def stop(self):
        if self.process and self.process.poll() is None:
            # Kill entire process group (bash + node child)
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                self.process.wait()
            self.process = None
        if hasattr(self, "_server_log") and self._server_log:
            try:
                self._server_log.close()
            except OSError:
                pass
            self._server_log = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def health_check(self) -> bool:
        """TCP connect to the WS port to verify the server is ready."""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2):
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False

    def maybe_restart(self) -> bool:
        """Restart if dead and cooldown has elapsed. Returns True if restarted."""
        if self.is_alive() and self.health_check():
            return False
        if time.time() - self.last_restart < self.cooldown:
            return False
        self.stop()
        self.start()
        return True


# ── Per-slot HLS livestream pipeline ──
# Xvfb virtual display + ffmpeg x11grab → HLS segments served by the dashboard.
# Both are best-effort: if either tool is missing or fails to start, the agent
# continues in headless mode and the dashboard falls back to the JPEG path.

HLS_BASE_DIR = Path("/tmp/hls")
HLS_DISPLAY_BASE = 99   # display = HLS_DISPLAY_BASE + agent_id
XVFB_AVAILABLE = shutil.which("Xvfb") is not None
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
HLS_ENABLED = XVFB_AVAILABLE and FFMPEG_AVAILABLE


@dataclass
class XvfbProcess:
    """Headed virtual X display per agent. Drives `KAETRAM_HEADED=1` Chromium."""

    agent_id: int
    width: int = 1280
    height: int = 810
    depth: int = 24
    process: subprocess.Popen | None = None
    log_dir: Path | None = None

    @property
    def display(self) -> int:
        return HLS_DISPLAY_BASE + self.agent_id

    @property
    def display_str(self) -> str:
        return f":{self.display}"

    def _socket_path(self) -> Path:
        return Path(f"/tmp/.X11-unix/X{self.display}")

    def start(self) -> bool:
        if not XVFB_AVAILABLE:
            return False
        if self.log_dir is not None:
            log_path = Path(self.log_dir) / f"xvfb_{self.display}.log"
        else:
            log_path = Path(f"/tmp/kaetram_agent_{self.agent_id}/xvfb_{self.display}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "a")
        self._log.write(f"\n--- Xvfb start at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        self._log.flush()
        self.process = subprocess.Popen(
            [
                "Xvfb", self.display_str,
                "-screen", "0", f"{self.width}x{self.height}x{self.depth}",
                "-nolisten", "tcp",
            ],
            stdout=self._log, stderr=self._log,
            preexec_fn=os.setsid,
        )
        # Wait up to 3s for the socket to appear.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._socket_path().exists():
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.05)
        return self._socket_path().exists()

    def is_alive(self) -> bool:
        return (
            self.process is not None
            and self.process.poll() is None
            and self._socket_path().exists()
        )

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        if hasattr(self, "_log") and self._log:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None


@dataclass
class FfmpegEncoder:
    """ffmpeg x11grab → HLS segments under /tmp/hls/agent_N/.

    Agent-mode HLS only. Tests-tab live video uses a separate MJPEG
    ffmpeg invocation in dashboard/test_runner.py (HLS's live-edge
    segment-rotation race made it unreliable for short test runs).
    """

    agent_id: int
    display: int
    width: int = 1280
    height: int = 720         # cropped from 810 to strip Chrome chrome
    crop_y: int = 90
    fps: int = 25
    process: subprocess.Popen | None = None
    last_restart: float = 0.0
    cooldown: float = 5.0

    def hls_dir(self) -> Path:
        return HLS_BASE_DIR / f"agent_{self.agent_id}"

    def playlist_path(self) -> Path:
        return self.hls_dir() / "stream.m3u8"

    def start(self) -> bool:
        if not FFMPEG_AVAILABLE:
            return False
        d = self.hls_dir()
        d.mkdir(parents=True, exist_ok=True)
        # Clear stale segments from any prior run before starting.
        for old in list(d.glob("seg_*.ts")) + list(d.glob("stream.m3u8")):
            try:
                old.unlink()
            except OSError:
                pass

        log_path = Path(f"/tmp/kaetram_agent_{self.agent_id}/ffmpeg_{self.display}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "a")
        self._log.write(f"\n--- ffmpeg start at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        self._log.flush()

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "x11grab",
            "-framerate", str(self.fps),
            "-video_size", f"{self.width}x{self.height + self.crop_y}",
            "-i", f":{self.display}",
            "-vf", f"crop={self.width}:{self.height}:0:{self.crop_y}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency", "-crf", "28", "-g", "50",
            "-an",
            "-f", "hls",
            "-hls_time", "2", "-hls_list_size", "5",
            "-hls_flags", "delete_segments+independent_segments",
            "-hls_segment_filename", str(d / "seg_%05d.ts"),
            str(self.playlist_path()),
        ]
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=self._log, stderr=self._log,
            preexec_fn=os.setsid,
        )
        self.last_restart = time.time()
        return True

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def maybe_restart(self) -> bool:
        if self.is_alive():
            return False
        if time.time() - self.last_restart < self.cooldown:
            return False
        self.stop()
        return self.start()

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        if hasattr(self, "_log") and self._log:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None


@dataclass
class AgentInstance:
    agent_id: int
    username: str
    server_port: int
    sandbox_dir: Path
    log_dir: Path
    adapter: CLIAdapter
    personality: str = "grinder"    # "grinder", "completionist", "explorer_tinkerer"
    process: subprocess.Popen | None = None
    session: int = 0
    max_turns: int = 150
    max_budget_usd: float | None = None
    auth_mode: str = "subscription"   # "api_key" or "subscription"
    pause_between: int = 10
    # Crash-loop guard: track session uptime to apply escalating backoff when a
    # session dies almost immediately (e.g. game server down, MCP init failure,
    # auth error). Without this, maybe_restart_session() respawns the full
    # CLI+MCP+Chromium stack every `pause_between` seconds indefinitely — the
    # same uncapped-respawn load-cascade class that froze the VM on 2026-05-29.
    _session_started_at: float = 0.0
    _consecutive_fast_fails: int = 0
    _MIN_HEALTHY_UPTIME: float = 60.0   # session must live this long to "count"
    _MAX_RESTART_BACKOFF: float = 300.0  # cap escalating backoff at 5 min
    # Per-agent livestream pipeline (None when HLS_ENABLED is False or boot failed).
    xvfb: "XvfbProcess | None" = None
    ffmpeg: "FfmpegEncoder | None" = None
    # Cache for opencode internal log path — invalidated when stale.
    _opencode_log_path: "Path | None" = None
    _opencode_log_mtime: float = 0.0

    def _refresh_session_from_disk(self) -> None:
        """Sync ``self.session`` with ``.session_counter`` on disk.

        For Qwen warm-session agents, play_qwen owns the counter; orchestrate
        reads it whenever status display needs the current session number.
        Idempotent + safe on missing/corrupt counter file.
        """
        counter_file = self.sandbox_dir / "state" / ".session_counter"
        try:
            n = int(counter_file.read_text().strip())
            if n > self.session:
                self.session = n
        except (OSError, ValueError):
            pass

    def setup(self):
        """Create sandbox directory with CLI config and state/."""
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / "state").mkdir(exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Write CLI-specific config (e.g. .mcp.json for Claude)
        self.adapter.setup_sandbox(
            self.sandbox_dir,
            port=str(self.server_port),
            username=self.username,
        )

        # Write personality metadata for dashboard
        metadata = {
            "agent_id": self.agent_id,
            "personality": self.personality,
            "mode": self.personality,  # backward compat for dashboard
            "username": self.username,
            "server_port": self.server_port,
            "harness": self.adapter.name,
            "model": self.adapter.model,
        }
        (self.sandbox_dir / "metadata.json").write_text(json.dumps(metadata))

        # Restore session counter if resuming
        counter_file = self.sandbox_dir / "state" / ".session_counter"
        if counter_file.exists():
            try:
                self.session = int(counter_file.read_text().strip())
            except (ValueError, OSError):
                pass

    def _build_system_prompt(self) -> str:
        """Build the system prompt with substituted placeholders.

        In multi-agent mode, state file paths (game_state, progress)
        are redirected to each agent's sandbox so agents don't overwrite each other.
        The state_extractor.js path stays in the project dir (shared, read-only).
        """
        template = SYSTEM_PROMPT_FILE.read_text()
        # First, replace state dir paths BEFORE the general __PROJECT_DIR__ replace,
        # so we can target them specifically.
        sandbox_state = str(self.sandbox_dir / "state")
        prompt = template.replace("__PROJECT_DIR__/state/", sandbox_state + "/")
        prompt = prompt.replace("__PROJECT_DIR__", str(PROJECT_DIR))
        prompt = prompt.replace("__USERNAME__", self.username)
        prompt = prompt.replace("__SERVER_PORT__", str(self.server_port))

        # Inject game knowledge block (before personality so agent reads world context first)
        game_knowledge = GAME_KNOWLEDGE_FILE.read_text() if GAME_KNOWLEDGE_FILE.exists() else ""
        prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", game_knowledge)

        personality_file = PERSONALITY_DIR / f"{self.personality}.md"
        personality_block = personality_file.read_text() if personality_file.exists() else ""
        prompt = prompt.replace("__PERSONALITY_BLOCK__", personality_block)

        return prompt

    def _build_user_prompt(self) -> str:
        """Build the user prompt for a session."""
        from bootstrap import build_orchestrate_bootstrap
        base_prompt = build_orchestrate_bootstrap(self.personality, self.session)

        # `codex exec` and `opencode run` differ in how the loop ends:
        #   - codex exec is genuinely one-shot per invocation; the CLI itself
        #     stops after the model's first non-tool response.
        #   - opencode run is an agent loop (build mode by default) and will
        #     keep calling tools as long as the model wants — but the model
        #     decides when to stop. Coder-tuned models like Qwen3 Coder 480B
        #     read "you must use tools to play the game" as "call observe,
        #     describe the state, finish," and exit after a single tool call.
        # Either way the symptom from our side is the same: a session that
        # ends after one tool. The addendum below tells the model to keep
        # looping regardless of how the harness frames "done."
        if self.adapter.name in ("codex", "opencode"):
            base_prompt += (
                "\n\nYou must keep playing continuously — call tools in a loop "
                "for the ENTIRE session. After every action, call observe again "
                "and pick the next action. Do NOT stop after the first observe. Do NOT stop "
                "after one action. Keep calling tools: observe → decide → act → "
                "observe → decide → act, hundreds of times. Never output a final "
                "message or conclude — just keep playing until the process is killed."
            )

        return base_prompt

    def start_session(self):
        """Launch a new agent session (Claude or Codex, depending on adapter).

        For Qwen specifically this is a long-lived warm-session loop process
        — play_qwen handles its own per-session log files + .session_counter
        + sidecar meta. We only spawn it once per AgentInstance lifetime
        (and once again on hard crash recovery). For all other harnesses,
        each call writes a new session log file and sidecar.
        """
        is_qwen_warm = self.adapter.name == "qwen"

        if is_qwen_warm:
            # play_qwen owns the counter — refresh self.session for status.
            self._refresh_session_from_disk()
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            # Singleton stderr-style log for the whole warm-loop process life.
            # Per-session JSONL records go directly into session_<N>_<TS>.log
            # files written by play_qwen's SessionLogger.
            log_file = self.log_dir / "harness_stdout.log"
        else:
            self.session += 1
            counter_file = self.sandbox_dir / "state" / ".session_counter"
            counter_file.write_text(str(self.session))
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            log_file = self.log_dir / f"session_{self.session}_{timestamp}.log"

            # Keep run.meta.json's session_count in sync.
            try:
                run_meta_path = self.log_dir / "run.meta.json"
                if run_meta_path.is_file():
                    run_meta = json.loads(run_meta_path.read_text())
                    run_meta["session_count"] = self.session
                    run_meta_path.write_text(json.dumps(run_meta, indent=2))
            except (OSError, ValueError):
                pass

            # Write sidecar metadata alongside the session log for auditing.
            sidecar = self.log_dir / f"session_{self.session}_{timestamp}.meta.json"
            sidecar.write_text(json.dumps({
                "agent_id": self.agent_id,
                "personality": self.personality,
                "harness": self.adapter.name,
                "model": self.adapter.model,
                "username": self.username,
                "session": self.session,
                "timestamp": timestamp,
                "log_file": log_file.name,
                "auth_mode": self.auth_mode,
                "max_budget_usd": self.max_budget_usd,
            }, indent=2))

        # Bring up the per-agent livestream pipeline before the CLI starts so
        # Chromium can attach to a live X display. Failures here are
        # non-fatal: agent runs headless + dashboard falls back to JPEG.
        # For Qwen this fires once per AgentInstance lifetime (and once on
        # crash recovery) — Xvfb+ffmpeg stay up across all warm sessions.
        self._start_livestream_pipeline()

        # Reset Codex stop hook turn counter so each session starts fresh
        if self.adapter.name == "codex":
            (self.sandbox_dir / ".turn_counter").write_text("0")

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt()

        # Write CLI-specific files (e.g. .mcp.json for game server, refreshed each session)
        self.adapter.setup_sandbox(
            self.sandbox_dir, system_prompt,
            port=str(self.server_port), username=self.username,
        )

        if is_qwen_warm:
            # Hand play_qwen the run_dir + harness_meta path; it'll write its
            # own session_*.log + sidecar files there. harness_meta lets
            # play_qwen reproduce the sidecar shape orchestrate writes for
            # other harnesses.
            harness_meta_path = self.log_dir / "harness_meta_template.json"
            harness_meta_path.write_text(json.dumps({
                "agent_id": self.agent_id,
                "personality": self.personality,
                "harness": self.adapter.name,
                "model": self.adapter.model,
                "username": self.username,
                "auth_mode": self.auth_mode,
                "max_budget_usd": self.max_budget_usd,
            }))
            self.adapter.run_dir = self.log_dir
            self.adapter.harness_meta_path = harness_meta_path
            # orchestrate doesn't bound by wall-clock — --hours triggers SIGTERM externally.
            self.adapter.max_duration_seconds = 0

        cmd = self.adapter.build_command(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            auth_mode=self.auth_mode,
            personality=self.personality,
            session_n=self.session,
        )

        # Build env: inherit current, layer harness env, then HLS overrides.
        env = {**os.environ, **self.adapter.get_env()}
        if self.xvfb is not None and self.xvfb.is_alive():
            env["DISPLAY"] = self.xvfb.display_str
            env["KAETRAM_HEADED"] = "1"
        # AGENT_ID needed by play_qwen to populate harness_meta defaults.
        env["AGENT_ID"] = str(self.agent_id)

        # Append for Qwen so successive crash-recovery spawns share the
        # singleton harness_stdout.log; truncate for other harnesses (each
        # call is a fresh session log file).
        log_fh = open(log_file, "a" if is_qwen_warm else "w")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.sandbox_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=os.setsid,
        )
        self._log_fh = log_fh
        # Mark launch time for the crash-loop backoff in maybe_restart_session().
        self._session_started_at = time.time()

    def _start_livestream_pipeline(self):
        """Best-effort Xvfb + ffmpeg bring-up. Sets self.xvfb / self.ffmpeg on success."""
        if not HLS_ENABLED:
            return
        # Idempotency guard: tear down any pipeline left over from a prior
        # session before spawning a new one. Without this, a crash-recovery
        # restart that bypassed stop() (or a Qwen warm re-spawn) would leak the
        # old Xvfb + ffmpeg encoder — orphaned ffmpeg is the heaviest load
        # contributor in the cascade class we are hardening against.
        if self.ffmpeg is not None:
            try:
                self.ffmpeg.stop()
            except Exception:
                pass
            self.ffmpeg = None
        if self.xvfb is not None:
            try:
                self.xvfb.stop()
            except Exception:
                pass
            self.xvfb = None
        # Xvfb first; ffmpeg depends on the X socket existing.
        try:
            xv = XvfbProcess(agent_id=self.agent_id)
            if xv.start():
                self.xvfb = xv
            else:
                xv.stop()
                print(f"[agent {self.agent_id}] Xvfb failed to bind :{xv.display}; "
                      "falling back to headless mode")
                return
        except Exception as e:
            print(f"[agent {self.agent_id}] Xvfb spawn error: {e}")
            return

        try:
            fm = FfmpegEncoder(agent_id=self.agent_id, display=self.xvfb.display)
            if fm.start():
                self.ffmpeg = fm
            else:
                print(f"[agent {self.agent_id}] ffmpeg failed to start; HLS disabled")
        except Exception as e:
            print(f"[agent {self.agent_id}] ffmpeg spawn error: {e}")

    def supervise_livestream(self):
        """Called from the orchestrator health loop. Restarts ffmpeg if dead
        but Xvfb is still up. If Xvfb is dead, leaves the slot to the agent
        restart path — the agent's stop() will tear ffmpeg down with it."""
        if self.ffmpeg and not self.ffmpeg.is_alive() and self.xvfb and self.xvfb.is_alive():
            self.ffmpeg.maybe_restart()

    def _get_all_descendant_pgids(self) -> set[int]:
        """Walk the process tree to find all unique PGIDs (including Chrome's own group)."""
        pgids = set()
        if not self.process:
            return pgids
        try:
            root_pid = self.process.pid
            # Recursively find all descendants via /proc
            to_visit = [root_pid]
            visited = set()
            while to_visit:
                pid = to_visit.pop()
                if pid in visited:
                    continue
                visited.add(pid)
                try:
                    pgids.add(os.getpgid(pid))
                except (ProcessLookupError, OSError):
                    pass
                # Find children of this pid
                try:
                    for entry in os.listdir("/proc"):
                        if entry.isdigit():
                            try:
                                with open(f"/proc/{entry}/stat") as f:
                                    stat = f.read().split()
                                    ppid = int(stat[3])
                                    if ppid == pid:
                                        to_visit.append(int(entry))
                            except (FileNotFoundError, IndexError, ValueError, PermissionError):
                                pass
                except FileNotFoundError:
                    pass
        except (ProcessLookupError, OSError):
            pass
        return pgids

    def stop(self):
        if self.process and self.process.poll() is None:
            # Collect all process group IDs BEFORE killing (Chrome uses its own pgid)
            pgids = self._get_all_descendant_pgids()
            # Kill the agent's own group first
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            # Kill any other process groups (e.g. Chrome's own group)
            for pgid in pgids:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                for pgid in pgids:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                self.process.wait()
            self.process = None
        if hasattr(self, "_log_fh") and self._log_fh:
            self._log_fh.close()
            self._log_fh = None

        # Tear down livestream pipeline AFTER the CLI/Chromium cascade so
        # Chromium doesn't try to draw to a dying X display.
        if self.ffmpeg is not None:
            try:
                self.ffmpeg.stop()
            except Exception:
                pass
            self.ffmpeg = None
        if self.xvfb is not None:
            try:
                self.xvfb.stop()
            except Exception:
                pass
            self.xvfb = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_stale(self, threshold_seconds: int = 900) -> bool:
        """True if agent process is alive but log hasn't grown in N seconds."""
        if not self.is_alive():
            return False
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return False
            return (time.time() - logs[0].stat().st_mtime) > threshold_seconds
        except OSError:
            return False

    def _check_rate_limit(self) -> dict | None:
        """Check if the latest session log contains a rate limit rejection.

        Returns dict with {reset_at, rate_limit_type, reason, source} if
        rate-limited, None otherwise.  Handles both subscription
        (rate_limit_event with overageStatus) and API key (429 errors).
        """
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return None
            log_path = logs[0]

            # Read tail of log (200KB covers rate limit events in long sessions)
            size = log_path.stat().st_size
            with open(log_path, "r", errors="replace") as f:
                if size > 200_000:
                    f.seek(size - 200_000)
                    f.readline()  # skip partial line
                data = f.read()

            # Strategy 1: Subscription — rate_limit_event with overageStatus=rejected
            for line in data.splitlines():
                if "rate_limit_event" not in line and "overageStatus" not in line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "rate_limit_event":
                        info = obj.get("rate_limit_info", {})
                        if info.get("overageStatus") == "rejected":
                            return {
                                "reset_at": float(info.get("resetsAt", 0)),
                                "rate_limit_type": info.get("rateLimitType", "unknown"),
                                "reason": info.get("overageDisabledReason", "rejected"),
                                "source": "subscription",
                            }
                except (json.JSONDecodeError, ValueError):
                    # Fallback: regex for malformed JSON — only match overageStatus rejected,
                    # NOT status rejected (which just means primary quota exhausted, overage may be active)
                    if '"overageStatus":"rejected"' in line or '"overageStatus": "rejected"' in line:
                        match = re.search(r'"resetsAt"\s*:\s*(\d+)', line)
                        if match:
                            return {
                                "reset_at": float(match.group(1)),
                                "rate_limit_type": "unknown",
                                "reason": "rejected",
                                "source": "subscription_fallback",
                            }

            # Strategy 2: API key — 429 errors or "rate_limit" error type
            for line in data.splitlines():
                if '"error"' not in line and "429" not in line:
                    continue
                try:
                    obj = json.loads(line)
                    err = obj.get("error", {})
                    if isinstance(err, str):
                        err = {"message": err}
                    if not isinstance(err, dict):
                        continue
                    err_type = err.get("type", "")
                    err_msg = err.get("message", "")
                    if ("rate_limit" in err_type or "429" in str(obj.get("error", ""))
                            or "rate limit" in err_msg.lower()):
                        retry_after = err.get("retry_after", 60)
                        return {
                            "reset_at": time.time() + float(retry_after),
                            "rate_limit_type": "api_rate_limit",
                            "reason": err_msg or err_type,
                            "source": "api",
                        }
                except (json.JSONDecodeError, ValueError):
                    continue

            # Strategy 3: Codex — turn.failed or error events with rate limit info
            for line in data.splitlines():
                if "rate_limit" not in line and "429" not in line and "too many requests" not in line.lower():
                    continue
                try:
                    obj = json.loads(line)
                    t = obj.get("type", "")
                    if t in ("turn.failed", "error"):
                        err_msg = obj.get("error", obj.get("message", ""))
                        if isinstance(err_msg, dict):
                            err_msg = err_msg.get("message", str(err_msg))
                        err_str = str(err_msg).lower()
                        if "rate_limit" in err_str or "429" in err_str or "too many requests" in err_str:
                            return {
                                "reset_at": time.time() + 60,
                                "rate_limit_type": "codex_rate_limit",
                                "reason": str(err_msg),
                                "source": "codex",
                            }
                except (json.JSONDecodeError, ValueError):
                    continue

            # Strategy 4: OpenCode — 429s land in opencode's internal log dir
            # (~/.local/share/opencode/log/*.log), not the session log. Scan
            # the most-recent internal log for AI_APICallError + statusCode 429.
            opencode_rl = self._check_opencode_rate_limit()
            if opencode_rl:
                return opencode_rl

            return None
        except OSError:
            return None

    def _check_opencode_rate_limit(self) -> dict | None:
        """Scan opencode's internal log dir for 429 / rate-limit errors.

        opencode keeps API errors in ~/.local/share/opencode/log/<ts>.log,
        completely separate from the agent session log. We read only the tail
        and only the most recent file to keep this cheap.

        Caches the most-recent log path on the instance: a single os.scandir
        pass replaces sorting all entries by mtime. The cache is invalidated
        when the cached file disappears or when scandir finds a newer one.
        """
        log_dir = Path.home() / ".local/share/opencode/log"
        if not log_dir.is_dir():
            return None
        try:
            # Refresh cache if missing or any newer *.log appeared.
            need_refresh = (
                self._opencode_log_path is None
                or not self._opencode_log_path.is_file()
            )
            newest_path: Path | None = None
            newest_mtime = 0.0
            if not need_refresh:
                # Cheap check: scandir + early-out when we find any *.log
                # newer than the cached mtime.
                with os.scandir(log_dir) as it:
                    for entry in it:
                        if not entry.name.endswith(".log"):
                            continue
                        try:
                            mt = entry.stat().st_mtime
                        except OSError:
                            continue
                        if mt > self._opencode_log_mtime:
                            need_refresh = True
                            break
            if need_refresh:
                with os.scandir(log_dir) as it:
                    for entry in it:
                        if not entry.name.endswith(".log"):
                            continue
                        try:
                            mt = entry.stat().st_mtime
                        except OSError:
                            continue
                        if mt > newest_mtime:
                            newest_mtime = mt
                            newest_path = Path(entry.path)
                if newest_path is None:
                    return None
                self._opencode_log_path = newest_path
                self._opencode_log_mtime = newest_mtime

            log_file = self._opencode_log_path
            if log_file is None or not log_file.is_file():
                return None
            # Only consider the log if it was modified in the last 5 minutes
            # — stale 429s from a prior run shouldn't trip the guard now.
            mtime_now = log_file.stat().st_mtime
            self._opencode_log_mtime = mtime_now
            if time.time() - mtime_now > 300:
                return None
            size = log_file.stat().st_size
            with open(log_file, "r", errors="replace") as f:
                if size > 200_000:
                    f.seek(size - 200_000)
                    f.readline()
                tail = f.read()
            if "AI_APICallError" not in tail or '"statusCode":429' not in tail:
                return None
            # NIM doesn't return retry_after; default to 60s and let the
            # outer health loop re-check after sleep.
            return {
                "reset_at": time.time() + 60,
                "rate_limit_type": "opencode_429",
                "reason": "NVIDIA NIM returned HTTP 429 (rate limited)",
                "source": "opencode",
            }
        except OSError:
            return None

    def _check_session_cost(self) -> dict:
        """Read cost and overage state from the latest session log.

        Returns ``{"cost_usd": float, "is_overage": bool}`` extracted from
        stream-json ``result`` events (Claude), ``turn.completed`` usage
        events (Codex), and ``rate_limit_event`` objects.
        """
        cost_usd = 0.0
        is_overage = False
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return {"cost_usd": 0.0, "is_overage": False}
            log_path = logs[0]
            size = log_path.stat().st_size
            with open(log_path, "r", errors="replace") as f:
                if size > 200_000:
                    f.seek(size - 200_000)
                    f.readline()
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = obj.get("type", "")
                    if t == "result":
                        cost_usd = max(cost_usd, obj.get("total_cost_usd", 0.0))
                    elif t == "rate_limit_event":
                        info = obj.get("rate_limit_info", {})
                        if info.get("isUsingOverage"):
                            is_overage = True
                    elif t == "turn.completed":
                        # Codex: estimate cost from token usage (GPT-5.4 ~$2/M in, ~$8/M out)
                        usage = obj.get("usage", {})
                        in_tok = usage.get("input_tokens", 0)
                        out_tok = usage.get("output_tokens", 0)
                        cost_usd += (in_tok * 2.0 + out_tok * 8.0) / 1_000_000
                    elif t == "assistant" and self.adapter.name == "qwen":
                        # Qwen: Modal A100-40GB billing is GPU-seconds, not
                        # tokens — these rates approximate ~$3/hr × ~30 tok/s
                        # decode. Used only to feed --max-budget-usd; not
                        # invoiced.
                        usage = obj.get("message", {}).get("usage", {})
                        in_tok = usage.get("input_tokens", 0)
                        out_tok = usage.get("output_tokens", 0)
                        cost_usd += (in_tok * 1.0 + out_tok * 30.0) / 1_000_000
        except OSError:
            pass
        return {"cost_usd": cost_usd, "is_overage": is_overage}

    def maybe_kill_if_over_budget(self) -> bool:
        """Kill agent if session cost exceeds ``max_budget_usd``.

        Works for both API key billing and subscription overage billing.
        Returns True if killed.
        """
        if self.max_budget_usd is None or not self.is_alive():
            return False
        cost_info = self._check_session_cost()
        if cost_info["cost_usd"] >= self.max_budget_usd:
            print(
                f"  [$] Agent {self.agent_id} ({self.username}): "
                f"cost ${cost_info['cost_usd']:.2f} >= budget ${self.max_budget_usd:.2f}"
                f"{' (overage)' if cost_info['is_overage'] else ''}, stopping session"
            )
            self.stop()
            return True
        return False

    def _update_metadata_rate_limit(self, rate_info: dict | None):
        """Write rate limit state to metadata.json for dashboard visibility."""
        meta_path = self.sandbox_dir / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        except (json.JSONDecodeError, OSError):
            meta = {}
        if rate_info:
            meta["rate_limited"] = True
            meta["rate_limit_until"] = rate_info["reset_at"]
            meta["rate_limit_type"] = rate_info["rate_limit_type"]
            meta["rate_limit_reason"] = rate_info["reason"]
            meta["rate_limit_source"] = rate_info["source"]
        else:
            meta["rate_limited"] = False
            meta["rate_limit_until"] = None
            meta.pop("rate_limit_type", None)
            meta.pop("rate_limit_reason", None)
            meta.pop("rate_limit_source", None)
        try:
            meta_path.write_text(json.dumps(meta, indent=2))
        except OSError:
            pass

    def maybe_restart_session(self) -> bool:
        """If the session exited, start a new one after a pause. Returns True if restarted."""
        if self.is_alive():
            return False
        # Check for rate limit before restarting
        rate_info = self._check_rate_limit()
        if rate_info:
            reset_at = rate_info["reset_at"]
            wait_seconds = max(0, reset_at - time.time())
            if wait_seconds > 0:
                wait_minutes = int(wait_seconds / 60)
                rl_type = rate_info.get("rate_limit_type", "")
                print(
                    f"  [!] Agent {self.agent_id} ({self.username}): "
                    f"rate-limited ({rl_type}), waiting {wait_minutes}min until reset"
                )
                self._rate_limit_until = reset_at
                self._rate_limit_info = rate_info
                self._update_metadata_rate_limit(rate_info)
                return False
        # Respect rate limit backoff if previously set
        if hasattr(self, "_rate_limit_until") and time.time() < self._rate_limit_until:
            return False
        # Rate limit expired — clear state
        if hasattr(self, "_rate_limit_info") and self._rate_limit_info:
            self._rate_limit_info = None
            self._update_metadata_rate_limit(None)
        self._rate_limit_until = 0
        self.stop()  # clean up file handle

        # Crash-loop guard: if the just-exited session never reached a healthy
        # uptime, it almost certainly failed at launch (server down, MCP init
        # failure, auth error). Respawning the full CLI+MCP+Chromium stack every
        # `pause_between` seconds in that state is the uncapped-respawn load
        # cascade we are hardening against — back off exponentially instead.
        if self._session_started_at and (
            time.time() - self._session_started_at < self._MIN_HEALTHY_UPTIME
        ):
            self._consecutive_fast_fails += 1
        else:
            self._consecutive_fast_fails = 0

        if self._consecutive_fast_fails > 0:
            # 10s, 20s, 40s, … capped at _MAX_RESTART_BACKOFF.
            backoff = min(
                self.pause_between * (2 ** (self._consecutive_fast_fails - 1)),
                self._MAX_RESTART_BACKOFF,
            )
            print(
                f"  [!] Agent {self.agent_id} ({self.username}): session failed "
                f"fast ({self._consecutive_fast_fails}x) — backing off {int(backoff)}s "
                "before respawn"
            )
            time.sleep(backoff)
        else:
            time.sleep(self.pause_between)
        self.start_session()
        return True

    def maybe_restart_if_stale(self, threshold_seconds: int = 900) -> bool:
        """Kill and restart if log is stale (Playwright hang). Returns True if restarted."""
        if not self.is_stale(threshold_seconds):
            return False
        self.stop()
        time.sleep(self.pause_between)
        self.start_session()
        return True

    def maybe_restart_if_disconnected(self) -> bool:
        """Kill and restart if the agent appears disconnected (position 0,0 repeatedly).

        Checks the last 20 lines of the latest log for pos (0,0) or state
        extractor errors, which indicate a server disconnect. If found in 3+ of
        the last 20 state reads, the session is likely stuck reconnecting.
        Returns True if restarted.
        """
        if not self.is_alive():
            return False
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return False
            log_path = logs[0]
            # Only check if the log is recent (modified in the last 2 minutes)
            if time.time() - log_path.stat().st_mtime > 120:
                return False
            # Read last 50KB of the log
            size = log_path.stat().st_size
            with open(log_path, "r", errors="replace") as f:
                if size > 50_000:
                    f.seek(size - 50_000)
                    f.readline()  # skip partial line
                lines = f.readlines()
            # Check last 20 tool results for disconnect indicators
            # Skip if log is too small (< 100KB) — early session startup has
            # normal "State extractor not loaded" errors that aren't disconnects
            if size < 100_000:
                return False
            disconnect_count = 0
            checked = 0
            for line in reversed(lines[-40:]):
                if checked >= 20:
                    break
                if '\\"pos\\"' in line:
                    checked += 1
                    if '\\"x\\": 0, \\"y\\": 0' in line or '\\"x\\":0,\\"y\\":0' in line:
                        disconnect_count += 1
                elif 'State extractor not loaded' in line or 'Game not loaded' in line:
                    checked += 1
                    disconnect_count += 1
            if disconnect_count >= 5:
                print(
                    f"  [!] Agent {self.agent_id} ({self.username}): "
                    f"detected disconnect ({disconnect_count} bad states), restarting session"
                )
                self.stop()
                time.sleep(self.pause_between)
                self.start_session()
                return True
        except OSError:
            pass
        return False

    def maybe_restart_if_mcp_failed(self, grace_seconds: int = 90) -> bool:
        """Kill and restart if MCP server is stuck in 'pending' or 'failed' status.

        Only checks sessions younger than grace_seconds (default 90s) to avoid
        false positives on sessions that are well underway. Reads the first line
        (system init) of the latest log which contains mcp_servers status.
        Returns True if restarted.
        """
        if not self.is_alive():
            return False
        # Only applies to harnesses that use MCP (claude, codex)
        if self.adapter.name not in ("claude", "codex", "gemini"):
            return False
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return False
            log_path = logs[0]
            age = time.time() - log_path.stat().st_mtime
            # Only check young sessions (MCP should connect within first ~30s)
            # but wait at least 30s to give it time to connect
            if age > grace_seconds or log_path.stat().st_size < 100:
                return False
            # Session must be at least 30s old to give MCP time
            session_age = time.time() - log_path.stat().st_ctime
            if session_age < 30:
                return False

            with open(log_path, "r") as f:
                content = f.read(50000)  # first 50KB

            if self.adapter.name == "claude":
                # Read first line (system init event)
                first_line = content.split("\n", 1)[0].strip()
                if not first_line:
                    return False
                init = json.loads(first_line)
                mcp_servers = (init.get("message", {}).get("content", "") if isinstance(init.get("message", {}).get("content"), str) else "")
                # Handle structured init format
                if not mcp_servers:
                    # Try nested format: message.content may be list
                    msg_content = init.get("message", {}).get("content", [])
                    if isinstance(msg_content, list):
                        for c in msg_content:
                            if isinstance(c, dict) and "text" in c:
                                mcp_servers = c["text"]
                                break
                # Also check top-level mcp_servers field
                if not mcp_servers:
                    mcp_servers = json.dumps(init.get("mcp_servers", []))
                if '"kaetram"' in mcp_servers and ('"pending"' in mcp_servers or '"failed"' in mcp_servers):
                    if "mcp__kaetram__" not in content:
                        print(
                            f"  [!] Agent {self.agent_id} ({self.username}): "
                            f"MCP stuck in pending/failed, restarting session"
                        )
                        self.stop()
                        time.sleep(max(self.pause_between, 15))
                        self.start_session()
                        return True
            else:
                # Codex: no system init event with MCP status, but we can check
                # for absence of any kaetram tool calls + presence of errors
                has_kaetram_call = "kaetram" in content
                has_error = '"turn.failed"' in content or '"error"' in content
                if not has_kaetram_call and has_error:
                    print(
                        f"  [!] Agent {self.agent_id} ({self.username}): "
                        f"MCP appears failed (no kaetram calls, errors present), restarting session"
                    )
                    self.stop()
                    time.sleep(max(self.pause_between, 15))
                    self.start_session()
                    return True
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        return False

    def maybe_restart_if_mcp_dead(self, error_threshold: int = 5) -> bool:
        """Kill and restart if MCP server died mid-session.

        Detects repeated 'MCP server ... is not connected' or 'Connection closed'
        errors in the tail of the latest log. This catches the case where MCP was
        connected at session start but crashed during gameplay.
        Returns True if restarted.
        """
        if not self.is_alive():
            return False
        if self.adapter.name not in ("claude", "codex", "gemini"):
            return False
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return False
            log_path = logs[0]
            # Only check if log was recently modified (active session)
            if time.time() - log_path.stat().st_mtime > 60:
                return False
            # Read last 30KB of the log
            size = log_path.stat().st_size
            if size < 5000:
                return False
            with open(log_path, "r", errors="replace") as f:
                if size > 30_000:
                    f.seek(size - 30_000)
                    f.readline()  # skip partial line
                tail = f.read()
            # Count MCP death indicators (works for both Claude and Codex)
            mcp_errors = (tail.count("is not connected") + tail.count("Connection closed")
                          + tail.count('"turn.failed"'))
            if mcp_errors >= error_threshold:
                print(
                    f"  [!] Agent {self.agent_id} ({self.username}): "
                    f"MCP server died mid-session ({mcp_errors} errors in log tail), "
                    f"restarting session"
                )
                self.stop()
                time.sleep(max(self.pause_between, 10))
                self.start_session()
                return True
        except OSError:
            pass
        return False

class Orchestrator:
    def __init__(self, n_agents: int, hours: float | None = None,
                 personality_counts: dict[str, int] | None = None,
                 harness_counts: dict[str, int] | None = None,
                 model: str | None = None,
                 opencode_model: str | None = None,
                 qwen_variants: list[str] | None = None,
                 max_budget_usd: float | None = None):
        self.n_agents = n_agents
        self.personality_counts = personality_counts
        self.harness_counts = harness_counts or {"claude": n_agents}
        self.model = model
        self.opencode_model = opencode_model
        # Per-Qwen-slot variant ("sft" | "base") in the same order setup()
        # walks the qwen-harness slots. Empty when no Qwen agents.
        self.qwen_variants = list(qwen_variants or [])
        self.max_budget_usd = max_budget_usd
        self.deadline = time.time() + hours * 3600 if hours else None
        self.servers: list[GameServer] = []
        self.agents: list[AgentInstance] = []
        self.running = True
        self.start_time = time.time()
        # Tracks proxy daemons we spawned (None if already running
        # externally, or no OpenCode agents needing them).
        self._nim_proxy_proc: subprocess.Popen | None = None
        self._deepseek_proxy_proc: subprocess.Popen | None = None
        # Detect auth mode once at startup (cached for all agents)
        self.auth_mode = detect_auth_mode()
        if self.auth_mode == "api_key":
            print(f"[i] Auth mode: API key (--max-budget-usd {'$' + str(max_budget_usd) if max_budget_usd else 'unlimited'})")
        else:
            print(f"[i] Auth mode: subscription"
                  f"{' (budget enforcement via cost tracking: $' + str(max_budget_usd) + ')' if max_budget_usd else ''}")

    def setup(self):
        """Create all server and agent instances."""
        from cli_adapter import QWEN_SFT_ENDPOINT, QWEN_BASE_ENDPOINT
        # Build per-agent harness assignment list
        harness_list = []
        for h in ("claude", "codex", "gemini", "opencode", "qwen"):
            harness_list.extend([h] * self.harness_counts.get(h, 0))
        # Pop-front queue of qwen variants (one per qwen slot, slot order
        # matches the qwen entries we just appended to harness_list).
        qwen_variant_queue = list(self.qwen_variants)

        # Build personality assignment list
        if self.personality_counts:
            base_pattern = []
            for p in VALID_PERSONALITIES:
                count = self.personality_counts.get(p, 0)
                base_pattern.extend([p] * count)
            # If more agents than personalities (e.g. 2 personalities × 2 harness groups),
            # repeat the pattern so each harness group gets the same personality set.
            if len(base_pattern) < self.n_agents:
                n_harness_groups = sum(1 for v in self.harness_counts.values() if v > 0)
                if n_harness_groups > 1:
                    assignments = base_pattern * n_harness_groups
                else:
                    assignments = base_pattern
            else:
                assignments = base_pattern
        else:
            # Default: round-robin across all 3 personalities
            assignments = [VALID_PERSONALITIES[i % len(VALID_PERSONALITIES)]
                           for i in range(self.n_agents)]

        for i in range(self.n_agents):
            port = BASE_SERVER_PORT + i * PORT_STRIDE
            server = GameServer(agent_id=i, port=port)
            self.servers.append(server)

            harness = harness_list[i] if i < len(harness_list) else "claude"
            # OpenCode has its own model override since the model is configured
            # via opencode.json rather than a CLI flag — see OpenCodeAdapter.
            adapter_model = self.opencode_model if harness == "opencode" else self.model
            slot_qwen_endpoint = None
            if harness == "qwen" and qwen_variant_queue:
                # SFT slots first, then base — order matches argparse build.
                variant = qwen_variant_queue.pop(0)
                slot_qwen_endpoint = (QWEN_BASE_ENDPOINT if variant == "base"
                                      else QWEN_SFT_ENDPOINT)
            adapter = get_adapter(
                harness=harness, model=adapter_model,
                qwen_endpoint=slot_qwen_endpoint,
            )
            personality = assignments[i] if i < len(assignments) else "grinder"

            # Username: most harnesses use ClaudeBot0/CodexBot1/etc (per-agent
            # numeric suffix). Qwen uses personality-based names so the in-game
            # bot maps 1:1 to the personality variant being evaluated.
            if harness == "qwen":
                qwen_username_map = {
                    "grinder": "QwenGrinder",
                    "completionist": "QwenCompletionist",
                    "explorer_tinkerer": "QwenExplorer",
                }
                username = qwen_username_map.get(personality, f"QwenBot{i}")
                bot_prefix = username  # used below for run.meta.json username field
            elif harness == "opencode":
                # Split by model family — adapter.model is the resolved full
                # ID after alias substitution, so substring matches work.
                bot_prefix = opencode_bot_prefix(adapter.model)
                username = f"{bot_prefix}{i}"
            else:
                prefix_map = {"codex": "CodexBot", "gemini": "GeminiBot"}
                bot_prefix = prefix_map.get(harness, "ClaudeBot")
                username = f"{bot_prefix}{i}"

            sandbox = Path(f"/tmp/kaetram_agent_{i}")

            # ── Runs hierarchy: dataset/raw/agent_N/runs/run_<EST_TS>/
            # Each orchestrator launch creates a new run directory. The
            # logs/ symlink is updated to point to it for backward compat.
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            _EST = _tz(_td(hours=-4))  # EDT
            run_ts = _dt.now(tz=_EST).strftime("%Y%m%d_%H%M%S")
            run_id = f"run_{run_ts}"
            agent_raw = PROJECT_DIR / "dataset" / "raw" / f"agent_{i}"
            runs_dir = agent_raw / "runs"
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            # Write run-level metadata. session_count is 0 at launch and
            # incremented by AgentInstance.start_session() on each new
            # session — that keeps the meta in sync with the migration
            # script's shape (which set session_count to the actual log
            # count at migration time).
            run_meta = {
                "run_id": run_id,
                "agent_id": i,
                "personality": personality,
                "harness": harness,
                "model": adapter.model,
                "username": username,
                "started_at": _dt.now(tz=_EST).isoformat(),
                "hours_budget": round((self.deadline - self.start_time) / 3600, 1) if self.deadline else None,
                "n_agents": self.n_agents,
                "session_count": 0,
            }
            (run_dir / "run.meta.json").write_text(json.dumps(run_meta, indent=2))

            # Update logs/ symlink → current run dir
            logs_link = agent_raw / "logs"
            rel_target = run_dir.relative_to(agent_raw)
            if logs_link.is_symlink() or logs_link.exists():
                if logs_link.is_symlink():
                    logs_link.unlink()
                elif logs_link.is_dir():
                    # First run after migration — logs/ might still be a real dir
                    import shutil as _shutil
                    _shutil.rmtree(logs_link)
            logs_link.symlink_to(rel_target)

            log_dir = run_dir
            agent = AgentInstance(
                agent_id=i,
                username=username,
                server_port=port,
                sandbox_dir=sandbox,
                log_dir=log_dir,
                adapter=adapter,
                personality=personality,
                max_budget_usd=self.max_budget_usd,
                auth_mode=self.auth_mode,
            )
            agent.setup()
            self.agents.append(agent)

    def _nim_proxy_reachable(self) -> bool:
        try:
            with socket.create_connection((NIM_PROXY_HOST, NIM_PROXY_PORT), timeout=1):
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False

    def _ensure_nim_proxy(self):
        """Start scripts/start-nim-proxy.sh if any OpenCode agent is configured.

        OpenCode points its baseURL at http://127.0.0.1:8889/v1 (see
        opencode.template.json), but nothing else in the stack starts that
        proxy. Without it, every OpenCode chat completion silently hangs on
        connect. We TCP-probe the port; if dead, spawn the daemon script and
        wait for it to come up. Fail-fast with a clear message rather than
        letting agents stall.
        """
        if self.harness_counts.get("opencode", 0) <= 0:
            return
        if self._nim_proxy_reachable():
            print(f"[i] NIM proxy already up on {NIM_PROXY_HOST}:{NIM_PROXY_PORT}, reusing")
            return
        if not NIM_PROXY_SCRIPT.exists():
            print(f"ERROR: OpenCode agents requested but {NIM_PROXY_SCRIPT} missing.")
            sys.exit(1)
        print(f"[i] Starting NIM proxy via {NIM_PROXY_SCRIPT.name}...")
        self._nim_proxy_proc = subprocess.Popen(
            ["bash", str(NIM_PROXY_SCRIPT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        for _ in range(20):  # up to ~5 s
            time.sleep(0.25)
            if self._nim_proxy_reachable():
                print(f"[i] NIM proxy ready on {NIM_PROXY_HOST}:{NIM_PROXY_PORT}")
                return
        print(f"ERROR: NIM proxy did not come up on {NIM_PROXY_HOST}:{NIM_PROXY_PORT}.")
        print("       Check /tmp/nim_proxy.log; OpenCode will hang without it.")
        sys.exit(1)

    def _stop_nim_proxy(self):
        if self._nim_proxy_proc is None or self._nim_proxy_proc.poll() is not None:
            self._nim_proxy_proc = None
            return
        try:
            os.killpg(os.getpgid(self._nim_proxy_proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            self._nim_proxy_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._nim_proxy_proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        self._nim_proxy_proc = None

    def _deepseek_proxy_reachable(self) -> bool:
        try:
            with socket.create_connection((NIM_PROXY_HOST, DEEPSEEK_PROXY_PORT), timeout=1):
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False

    def _ensure_deepseek_proxy(self):
        """Start scripts/start-deepseek-proxy.sh if any OpenCode agent uses
        a DeepSeek model. Same role as _ensure_nim_proxy but for the
        api.deepseek.com upstream (port 8890). Without it, opencode silently
        drops DeepSeek V4 Pro/Flash reasoning_content."""
        if self.harness_counts.get("opencode", 0) <= 0:
            return
        if "deepseek" not in (self.opencode_model or ""):
            return
        if self._deepseek_proxy_reachable():
            print(f"[i] DeepSeek proxy already up on {NIM_PROXY_HOST}:{DEEPSEEK_PROXY_PORT}, reusing")
            return
        if not DEEPSEEK_PROXY_SCRIPT.exists():
            print(f"ERROR: DeepSeek opencode model requested but {DEEPSEEK_PROXY_SCRIPT} missing.")
            sys.exit(1)
        print(f"[i] Starting DeepSeek proxy via {DEEPSEEK_PROXY_SCRIPT.name}...")
        self._deepseek_proxy_proc = subprocess.Popen(
            ["bash", str(DEEPSEEK_PROXY_SCRIPT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        for _ in range(20):
            time.sleep(0.25)
            if self._deepseek_proxy_reachable():
                print(f"[i] DeepSeek proxy ready on {NIM_PROXY_HOST}:{DEEPSEEK_PROXY_PORT}")
                return
        print(f"ERROR: DeepSeek proxy did not come up on {NIM_PROXY_HOST}:{DEEPSEEK_PROXY_PORT}.")
        print("       Check /tmp/deepseek_proxy.log; reasoning capture will be broken.")
        sys.exit(1)

    def _stop_deepseek_proxy(self):
        if self._deepseek_proxy_proc is None or self._deepseek_proxy_proc.poll() is not None:
            self._deepseek_proxy_proc = None
            return
        try:
            os.killpg(os.getpgid(self._deepseek_proxy_proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            self._deepseek_proxy_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._deepseek_proxy_proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        self._deepseek_proxy_proc = None

    def start(self):
        """Start all servers, wait for health, then start all agents."""
        # Bring up reasoning-capture proxies first (only if needed) — agents
        # must not race proxy boot.
        self._ensure_nim_proxy()
        self._ensure_deepseek_proxy()

        harness_parts = []
        for label, key in [("Claude", "claude"), ("Codex", "codex"), ("Gemini", "gemini"), ("OpenCode", "opencode")]:
            n = self.harness_counts.get(key, 0)
            if n > 0:
                harness_parts.append(f"{n} {label}")
        # Qwen tracks variant (sft/base) per slot — count from qwen_variants
        # so the banner reflects what was actually requested.
        n_sft = self.qwen_variants.count("sft")
        n_base = self.qwen_variants.count("base")
        if n_sft:
            harness_parts.append(f"{n_sft} Qwen-SFT")
        if n_base:
            harness_parts.append(f"{n_base} Qwen-Base")
        mix_label = " + ".join(harness_parts) if harness_parts else "Claude"
        print(f"Starting {self.n_agents} game servers ({mix_label})...")
        for server in self.servers:
            server.start()
            print(f"  Server {server.agent_id}: port {server.port} (PID {server.process.pid})")

        # Wait for servers to be ready
        print("Waiting for servers to be healthy...")
        for _ in range(30):
            time.sleep(2)
            healthy = sum(1 for s in self.servers if s.health_check())
            if healthy == self.n_agents:
                break
        else:
            healthy = sum(1 for s in self.servers if s.health_check())
            if healthy == 0:
                print("ERROR: No servers came up healthy. Check Kaetram installation.")
                self.shutdown()
                sys.exit(1)
            print(f"WARNING: Only {healthy}/{self.n_agents} servers healthy, proceeding anyway.")

        print(f"\nStarting {self.n_agents} agents...")
        for agent in self.agents:
            agent.start_session()
            print(
                f"  Agent {agent.agent_id} ({agent.username}) [{agent.adapter.name}/{agent.personality}]: "
                f"server :{agent.server_port}, session {agent.session}"
            )

        print(f"\nAll {self.n_agents} agents running. Ctrl-C to stop.\n")

    def monitor_loop(self):
        """Main monitoring loop. Checks health and restarts as needed."""
        last_status = 0
        status_interval = 60  # print status every 60s

        while self.running:
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                self.running = False
                break

            if self.deadline and time.time() > self.deadline:
                print("\nTime limit reached. Shutting down...")
                self.running = False
                break

            # Check servers
            for server in self.servers:
                if server.maybe_restart():
                    print(
                        f"  [!] Server {server.agent_id} restarted "
                        f"(restart #{server.restart_count})"
                    )
                    # Wait for it to come up
                    time.sleep(5)

            # Check agents
            for agent in self.agents:
                if agent.maybe_kill_if_over_budget():
                    pass  # already printed; agent will restart with new budget next loop
                elif agent.maybe_restart_session():
                    print(
                        f"  [>] Agent {agent.agent_id} ({agent.username}): "
                        f"new session #{agent.session}"
                    )
                elif agent.maybe_restart_if_mcp_failed():
                    pass  # already printed inside the method
                elif agent.maybe_restart_if_mcp_dead():
                    pass  # already printed inside the method
                elif agent.maybe_restart_if_disconnected():
                    pass  # already printed inside the method
                else:
                    # Tighter watchdog for Qwen warm-session agents: sessions
                    # are short (~60s), so 5 min of log silence already means
                    # a hung browser. Other harnesses keep the conservative
                    # 15-min default.
                    stale_threshold = 300 if agent.adapter.name == "qwen" else 900
                    if agent.maybe_restart_if_stale(threshold_seconds=stale_threshold):
                        mins = stale_threshold // 60
                        print(
                            f"  [!] Agent {agent.agent_id} ({agent.username}): "
                            f"stale {mins}min, restarted → session #{agent.session}"
                        )

                # Independent of agent state: if ffmpeg died but Xvfb is still
                # up, restart only the encoder so the dashboard tile recovers.
                agent.supervise_livestream()

            # If all agents are rate-limited with a distant reset, shut down
            rate_limit_times = []
            for agent in self.agents:
                rl = getattr(agent, "_rate_limit_until", 0)
                if rl > time.time():
                    rate_limit_times.append(rl)
            if len(rate_limit_times) == len(self.agents) and rate_limit_times:
                min_wait = min(rate_limit_times) - time.time()
                if min_wait > 7200:  # more than 2 hours
                    wait_h = int(min_wait / 3600)
                    print(
                        f"\n[!!] All {len(self.agents)} agents are rate-limited. "
                        f"Earliest reset in ~{wait_h}h. Shutting down to avoid idle waste."
                    )
                    self.running = False
                    break

            # Periodic status
            if time.time() - last_status > status_interval:
                self.print_status()
                last_status = time.time()

    def print_status(self):
        """Print a status table."""
        elapsed = time.time() - self.start_time
        h, m = divmod(int(elapsed), 3600)
        m, s = divmod(m, 60)
        print(f"\n--- Status ({h:02d}:{m:02d}:{s:02d} elapsed) ---")
        print(f"{'Agent':>10} {'Harness':>8} {'Personality':>12} {'Server':>8} {'Health':>8} {'Session':>8} {'Status':>12}")
        for i in range(self.n_agents):
            srv = self.servers[i]
            agt = self.agents[i]
            srv_health = "OK" if srv.health_check() else "DOWN"
            rl = getattr(agt, "_rate_limit_until", 0)
            if rl > time.time():
                wait_min = int((rl - time.time()) / 60)
                agt_status = f"rl_{wait_min}m"
            elif agt.is_alive():
                agt_status = "running"
            else:
                agt_status = "exited"
            # Qwen warm-session agents have play_qwen rotate sessions
            # internally; refresh from .session_counter so the table reflects
            # the live count, not the value at start_session().
            if agt.adapter.name == "qwen":
                agt._refresh_session_from_disk()
            print(
                f"{agt.username:>10} {agt.adapter.name:>8} {agt.personality:>12} :{srv.port:>5} {srv_health:>8} "
                f"#{agt.session:>6} {agt_status:>12}"
            )

        # Count total logs
        total_logs = sum(len(list(a.log_dir.glob("session_*.log"))) for a in self.agents)
        print(f"Total session logs: {total_logs}")
        if self.deadline:
            remaining = max(0, self.deadline - time.time())
            rm, rs = divmod(int(remaining), 60)
            rh, rm = divmod(rm, 60)
            print(f"Time remaining: {rh:02d}:{rm:02d}:{rs:02d}")
        print()

    def shutdown(self):
        """Graceful shutdown: stop agents, stop servers, copy logs."""
        print("\nShutting down...")
        for agent in self.agents:
            agent.stop()
        for server in self.servers:
            server.stop()
        self._stop_nim_proxy()
        self._stop_deepseek_proxy()

        # Copy any remaining sandbox state
        for agent in self.agents:
            sandbox_logs = agent.sandbox_dir / "state"
            if sandbox_logs.exists():
                dst = agent.log_dir.parent / "state"
                if not dst.exists():
                    shutil.copytree(sandbox_logs, dst, dirs_exist_ok=True)

        self.print_status()
        print("All agents and servers stopped.")
        print(f"Logs saved in: {PROJECT_DIR / 'dataset' / 'raw'}")


def main():
    parser = argparse.ArgumentParser(description="Multi-agent Kaetram data collection orchestrator")
    parser.add_argument(
        "--agents", type=int, default=3, help="Number of parallel agents (default: 3)"
    )
    parser.add_argument(
        "--hours", type=float, default=None, help="Auto-stop after N hours (default: run forever)"
    )
    parser.add_argument(
        "--grinder", type=int, default=0, help="Number of GRINDER agents (combat/leveling archetype)"
    )
    parser.add_argument(
        "--completionist", type=int, default=0, help="Number of COMPLETIONIST agents (progression/quest archetype)"
    )
    parser.add_argument(
        "--explorer-tinkerer", "--explorer", dest="explorer_tinkerer", type=int, default=0,
        help="Number of EXPLORER/TINKERER agents (world + systems coverage)"
    )
    parser.add_argument(
        "--claude", type=int, nargs="?", const=-1, default=0,
        help="Number of Claude agents (bare --claude = all agents)"
    )
    parser.add_argument(
        "--codex", type=int, nargs="?", const=-1, default=0,
        help="Number of Codex agents (bare --codex = all agents)"
    )
    parser.add_argument(
        "--gemini", type=int, nargs="?", const=-1, default=0,
        help="Number of Gemini agents (bare --gemini = all agents)"
    )
    parser.add_argument(
        "--opencode", type=int, nargs="?", const=-1, default=0,
        help="Number of OpenCode agents (bare --opencode = all agents). "
             "Uses NVIDIA Qwen free API via opencode.template.json."
    )
    parser.add_argument(
        "--qwen-sft", dest="qwen_sft", type=int, nargs="?", const=-1, default=0,
        help="Number of Qwen3.5-9B SFT (finetuned) agents. Routes to "
             "cli_adapter.QWEN_SFT_ENDPOINT and labels model='r10-sft'."
    )
    parser.add_argument(
        "--qwen-base", dest="qwen_base", type=int, nargs="?", const=-1, default=0,
        help="Number of Qwen3.5-9B base (unfinetuned) agents. Routes to "
             "cli_adapter.QWEN_BASE_ENDPOINT and labels model='kaetram-base'. "
             "Mixable with --qwen-sft in the same run."
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Model name override (default: sonnet for Claude, gpt-5.4 for Codex, r10-sft for Qwen)"
    )
    parser.add_argument(
        "--opencode-model", dest="opencode_model", type=str, default=None,
        help="OpenCode model override (alias or full ID). Aliases: grok-4-1-fast, "
             "qwen3.5-35a3b, qwen3.5-397a17b, qwen3-80a3b, deepseek-v4-flash, "
             "deepseek-v4-pro. Falls back to opencode.template.json default."
    )
    parser.add_argument(
        "--max-budget-usd", type=float, default=None,
        help="Max USD budget per agent session (API key only, auto-detected). Default: no limit."
    )
    args = parser.parse_args()

    personality_counts = {
        "grinder":           args.grinder,
        "completionist":     args.completionist,
        "explorer_tinkerer": args.explorer_tinkerer,
    }
    explicit_total = sum(personality_counts.values())

    if explicit_total:
        n_total = explicit_total
    else:
        n_total = args.agents
        personality_counts = None  # round-robin default

    if n_total < 1 or n_total > 8:
        parser.error("Total agent count must be 1-8")

    # Resolve harness counts. Qwen has two variants (SFT / base) handled
    # as separate harness flags; both can be mixed in one run.
    claude_n = args.claude or 0
    codex_n = args.codex or 0
    gemini_n = args.gemini or 0
    opencode_n = args.opencode or 0
    qwen_sft_n = args.qwen_sft or 0
    qwen_base_n = args.qwen_base or 0

    bare_flags = sum(
        1 for v in [claude_n, codex_n, gemini_n, opencode_n, qwen_sft_n, qwen_base_n]
        if v == -1
    )
    if bare_flags > 1:
        parser.error(
            "Cannot use multiple bare harness flags "
            "(--claude, --codex, --gemini, --opencode, --qwen-sft, --qwen-base) "
            "without counts"
        )

    # Handle bare flags (e.g. --qwen-sft alone means all agents)
    if qwen_sft_n == -1:
        qwen_sft_n = n_total
        claude_n = codex_n = gemini_n = opencode_n = qwen_base_n = 0
    elif qwen_base_n == -1:
        qwen_base_n = n_total
        claude_n = codex_n = gemini_n = opencode_n = qwen_sft_n = 0
    elif opencode_n == -1:
        opencode_n = n_total
        claude_n = codex_n = gemini_n = qwen_sft_n = qwen_base_n = 0
    elif gemini_n == -1:
        gemini_n = n_total
        claude_n = codex_n = opencode_n = qwen_sft_n = qwen_base_n = 0
    elif codex_n == -1:
        codex_n = n_total
        claude_n = gemini_n = opencode_n = qwen_sft_n = qwen_base_n = 0
    elif claude_n == -1:
        claude_n = n_total
        codex_n = gemini_n = opencode_n = qwen_sft_n = qwen_base_n = 0
    elif (claude_n == 0 and codex_n == 0 and gemini_n == 0 and opencode_n == 0
          and qwen_sft_n == 0 and qwen_base_n == 0):
        # No harness specified: default all Claude
        claude_n = n_total
    else:
        # Explicit counts: fill remainder with Claude
        explicit_total = (claude_n + codex_n + gemini_n + opencode_n
                          + qwen_sft_n + qwen_base_n)
        if explicit_total < n_total:
            claude_n = n_total - explicit_total
        elif explicit_total > n_total:
            n_total = explicit_total

    qwen_n = qwen_sft_n + qwen_base_n
    harness_counts = {
        "claude": claude_n, "codex": codex_n, "gemini": gemini_n,
        "opencode": opencode_n, "qwen": qwen_n,
    }
    # Per-Qwen-slot variant labels in slot order: SFT slots first, then base.
    # Consumed pop-front by Orchestrator.setup() to pick endpoint per slot.
    qwen_variants = ["sft"] * qwen_sft_n + ["base"] * qwen_base_n

    # Check for required CLIs
    if codex_n > 0 and shutil.which("codex") is None:
        parser.error("codex CLI not found. Install with: npm install -g @openai/codex")
    if gemini_n > 0 and shutil.which("gemini") is None:
        parser.error("gemini CLI not found. Install with: npm install -g @google/gemini-cli")
    if opencode_n > 0 and shutil.which("opencode") is None:
        parser.error("opencode CLI not found. Install with: npm install -g opencode")

    orch = Orchestrator(
        n_agents=n_total, hours=args.hours,
        personality_counts=personality_counts,
        harness_counts=harness_counts, model=args.model,
        opencode_model=args.opencode_model,
        qwen_variants=qwen_variants,
        max_budget_usd=args.max_budget_usd,
    )

    # Handle SIGINT/SIGTERM gracefully
    def signal_handler(sig, frame):
        orch.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    started = False
    try:
        orch.setup()
        orch.start()
        started = True

        subject, body = format_notification(
            "Kaetram Claude Run Started",
            [
                f"Agents: {n_total}",
                f"Hours: {args.hours if args.hours is not None else 'until stopped'}",
                f"Harness counts: {harness_counts}",
                f"Personality counts: {personality_counts or 'round-robin default'}",
                f"Project dir: {PROJECT_DIR}",
            ],
        )
        send_email_notification(subject, body)

        orch.monitor_loop()
    except Exception as e:
        subject, body = format_notification(
            "Kaetram Claude Run Failed",
            [
                f"Agents: {n_total}",
                f"Harness counts: {harness_counts}",
                f"Error: {type(e).__name__}: {e}",
            ],
        )
        send_email_notification(subject, body)
        raise
    finally:
        orch.shutdown()
        if started:
            total_logs = sum(len(list(a.log_dir.glob('session_*.log'))) for a in orch.agents)
            subject, body = format_notification(
                "Kaetram Claude Run Finished",
                [
                    f"Agents: {n_total}",
                    f"Total session logs: {total_logs}",
                    f"Logs dir: {PROJECT_DIR / 'dataset' / 'raw'}",
                ],
            )
            send_email_notification(subject, body)


if __name__ == "__main__":
    main()
