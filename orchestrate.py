#!/usr/bin/env python3
"""
orchestrate.py — Multi-agent launcher and monitor for Kaetram SFT data collection.

Launches N independent (Kaetram server + AI agent) pairs, monitors health,
auto-restarts on crash, and collects logs for post-processing.

Usage:
    python3 orchestrate.py --agents 4                     # 4 Claude agents (default)
    python3 orchestrate.py --agents 2 --hours 8           # auto-stop after 8h
    python3 orchestrate.py --codex                        # all agents use Codex
    python3 orchestrate.py --claude 2 --codex 2           # mixed: 2 Claude + 2 Codex
    python3 orchestrate.py --claude 2 --codex 2 --aggressive 2 --efficient 2
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
from dataclasses import dataclass, field
from pathlib import Path

from cli_adapter import CLIAdapter, get_adapter

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
VALID_PERSONALITIES = ("aggressive", "methodical", "curious")

# Port allocation: agent N gets server WS on 9001 + N*10
BASE_SERVER_PORT = 9001
PORT_STRIDE = 10
CLIENT_PORT = 9000  # shared static client


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
        cmd = (
            f'source "{NVM_SH}" && nvm use 20 --silent && '
            f'exec node --enable-source-maps dist/main.js --port {self.port}'
        )
        self.process = subprocess.Popen(
            ["bash", "-c", cmd],
            cwd=str(KAETRAM_SERVER_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


@dataclass
class AgentInstance:
    agent_id: int
    username: str
    server_port: int
    sandbox_dir: Path
    log_dir: Path
    adapter: CLIAdapter
    personality: str = "aggressive"    # "aggressive", "methodical", "curious"
    process: subprocess.Popen | None = None
    session: int = 0
    max_turns: int = 150
    max_budget_usd: float | None = None
    auth_mode: str = "subscription"   # "api_key" or "subscription"
    pause_between: int = 10

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

        In multi-agent mode, state file paths (screenshots, game_state, progress)
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

        # Inject personality block
        personality_file = PERSONALITY_DIR / f"{self.personality}.md"
        personality_block = personality_file.read_text() if personality_file.exists() else ""
        prompt = prompt.replace("__PERSONALITY_BLOCK__", personality_block)

        return prompt

    def _extract_game_state_from_log(self) -> str | None:
        """Extract the last game state JSON from the most recent session log."""
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return None
            return self.adapter.parse_game_state_from_log(logs[0])
        except OSError:
            return None

    def _build_user_prompt(self) -> str:
        """Build the user prompt for a session."""
        playstyle_hint = {
            "aggressive": "You play AGGRESSIVE — fight hard mobs, push into new zones, attempt bosses. Combat is your priority.",
            "methodical": "You play METHODICAL — prepare thoroughly, gather resources, craft items, build skills before advancing.",
            "curious": "You play CURIOUS — talk to every NPC, enter every building, discover hidden paths, accept all quests.",
        }.get(self.personality, "")

        game_state_block = ""
        game_state = self._extract_game_state_from_log()
        if game_state:
            game_state_block = (
                "\nPrevious game state (from last observe step):\n"
                f"{game_state}\n"
                "Use nearest_mob.click_x/click_y to click on targets. "
                "Use player_position for spatial awareness."
            )

        return (
            f"{playstyle_hint}\n\n"
            "IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. "
            "Your ONLY job is to play the game via the browser. "
            "Start IMMEDIATELY with the login code block in your system instructions.\n\n"
            f"Session #{self.session}.\n"
            f"{game_state_block}\n"
            "Follow your system instructions exactly. Load tools, then login, "
            "then run the OBSERVE-ACT loop."
        )

    def start_session(self):
        """Launch a new agent session (Claude or Codex, depending on adapter)."""
        self.session += 1
        # Persist session counter to disk for resume support
        counter_file = self.sandbox_dir / "state" / ".session_counter"
        counter_file.write_text(str(self.session))
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"session_{self.session}_{timestamp}.log"

        # Write sidecar metadata alongside the session log for auditing/filtering
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

        # Clear stale screenshots from previous session so dashboard doesn't show old frames
        state_dir = self.sandbox_dir / "state"
        for f in ("screenshot.png", "live_screen.png"):
            (state_dir / f).unlink(missing_ok=True)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt()

        # Write CLI-specific files (e.g. .mcp.json for game server, refreshed each session)
        self.adapter.setup_sandbox(
            self.sandbox_dir, system_prompt,
            port=str(self.server_port), username=self.username,
        )

        cmd = self.adapter.build_command(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_turns=self.max_turns,
            max_budget_usd=self.max_budget_usd,
            auth_mode=self.auth_mode,
        )

        log_fh = open(log_file, "w")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.sandbox_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env={**os.environ, **self.adapter.get_env()},
        )
        self._log_fh = log_fh

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
        if hasattr(self, "_log_fh") and self._log_fh:
            self._log_fh.close()
            self._log_fh = None

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

            return None
        except OSError:
            return None

    def _check_session_cost(self) -> dict:
        """Read cost and overage state from the latest session log.

        Returns ``{"cost_usd": float, "is_overage": bool}`` extracted from
        stream-json ``result`` events and ``rate_limit_event`` objects.
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

        Checks the last 20 lines of the latest log for player_position (0,0) or
        state extractor errors, which indicate a server disconnect. If found in 3+
        of the last 20 state reads, the session is likely stuck reconnecting.
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
                if '"player_position"' in line:
                    checked += 1
                    if '"x":0,"y":0' in line or '"x": 0, "y": 0' in line:
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
        # Only applies to harnesses that use MCP (claude)
        if self.adapter.name != "claude":
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
            # Read first line (system init event)
            with open(log_path, "r") as f:
                first_line = f.readline().strip()
            if not first_line:
                return False
            init = json.loads(first_line)
            mcp_servers = (init.get("message", {}).get("content", "") if isinstance(init.get("message", {}).get("content"), str) else "")
            # Handle structured init format
            if not mcp_servers:
                # Try nested format: message.content may be list
                content = init.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            mcp_servers = c["text"]
                            break
            # Also check top-level mcp_servers field
            if not mcp_servers:
                mcp_servers = json.dumps(init.get("mcp_servers", []))
            if '"kaetram"' in mcp_servers and ('"pending"' in mcp_servers or '"failed"' in mcp_servers):
                # Confirm it hasn't connected since (check if any mcp tool calls exist)
                with open(log_path, "r") as f:
                    content = f.read(50000)  # first 50KB
                if "mcp__kaetram__" not in content:
                    print(
                        f"  [!] Agent {self.agent_id} ({self.username}): "
                        f"MCP stuck in pending/failed, restarting session"
                    )
                    self.stop()
                    time.sleep(max(self.pause_between, 15))  # extra time for cleanup
                    self.start_session()
                    return True
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        return False


