#!/usr/bin/env python3
"""
orchestrate.py — Multi-agent launcher and monitor for Kaetram SFT data collection.

Launches N independent (Kaetram server + Claude agent) pairs, monitors health,
auto-restarts on crash, and collects logs for post-processing.

Usage:
    python3 orchestrate.py --agents 4               # run until ctrl-c
    python3 orchestrate.py --agents 2 --hours 8     # auto-stop after 8h
"""

import argparse
import functools
import json
import os
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

PROJECT_DIR = Path(__file__).parent
KAETRAM_DIR = Path.home() / "projects" / "Kaetram-Open"
KAETRAM_SERVER_DIR = KAETRAM_DIR / "packages" / "server"
NVM_SH = Path.home() / ".nvm" / "nvm.sh"
SYSTEM_PROMPT_FILE = PROJECT_DIR / "prompts" / "system.md"
GAME_KNOWLEDGE_FILE = PROJECT_DIR / "prompts" / "game_knowledge.md"
PERSONALITY_DIR = PROJECT_DIR / "prompts" / "personalities"
VALID_PERSONALITIES = ("aggressive", "methodical", "curious", "efficient")
MCP_JSON = PROJECT_DIR / ".mcp.json"
STATE_TEMPLATE = {
    "sessions": 0,
    "level": 1,
    "xp_estimate": "0",
    "active_quests": [],
    "completed_quests": [],
    "active_achievements": [],
    "completed_achievements": [],
    "inventory_summary": [],
    "locations_visited": ["mudwich"],
    "kills_this_session": 0,
    "last_action": "none",
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
            f'node --enable-source-maps dist/main.js --port {self.port}'
        )
        self.process = subprocess.Popen(
            ["bash", "-c", cmd],
            cwd=str(KAETRAM_SERVER_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.last_restart = time.time()
        self.restart_count += 1

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
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
    personality: str = "efficient"    # "aggressive", "methodical", "curious", "efficient"
    process: subprocess.Popen | None = None
    session: int = 0
    max_turns: int = 10000
    pause_between: int = 10

    def setup(self):
        """Create sandbox directory with .mcp.json and state/."""
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / "state").mkdir(exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Copy .mcp.json
        shutil.copy2(MCP_JSON, self.sandbox_dir / ".mcp.json")

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

        # Inject personality block
        personality_file = PERSONALITY_DIR / f"{self.personality}.md"
        if personality_file.exists():
            personality_block = personality_file.read_text()
        else:
            personality_block = ""
        prompt = prompt.replace("__PERSONALITY_BLOCK__", personality_block)

        # All agents get game knowledge
        if GAME_KNOWLEDGE_FILE.exists():
            prompt += "\n\n---\n\n" + GAME_KNOWLEDGE_FILE.read_text()

        return prompt

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

        return (
            f"{playstyle_hint}\n\n"
            "IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. "
            "Your ONLY job is to play the game via the browser. "
            "Start IMMEDIATELY with the login code block in your system instructions.\n\n"
            f"Session #{self.session}. Your previous progress: {progress}\n"
            "Follow your system instructions exactly. Load tools, then login, "
            "then run the OBSERVE-ACT loop. Write progress.json before session ends."
        )

    def start_session(self):
        """Launch a new Claude agent session."""
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

        cmd = [
            "claude",
            "-p",
            user_prompt,
            "--model",
            "sonnet",
            "--max-turns",
            str(self.max_turns),
            "--append-system-prompt",
            system_prompt,
            "--dangerously-skip-permissions",
            "--disallowedTools",
            "Glob Grep Agent Edit WebFetch WebSearch Write Skill "
            "mcp__playwright__browser_evaluate mcp__playwright__browser_snapshot "
            "mcp__playwright__browser_console_messages "
            "mcp__playwright__browser_take_screenshot mcp__playwright__browser_click",
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        log_fh = open(log_file, "w")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.sandbox_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env={**os.environ, "CLAUDECODE": ""},
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

    def maybe_restart_session(self) -> bool:
        """If the session exited, start a new one after a pause. Returns True if restarted."""
        if self.is_alive():
            return False
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


class Orchestrator:
    def __init__(self, n_agents: int, hours: float | None = None,
                 personality_counts: dict[str, int] | None = None):
        self.n_agents = n_agents
        self.personality_counts = personality_counts
        self.deadline = time.time() + hours * 3600 if hours else None
        self.servers: list[GameServer] = []
        self.agents: list[AgentInstance] = []
        self.running = True
        self.start_time = time.time()

    def setup(self):
        """Create all server and agent instances."""
        # Build personality assignment list
        if self.personality_counts:
            assignments = []
            for p in VALID_PERSONALITIES:
                count = self.personality_counts.get(p, 0)
                assignments.extend([p] * count)
        else:
            # Default: round-robin across all 4 personalities
            assignments = [VALID_PERSONALITIES[i % len(VALID_PERSONALITIES)]
                           for i in range(self.n_agents)]

        for i in range(self.n_agents):
            port = BASE_SERVER_PORT + i * PORT_STRIDE
            server = GameServer(agent_id=i, port=port)
            self.servers.append(server)

            personality = assignments[i] if i < len(assignments) else "efficient"
            sandbox = Path(f"/tmp/kaetram_agent_{i}")
            log_dir = PROJECT_DIR / "dataset" / "raw" / f"agent_{i}" / "logs"
            agent = AgentInstance(
                agent_id=i,
                username=f"ClaudeBot{i}",
                server_port=port,
                sandbox_dir=sandbox,
                log_dir=log_dir,
                personality=personality,
            )
            agent.setup()
            self.agents.append(agent)

    def start(self):
        """Start all servers, wait for health, then start all agents."""
        print(f"Starting {self.n_agents} game servers...")
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
                f"  Agent {agent.agent_id} ({agent.username}) [{agent.personality}]: "
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
                elif agent.maybe_restart_if_stale(threshold_seconds=900):
                    print(
                        f"  [!] Agent {agent.agent_id} ({agent.username}): "
                        f"stale 15min, restarted → session #{agent.session}"
                    )

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
        print(f"{'Agent':>10} {'Personality':>12} {'Server':>8} {'Health':>8} {'Session':>8} {'Status':>10}")
        for i in range(self.n_agents):
            srv = self.servers[i]
            agt = self.agents[i]
            srv_health = "OK" if srv.health_check() else "DOWN"
            agt_status = "running" if agt.is_alive() else "exited"
            print(
                f"{agt.username:>10} {agt.personality:>12} :{srv.port:>5} {srv_health:>8} "
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

    orch = Orchestrator(
        n_agents=n_total, hours=args.hours,
        personality_counts=personality_counts,
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
