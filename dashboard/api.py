"""API endpoint methods for the dashboard HTTP handler.

These are mixed into DashboardHandler via APIMixin to keep the handler module small.
"""

import json
import os
import glob
import socket
import subprocess
import time
from datetime import datetime

from dashboard.constants import (
    PROJECT_DIR, STATE_DIR, LOG_DIR, DATASET_DIR,
    BASE_SERVER_PORT, PORT_STRIDE, MAX_AGENTS,
    sanitize,
)
from dashboard.parsers import parse_session_log, quick_session_summary, live_session_stats
from dashboard.game_state import extract_game_state_from_log, extract_game_state_from_db


_agents_cache = {"data": None, "time": 0}
_AGENTS_CACHE_TTL = 5  # seconds — avoid re-parsing logs and probing ports every 2s


class APIMixin:
    """API endpoint methods mixed into DashboardHandler."""

    def send_json_state(self, qs=None):
        self._send_json({})

    def send_game_state(self, qs=None):
        state_dir = self._resolve_state_dir(qs)
        data = {}
        freshness = -1

        # Priority 1: Direct MongoDB query (authoritative, fast)
        agent_id = qs.get("agent", [None])[0] if qs else None
        # Read username from metadata.json for correct DB lookup (supports Codex agents)
        username = None
        if agent_id is not None:
            metadata_file = os.path.join("/tmp", f"kaetram_agent_{agent_id}", "metadata.json")
            if os.path.isfile(metadata_file):
                try:
                    with open(metadata_file) as mf:
                        meta = json.load(mf)
                    username = meta.get("username", "").lower()
                except Exception:
                    pass
            if not username:
                username = f"claudebot{agent_id}"
        else:
            username = "claudebot0"  # default single-agent
        db_state = extract_game_state_from_db(username)
        if db_state:
            data = db_state
            freshness = 0  # DB data is always current

        # Priority 2: game_state.json file
        if not data:
            gs_file = os.path.join(state_dir, "game_state.json")
            if os.path.isfile(gs_file):
                try:
                    with open(gs_file) as fh:
                        data = json.load(fh)
                    mtime = os.path.getmtime(gs_file)
                    freshness = round(time.time() - mtime, 1)
                except Exception:
                    pass

        # Priority 3: Fallback — extract from session log
        if not data or data.get("error"):
            extracted = extract_game_state_from_log(qs)
            if extracted:
                data = extracted
                freshness = data.pop("_freshness", -1)

        data["freshness_seconds"] = freshness
        self._send_json(data)

    def send_prompt(self):
        prompt_file = os.path.join(PROJECT_DIR, "prompts", "system.md")
        text = ""
        if os.path.isfile(prompt_file):
            try:
                with open(prompt_file) as fh:
                    text = fh.read()
            except Exception:
                text = "(error reading file)"

        # Read game knowledge
        gk_file = os.path.join(PROJECT_DIR, "prompts", "game_knowledge.md")
        game_knowledge = ""
        if os.path.isfile(gk_file):
            try:
                with open(gk_file) as fh:
                    game_knowledge = fh.read()
            except Exception:
                pass

        # Read personality files
        personalities = {}
        pdir = os.path.join(PROJECT_DIR, "prompts", "personalities")
        if os.path.isdir(pdir):
            for name in ("aggressive", "methodical", "curious", "efficient"):
                pfile = os.path.join(pdir, f"{name}.md")
                if os.path.isfile(pfile):
                    try:
                        with open(pfile) as fh:
                            personalities[name] = sanitize(fh.read())
                    except Exception:
                        pass

        self._send_json({
            "content": sanitize(text),
            "file": "prompts/system.md",
            "game_knowledge": sanitize(game_knowledge),
            "personalities": personalities,
        })

    def send_session_log(self):
        log_file = os.path.join(PROJECT_DIR, "session_log.md")
        text = ""
        if os.path.isfile(log_file):
            try:
                with open(log_file) as fh:
                    text = fh.read()
            except Exception:
                text = "(error reading file)"
        self._send_json({"content": sanitize(text), "file": "session_log.md"})

    def send_session_detail(self, name, log_dir=None):
        if not name:
            return self._send_json({"error": "missing name param"})
        safe = os.path.basename(name)
        if log_dir:
            # Validate log_dir is an allowed path
            allowed_dirs = [LOG_DIR]
            for i in range(MAX_AGENTS):
                allowed_dirs.append(os.path.join(DATASET_DIR, "raw", f"agent_{i}", "logs"))
            resolved = os.path.realpath(log_dir)
            if not any(os.path.realpath(d) == resolved for d in allowed_dirs):
                return self._send_json({"error": "invalid log directory"})
            filepath = os.path.join(resolved, safe)
        else:
            filepath = os.path.join(LOG_DIR, safe)
        if not os.path.isfile(filepath):
            return self._send_json({"error": "not found"})

        parsed = parse_session_log(filepath)
        parsed["name"] = safe
        self._send_json(parsed)

    # ── Dataset stats ──

    def send_dataset_stats(self):
        stats = {"raw_sessions": 0, "raw_total_size": 0}
        if os.path.isdir(DATASET_DIR):
            raw_dir = os.path.join(DATASET_DIR, "raw")
            if os.path.isdir(raw_dir):
                raw_logs = glob.glob(os.path.join(raw_dir, "agent_*", "logs", "session_*.log"))
                stats["raw_sessions"] = len(raw_logs)
                stats["raw_total_size"] = sum(os.path.getsize(f) for f in raw_logs)
        self._send_json(stats)

    def send_sft_stats(self):
        """SFT pipeline output stats: extracted turns + Qwen3.5 SFT records."""
        stats = {"extracted": {"files": 0, "total_turns": 0}, "qwen_sft": {"train": 0, "val": 0, "total": 0}}

        extracted_dir = os.path.join(DATASET_DIR, "extracted")
        if os.path.isdir(extracted_dir):
            turns_files = glob.glob(os.path.join(extracted_dir, "**", "turns.jsonl"), recursive=True)
            total_turns = 0
            for tf in turns_files:
                try:
                    with open(tf) as fh:
                        total_turns += sum(1 for line in fh if line.strip())
                except Exception:
                    pass
            stats["extracted"] = {"files": len(turns_files), "total_turns": total_turns}

        qwen_dir = os.path.join(DATASET_DIR, "qwen_sft")
        train_file = os.path.join(qwen_dir, "train.json")
        val_file = os.path.join(qwen_dir, "val.json")
        if os.path.isfile(train_file):
            try:
                train_count = len(json.load(open(train_file)))
                val_count = len(json.load(open(val_file))) if os.path.isfile(val_file) else 0
                stats["qwen_sft"] = {"train": train_count, "val": val_count, "total": train_count + val_count}
            except Exception:
                pass

        self._send_json(stats)

    # ── Raw file viewer ──

    def send_raw_file(self, which, qs=None):
        state_dir = self._resolve_state_dir(qs)
        allowed = {
            "game_state": os.path.join(state_dir, "game_state.json"),
            "session_log": os.path.join(PROJECT_DIR, "session_log.md"),
            "claude_md": os.path.join(PROJECT_DIR, "CLAUDE.md"),
            "state_extractor": os.path.join(PROJECT_DIR, "state_extractor.js"),
            "orchestrate": os.path.join(PROJECT_DIR, "orchestrate.py"),
        }
        path = allowed.get(which)
        if not path or not os.path.isfile(path):
            return self._send_json({"error": "not found", "allowed": list(allowed.keys())})
        try:
            with open(path) as fh:
                content = fh.read()
            self._send_json({"file": which, "path": path, "content": sanitize(content), "size": len(content)})
        except Exception as e:
            self._send_json({"error": str(e)})

    # ── Live status (multi-agent aware) ──

    def send_live_status(self):
        mode = "none"
        agent_count = 0
        try:
            result = subprocess.run(["pgrep", "-f", "python3 orchestrate.py"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                mode = "multi"
                for j in range(MAX_AGENTS):
                    meta_file = os.path.join("/tmp", f"kaetram_agent_{j}", "metadata.json")
                    if os.path.isfile(meta_file):
                        try:
                            with open(meta_file) as mf:
                                meta = json.load(mf)
                            if meta.get("personality") != "qwen":
                                agent_count += 1
                        except Exception:
                            pass
        except Exception:
            pass
        if mode == "none":
            try:
                result = subprocess.run(["pgrep", "-f", "play.sh"], capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    mode = "single"
                    agent_count = 1
            except Exception:
                pass

        agent_running = mode != "none"

        gs_fresh = False
        game_state_age = -1

        screenshot_age = -1
        screenshot_time = ""
        if mode == "multi":
            for i in range(MAX_AGENTS):
                for ss_name in ("live_screen.png", "screenshot.png"):
                    ss = os.path.join("/tmp", f"kaetram_agent_{i}", "state", ss_name)
                    if os.path.isfile(ss):
                        mtime = os.path.getmtime(ss)
                        age = int(time.time() - mtime)
                        if screenshot_age < 0 or age < screenshot_age:
                            screenshot_age = age
                            screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                        break
        else:
            screenshot = os.path.join(STATE_DIR, "live_screen.png")
            if not os.path.isfile(screenshot):
                screenshot = os.path.join(STATE_DIR, "screenshot.png")
            if os.path.isfile(screenshot):
                mtime = os.path.getmtime(screenshot)
                screenshot_age = int(time.time() - mtime)
                screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

        active_ports = []
        game_server_up = False
        try:
            result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=3)
            ss_out = result.stdout
            if ":9000" in ss_out:
                game_server_up = True
            for i in range(MAX_AGENTS):
                port = BASE_SERVER_PORT + i * PORT_STRIDE
                if f":{port}" in ss_out:
                    active_ports.append(port)
            if not game_server_up and active_ports:
                game_server_up = True
        except Exception:
            pass

        single_sessions = len(glob.glob(os.path.join(LOG_DIR, "session_*.log")))
        multi_sessions = len(glob.glob(os.path.join(DATASET_DIR, "raw", "agent_*", "logs", "session_*.log")))
        total_sessions = single_sessions + multi_sessions

        self._send_json({
            "mode": mode,
            "agent_running": agent_running,
            "agent_count": agent_count,
            "game_state_fresh": gs_fresh,
            "game_server_up": game_server_up,
            "active_ports": active_ports,
            "game_state_age_seconds": game_state_age,
            "screenshot_age_seconds": screenshot_age,
            "screenshot_time": screenshot_time,
            "total_sessions": total_sessions,
            "single_sessions": single_sessions,
            "multi_sessions": multi_sessions,
        })

    # ── Multi-agent endpoint ──

    def send_agents(self):
        # Cache agent data to avoid re-parsing logs and probing ports every 2s
        now = time.time()
        if _agents_cache["data"] is not None and now - _agents_cache["time"] < _AGENTS_CACHE_TTL:
            return self._send_json(_agents_cache["data"])

        # Check which ports are listening (single ss call instead of per-agent TCP probes)
        listening_ports = set()
        try:
            result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=3)
            for line in result.stdout.splitlines():
                for i in range(MAX_AGENTS):
                    port = BASE_SERVER_PORT + i * PORT_STRIDE
                    if f":{port}" in line:
                        listening_ports.add(port)
        except Exception:
            pass

        agents = []
        for i in range(MAX_AGENTS):
            sandbox = os.path.join("/tmp", f"kaetram_agent_{i}")
            if not os.path.isdir(sandbox):
                continue
            # Only show agents that were launched by orchestrator (have metadata.json)
            if not os.path.isfile(os.path.join(sandbox, "metadata.json")):
                continue
            state_dir = os.path.join(sandbox, "state")
            agent = {"id": i, "username": f"Agent{i}", "server_port": BASE_SERVER_PORT + i * PORT_STRIDE}

            metadata_file = os.path.join(sandbox, "metadata.json")
            default_models = {
                "claude": "sonnet",
                "codex": "gpt-5.4",
                "kimi": "kimi-k2",
                "qwen-code": "qwen3-coder",
            }
            if os.path.isfile(metadata_file):
                try:
                    with open(metadata_file) as mf:
                        meta = json.load(mf)
                    agent["mode"] = meta.get("personality", meta.get("mode", "efficient"))
                    agent["harness"] = meta.get("harness", "claude")
                    agent["harness_model"] = meta.get("model") or default_models.get(agent["harness"], "")
                    if meta.get("username"):
                        agent["username"] = meta["username"]
                except Exception:
                    agent["mode"] = "efficient"
                    agent["harness"] = "claude"
                    agent["harness_model"] = default_models.get("claude", "")
            else:
                agent["mode"] = "efficient"
                agent["harness"] = "claude"
                agent["harness_model"] = default_models.get("claude", "")

            if agent["mode"] == "qwen":
                continue

            for ss_name in ("live_screen.png", "screenshot.png"):
                ss = os.path.join(state_dir, ss_name)
                if os.path.isfile(ss):
                    agent["screenshot_age"] = int(time.time() - os.path.getmtime(ss))
                    break

            # Use ss port check instead of raw TCP probe (avoids TIME-WAIT flood on game servers)
            agent["server_healthy"] = agent["server_port"] in listening_ports

            log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{i}", "logs")
            agent["log_dir"] = log_dir
            if os.path.isdir(log_dir):
                logs = glob.glob(os.path.join(log_dir, "session_*.log"))
                agent["session_count"] = len(logs)
                if logs:
                    latest = max(logs, key=os.path.getmtime)
                    agent["last_active"] = int(time.time() - os.path.getmtime(latest))
                    live = live_session_stats(latest)
                    agent["latest_cost"] = live["cost_usd"]
                    agent["latest_model"] = live["model"]
                    agent["turns"] = live["turns"]
                    agent["context_tokens"] = live["context_tokens"]
                    agent["output_tokens"] = live["output_tokens"]
                    # Try DB first, fall back to log parsing
                    db_state = extract_game_state_from_db(agent["username"].lower())
                    if db_state:
                        db_state.pop("_source", None)
                        agent["game_state"] = db_state
                    else:
                        extracted = extract_game_state_from_log({"agent": [str(i)]})
                        if extracted:
                            extracted.pop("_freshness", None)
                            agent["game_state"] = extracted
            else:
                agent["session_count"] = 0

            agents.append(agent)

        _agents_cache["data"] = agents
        _agents_cache["time"] = now
        self._send_json(agents)

    # ── Qwen agent log endpoint ──

    def send_qwen_log(self):
        """Return latest log entries + state from the Qwen agent sandbox."""
        import glob as _glob
        sandbox = "/tmp/kaetram_agent_4"
        state_dir = os.path.join(sandbox, "state")
        log_dir = os.path.join(sandbox, "logs")

        result = {"entries": [], "screenshot_age": 9999, "game_state": {}, "memory": {}}

        # Screenshot age
        ss = os.path.join(state_dir, "live_screen.png")
        if os.path.isfile(ss):
            result["screenshot_age"] = time.time() - os.path.getmtime(ss)

        # Game state
        gs_path = os.path.join(state_dir, "game_state.json")
        if os.path.isfile(gs_path):
            try:
                result["game_state"] = json.load(open(gs_path))
            except Exception:
                pass

        # Parse latest log file
        if os.path.isdir(log_dir):
            logs = sorted(_glob.glob(os.path.join(log_dir, "*.log")), key=os.path.getmtime)
            if logs:
                entries = []
                try:
                    for line in open(logs[-1]):
                        try:
                            e = json.loads(line)
                            entries.append(e)
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    pass
                result["entries"] = entries[-100:]

        self._send_json(result)

    # ── Activity feed (multi-agent aware) ──

    def send_activity(self, qs=None):
        agent_id = qs.get("agent", [None])[0] if qs else None
        if agent_id is not None:
            log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{agent_id}", "logs")
        else:
            log_dir = LOG_DIR

        logs = sorted(glob.glob(os.path.join(log_dir, "session_*.log")), key=os.path.getmtime)
        if not logs:
            return self._send_json({"events": [], "turn": 0, "cost_usd": 0})

        latest = logs[-1]
        parsed = parse_session_log(latest)
        parsed["log_file"] = os.path.basename(latest)
        self._send_json(parsed)

    # ── Sessions list (multi-agent aware) ──

    def send_sessions(self, qs=None):
        source = qs.get("source", ["single"])[0] if qs else "single"
        agent_filter = qs.get("agent", [None])[0] if qs else None

        entries = []
        if source == "multi" or source == "all":
            raw_dir = os.path.join(DATASET_DIR, "raw")
            if os.path.isdir(raw_dir):
                if agent_filter is not None:
                    dirs = [os.path.join(raw_dir, f"agent_{agent_filter}", "logs")]
                else:
                    dirs = sorted(glob.glob(os.path.join(raw_dir, "agent_*", "logs")))
                for d in dirs:
                    if not os.path.isdir(d):
                        continue
                    agent_name = os.path.basename(os.path.dirname(d))
                    for log in sorted(glob.glob(os.path.join(d, "*.log")), key=os.path.getmtime, reverse=True)[:20]:
                        name = os.path.basename(log)
                        size = os.path.getsize(log)
                        mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
                        summary = quick_session_summary(log)
                        entries.append({
                            "name": name, "time": mtime, "size": size,
                            "agent": agent_name, "log_dir": d,
                            **summary,
                        })

        if source == "single" or source == "all":
            for log in sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True)[:50]:
                name = os.path.basename(log)
                size = os.path.getsize(log)
                mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
                summary = quick_session_summary(log)
                entries.append({
                    "name": name, "time": mtime, "size": size,
                    "agent": "single", "log_dir": LOG_DIR,
                    **summary,
                })

        entries.sort(key=lambda e: e["time"], reverse=True)
        self._send_json(entries[:50])