class Orchestrator:
    def __init__(self, n_agents: int, hours: float | None = None,
                 personality_counts: dict[str, int] | None = None,
                 harness_counts: dict[str, int] | None = None,
                 model: str | None = None,
                 max_budget_usd: float | None = None):
        self.n_agents = n_agents
        self.personality_counts = personality_counts
        self.harness_counts = harness_counts or {"claude": n_agents}
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.deadline = time.time() + hours * 3600 if hours else None
        self.servers: list[GameServer] = []
        self.agents: list[AgentInstance] = []
        self.running = True
        self.start_time = time.time()
        # Detect auth mode once at startup (cached for all agents)
        self.auth_mode = detect_auth_mode()
        if self.auth_mode == "api_key":
            print(f"[i] Auth mode: API key (--max-budget-usd {'$' + str(max_budget_usd) if max_budget_usd else 'unlimited'})")
        else:
            print(f"[i] Auth mode: subscription"
                  f"{' (budget enforcement via cost tracking: $' + str(max_budget_usd) + ')' if max_budget_usd else ''}")

    def setup(self):
        """Create all server and agent instances."""
        # Build per-agent harness assignment list
        harness_list = []
        for h in ("claude", "codex", "kimi", "qwen-code"):
            harness_list.extend([h] * self.harness_counts.get(h, 0))

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
            # Default: round-robin across all 4 personalities
            assignments = [VALID_PERSONALITIES[i % len(VALID_PERSONALITIES)]
                           for i in range(self.n_agents)]

        for i in range(self.n_agents):
            port = BASE_SERVER_PORT + i * PORT_STRIDE
            server = GameServer(agent_id=i, port=port)
            self.servers.append(server)

            harness = harness_list[i] if i < len(harness_list) else "claude"
            adapter = get_adapter(harness=harness, model=self.model)
            prefix_map = {"codex": "CodexBot", "kimi": "KimiBot", "qwen-code": "QwenBot"}
            bot_prefix = prefix_map.get(harness, "ClaudeBot")

            personality = assignments[i] if i < len(assignments) else "aggressive"
            sandbox = Path(f"/tmp/kaetram_agent_{i}")
            log_dir = PROJECT_DIR / "dataset" / "raw" / f"agent_{i}" / "logs"
            agent = AgentInstance(
                agent_id=i,
                username=f"{bot_prefix}{i}",
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

    def start(self):
        """Start all servers, wait for health, then start all agents."""
        harness_parts = []
        for h, count in [("Claude", "claude"), ("Codex", "codex"), ("Kimi", "kimi"), ("Qwen Code", "qwen-code")]:
            n = self.harness_counts.get(count, 0)
            if n > 0:
                harness_parts.append(f"{n} {h}")
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
                elif agent.maybe_restart_if_disconnected():
                    pass  # already printed inside the method
                elif agent.maybe_restart_if_stale(threshold_seconds=900):
                    print(
                        f"  [!] Agent {agent.agent_id} ({agent.username}): "
                        f"stale 15min, restarted → session #{agent.session}"
                    )

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
        "--agents", type=int, default=4, help="Number of parallel agents (default: 4)"
    )
    parser.add_argument(
        "--hours", type=float, default=None, help="Auto-stop after N hours (default: run forever)"
    )
    parser.add_argument(
        "--aggressive", type=int, default=0, help="Number of aggressive-playstyle agents"
    )
    parser.add_argument(
        "--methodical", type=int, default=0, help="Number of methodical-playstyle agents"
    )
    parser.add_argument(
        "--curious", type=int, default=0, help="Number of curious-playstyle agents"
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
        "--kimi", type=int, nargs="?", const=-1, default=0,
        help="Number of Kimi agents (bare --kimi = all agents)"
    )
    parser.add_argument(
        "--qwen-code", type=int, nargs="?", const=-1, default=0,
        help="Number of Qwen Code agents (bare --qwen-code = all agents)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Model name override (default: sonnet for Claude, gpt-5.4 for Codex)"
    )
    parser.add_argument(
        "--max-budget-usd", type=float, default=None,
        help="Max USD budget per agent session (API key only, auto-detected). Default: no limit."
    )
    args = parser.parse_args()

    personality_counts = {
        "aggressive": args.aggressive,
        "methodical": args.methodical,
        "curious": args.curious,
    }
    explicit_total = sum(personality_counts.values())

    if explicit_total:
        n_total = explicit_total
    else:
        n_total = args.agents
        personality_counts = None  # round-robin default

    if n_total < 1 or n_total > 8:
        parser.error("Total agent count must be 1-8")

    # Resolve harness counts (--claude N / --codex N / --kimi N / --qwen-code N)
    claude_n = args.claude or 0
    codex_n = args.codex or 0
    kimi_n = args.kimi or 0
    qwen_code_n = args.qwen_code or 0

    bare_flags = sum(1 for v in [claude_n, codex_n, kimi_n, qwen_code_n] if v == -1)
    if bare_flags > 1:
        parser.error("Cannot use multiple bare harness flags (--claude, --codex, --kimi, --qwen-code) without counts")

    # Handle bare flags (e.g. --codex alone means all agents)
    if qwen_code_n == -1:
        qwen_code_n = n_total
        claude_n = codex_n = kimi_n = 0
    elif kimi_n == -1:
        kimi_n = n_total
        claude_n = codex_n = qwen_code_n = 0
    elif codex_n == -1:
        codex_n = n_total
        claude_n = kimi_n = qwen_code_n = 0
    elif claude_n == -1:
        claude_n = n_total
        codex_n = kimi_n = qwen_code_n = 0
    elif claude_n == 0 and codex_n == 0 and kimi_n == 0 and qwen_code_n == 0:
        # No harness specified: default all Claude
        claude_n = n_total
    else:
        # Explicit counts: fill remainder with Claude
        explicit_total = claude_n + codex_n + kimi_n + qwen_code_n
        if explicit_total < n_total:
            claude_n = n_total - explicit_total
        elif explicit_total > n_total:
            n_total = explicit_total

    harness_counts = {"claude": claude_n, "codex": codex_n, "kimi": kimi_n, "qwen-code": qwen_code_n}

    # Check for required CLIs
    if codex_n > 0 and shutil.which("codex") is None:
        parser.error("codex CLI not found. Install with: npm install -g @openai/codex")
    if kimi_n > 0 and shutil.which("kimi") is None:
        parser.error("kimi CLI not found. Install with: curl -LsSf https://code.kimi.com/install.sh | bash")
    if qwen_code_n > 0 and shutil.which("qwen") is None:
        parser.error("qwen-code CLI not found. Install with: npm install -g @qwen-code/qwen-code")

    orch = Orchestrator(
        n_agents=n_total, hours=args.hours,
        personality_counts=personality_counts,
        harness_counts=harness_counts, model=args.model,
        max_budget_usd=args.max_budget_usd,
    )

    # Handle SIGINT/SIGTERM gracefully
    def signal_handler(sig, frame):
        orch.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    orch.setup()
    orch.start()
    orch.monitor_loop()
    orch.shutdown()


if __name__ == "__main__":
    main()
