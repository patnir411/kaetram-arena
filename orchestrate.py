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
KAETRAM_DIR = Path.home() / "projects" / "Kaetram-Open"
KAETRAM_SERVER_DIR = KAETRAM_DIR / "packages" / "server"
NVM_SH = Path.home() / ".nvm" / "nvm.sh"
SYSTEM_PROMPT_FILE = PROJECT_DIR / "prompts" / "system.md"
GAME_KNOWLEDGE_FILE = PROJECT_DIR / "prompts" / "game_knowledge.md"
PERSONALITY_DIR = PROJECT_DIR / "prompts" / "personalities"
VALID_PERSONALITIES = ("aggressive", "methodical", "curious", "efficient")
STATE_TEMPLATE = {
    "sessions": 0,
    "level": 1,
    "active_quests": [],
    "completed_quests": [],
    "inventory_summary": [],
    "kills_this_session": 0,
    "next_objective": "accept quests from NPCs",
    "notes": "fresh start",
}

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
    personality: str = "efficient"    # "aggressive", "methodical", "curious", "efficient"
    process: subprocess.Popen | None = None
    session: int = 0
    max_turns: int = 150
    pause_between: int = 10

    def setup(self):
        """Create sandbox directory with CLI config and state/."""
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / "state").mkdir(exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Write CLI-specific config (e.g. .mcp.json for Claude, .codex/config.toml for Codex)
        self.adapter.setup_sandbox(self.sandbox_dir)

        # Initialize progress.json
        state_file = self.sandbox_dir / "state" / "progress.json"
        if not state_file.exists():
            state_file.write_text(json.dumps(STATE_TEMPLATE))

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
        state_file = self.sandbox_dir / "state" / "progress.json"
        try:
            progress = state_file.read_text()
        except OSError:
            progress = "{}"

        playstyle_hint = {
            "aggressive": "You play AGGRESSIVE — fight hard mobs, push into new zones, attempt bosses. Combat is your priority.",
            "methodical": "You play METHODICAL — prepare thoroughly, gather resources, craft items, build skills before advancing.",
            "curious": "You play CURIOUS — talk to every NPC, enter every building, discover hidden paths, accept all quests.",
            "efficient": "You play EFFICIENT — shortest path through quest chain, minimal waste, turn in immediately.",
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
            f"Session #{self.session}. Your previous progress: {progress}\n"
            f"{game_state_block}\n"
            "Follow your system instructions exactly. Load tools, then login, "
            "then run the OBSERVE-ACT loop. Write progress.json before session ends."
        )

    def start_session(self):
        """Launch a new agent session (Claude or Codex, depending on adapter)."""
        self.session += 1
        # Persist session counter to disk for resume support
        counter_file = self.sandbox_dir / "state" / ".session_counter"
        counter_file.write_text(str(self.session))
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"session_{self.session}_{timestamp}.log"

        # Clear stale screenshots from previous session so dashboard doesn't show old frames
        state_dir = self.sandbox_dir / "state"
        for f in ("screenshot.png", "live_screen.png"):
            (state_dir / f).unlink(missing_ok=True)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt()

        # Write CLI-specific files (e.g. AGENTS.md for Codex, refreshed each session)
        self.adapter.setup_sandbox(self.sandbox_dir, system_prompt)

        cmd = self.adapter.build_command(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_turns=self.max_turns,
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

    def _check_rate_limit(self) -> float | None:
        """Check if the latest session log ended with a rate limit rejection.

        Returns the reset timestamp if rate-limited, None otherwise.
        """
        try:
            logs = sorted(self.log_dir.glob("session_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return None
            log_path = logs[0]
            # Only check small logs (rate-limit kills produce <10KB logs)
            if log_path.stat().st_size > 50_000:
                return None
            data = log_path.read_text(errors="replace")
            if "overageStatus" not in data:
                return None
            for line in data.splitlines():
                if '"rejected"' in line and "resetsAt" in line:
                    try:
                        obj = json.loads(line)
                        # Navigate nested structure to find resetsAt
                        content = obj.get("message", {}).get("content", [])
                        for block in content:
                            text = block.get("text", "") if isinstance(block, dict) else ""
                            if "resetsAt" in text:
                                match = re.search(r'"resetsAt"\s*:\s*(\d+)', text)
                                if match:
                                    return float(match.group(1))
                    except (json.JSONDecodeError, AttributeError):
                        pass
                # Also check raw line for resetsAt pattern
                if '"rejected"' in line:
                    match = re.search(r'"resetsAt"\s*:\s*(\d+)', line)
                    if match:
                        return float(match.group(1))
            return None
        except OSError:
            return None

    def maybe_restart_session(self) -> bool:
        """If the session exited, start a new one after a pause. Returns True if restarted."""
        if self.is_alive():
            return False
        # Check for rate limit before restarting
        reset_at = self._check_rate_limit()
        if reset_at:
            wait_seconds = max(0, reset_at - time.time())
            if wait_seconds > 0:
                wait_minutes = int(wait_seconds / 60)
                print(
                    f"  [!] Agent {self.agent_id} ({self.username}): "
                    f"rate-limited, waiting {wait_minutes}min until reset"
                )
                # Don't restart — the orchestrator will check again next loop
                self._rate_limit_until = reset_at
                return False
        # Respect rate limit backoff if previously set
        if hasattr(self, "_rate_limit_until") and time.time() < self._rate_limit_until:
            return False
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


class Orchestrator:
    def __init__(self, n_agents: int, hours: float | None = None,
                 personality_counts: dict[str, int] | None = None,
                 harness_counts: dict[str, int] | None = None,
                 model: str | None = None):
        self.n_agents = n_agents
        self.personality_counts = personality_counts
        self.harness_counts = harness_counts or {"claude": n_agents}
        self.model = model
        self.deadline = time.time() + hours * 3600 if hours else None
        self.servers: list[GameServer] = []
        self.agents: list[AgentInstance] = []
        self.running = True
        self.start_time = time.time()

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

            personality = assignments[i] if i < len(assignments) else "efficient"
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
                if agent.maybe_restart_session():
                    print(
                        f"  [>] Agent {agent.agent_id} ({agent.username}): "
                        f"new session #{agent.session}"
                    )
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
        print(f"{'Agent':>10} {'Harness':>8} {'Personality':>12} {'Server':>8} {'Health':>8} {'Session':>8} {'Status':>10}")
        for i in range(self.n_agents):
            srv = self.servers[i]
            agt = self.agents[i]
            srv_health = "OK" if srv.health_check() else "DOWN"
            agt_status = "running" if agt.is_alive() else "exited"
            print(
                f"{agt.username:>10} {agt.adapter.name:>8} {agt.personality:>12} :{srv.port:>5} {srv_health:>8} "
                f"#{agt.session:>6} {agt_status:>10}"
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
        "--efficient", type=int, default=0, help="Number of efficient-playstyle agents"
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
    args = parser.parse_args()

    personality_counts = {
        "aggressive": args.aggressive,
        "methodical": args.methodical,
        "curious": args.curious,
        "efficient": args.efficient,
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
