#!/usr/bin/env python3
"""Live dashboard for Kaetram AI Agent — serves on port 8080.

Supports both single-agent (play.sh) and multi-agent (orchestrate.py) modes.
"""

import http.server
import json
import os
import glob
import mimetypes
import socket
import threading
import subprocess
import re
import time
import urllib.parse
from datetime import datetime

# Patterns to redact from public-facing output
SENSITIVE_PATTERNS = re.compile(
    r'(GEMINI_API_KEY|API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS|Authorization|Bearer\s+\S+)'
    r'|([A-Za-z0-9_-]{30,}(?=[\s"\']))',  # long token-like strings
    re.IGNORECASE
)

def sanitize(text):
    """Remove API keys and sensitive strings from text before serving."""
    return SENSITIVE_PATTERNS.sub('[REDACTED]', text)

PROJECT_DIR = os.path.expanduser("~/projects/kaetram-agent")
STATE_DIR = os.path.join(PROJECT_DIR, "state")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
DATASET_DIR = os.path.join(PROJECT_DIR, "dataset")

# Multi-agent constants (must match orchestrate.py)
BASE_SERVER_PORT = 9001
PORT_STRIDE = 10
MAX_AGENTS = 8


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/" or path == "/index.html":
                self.send_dashboard()
            elif path == "/api/state":
                self.send_json_state(qs)
            elif path == "/api/sessions":
                self.send_sessions(qs)
            elif path == "/api/screenshots":
                self.send_screenshot_list()
            elif path == "/api/live":
                self.send_live_status()
            elif path == "/api/activity":
                self.send_activity(qs)
            elif path == "/api/game-state":
                self.send_game_state(qs)
            elif path == "/api/prompt":
                self.send_prompt()
            elif path == "/api/session-log":
                self.send_session_log()
            elif path == "/api/session-detail":
                name = qs.get("name", [None])[0]
                log_dir = qs.get("log_dir", [None])[0]
                self.send_session_detail(name, log_dir)
            elif path == "/api/dataset-stats":
                self.send_dataset_stats()
            elif path == "/api/sft-stats":
                self.send_sft_stats()
            elif path == "/api/agents":
                self.send_agents()
            elif path == "/api/raw":
                which = qs.get("file", [None])[0]
                self.send_raw_file(which)
            elif path.startswith("/screenshots/"):
                self.send_screenshot_file()
            else:
                self.send_error(404)
        except Exception as e:
            try:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
            except Exception:
                pass

    # ── Shared JSONL parser (Phase 1A) ──

    def _parse_session_log(self, filepath):
        """Parse a Claude Code JSONL session log. Returns dict with events, turn, cost, tokens, model, duration."""
        events = []
        turn = 0
        cost_usd = 0
        model = ""
        tokens = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
        last_context = 0
        seen_msg_ids = set()
        duration_ms = 0
        num_turns = 0

        try:
            with open(filepath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    t = obj.get("type", "")

                    if t == "assistant":
                        msg = obj.get("message", {})
                        if not model:
                            model = msg.get("model", "")
                        msg_id = msg.get("id", "")
                        if msg_id and msg_id not in seen_msg_ids:
                            seen_msg_ids.add(msg_id)
                            usage = msg.get("usage", {})
                            tokens["output"] += usage.get("output_tokens", 0)
                            tokens["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                            tokens["cache_read"] += usage.get("cache_read_input_tokens", 0)
                            tokens["input"] += usage.get("input_tokens", 0)
                            last_context = (usage.get("input_tokens", 0)
                                + usage.get("cache_creation_input_tokens", 0)
                                + usage.get("cache_read_input_tokens", 0))
                        contents = msg.get("content", [])
                        for c in contents:
                            ct = c.get("type", "")
                            if ct == "tool_use":
                                tool = c.get("name", "unknown")
                                tool_display = tool.replace("mcp__playwright__", "pw:")
                                inp = c.get("input", {})
                                summary = ""
                                detail = ""
                                if "code" in inp:
                                    detail = inp["code"][:500]
                                    code = inp["code"][:120]
                                    summary = code.split("return ")[1].split("'")[1] if "return '" in code else code[:80]
                                elif "command" in inp:
                                    summary = inp["command"][:80]
                                    detail = inp["command"]
                                elif "url" in inp:
                                    summary = inp["url"][:80]
                                    detail = inp["url"]
                                elif "file_path" in inp:
                                    summary = inp["file_path"].split("/")[-1]
                                    detail = inp["file_path"]
                                elif "query" in inp:
                                    summary = inp["query"][:80]
                                    detail = inp.get("query", "")
                                elif "path" in inp:
                                    summary = str(inp["path"])[:80]
                                elif "pattern" in inp:
                                    summary = inp["pattern"][:80]
                                    detail = json.dumps(inp, indent=2)[:500]
                                elif "text" in inp:
                                    summary = inp["text"][:80]
                                elif inp:
                                    parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
                                    summary = " ".join(parts)
                                turn += 1
                                events.append({
                                    "turn": turn, "type": "tool",
                                    "tool": tool_display,
                                    "tool_full": tool,
                                    "summary": sanitize(summary),
                                    "detail": sanitize(detail),
                                    "id": c.get("id", ""),
                                })
                            elif ct == "text":
                                text = c.get("text", "")
                                if text.strip():
                                    events.append({"turn": turn, "type": "text", "text": sanitize(text)})
                            elif ct == "thinking":
                                thinking = c.get("thinking", "")
                                if thinking.strip():
                                    events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

                    elif t == "result":
                        cost_usd = obj.get("total_cost_usd", 0)
                        duration_ms = obj.get("duration_ms", 0)
                        num_turns = obj.get("num_turns", 0)

        except Exception:
            pass

        return {
            "events": events,
            "turn": turn,
            "cost_usd": round(cost_usd, 4),
            "model": model,
            "tokens": {
                "input": tokens["input"],
                "output": tokens["output"],
                "cache_create": tokens["cache_create"],
                "cache_read": tokens["cache_read"],
                "context": last_context,
                "total": last_context + tokens["output"],
            },
            "duration_ms": duration_ms,
            "num_turns": num_turns,
        }

    def _quick_session_summary(self, filepath):
        """Read cost/turns/model from the result event at end of session log (fast — reads last 10KB only)."""
        cost = 0
        turns = 0
        model = ""
        duration_ms = 0
        try:
            with open(filepath) as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 10240))
                for line in fh:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "result":
                            cost = obj.get("total_cost_usd", 0)
                            turns = obj.get("num_turns", 0)
                            duration_ms = obj.get("duration_ms", 0)
                            for m in (obj.get("modelUsage") or {}):
                                model = m
                                break
                        elif obj.get("type") == "assistant" and not model:
                            model = obj.get("message", {}).get("model", "")
                    except Exception:
                        pass
        except Exception:
            pass
        return {"cost_usd": round(cost, 4), "turns": turns, "model": model, "duration_ms": duration_ms}

    def _live_session_stats(self, filepath):
        """Read turn count + latest context tokens from a running session log.

        Scans the last ~100KB for tool_use events (turn count) and the most recent
        assistant usage block (context window size). Works on in-progress sessions
        that don't yet have a 'result' event.
        """
        turns = 0
        context_tokens = 0
        output_tokens_total = 0
        model = ""
        seen_msg_ids = set()
        try:
            with open(filepath) as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 102400))
                for line in fh:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    t = obj.get("type", "")
                    if t == "assistant":
                        msg = obj.get("message", {})
                        if not model:
                            model = msg.get("model", "")
                        msg_id = msg.get("id", "")
                        if msg_id and msg_id not in seen_msg_ids:
                            seen_msg_ids.add(msg_id)
                            usage = msg.get("usage", {})
                            inp = usage.get("input_tokens", 0)
                            cache_create = usage.get("cache_creation_input_tokens", 0)
                            cache_read = usage.get("cache_read_input_tokens", 0)
                            out = usage.get("output_tokens", 0)
                            ctx = inp + cache_create + cache_read
                            if ctx > 0:
                                context_tokens = ctx
                            output_tokens_total += out
                        for c in msg.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                turns += 1
                    elif t == "result":
                        # Completed session — use its data
                        turns = obj.get("num_turns", turns)
        except Exception:
            pass
        return {
            "turns": turns,
            "context_tokens": context_tokens,
            "output_tokens": output_tokens_total,
            "model": model,
        }

    # ── Screenshot serving ──

    def send_screenshot_file(self):
        raw = self.path.split("?")[0]
        parts = raw.split("/")

        # Per-agent screenshots: /screenshots/agent_N/filename.png
        if len(parts) >= 4 and parts[2].startswith("agent_"):
            idx = parts[2].replace("agent_", "")
            filename = os.path.basename(parts[3])
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return self.send_error(403)
            filepath = os.path.join("/tmp", f"kaetram_agent_{idx}", "state", filename)
            if filename == "live_screen.png" and not os.path.isfile(filepath):
                filepath = os.path.join("/tmp", f"kaetram_agent_{idx}", "state", "screenshot.png")
        else:
            filename = os.path.basename(raw)
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return self.send_error(403)
            filepath = os.path.join(STATE_DIR, filename)
            if filename == "live_screen.png" and not os.path.isfile(filepath):
                filepath = os.path.join(STATE_DIR, "screenshot.png")

        if not os.path.isfile(filepath):
            return self.send_error(404)
        mime, _ = mimetypes.guess_type(filepath)
        size = os.path.getsize(filepath)
        mtime = os.path.getmtime(filepath)
        last_modified = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(mtime))
        self.send_response(200)
        self.send_header("Content-Type", mime or "image/png")
        self.send_header("Content-Length", str(size))
        self.send_header("Last-Modified", last_modified)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())

    def send_screenshot_list(self):
        images = []
        for ext in ('*.png',):
            images.extend(glob.glob(os.path.join(STATE_DIR, ext)))
        images.sort(key=os.path.getmtime, reverse=True)
        result = []
        for img in images[:50]:
            name = os.path.basename(img)
            mtime = datetime.fromtimestamp(os.path.getmtime(img)).strftime("%Y-%m-%d %H:%M:%S")
            result.append({"name": name, "time": mtime, "size": os.path.getsize(img)})
        self._send_json(result)

    # ── State endpoints ──

    def send_json_state(self, qs=None):
        state_dir = self._resolve_state_dir(qs)
        state_file = os.path.join(state_dir, "progress.json")
        data = {}
        if os.path.isfile(state_file):
            try:
                with open(state_file) as fh:
                    data = json.load(fh)
            except Exception:
                pass
        self._send_json(data)

    def send_game_state(self, qs=None):
        state_dir = self._resolve_state_dir(qs)
        gs_file = os.path.join(state_dir, "game_state.json")
        data = {}
        freshness = -1
        if os.path.isfile(gs_file):
            try:
                with open(gs_file) as fh:
                    data = json.load(fh)
                mtime = os.path.getmtime(gs_file)
                freshness = round(time.time() - mtime, 1)
            except Exception:
                pass
        data["freshness_seconds"] = freshness
        self._send_json(data)

    def _resolve_state_dir(self, qs):
        """Return state directory — either default or per-agent sandbox."""
        if qs:
            agent_id = qs.get("agent", [None])[0]
            if agent_id is not None:
                sandbox = os.path.join("/tmp", f"kaetram_agent_{agent_id}", "state")
                if os.path.isdir(sandbox):
                    return sandbox
        return STATE_DIR

    def send_prompt(self):
        prompt_file = os.path.join(PROJECT_DIR, "prompts", "system.md")
        text = ""
        if os.path.isfile(prompt_file):
            try:
                with open(prompt_file) as fh:
                    text = fh.read()
            except Exception:
                text = "(error reading file)"
        self._send_json({"content": sanitize(text), "file": "prompts/system.md"})

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
        # Support multi-agent log dirs
        if log_dir:
            safe_dir = os.path.basename(os.path.dirname(log_dir)) if log_dir.endswith("/") else os.path.basename(log_dir)
            filepath = os.path.join(log_dir, safe)
        else:
            filepath = os.path.join(LOG_DIR, safe)
        if not os.path.isfile(filepath):
            return self._send_json({"error": "not found"})

        parsed = self._parse_session_log(filepath)
        parsed["name"] = safe
        self._send_json(parsed)

    # ── Dataset stats ──

    def send_dataset_stats(self):
        stats = {"sessions": [], "total_steps": 0, "total_reward": 0, "rewards": [], "actions": {},
                 "raw_sessions": 0, "raw_total_size": 0}
        if os.path.isdir(DATASET_DIR):
            # logger.py dataset sessions
            for sd in sorted(glob.glob(os.path.join(DATASET_DIR, "session_*"))):
                sname = os.path.basename(sd)
                steps_file = os.path.join(sd, "steps.jsonl")
                if not os.path.isfile(steps_file):
                    continue
                step_count = 0
                total_reward = 0
                last_reward = 0
                session_rewards = []
                try:
                    with open(steps_file) as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            rec = json.loads(line)
                            step_count += 1
                            r = rec.get("reward", 0)
                            total_reward += r
                            last_reward = r
                            session_rewards.append(round(r, 3))
                            action = rec.get("action")
                            if isinstance(action, dict):
                                tool = action.get("tool", "unknown").split("__")[-1]
                            elif isinstance(action, str):
                                tool = action[:30]
                            else:
                                tool = "unknown"
                            stats["actions"][tool] = stats["actions"].get(tool, 0) + 1
                except Exception:
                    pass
                stats["sessions"].append({
                    "name": sname, "steps": step_count,
                    "total_reward": round(total_reward, 3),
                    "last_reward": round(last_reward, 3),
                })
                stats["rewards"].extend(session_rewards)
                stats["total_steps"] += step_count
                stats["total_reward"] += total_reward

            # Multi-agent raw session logs
            raw_dir = os.path.join(DATASET_DIR, "raw")
            if os.path.isdir(raw_dir):
                raw_logs = glob.glob(os.path.join(raw_dir, "agent_*", "logs", "session_*.log"))
                stats["raw_sessions"] = len(raw_logs)
                stats["raw_total_size"] = sum(os.path.getsize(f) for f in raw_logs)

        stats["total_reward"] = round(stats["total_reward"], 3)
        stats["rewards"] = stats["rewards"][-200:]
        self._send_json(stats)

    def send_sft_stats(self):
        """SFT pipeline output stats: extracted turns + Qwen SFT records."""
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

    def send_raw_file(self, which):
        allowed = {
            "progress": os.path.join(STATE_DIR, "progress.json"),
            "game_state": os.path.join(STATE_DIR, "game_state.json"),
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

    # ── Live status (Phase 2A: multi-agent aware) ──

    def send_live_status(self):
        # Detect mode
        mode = "none"
        agent_count = 0
        try:
            result = subprocess.run(["pgrep", "-f", "orchestrate.py"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                mode = "multi"
                try:
                    r2 = subprocess.run(["pgrep", "-cf", "claude -p"], capture_output=True, text=True, timeout=3)
                    agent_count = int(r2.stdout.strip()) if r2.returncode == 0 else 0
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

        # Game state freshness (single-agent state dir)
        gs_file = os.path.join(STATE_DIR, "game_state.json")
        gs_fresh = False
        game_state_age = -1
        if os.path.isfile(gs_file):
            game_state_age = int(time.time() - os.path.getmtime(gs_file))
            gs_fresh = game_state_age < 30

        # Screenshot age
        screenshot = os.path.join(STATE_DIR, "live_screen.png")
        if not os.path.isfile(screenshot):
            screenshot = os.path.join(STATE_DIR, "screenshot.png")
        screenshot_age = -1
        screenshot_time = ""
        if os.path.isfile(screenshot):
            mtime = os.path.getmtime(screenshot)
            screenshot_age = int(datetime.now().timestamp() - mtime)
            screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

        # Check all relevant server ports in one ss call
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
                game_server_up = True  # multi-agent servers are up even without 9000
        except Exception:
            pass

        # Session counts (single-agent + multi-agent)
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

    # ── Multi-agent endpoint (Phase 2B) ──

    def send_agents(self):
        agents = []
        for i in range(MAX_AGENTS):
            sandbox = os.path.join("/tmp", f"kaetram_agent_{i}")
            if not os.path.isdir(sandbox):
                continue
            state_dir = os.path.join(sandbox, "state")
            agent = {"id": i, "username": f"ClaudeBot{i}", "server_port": BASE_SERVER_PORT + i * PORT_STRIDE}

            # Read progress.json
            progress_file = os.path.join(state_dir, "progress.json")
            if os.path.isfile(progress_file):
                try:
                    with open(progress_file) as fh:
                        agent["progress"] = json.load(fh)
                except Exception:
                    agent["progress"] = {}

            # Read game_state.json
            gs_file = os.path.join(state_dir, "game_state.json")
            if os.path.isfile(gs_file):
                try:
                    gs = json.load(open(gs_file))
                    agent["game_state"] = {
                        "player_stats": gs.get("player_stats"),
                        "player_position": gs.get("player_position"),
                        "current_target": gs.get("current_target"),
                        "nearest_mob": gs.get("nearest_mob"),
                        "entity_count": len(gs.get("nearby_entities", [])),
                    }
                    agent["gs_age"] = int(time.time() - os.path.getmtime(gs_file))
                except Exception:
                    pass

            # Screenshot age
            for ss_name in ("live_screen.png", "screenshot.png"):
                ss = os.path.join(state_dir, ss_name)
                if os.path.isfile(ss):
                    agent["screenshot_age"] = int(time.time() - os.path.getmtime(ss))
                    break

            # Server health
            port = agent["server_port"]
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    agent["server_healthy"] = True
            except Exception:
                agent["server_healthy"] = False

            # Count sessions from persistent log directory
            log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{i}", "logs")
            agent["log_dir"] = log_dir
            if os.path.isdir(log_dir):
                logs = glob.glob(os.path.join(log_dir, "session_*.log"))
                agent["session_count"] = len(logs)
                if logs:
                    latest = max(logs, key=os.path.getmtime)
                    summary = self._quick_session_summary(latest)
                    agent["latest_cost"] = summary["cost_usd"]
                    agent["latest_model"] = summary["model"]
                    # Live stats: turns + context tokens
                    live = self._live_session_stats(latest)
                    agent["turns"] = live["turns"]
                    agent["context_tokens"] = live["context_tokens"]
                    agent["output_tokens"] = live["output_tokens"]
            else:
                agent["session_count"] = 0

            agents.append(agent)

        self._send_json(agents)

    # ── Activity feed (Phase 2D: multi-agent aware) ──

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
        parsed = self._parse_session_log(latest)
        parsed["log_file"] = os.path.basename(latest)
        self._send_json(parsed)

    # ── Sessions list (Phase 2E: multi-agent aware) ──

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
                        summary = self._quick_session_summary(log)
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
                summary = self._quick_session_summary(log)
                entries.append({
                    "name": name, "time": mtime, "size": size,
                    "agent": "single", "log_dir": LOG_DIR,
                    **summary,
                })

        # Sort all entries by time descending
        entries.sort(key=lambda e: e["time"], reverse=True)
        self._send_json(entries[:50])

    # ── JSON response helper ──

    def _send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_dashboard(self):
        host = self.headers.get('Host', 'localhost:8080')
        game_host = host.split(':')[0]
        html = DASHBOARD_HTML.replace("__GAME_HOST__", game_host)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(http.server.HTTPServer):
    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address))
        thread.daemon = True
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<title>Kaetram AI Agent — Live Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg: #0a0a0a; --card: #111; --border: #222; --green: #00ff41; --amber: #ffaa00; --red: #ff4141; --blue: #00aaff; --purple: #c084fc; --dim: #555; --text: #ccc; --card-hover: #161616; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace; background: var(--bg); color: var(--text); font-size: 13px; }

  /* Header */
  header { background: #0d0d0d; border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
  header h1 { color: var(--green); font-size: 18px; letter-spacing: 1px; }
  header h1 span { font-weight: normal; color: var(--dim); font-size: 12px; }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .header-links a { color: var(--blue); text-decoration: none; border: 1px solid #333; padding: 3px 10px; border-radius: 4px; font-size: 11px; transition: all 0.2s; }
  .header-links a:hover { border-color: var(--green); color: var(--green); }

  /* Status bar */
  .status-bar { background: #0d0d0d; border-bottom: 1px solid var(--border); padding: 6px 24px; display: flex; gap: 20px; font-size: 11px; flex-wrap: wrap; align-items: center; }
  .status-item { display: flex; align-items: center; gap: 5px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
  .dot.green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.red { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.amber { background: var(--amber); box-shadow: 0 0 6px var(--amber); }

  /* Tabs */
  .tabs { background: #0d0d0d; border-bottom: 1px solid var(--border); padding: 0 24px; display: flex; gap: 0; }
  .tab { padding: 10px 20px; font-size: 12px; color: var(--dim); cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.5px; }
  .tab:hover { color: var(--text); background: #151515; }
  .tab.active { color: var(--green); border-bottom-color: var(--green); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Main layout */
  main { max-width: 1600px; margin: 0 auto; padding: 16px; }

  /* Grid */
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; margin-bottom: 14px; }
  @media (max-width: 1000px) { .grid-3 { grid-template-columns: 1fr; } }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }

  /* Cards */
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
  .card h2 { color: var(--amber); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
  .card h2 .badge { font-size: 10px; background: #1a1a1a; padding: 2px 8px; border-radius: 10px; color: var(--green); font-weight: normal; }
  .card.full { grid-column: 1 / -1; }
  .card.clickable { cursor: pointer; transition: border-color 0.2s; }
  .card.clickable:hover { border-color: #444; }

  .stat { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #1a1a1a; font-size: 12px; }
  .stat:last-child { border-bottom: none; }
  .stat-label { color: var(--dim); }
  .stat-value { color: var(--green); font-weight: bold; }

  /* Hero screenshot */
  .hero { margin-bottom: 14px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .hero img { width: 100%; max-height: 450px; object-fit: contain; background: #000; display: block; cursor: pointer; }
  .hero .caption { padding: 8px 14px; font-size: 11px; color: var(--dim); display: flex; justify-content: space-between; }

  /* Screenshot gallery */
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
  .thumb { background: #0d0d0d; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; transition: border-color 0.2s; cursor: pointer; }
  .thumb:hover { border-color: var(--green); }
  .thumb img { width: 100%; display: block; }
  .thumb .meta { padding: 4px 8px; font-size: 9px; color: var(--dim); }

  /* Activity feed */
  .activity-event { padding: 4px 10px; margin-bottom: 1px; font-size: 11px; border-left: 2px solid #333; cursor: pointer; transition: background 0.1s; }
  .activity-event:hover { background: #1a1a1a; }
  .activity-event .turn-num { color: var(--dim); margin-right: 6px; min-width: 30px; display: inline-block; }
  .activity-event .tool-name { color: var(--blue); font-weight: bold; }
  .activity-event .summary { color: var(--text); margin-left: 6px; }
  .activity-event.text-event { border-left-color: var(--green); }
  .activity-event.text-event .agent-text { color: var(--green); }
  .activity-event.thinking-event { border-left-color: var(--purple); }
  .activity-event.thinking-event .think-text { color: var(--purple); font-style: italic; }
  .activity-feed { max-height: 500px; overflow-y: auto; }
  .event-detail { display: none; padding: 6px 10px 6px 40px; background: #0d0d0d; font-size: 10px; color: var(--dim); white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; border-left: 2px solid #222; }
  .event-detail.open { display: block; }

  /* Entity table */
  .entity-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .entity-table th { text-align: left; color: var(--dim); font-weight: normal; padding: 4px 6px; border-bottom: 1px solid #222; font-size: 10px; text-transform: uppercase; cursor: pointer; }
  .entity-table th:hover { color: var(--text); }
  .entity-table td { padding: 3px 6px; border-bottom: 1px solid #1a1a1a; }
  .entity-table tr:hover { background: #1a1a1a; }
  .entity-type { font-size: 9px; padding: 1px 5px; border-radius: 3px; }
  .entity-type.mob { background: #3a1a1a; color: var(--red); }
  .entity-type.player { background: #1a2a3a; color: var(--blue); }
  .entity-type.npc { background: #2a2a1a; color: var(--amber); }
  .entity-type.item { background: #1a3a1a; color: var(--green); }
  .hp-bar-bg { width: 50px; height: 6px; background: #222; border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; }
  .hp-bar-fill { height: 100%; border-radius: 3px; }
  .hp-bar-fill.high { background: var(--green); }
  .hp-bar-fill.mid { background: var(--amber); }
  .hp-bar-fill.low { background: var(--red); }

  /* Combat/XP */
  .combat-entry { font-size: 11px; padding: 6px 8px; background: #1a1111; border-left: 3px solid var(--red); border-radius: 4px; margin-bottom: 6px; }
  .combat-entry .label { color: var(--dim); font-size: 9px; text-transform: uppercase; }
  .xp-entry { font-size: 11px; padding: 6px 8px; background: #111a11; border-left: 3px solid var(--green); border-radius: 4px; }
  .xp-entry .label { color: var(--dim); font-size: 9px; text-transform: uppercase; }

  /* Session list */
  .session-entry { padding: 6px 10px; border-bottom: 1px solid #1a1a1a; font-size: 11px; display: flex; justify-content: space-between; cursor: pointer; transition: background 0.1s; align-items: center; gap: 8px; }
  .session-entry:hover { background: #1a1a1a; }
  .session-entry .name { color: var(--text); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .session-entry .time { color: var(--dim); font-size: 10px; }
  .session-entry .size { color: var(--amber); min-width: 50px; text-align: right; font-size: 10px; }
  .session-entry .arrow { color: var(--dim); }
  .session-entry .agent-badge { font-size: 8px; padding: 1px 5px; border-radius: 3px; background: #1a2a3a; color: var(--blue); white-space: nowrap; }
  .session-entry .cost-badge { color: var(--amber); font-size: 10px; min-width: 45px; text-align: right; }
  .session-entry .turns-badge { color: var(--purple); font-size: 10px; min-width: 30px; text-align: right; }

  /* Code/text blocks */
  .code-block { background: #0d0d0d; border: 1px solid var(--border); border-radius: 6px; padding: 14px; font-size: 11px; line-height: 1.6; overflow-x: auto; white-space: pre-wrap; word-break: break-word; max-height: 600px; overflow-y: auto; color: var(--text); }
  .md-block { background: #0d0d0d; border: 1px solid var(--border); border-radius: 6px; padding: 14px; font-size: 11px; line-height: 1.7; overflow-y: auto; max-height: 600px; }
  .md-block h1, .md-block h2, .md-block h3 { color: var(--amber); margin: 12px 0 6px 0; }
  .md-block h1 { font-size: 16px; } .md-block h2 { font-size: 14px; } .md-block h3 { font-size: 12px; }
  .md-block code { background: #1a1a1a; padding: 1px 4px; border-radius: 3px; color: var(--green); font-size: 11px; }
  .md-block pre { background: #0a0a0a; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 8px 0; }
  .md-block pre code { background: none; padding: 0; }
  .md-block ul, .md-block ol { padding-left: 20px; margin: 6px 0; }
  .md-block li { margin: 3px 0; }
  .md-block hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
  .md-block strong { color: var(--text); }
  .md-block table { border-collapse: collapse; margin: 8px 0; }
  .md-block th, .md-block td { border: 1px solid var(--border); padding: 4px 8px; font-size: 11px; }
  .md-block th { background: #1a1a1a; color: var(--amber); }

  /* Modal / Lightbox */
  .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.95); z-index: 100; justify-content: center; align-items: center; cursor: pointer; }
  .modal.active { display: flex; }
  .modal img { max-width: 95vw; max-height: 95vh; }

  /* Detail drawer */
  .drawer { display: none; position: fixed; top: 0; right: 0; width: 55%; max-width: 800px; height: 100%; background: var(--bg); border-left: 1px solid var(--border); z-index: 90; overflow-y: auto; padding: 20px; }
  .drawer.open { display: block; }
  .drawer-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 89; }
  .drawer-overlay.open { display: block; }
  .drawer-close { position: sticky; top: 0; background: var(--bg); padding: 8px 0; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
  .drawer-close button { background: none; border: 1px solid var(--border); color: var(--text); padding: 4px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; }
  .drawer-close button:hover { border-color: var(--red); color: var(--red); }
  .drawer-title { color: var(--amber); font-size: 14px; }

  .empty { color: #333; font-style: italic; padding: 20px; text-align: center; }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
  .badge-count { background: var(--green); color: #000; font-size: 9px; padding: 1px 6px; border-radius: 8px; font-weight: bold; margin-left: 6px; }

  /* Player vital bars */
  .vital-bar { margin: 6px 0; }
  .vital-bar .vital-label { display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 2px; }
  .vital-bar .vital-label .vl-name { color: var(--dim); text-transform: uppercase; }
  .vital-bar .vital-label .vl-val { font-weight: bold; }
  .vital-track { width: 100%; height: 10px; background: #1a1a1a; border-radius: 5px; overflow: hidden; }
  .vital-fill { height: 100%; border-radius: 5px; transition: width 0.5s ease; }
  .vital-fill.hp.low { background: var(--red); }
  .vital-fill.hp.mid { background: var(--amber); }
  .vital-fill.hp.high { background: var(--green); }
  .vital-fill.mana { background: linear-gradient(90deg, #1a6bff, #00aaff); }
  .vital-fill.xp { background: linear-gradient(90deg, #c084fc, #e0aaff); }

  /* Current target */
  .target-box { margin-top: 8px; padding: 6px 8px; background: #1a1111; border: 1px solid #2a1a1a; border-left: 3px solid var(--red); border-radius: 4px; font-size: 11px; }
  .target-box .tgt-name { color: var(--red); font-weight: bold; }
  .target-box .tgt-info { color: var(--dim); font-size: 10px; }

  /* Quest/achievement rows */
  .quest-row { padding: 5px 0; border-bottom: 1px solid #1a1a1a; font-size: 11px; }
  .quest-row:last-child { border-bottom: none; }
  .quest-header { display: flex; justify-content: space-between; align-items: center; }
  .quest-name { color: var(--text); }
  .quest-status { font-size: 9px; padding: 1px 6px; border-radius: 3px; }
  .quest-status.active { background: #1a2a1a; color: var(--green); }
  .quest-status.done { background: #1a1a2a; color: var(--blue); }
  .quest-status.not-started { background: #1a1a1a; color: var(--dim); }
  .quest-desc { color: var(--dim); font-size: 10px; margin-top: 2px; }
  .quest-progress { margin-top: 3px; }
  .quest-progress-track { width: 100%; height: 4px; background: #1a1a1a; border-radius: 2px; overflow: hidden; }
  .quest-progress-fill { height: 100%; background: var(--green); border-radius: 2px; }
  .section-label { color: var(--amber); font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; margin: 8px 0 4px 0; padding-bottom: 3px; border-bottom: 1px solid #1a1a1a; }
  .section-label:first-child { margin-top: 0; }

  /* Inventory grid */
  .inv-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
  .inv-slot { background: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 4px; padding: 4px; text-align: center; font-size: 9px; min-height: 36px; display: flex; flex-direction: column; justify-content: center; transition: border-color 0.2s; }
  .inv-slot:hover { border-color: #333; }
  .inv-slot .inv-name { color: var(--text); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .inv-slot .inv-count { color: var(--amber); font-size: 9px; }
  .inv-slot .inv-tags { margin-top: 1px; }
  .inv-slot .inv-tag { font-size: 7px; padding: 0 3px; border-radius: 2px; display: inline-block; }
  .inv-tag.edible { background: #1a2a1a; color: var(--green); }
  .inv-tag.equip { background: #1a1a2a; color: var(--blue); }

  /* Reward sparkline */
  .sparkline-container { margin-top: 8px; }
  .sparkline-canvas { width: 100%; height: 60px; background: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 4px; }

  /* Grid 4 */
  .grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 14px; margin-bottom: 14px; }
  @media (max-width: 1200px) { .grid-4 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 800px) { .grid-4 { grid-template-columns: 1fr; } }

  /* Next objective */
  .objective-box { margin-top: 8px; padding: 6px 8px; background: #111a11; border: 1px solid #1a2a1a; border-left: 3px solid var(--green); border-radius: 4px; font-size: 10px; color: var(--green); }

  /* Agent grid (multi-agent) */
  .agent-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; margin-bottom: 14px; }
  .agent-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px; cursor: pointer; transition: border-color 0.2s; }
  .agent-card:hover { border-color: #444; }
  .agent-card.selected { border-color: var(--green); box-shadow: 0 0 8px rgba(0,255,65,0.15); }
  .agent-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .agent-card-name { font-weight: bold; font-size: 12px; }
  .agent-card-thumb { width: 100%; height: 120px; object-fit: contain; background: #000; border-radius: 4px; margin-top: 6px; }

  /* Agent activity selector */
  .agent-selector { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
  .agent-selector button { background: #1a1a1a; border: 1px solid var(--border); color: var(--text); padding: 4px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; transition: all 0.2s; }
  .agent-selector button:hover { border-color: #444; }
  .agent-selector button.active { border-color: var(--green); color: var(--green); }
</style>
</head>
<body>

<header>
  <h1>KAETRAM AI AGENT <span>// live observability</span></h1>
  <div class="header-right">
    <div class="header-links">
      <a href="http://__GAME_HOST__:9000" target="_blank">Play Game</a>
      <a href="/api/live">API</a>
    </div>
  </div>
</header>

<div class="status-bar">
  <div class="status-item"><span class="dot amber" id="dot-agent"></span> Agent: <span id="status-agent">...</span></div>
  <div class="status-item"><span class="dot amber" id="dot-server"></span> Server: <span id="status-server">...</span></div>
  <div class="status-item">State: <span id="status-gs-age" style="color:var(--green)">-</span></div>
  <div class="status-item">Shot: <span id="status-screenshot-age" style="color:var(--green)">-</span></div>
  <div class="status-item">Turn: <span id="status-turn" style="color:var(--green)">-</span></div>
  <div class="status-item">Tokens: <span id="status-tokens" style="color:var(--purple)">-</span></div>
  <div class="status-item">Cost: $<span id="status-cost" style="color:var(--amber)">-</span></div>
  <div class="status-item" style="color:#333;margin-left:auto" id="refresh-indicator">2s</div>
</div>

<div class="tabs">
  <div class="tab active" data-tab="overview">Overview</div>
  <div class="tab" data-tab="activity">Activity</div>
  <div class="tab" data-tab="world">World</div>
  <div class="tab" data-tab="sessions">Sessions</div>
  <div class="tab" data-tab="prompt">Prompt</div>
  <div class="tab" data-tab="data">Raw Data</div>
</div>

<main>
  <!-- ===== OVERVIEW TAB ===== -->
  <div class="tab-content active" id="tab-overview">
    <!-- Multi-agent grid (hidden in single-agent mode) -->
    <div id="agent-grid-container" class="agent-grid" style="display:none"></div>

    <div class="hero" id="hero">
      <img id="hero-img" src="/screenshots/live_screen.png" alt="Latest game screenshot" onclick="openLightbox(this.src)">
      <div class="caption">
        <span id="hero-caption">Latest agent view</span>
        <span id="hero-time">-</span>
      </div>
    </div>
    <div class="grid-4">
      <div class="card">
        <h2>Player Status</h2>
        <div id="player-vitals"></div>
        <div id="player-stats"><div class="empty">Waiting...</div></div>
        <div id="player-target"></div>
        <div id="player-objective"></div>
      </div>
      <div class="card">
        <h2>Combat & XP</h2>
        <div id="combat-log"><div class="empty">No combat</div></div>
        <div style="margin-top:8px" id="xp-tracker"><div class="empty">No XP</div></div>
      </div>
      <div class="card">
        <h2>Mission Progress</h2>
        <div id="mission-stats"><div class="empty">Waiting...</div></div>
      </div>
      <div class="card">
        <h2>Inventory <span class="badge" id="inv-count">0</span></h2>
        <div id="inventory-panel"><div class="empty">No items</div></div>
      </div>
    </div>
    <div class="card full">
      <h2>Live Activity <span class="badge" id="activity-log-name">-</span></h2>
      <div class="activity-feed" id="activity-feed-overview"><div class="empty">Waiting for agent...</div></div>
    </div>
  </div>

  <!-- ===== ACTIVITY TAB ===== -->
  <div class="tab-content" id="tab-activity">
    <div class="card full">
      <h2>Full Activity Feed <span class="badge" id="activity-log-name-full">-</span></h2>
      <div id="activity-agent-selector" class="agent-selector" style="display:none"></div>
      <p style="font-size:10px;color:var(--dim);margin-bottom:8px">Click any event to expand details. Tool calls show input code/params.</p>
      <div class="activity-feed" id="activity-feed-full" style="max-height:none"><div class="empty">Waiting for agent...</div></div>
    </div>
  </div>

  <!-- ===== WORLD TAB ===== -->
  <div class="tab-content" id="tab-world">
    <div class="grid">
      <div class="card">
        <h2>Nearby Entities <span class="badge" id="entity-count">0</span></h2>
        <div id="entity-list" style="max-height:500px;overflow-y:auto"><div class="empty">Waiting for game state...</div></div>
      </div>
      <div class="card">
        <h2>Screenshots <span class="badge" id="shot-count">0</span></h2>
        <div class="gallery" id="gallery"><div class="empty">No screenshots</div></div>
      </div>
    </div>
  </div>

  <!-- ===== SESSIONS TAB ===== -->
  <div class="tab-content" id="tab-sessions">
    <div class="grid">
      <div class="card">
        <h2>Session History</h2>
        <div id="sessions-list"><div class="empty">No sessions</div></div>
      </div>
      <div class="card">
        <h2>Session Notes <span class="badge">session_log.md</span></h2>
        <div id="session-notes" class="md-block" style="max-height:500px"><div class="empty">Loading...</div></div>
      </div>
    </div>
    <div class="grid" style="margin-top:14px">
      <div class="card">
        <h2>Dataset Stats</h2>
        <div id="dataset-stats"><div class="empty">No dataset recorded yet</div></div>
      </div>
      <div class="card">
        <h2>SFT Pipeline</h2>
        <div id="sft-stats"><div class="empty">No SFT data yet</div></div>
      </div>
    </div>
  </div>

  <!-- ===== PROMPT TAB ===== -->
  <div class="tab-content" id="tab-prompt">
    <div class="card full">
      <h2>System Prompt <span class="badge">prompts/system.md</span></h2>
      <div id="prompt-content" class="md-block"><div class="empty">Loading...</div></div>
    </div>
  </div>

  <!-- ===== RAW DATA TAB ===== -->
  <div class="tab-content" id="tab-data">
    <div class="grid">
      <div class="card">
        <h2>progress.json <span class="badge">state/progress.json</span></h2>
        <div id="raw-progress" class="code-block"><div class="empty">Loading...</div></div>
      </div>
      <div class="card">
        <h2>game_state.json <span class="badge" id="raw-gs-freshness">-</span></h2>
        <div id="raw-game-state" class="code-block" style="max-height:500px"><div class="empty">Loading...</div></div>
      </div>
    </div>
    <div class="grid" style="margin-top:14px">
      <div class="card">
        <h2>state_extractor.js <span class="badge">browser injection</span></h2>
        <div id="raw-state-extractor" class="code-block" style="max-height:400px"><div class="empty">Loading...</div></div>
      </div>
      <div class="card">
        <h2>orchestrate.py <span class="badge">multi-agent launcher</span></h2>
        <div id="raw-orchestrate" class="code-block" style="max-height:400px"><div class="empty">Loading...</div></div>
      </div>
    </div>
    <div class="card full" style="margin-top:14px">
      <h2>CLAUDE.md <span class="badge">project config</span></h2>
      <div id="raw-claude-md" class="md-block"><div class="empty">Loading...</div></div>
    </div>
  </div>
</main>

<!-- Lightbox -->
<div class="modal" id="lightbox" onclick="this.classList.remove('active')">
  <img id="lightbox-img" src="">
</div>

<!-- Session detail drawer -->
<div class="drawer-overlay" id="drawer-overlay" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-close">
    <span class="drawer-title" id="drawer-title">Session Detail</span>
    <button onclick="closeDrawer()">Close (Esc)</button>
  </div>
  <div id="drawer-body"></div>
</div>

<script>
// === Global state ===
let currentMode = 'none';   // 'single', 'multi', 'none'
let selectedAgent = null;   // null = default (single-agent), or agent ID for multi
let agentList = [];         // cached /api/agents response

// === Tab switching ===
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'prompt' && !promptLoaded) loadPrompt();
    if (tab.dataset.tab === 'sessions') { loadSessionNotes(); loadDatasetStats(); loadSftStats(); }
    if (tab.dataset.tab === 'data' && !rawLoaded) loadRawData();
  });
});

// === Lightbox ===
function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('active');
}

// === Drawer ===
function openDrawer(title, html) {
  document.getElementById('drawer-title').textContent = title;
  document.getElementById('drawer-body').innerHTML = html;
  document.getElementById('drawer').classList.add('open');
  document.getElementById('drawer-overlay').classList.add('open');
}
function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-overlay').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); document.getElementById('lightbox').classList.remove('active'); }
});

// === Helpers ===
function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function humanTime(s) {
  if (s < 0) return 'never';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ago';
}
function sizeStr(bytes) {
  if (bytes > 1048576) return (bytes/1048576).toFixed(1) + ' MB';
  if (bytes > 1024) return Math.round(bytes/1024) + ' KB';
  return bytes + ' B';
}
function simpleMarkdown(text) {
  return text
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^---$/gm, '<hr>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}

const typeMap = {0:'player',1:'npc',2:'npc',3:'mob',4:'item',player:'player',mob:'mob',npc:'npc',item:'item'};

// === Build activity HTML ===
function buildActivityHTML(events, withDetails, prefix='ev') {
  if (!events || !events.length) return '<div class="empty">No events yet</div>';
  let html = '';
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const id = prefix + '-' + i;
    if (ev.type === 'tool') {
      html += '<div class="activity-event" onclick="toggleDetail(\'' + id + '\')">'
        + '<span class="turn-num">#' + ev.turn + '</span>'
        + '<span class="tool-name">' + esc(ev.tool) + '</span>'
        + '<span class="summary">' + esc(ev.summary) + '</span></div>';
      if (withDetails && ev.detail) {
        html += '<div class="event-detail" id="' + id + '">' + esc(ev.detail) + '</div>';
      }
    } else if (ev.type === 'text') {
      html += '<div class="activity-event text-event" onclick="toggleDetail(\'' + id + '\')">'
        + '<span class="turn-num">#' + ev.turn + '</span>'
        + '<span class="agent-text">' + esc(ev.text).substring(0, 200) + '</span></div>';
      if (withDetails) {
        html += '<div class="event-detail" id="' + id + '">' + esc(ev.text) + '</div>';
      }
    } else if (ev.type === 'thinking') {
      html += '<div class="activity-event thinking-event" onclick="toggleDetail(\'' + id + '\')">'
        + '<span class="turn-num">#' + ev.turn + '</span>'
        + '<span class="think-text">[thinking] ' + esc(ev.text).substring(0, 120) + '...</span></div>';
      if (withDetails) {
        html += '<div class="event-detail" id="' + id + '">' + esc(ev.text) + '</div>';
      }
    }
  }
  return html;
}

function toggleDetail(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}

// === HP bar helper ===
function hpBar(hp, maxHp, width) {
  if (!maxHp || maxHp <= 0) return '';
  const pct = Math.max(0, Math.min(100, Math.round(hp / maxHp * 100)));
  const cls = pct > 60 ? 'high' : pct > 25 ? 'mid' : 'low';
  const w = width || 50;
  return '<div class="hp-bar-bg" style="width:' + w + 'px"><div class="hp-bar-fill ' + cls + '" style="width:' + pct + '%"></div></div> <span style="font-size:9px">' + hp + '/' + maxHp + '</span>';
}

// === Lazy-loaded tabs ===
let promptLoaded = false;
let rawLoaded = false;
let lastGalleryKey = '';

async function loadPrompt() {
  try {
    const data = await (await fetch('/api/prompt')).json();
    document.getElementById('prompt-content').innerHTML = simpleMarkdown(esc(data.content || ''));
    promptLoaded = true;
  } catch(e) {}
}

async function loadSessionNotes() {
  try {
    const data = await (await fetch('/api/session-log')).json();
    document.getElementById('session-notes').innerHTML = simpleMarkdown(esc(data.content || ''));
  } catch(e) {}
}

async function loadDatasetStats() {
  try {
    const data = await (await fetch('/api/dataset-stats')).json();
    let html = '';
    if (data.sessions && data.sessions.length > 0) {
      html += '<div class="stat"><span class="stat-label">Total Steps</span><span class="stat-value">' + data.total_steps + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Total Reward</span><span class="stat-value">' + data.total_reward + '</span></div>';
      if (data.actions && Object.keys(data.actions).length > 0) {
        const sorted = Object.entries(data.actions).sort((a,b) => b[1] - a[1]);
        html += '<div class="section-label" style="margin-top:10px">Action Breakdown</div>';
        for (const [tool, count] of sorted) {
          const pct = Math.round(count / data.total_steps * 100);
          html += '<div class="stat"><span class="stat-label">' + esc(tool) + '</span><span class="stat-value">' + count + ' (' + pct + '%)</span></div>';
        }
      }
      html += '<div class="section-label" style="margin-top:10px">Sessions</div>';
      for (const s of data.sessions) {
        const avgR = s.steps > 0 ? (s.total_reward / s.steps).toFixed(3) : '0';
        html += '<div class="stat"><span class="stat-label">' + esc(s.name) + '</span><span class="stat-value">' + s.steps + ' steps, R=' + s.total_reward + ' (avg ' + avgR + ')</span></div>';
      }
      if (data.rewards && data.rewards.length > 1) {
        html += '<div class="section-label" style="margin-top:10px">Reward Trend</div>';
        html += '<div class="sparkline-container"><canvas class="sparkline-canvas" id="reward-sparkline"></canvas></div>';
      }
    }
    // Multi-agent raw sessions
    if (data.raw_sessions > 0) {
      html += '<div class="section-label" style="margin-top:10px">Multi-Agent Raw Logs</div>';
      html += '<div class="stat"><span class="stat-label">Raw Sessions</span><span class="stat-value">' + data.raw_sessions + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Raw Size</span><span class="stat-value">' + sizeStr(data.raw_total_size) + '</span></div>';
    }
    if (!html) html = '<div class="empty">No dataset recorded yet</div>';
    document.getElementById('dataset-stats').innerHTML = html;
    if (data.rewards && data.rewards.length > 1) {
      setTimeout(() => drawSparkline('reward-sparkline', data.rewards), 50);
    }
  } catch(e) {}
}

async function loadSftStats() {
  try {
    const data = await (await fetch('/api/sft-stats')).json();
    let html = '';
    const ext = data.extracted || {};
    const qw = data.qwen_sft || {};
    if (ext.total_turns > 0 || qw.total > 0) {
      html += '<div class="section-label">Extracted Turns</div>';
      html += '<div class="stat"><span class="stat-label">Turn Files</span><span class="stat-value">' + (ext.files || 0) + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Total Turns</span><span class="stat-value">' + (ext.total_turns || 0) + '</span></div>';
      if (qw.total > 0) {
        html += '<div class="section-label" style="margin-top:8px">Qwen SFT Dataset</div>';
        html += '<div class="stat"><span class="stat-label">Train Records</span><span class="stat-value">' + qw.train + '</span></div>';
        html += '<div class="stat"><span class="stat-label">Val Records</span><span class="stat-value">' + qw.val + '</span></div>';
        html += '<div class="stat"><span class="stat-label">Total</span><span class="stat-value" style="color:var(--purple)">' + qw.total + '</span></div>';
      }
    } else {
      html = '<div class="empty">No SFT data yet. Run extract_turns.py + convert_to_qwen.py</div>';
    }
    document.getElementById('sft-stats').innerHTML = html;
  } catch(e) {}
}

function drawSparkline(canvasId, values) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height;
  const pad = 4;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const zeroY = h - pad - ((0 - min) / range) * (h - pad * 2);
  ctx.strokeStyle = '#222'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad, zeroY); ctx.lineTo(w - pad, zeroY); ctx.stroke();
  ctx.strokeStyle = '#00ff41'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < values.length; i++) {
    const x = pad + (i / (values.length - 1)) * (w - pad * 2);
    const y = h - pad - ((values[i] - min) / range) * (h - pad * 2);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.lineTo(w - pad, h - pad); ctx.lineTo(pad, h - pad); ctx.closePath();
  ctx.fillStyle = 'rgba(0, 255, 65, 0.05)'; ctx.fill();
  ctx.fillStyle = '#555'; ctx.font = '9px monospace';
  ctx.fillText(max.toFixed(2), pad + 2, pad + 8);
  ctx.fillText(min.toFixed(2), pad + 2, h - pad - 2);
}

async function loadRawData() {
  try {
    const [prog, gs, cmd, se, orch] = await Promise.all([
      fetch('/api/raw?file=progress').then(r => r.json()),
      fetch('/api/raw?file=game_state').then(r => r.json()),
      fetch('/api/raw?file=claude_md').then(r => r.json()),
      fetch('/api/raw?file=state_extractor').then(r => r.json()),
      fetch('/api/raw?file=orchestrate').then(r => r.json()),
    ]);
    try { document.getElementById('raw-progress').textContent = JSON.stringify(JSON.parse(prog.content || '{}'), null, 2); } catch(e) { document.getElementById('raw-progress').textContent = prog.content || prog.error || ''; }
    try { document.getElementById('raw-game-state').textContent = JSON.stringify(JSON.parse(gs.content || '{}'), null, 2); } catch(e) { document.getElementById('raw-game-state').textContent = gs.content || gs.error || ''; }
    document.getElementById('raw-claude-md').innerHTML = simpleMarkdown(esc(cmd.content || cmd.error || ''));
    document.getElementById('raw-state-extractor').textContent = se.content || se.error || '(not found)';
    document.getElementById('raw-orchestrate').textContent = orch.content || orch.error || '(not found)';
    rawLoaded = true;
  } catch(e) { rawLoaded = true; }
}

// === Load session detail in drawer ===
async function openSession(name, logDir) {
  openDrawer('Loading ' + name + '...', '<div class="empty pulse">Parsing session log...</div>');
  try {
    let url = '/api/session-detail?name=' + encodeURIComponent(name);
    if (logDir) url += '&log_dir=' + encodeURIComponent(logDir);
    const data = await (await fetch(url)).json();
    let html = '<div style="margin-bottom:12px">';
    html += '<div class="stat"><span class="stat-label">Model</span><span class="stat-value">' + esc(data.model) + '</span></div>';
    html += '<div class="stat"><span class="stat-label">Turns</span><span class="stat-value">' + data.turn + '</span></div>';
    html += '<div class="stat"><span class="stat-label">Cost</span><span class="stat-value">$' + data.cost_usd + '</span></div>';
    if (data.duration_ms > 0) {
      html += '<div class="stat"><span class="stat-label">Duration</span><span class="stat-value">' + Math.round(data.duration_ms / 1000) + 's</span></div>';
    }
    // Token breakdown
    const tok = data.tokens || {};
    if (tok.input > 0 || tok.output > 0) {
      html += '<div class="section-label" style="margin-top:8px">Token Breakdown</div>';
      html += '<div class="stat"><span class="stat-label">Input</span><span class="stat-value">' + (tok.input || 0).toLocaleString() + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Cache Read</span><span class="stat-value">' + (tok.cache_read || 0).toLocaleString() + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Cache Create</span><span class="stat-value">' + (tok.cache_create || 0).toLocaleString() + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Output</span><span class="stat-value">' + (tok.output || 0).toLocaleString() + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Context (last)</span><span class="stat-value">' + (tok.context || 0).toLocaleString() + '</span></div>';
    }
    html += '</div>';
    html += buildActivityHTML(data.events, true);
    document.getElementById('drawer-title').textContent = data.name;
    document.getElementById('drawer-body').innerHTML = html;
  } catch(e) {
    document.getElementById('drawer-body').innerHTML = '<div class="empty">Error loading session</div>';
  }
}

// === Multi-agent: select agent ===
function selectAgent(id) {
  selectedAgent = id;
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('selected'));
  const el = document.getElementById('agent-card-' + id);
  if (el) el.classList.add('selected');
  // Preload before swapping to avoid black flash
  const img = document.getElementById('hero-img');
  const newSrc = '/screenshots/agent_' + id + '/live_screen.png?t=' + Date.now();
  const pre = new Image();
  pre.onload = () => { img.src = newSrc; };
  pre.src = newSrc;
  refreshSlow();
}

// === Refresh loops ===

async function refreshFast() {
  // Screenshot — only update when file has actually changed (use mtime as cache key)
  const img = document.getElementById('hero-img');
  let screenshotBase;
  if (currentMode === 'multi' && selectedAgent !== null) {
    screenshotBase = '/screenshots/agent_' + selectedAgent + '/live_screen.png';
  } else {
    screenshotBase = '/screenshots/live_screen.png';
  }
  // Fetch HEAD to get Last-Modified, only reload if changed
  try {
    const headResp = await fetch(screenshotBase, { method: 'HEAD' });
    const lastMod = headResp.headers.get('Last-Modified') || '';
    const cacheKey = lastMod || Date.now();
    const newUrl = screenshotBase + '?v=' + encodeURIComponent(cacheKey);
    if (img.src !== newUrl && img.dataset.lastMod !== lastMod) {
      img.dataset.lastMod = lastMod;
      const preload = new Image();
      preload.onload = () => { img.src = newUrl; };
      preload.src = newUrl;
    }
  } catch(e) {}

  // Live status
  try {
    const live = await (await fetch('/api/live')).json();
    currentMode = live.mode || 'none';

    // Agent status
    const dotAgent = document.getElementById('dot-agent');
    const statusAgent = document.getElementById('status-agent');
    if (live.mode === 'multi') {
      dotAgent.className = 'dot ' + (live.agent_count > 0 ? 'green' : 'red');
      statusAgent.textContent = live.agent_count + ' AGENTS';
      statusAgent.style.color = live.agent_count > 0 ? 'var(--green)' : 'var(--red)';
    } else {
      dotAgent.className = 'dot ' + (live.agent_running ? 'green' : 'red');
      statusAgent.textContent = live.agent_running ? 'PLAYING' : 'STOPPED';
      statusAgent.style.color = live.agent_running ? 'var(--green)' : 'var(--red)';
    }

    // Server status
    const dotServer = document.getElementById('dot-server');
    const statusServer = document.getElementById('status-server');
    dotServer.className = 'dot ' + (live.game_server_up ? 'green' : 'red');
    if (live.active_ports && live.active_ports.length > 0) {
      statusServer.textContent = 'ONLINE (' + live.active_ports.join(', ') + ')';
    } else {
      statusServer.textContent = live.game_server_up ? 'ONLINE' : 'DOWN';
    }
    statusServer.style.color = live.game_server_up ? 'var(--green)' : 'var(--red)';

    // Screenshot age
    const ssAge = live.screenshot_age_seconds;
    const ssAgeEl = document.getElementById('status-screenshot-age');
    ssAgeEl.textContent = humanTime(ssAge);
    ssAgeEl.style.color = ssAge > 30 ? 'var(--red)' : 'var(--green)';
    document.getElementById('hero-time').textContent = live.screenshot_time || '-';
    // Stale warning on hero
    const captionEl = document.getElementById('hero-caption');
    if (ssAge > 30) {
      captionEl.innerHTML = '<span style="color:var(--red)">STALE</span> \u2014 last update ' + humanTime(ssAge);
    } else {
      captionEl.textContent = 'Latest agent view';
    }

    // Game state age
    const gsAge = live.game_state_age_seconds;
    const gsAgeEl = document.getElementById('status-gs-age');
    if (gsAge >= 0) {
      gsAgeEl.textContent = humanTime(gsAge);
      gsAgeEl.style.color = gsAge > 30 ? 'var(--red)' : gsAge > 10 ? 'var(--amber)' : 'var(--green)';
    } else {
      gsAgeEl.textContent = 'none';
      gsAgeEl.style.color = 'var(--red)';
    }
  } catch(e) {}

  // Game state (respects selected agent)
  try {
    const gsUrl = selectedAgent !== null ? '/api/game-state?agent=' + selectedAgent : '/api/game-state';
    const gs = await (await fetch(gsUrl)).json();
    const entities = gs.nearby_entities || [];
    document.getElementById('entity-count').textContent = entities.length;

    // Player vitals
    const ps = gs.player_stats;
    if (ps) {
      let vhtml = '';
      const hpPct = ps.max_hp > 0 ? Math.round(ps.hp / ps.max_hp * 100) : 0;
      const hpCls = hpPct > 60 ? 'high' : hpPct > 25 ? 'mid' : 'low';
      vhtml += '<div class="vital-bar"><div class="vital-label"><span class="vl-name">HP</span><span class="vl-val" style="color:var(--' + (hpPct > 60 ? 'green' : hpPct > 25 ? 'amber' : 'red') + ')">' + ps.hp + ' / ' + ps.max_hp + '</span></div>';
      vhtml += '<div class="vital-track"><div class="vital-fill hp ' + hpCls + '" style="width:' + hpPct + '%"></div></div></div>';
      if (ps.max_mana > 0) {
        const manaPct = Math.round(ps.mana / ps.max_mana * 100);
        vhtml += '<div class="vital-bar"><div class="vital-label"><span class="vl-name">Mana</span><span class="vl-val" style="color:var(--blue)">' + ps.mana + ' / ' + ps.max_mana + '</span></div>';
        vhtml += '<div class="vital-track"><div class="vital-fill mana" style="width:' + manaPct + '%"></div></div></div>';
      }
      vhtml += '<div class="vital-bar"><div class="vital-label"><span class="vl-name">Level ' + (ps.level||1) + '</span><span class="vl-val" style="color:var(--purple)">' + (ps.experience||0) + ' XP</span></div></div>';

      // Position (Phase 3A)
      const pos = gs.player_position;
      if (pos) {
        vhtml += '<div class="stat" style="margin-top:4px"><span class="stat-label">Position</span><span class="stat-value" style="color:var(--amber)">' + pos.x + ', ' + pos.y + '</span></div>';
      }
      // Nearest mob (Phase 3B)
      const nm = gs.nearest_mob;
      if (nm) {
        vhtml += '<div class="stat"><span class="stat-label">Nearest Mob</span><span class="stat-value">' + esc(nm.name) + ' (d=' + nm.distance + ')' + (nm.on_screen ? ' <span style="color:var(--green)">visible</span>' : '') + '</span></div>';
      }
      // Players nearby (Phase 3C)
      if (gs.player_count_nearby > 0) {
        vhtml += '<div class="stat"><span class="stat-label">Players Nearby</span><span class="stat-value">' + gs.player_count_nearby + '</span></div>';
      }
      document.getElementById('player-vitals').innerHTML = vhtml;
    }

    // Current target
    const ct = gs.current_target;
    const tgtEl = document.getElementById('player-target');
    if (ct) {
      const tHpPct = ct.max_hp > 0 ? Math.round(ct.hp / ct.max_hp * 100) : 0;
      const tCls = tHpPct > 60 ? 'high' : tHpPct > 25 ? 'mid' : 'low';
      tgtEl.innerHTML = '<div class="target-box"><span class="tgt-name">\u2694 ' + esc(ct.name) + '</span>' +
        ' <div class="hp-bar-bg" style="width:80px"><div class="hp-bar-fill ' + tCls + '" style="width:' + tHpPct + '%"></div></div>' +
        ' <span style="font-size:9px;color:var(--dim)">' + ct.hp + '/' + ct.max_hp + ' d=' + (ct.distance??'?') + '</span></div>';
    } else {
      tgtEl.innerHTML = '';
    }

    // Inventory
    const inv = gs.inventory || [];
    document.getElementById('inv-count').textContent = inv.length;
    if (inv.length > 0) {
      let ihtml = '<div class="inv-grid">';
      for (const item of inv) {
        let tags = '';
        if (item.edible) tags += '<span class="inv-tag edible">eat</span> ';
        if (item.equippable) tags += '<span class="inv-tag equip">equip</span>';
        ihtml += '<div class="inv-slot" title="' + esc(item.name || item.key) + '">' +
          '<div class="inv-name">' + esc(item.name || item.key || '?') + '</div>' +
          (item.count > 1 ? '<div class="inv-count">x' + item.count + '</div>' : '') +
          (tags ? '<div class="inv-tags">' + tags + '</div>' : '') + '</div>';
      }
      ihtml += '</div>';
      document.getElementById('inventory-panel').innerHTML = ihtml;
    } else if (gs.freshness_seconds >= 0 && gs.freshness_seconds < 60) {
      document.getElementById('inventory-panel').innerHTML = '<div class="empty">Empty inventory</div>';
    }

    // Quests & Achievements
    const quests = gs.quests || [];
    const achievements = gs.achievements || [];
    if (quests.length > 0 || achievements.length > 0) {
      let qhtml = '';
      if (quests.length > 0) {
        qhtml += '<div class="section-label">Quests</div>';
        for (const q of quests) {
          const statusCls = q.finished ? 'done' : q.started ? 'active' : 'not-started';
          const statusTxt = q.finished ? 'DONE' : q.started ? (q.stage + '/' + q.stageCount) : 'NEW';
          const pct = q.stageCount > 0 ? Math.round(q.stage / q.stageCount * 100) : 0;
          qhtml += '<div class="quest-row"><div class="quest-header"><span class="quest-name">' + esc(q.name || q.key) + '</span>';
          qhtml += '<span class="quest-status ' + statusCls + '">' + statusTxt + '</span></div>';
          if (q.description) qhtml += '<div class="quest-desc">' + esc(q.description) + '</div>';
          if (q.started && !q.finished && q.stageCount > 0) {
            qhtml += '<div class="quest-progress"><div class="quest-progress-track"><div class="quest-progress-fill" style="width:' + pct + '%"></div></div></div>';
          }
          qhtml += '</div>';
        }
      }
      if (achievements.length > 0) {
        qhtml += '<div class="section-label">Achievements</div>';
        for (const a of achievements) {
          const statusCls = a.finished ? 'done' : a.started ? 'active' : 'not-started';
          const statusTxt = a.finished ? 'DONE' : a.started ? (a.stage + '/' + a.stageCount) : '';
          qhtml += '<div class="quest-row"><div class="quest-header"><span class="quest-name">' + esc(a.name || a.key) + '</span>';
          qhtml += '<span class="quest-status ' + statusCls + '">' + statusTxt + '</span></div></div>';
        }
      }
      document.getElementById('mission-stats').innerHTML = qhtml;
    }

    // Entity table (Phase 1C: fix HP field priority)
    if (entities.length > 0) {
      entities.sort((a,b) => (a.distance??9999) - (b.distance??9999));
      let ehtml = '<table class="entity-table"><tr><th>Name</th><th>Type</th><th>HP</th><th>Dist</th><th>Click</th><th>Pos</th></tr>';
      for (const e of entities) {
        const name = esc(e.name || e.id || '?');
        const tc = typeMap[e.type] || '';
        const tn = typeMap[e.type] || String(e.type);
        const hp = e.hp ?? e.hitPoints ?? '';
        const hpMax = e.max_hp ?? e.maxHitPoints ?? '';
        let hpBarHtml = '';
        if (hp !== '' && hpMax !== '' && hpMax > 0) {
          hpBarHtml = hpBar(hp, hpMax, 50);
        }
        const dist = e.distance !== undefined ? e.distance : '';
        let clickInfo = '';
        if (e.on_screen === true) clickInfo = '<span style="color:var(--green)">' + e.click_x + ',' + e.click_y + '</span>';
        else if (e.on_screen === false) clickInfo = '<span style="color:var(--dim)">off</span>';
        const pos = (e.x !== undefined && e.y !== undefined) ? e.x + ',' + e.y : '';
        ehtml += '<tr><td>' + name + '</td><td><span class="entity-type ' + tc + '">' + tn + '</span></td><td>' + hpBarHtml + '</td><td style="color:var(--amber)">' + dist + '</td><td>' + clickInfo + '</td><td style="color:var(--dim);font-size:9px">' + pos + '</td></tr>';
      }
      ehtml += '</table>';
      document.getElementById('entity-list').innerHTML = ehtml;
    } else if (gs.freshness_seconds >= 0) {
      document.getElementById('entity-list').innerHTML = '<div class="empty">No entities nearby</div>';
    }

    // Combat
    const combat = gs.last_combat;
    if (combat) {
      document.getElementById('combat-log').innerHTML =
        '<div class="combat-entry"><div class="label">Last Combat</div>' +
        '<span style="color:var(--blue)">' + esc(combat.attacker) + '</span> hit ' +
        '<span style="color:var(--red)">' + esc(combat.target) + '</span> for ' +
        '<span style="color:var(--amber)">' + (combat.damage??'?') + '</span> dmg</div>';
    }

    // XP
    const xp = gs.last_xp_event;
    if (xp) {
      const amt = xp.amount ?? xp.experience ?? '?';
      document.getElementById('xp-tracker').innerHTML =
        '<div class="xp-entry"><div class="label">Last XP</div>+' +
        '<span style="color:var(--green);font-weight:bold">' + amt + '</span> XP' +
        (xp.skill ? ' (' + xp.skill + ')' : '') +
        (xp.level ? ' Lvl ' + xp.level : '') + '</div>';
    }
  } catch(e) {}

  // Multi-agent grid (Phase 4A) — update in-place to avoid flicker
  if (currentMode === 'multi') {
    try {
      const agents = await (await fetch('/api/agents')).json();
      agentList = agents;
      const container = document.getElementById('agent-grid-container');
      container.style.display = '';
      if (agents.length > 0) {
        // Check if we need to rebuild (agent count changed)
        const existingIds = new Set([...container.querySelectorAll('.agent-card')].map(c => c.dataset.agentId));
        const newIds = new Set(agents.map(a => String(a.id)));
        const needRebuild = existingIds.size !== newIds.size || [...newIds].some(id => !existingIds.has(id));

        if (needRebuild) {
          // Full rebuild — only when agents are added/removed
          let html = '';
          for (const a of agents) {
            html += '<div class="agent-card" data-agent-id="' + a.id + '" id="agent-card-' + a.id + '" onclick="selectAgent(' + a.id + ')">';
            html += '<div class="agent-card-header"><span class="agent-card-name" style="color:var(--green)">' + esc(a.username) + '</span><span class="agent-server-dot"></span></div>';
            html += '<div class="agent-info-line" style="font-size:10px;color:var(--dim)"></div>';
            html += '<div class="agent-turns-line" style="font-size:10px;margin-top:3px"></div>';
            html += '<div class="agent-hp-line" style="margin-top:4px"></div>';
            html += '<div class="agent-target-line" style="font-size:10px;margin-top:4px"></div>';
            html += '<div class="agent-gs-age" style="font-size:9px"></div>';
            html += '<img class="agent-card-thumb" src="" style="display:none">';
            html += '</div>';
          }
          container.innerHTML = html;
        }

        // In-place update of each card (no DOM destruction = no flicker)
        for (const a of agents) {
          const card = document.getElementById('agent-card-' + a.id);
          if (!card) continue;

          // Selected state
          if (selectedAgent === a.id) card.classList.add('selected');
          else card.classList.remove('selected');

          const ps = (a.game_state || {}).player_stats || {};
          const level = ps.level || (a.progress || {}).level || '?';
          const target = (a.game_state || {}).current_target;
          const targetStr = target ? '\u2694 ' + esc(target.name) : '<span style="color:var(--dim)">idle</span>';

          // Server dot
          const dotEl = card.querySelector('.agent-server-dot');
          dotEl.innerHTML = a.server_healthy ? '<span class="dot green"></span>' : '<span class="dot red"></span>';

          // Info line
          card.querySelector('.agent-info-line').textContent = 'Lvl ' + level + ' | ' + (a.session_count || 0) + ' sessions | :' + a.server_port;

          // Turns + context tokens
          const turns = a.turns || 0;
          const ctxTok = a.context_tokens || 0;
          const ctxLabel = ctxTok >= 1e6 ? (ctxTok / 1e6).toFixed(1) + 'M' : ctxTok >= 1e3 ? Math.round(ctxTok / 1e3) + 'K' : ctxTok;
          card.querySelector('.agent-turns-line').innerHTML = '<span style="color:var(--amber)">T' + turns + '</span> <span style="color:var(--dim)">\u2502</span> <span style="color:var(--purple)">' + ctxLabel + ' ctx</span>';

          // HP bar
          const hpLine = card.querySelector('.agent-hp-line');
          if (ps.max_hp > 0) {
            hpLine.innerHTML = hpBar(ps.hp, ps.max_hp, 80);
            hpLine.style.display = '';
          } else {
            hpLine.style.display = 'none';
          }

          // Target
          card.querySelector('.agent-target-line').innerHTML = targetStr;

          // Game state age
          const gsEl = card.querySelector('.agent-gs-age');
          if (a.gs_age !== undefined) {
            const gsColor = a.gs_age > 30 ? 'var(--red)' : a.gs_age > 10 ? 'var(--amber)' : 'var(--green)';
            gsEl.style.color = gsColor;
            gsEl.textContent = 'state: ' + humanTime(a.gs_age);
          } else {
            gsEl.textContent = '';
          }

          // Thumbnail — preload, only swap when loaded, never blank
          const thumb = card.querySelector('.agent-card-thumb');
          const thumbUrl = '/screenshots/agent_' + a.id + '/live_screen.png?t=' + Date.now();
          const pre = new Image();
          pre.onload = () => { thumb.src = thumbUrl; thumb.style.display = ''; };
          // Don't set onerror to hide — keep showing the last valid frame
          pre.src = thumbUrl;
        }
      }
    } catch(e) {}
  } else {
    document.getElementById('agent-grid-container').style.display = 'none';
  }

  // Tick refresh indicator
  const ri = document.getElementById('refresh-indicator');
  ri.style.color = 'var(--green)';
  setTimeout(() => ri.style.color = '#333', 300);
}

// Slow loop: activity feed, progress, sessions, screenshots
async function refreshSlow() {
  // Activity feed
  try {
    const agentParam = (currentMode === 'multi' && selectedAgent !== null) ? '?agent=' + selectedAgent : '';
    const activity = await (await fetch('/api/activity' + agentParam)).json();
    const turn = activity.turn || 0;
    const cost = activity.cost_usd || 0;

    // In multi-agent mode, show aggregate turns/tokens across all agents
    if (currentMode === 'multi' && agentList.length > 0 && selectedAgent === null) {
      let totalTurns = 0, maxCtx = 0;
      for (const a of agentList) {
        totalTurns += a.turns || 0;
        maxCtx = Math.max(maxCtx, a.context_tokens || 0);
      }
      document.getElementById('status-turn').textContent = totalTurns + ' (' + agentList.length + ' agents)';
      const ctxLabel = maxCtx >= 1e6 ? (maxCtx / 1e6).toFixed(1) + 'M' : maxCtx >= 1e3 ? Math.round(maxCtx / 1e3) + 'K' : maxCtx;
      document.getElementById('status-tokens').textContent = ctxLabel + ' max';
    } else {
      document.getElementById('status-turn').textContent = turn;
      const tok = activity.tokens;
      if (tok && tok.context > 0) {
        const t = tok.context;
        const label = t >= 1e6 ? (t / 1e6).toFixed(1) + 'M' : Math.round(t / 1e3) + 'K';
        document.getElementById('status-tokens').textContent = label;
      }
    }
    document.getElementById('status-cost').textContent = cost.toFixed(2);

    const logName = activity.log_file || '-';
    document.getElementById('activity-log-name').textContent = logName;
    document.getElementById('activity-log-name-full').textContent = logName;

    if (activity.events && activity.events.length > 0) {
      const overviewHtml = buildActivityHTML(activity.events.slice(-25), true, 'ov');
      const feed1 = document.getElementById('activity-feed-overview');
      feed1.innerHTML = overviewHtml;
      feed1.scrollTop = feed1.scrollHeight;

      const fullHtml = buildActivityHTML(activity.events, true, 'full');
      const feed2 = document.getElementById('activity-feed-full');
      feed2.innerHTML = fullHtml;
      feed2.scrollTop = feed2.scrollHeight;
    }

    // Multi-agent: agent selector on Activity tab (Phase 4C)
    const selectorEl = document.getElementById('activity-agent-selector');
    if (currentMode === 'multi' && agentList.length > 0) {
      selectorEl.style.display = '';
      let shtml = '<button class="' + (selectedAgent === null ? 'active' : '') + '" onclick="selectedAgent=null;refreshSlow()">Default</button>';
      for (const a of agentList) {
        shtml += '<button class="' + (selectedAgent === a.id ? 'active' : '') + '" onclick="selectAgent(' + a.id + ')">' + esc(a.username) + '</button>';
      }
      selectorEl.innerHTML = shtml;
    } else {
      selectorEl.style.display = 'none';
    }
  } catch(e) {}

  // Progress state
  try {
    const stateUrl = selectedAgent !== null ? '/api/state?agent=' + selectedAgent : '/api/state';
    const state = await (await fetch(stateUrl)).json();
    if (state && Object.keys(state).length > 0) {
      let html = '';
      const fields = [
        ['Sessions', state.sessions],
        ['Kills', state.kills_this_session],
        ['Last Action', state.last_action],
        ['Locations', (state.locations_visited||[]).join(', ')],
      ];
      for (const [k,v] of fields) {
        if (v !== undefined && v !== null) {
          html += '<div class="stat"><span class="stat-label">' + k + '</span><span class="stat-value">' + esc(String(v)) + '</span></div>';
        }
      }
      document.getElementById('player-stats').innerHTML = html || '<div class="empty">Empty progress.json</div>';

      const objEl = document.getElementById('player-objective');
      if (state.next_objective) {
        objEl.innerHTML = '<div class="objective-box">' + esc(state.next_objective) + '</div>';
      } else {
        objEl.innerHTML = '';
      }

      // Fallback quest data from progress.json
      const missionEl = document.getElementById('mission-stats');
      if (!missionEl.querySelector('.quest-row')) {
        let mhtml = '';
        const quests_s = (state.quests_started || state.active_quests || []);
        const quests_c = (state.quests_completed || state.completed_quests || []);
        mhtml += '<div class="stat"><span class="stat-label">Quests Started</span><span class="stat-value">' + (quests_s.length ? quests_s.join(', ') : 'none') + '</span></div>';
        mhtml += '<div class="stat"><span class="stat-label">Quests Done</span><span class="stat-value">' + (quests_c.length ? quests_c.join(', ') : 'none') + '</span></div>';
        const ach_s = (state.active_achievements || []);
        const ach_c = (state.completed_achievements || []);
        if (ach_s.length || ach_c.length) {
          mhtml += '<div class="stat"><span class="stat-label">Achievements</span><span class="stat-value">' + ach_c.length + ' done, ' + ach_s.length + ' active</span></div>';
        }
        if (state.notes) mhtml += '<div class="stat"><span class="stat-label">Notes</span><span class="stat-value" style="max-width:200px;text-align:right;font-size:10px">' + esc(state.notes).substring(0,150) + '</span></div>';
        missionEl.innerHTML = mhtml;
      }
    }
  } catch(e) {}

  // Screenshots (world tab) — only rebuild when list changes
  try {
    const shots = await (await fetch('/api/screenshots')).json();
    document.getElementById('shot-count').textContent = shots.length;
    if (shots.length > 0) {
      const newKey = shots.slice(0, 20).map(s => s.name + s.time).join('|');
      if (newKey !== lastGalleryKey) {
        lastGalleryKey = newKey;
        let html = '';
        for (const s of shots.slice(0, 20)) {
          html += '<div class="thumb" onclick="openLightbox(\'/screenshots/' + s.name + '?t=' + Date.now() + '\')">'
            + '<img src="/screenshots/' + s.name + '" alt="' + esc(s.name) + '" loading="lazy">'
            + '<div class="meta">' + esc(s.name) + '<br>' + s.time + '</div></div>';
        }
        document.getElementById('gallery').innerHTML = html;
      }
    }
  } catch(e) {}

  // Sessions list (Phase 5A: with cost/turns/model)
  try {
    const source = currentMode === 'multi' ? 'all' : 'single';
    const sessions = await (await fetch('/api/sessions?source=' + source)).json();
    if (sessions.length > 0) {
      let html = '';
      for (const s of sessions) {
        const agentBadge = s.agent && s.agent !== 'single' ? '<span class="agent-badge">' + esc(s.agent) + '</span>' : '';
        const turnsBadge = s.turns ? '<span class="turns-badge">' + s.turns + 't</span>' : '';
        const costBadge = s.cost_usd ? '<span class="cost-badge">$' + s.cost_usd + '</span>' : '';
        const logDir = s.log_dir || '';
        html += '<div class="session-entry" onclick="openSession(\'' + esc(s.name) + '\', \'' + esc(logDir) + '\')">'
          + agentBadge
          + '<span class="name">' + esc(s.name) + '</span>'
          + turnsBadge
          + costBadge
          + '<span class="time">' + s.time + '</span>'
          + '<span class="size">' + sizeStr(s.size) + '</span>'
          + '<span class="arrow">&rarr;</span></div>';
      }
      document.getElementById('sessions-list').innerHTML = html;
    }
  } catch(e) {}

  // Auto-refresh dataset stats when Sessions tab is active (Phase 5C)
  const sessTab = document.getElementById('tab-sessions');
  if (sessTab.classList.contains('active')) {
    loadDatasetStats();
    loadSftStats();
  }
}

// Boot both loops
refreshFast();
refreshSlow();
setInterval(refreshFast, 2000);
setInterval(refreshSlow, 5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", 8080), DashboardHandler)
    print(f"Dashboard running at http://0.0.0.0:8080")
    server.serve_forever()
