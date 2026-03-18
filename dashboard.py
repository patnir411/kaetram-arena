#!/usr/bin/env python3
"""Live dashboard for Kaetram AI Agent — serves on port 8080."""

import http.server
import json
import os
import glob
import mimetypes
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


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/" or path == "/index.html":
                self.send_dashboard()
            elif path == "/api/state":
                self.send_json_state()
            elif path == "/api/sessions":
                self.send_sessions()
            elif path == "/api/screenshots":
                self.send_screenshot_list()
            elif path == "/api/live":
                self.send_live_status()
            elif path == "/api/activity":
                self.send_activity()
            elif path == "/api/game-state":
                self.send_game_state()
            elif path == "/api/prompt":
                self.send_prompt()
            elif path == "/api/session-log":
                self.send_session_log()
            elif path == "/api/session-detail":
                name = qs.get("name", [None])[0]
                self.send_session_detail(name)
            elif path == "/api/dataset-stats":
                self.send_dataset_stats()
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

    def send_screenshot_file(self):
        raw = self.path.split("?")[0]
        filename = os.path.basename(raw)
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            return self.send_error(403)
        filepath = os.path.join(STATE_DIR, filename)
        if not os.path.isfile(filepath):
            return self.send_error(404)
        mime, _ = mimetypes.guess_type(filepath)
        size = os.path.getsize(filepath)
        self.send_response(200)
        self.send_header("Content-Type", mime or "image/png")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=5")
        self.end_headers()
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

    def send_json_state(self):
        state_file = os.path.join(STATE_DIR, "progress.json")
        data = {}
        if os.path.isfile(state_file):
            try:
                with open(state_file) as fh:
                    data = json.load(fh)
            except Exception:
                pass
        self._send_json(data)

    def send_game_state(self):
        gs_file = os.path.join(STATE_DIR, "game_state.json")
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

    def send_session_detail(self, name):
        if not name:
            return self._send_json({"error": "missing name param"})
        safe = os.path.basename(name)
        filepath = os.path.join(LOG_DIR, safe)
        if not os.path.isfile(filepath):
            return self._send_json({"error": "not found"})

        events = []
        turn = 0
        cost_usd = 0
        model = ""
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
                                elif "pattern" in inp:
                                    summary = inp["pattern"][:80]
                                    detail = json.dumps(inp, indent=2)[:500]
                                else:
                                    detail = json.dumps(inp, indent=2)[:500]
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
                                text = c.get("text", "")[:500]
                                if text.strip():
                                    events.append({"turn": turn, "type": "text", "text": sanitize(text)})
                            elif ct == "thinking":
                                thinking = c.get("thinking", "")[:300]
                                if thinking.strip():
                                    events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

                    elif t == "result":
                        cost_usd = obj.get("total_cost_usd", 0)

        except Exception:
            pass

        self._send_json({
            "name": safe,
            "events": events,
            "turn": turn,
            "cost_usd": round(cost_usd, 4),
            "model": model,
        })

    def send_dataset_stats(self):
        stats = {"sessions": [], "total_steps": 0, "total_reward": 0}
        if not os.path.isdir(DATASET_DIR):
            return self._send_json(stats)
        for sd in sorted(glob.glob(os.path.join(DATASET_DIR, "session_*"))):
            sname = os.path.basename(sd)
            steps_file = os.path.join(sd, "steps.jsonl")
            if not os.path.isfile(steps_file):
                continue
            step_count = 0
            total_reward = 0
            last_reward = 0
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
            except Exception:
                pass
            stats["sessions"].append({
                "name": sname, "steps": step_count,
                "total_reward": round(total_reward, 3),
                "last_reward": round(last_reward, 3),
            })
            stats["total_steps"] += step_count
            stats["total_reward"] += total_reward
        stats["total_reward"] = round(stats["total_reward"], 3)
        self._send_json(stats)

    def send_raw_file(self, which):
        allowed = {
            "progress": os.path.join(STATE_DIR, "progress.json"),
            "game_state": os.path.join(STATE_DIR, "game_state.json"),
            "session_log": os.path.join(PROJECT_DIR, "session_log.md"),
            "claude_md": os.path.join(PROJECT_DIR, "CLAUDE.md"),
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

    def send_live_status(self):
        try:
            result = subprocess.run(["pgrep", "-f", "play.sh"], capture_output=True, text=True, timeout=3)
            agent_running = result.returncode == 0
        except Exception:
            agent_running = False

        try:
            result = subprocess.run(["pgrep", "-f", "ws_observer"], capture_output=True, text=True, timeout=3)
            ws_observer_running = result.returncode == 0
        except Exception:
            ws_observer_running = False

        gs_file = os.path.join(STATE_DIR, "game_state.json")
        game_state_age = -1
        if os.path.isfile(gs_file):
            game_state_age = int(time.time() - os.path.getmtime(gs_file))

        screenshot = os.path.join(STATE_DIR, "screenshot.png")
        screenshot_age = -1
        screenshot_time = ""
        if os.path.isfile(screenshot):
            mtime = os.path.getmtime(screenshot)
            screenshot_age = int(datetime.now().timestamp() - mtime)
            screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

        try:
            result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=3)
            game_server_up = ":9000" in result.stdout and ":9001" in result.stdout
        except Exception:
            game_server_up = False

        logs = glob.glob(os.path.join(LOG_DIR, "session_*.log"))
        total_sessions = len(logs)

        highlights_file = os.path.join(STATE_DIR, "highlights.jsonl")
        highlights = []
        if os.path.isfile(highlights_file):
            try:
                with open(highlights_file) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            highlights.append(json.loads(line))
            except Exception:
                pass

        self._send_json({
            "agent_running": agent_running,
            "ws_observer_running": ws_observer_running,
            "game_server_up": game_server_up,
            "game_state_age_seconds": game_state_age,
            "screenshot_age_seconds": screenshot_age,
            "screenshot_time": screenshot_time,
            "total_sessions": total_sessions,
            "highlights": highlights[-10:],
        })

    def send_activity(self):
        logs = sorted(glob.glob(os.path.join(LOG_DIR, "session_*.log")), key=os.path.getmtime)
        if not logs:
            return self._send_json({"events": [], "turn": 0, "cost_usd": 0})

        latest = logs[-1]
        events = []
        turn = 0
        cost_usd = 0
        try:
            with open(latest) as fh:
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
                        contents = msg.get("content", [])
                        for c in contents:
                            ct = c.get("type", "")
                            if ct == "tool_use":
                                tool = c.get("name", "unknown")
                                tool = tool.replace("mcp__playwright__", "pw:")
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
                                elif "file_path" in inp:
                                    summary = inp["file_path"].split("/")[-1]
                                    detail = inp["file_path"]
                                turn += 1
                                events.append({"turn": turn, "type": "tool", "tool": tool, "summary": sanitize(summary), "detail": sanitize(detail)})
                            elif ct == "text":
                                text = c.get("text", "")[:300]
                                if text.strip():
                                    events.append({"turn": turn, "type": "text", "text": sanitize(text)})

                    elif t == "result":
                        cost_usd = obj.get("total_cost_usd", 0)

        except Exception:
            pass

        self._send_json({
            "events": events[-50:],
            "turn": turn,
            "cost_usd": round(cost_usd, 4),
            "log_file": os.path.basename(latest),
        })

    def send_sessions(self):
        logs = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True)
        entries = []
        for log in logs[:50]:
            name = os.path.basename(log)
            size = os.path.getsize(log)
            mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
            entries.append({"name": name, "time": mtime, "size": size})
        self._send_json(entries)

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
  .session-entry { padding: 6px 10px; border-bottom: 1px solid #1a1a1a; font-size: 11px; display: flex; justify-content: space-between; cursor: pointer; transition: background 0.1s; align-items: center; }
  .session-entry:hover { background: #1a1a1a; }
  .session-entry .name { color: var(--text); }
  .session-entry .time { color: var(--dim); font-size: 10px; }
  .session-entry .size { color: var(--amber); min-width: 60px; text-align: right; font-size: 10px; }
  .session-entry .arrow { color: var(--dim); margin-left: 8px; }

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
  <div class="status-item"><span class="dot amber" id="dot-observer"></span> Observer: <span id="status-observer">...</span></div>
  <div class="status-item">GS: <span id="status-gs-age" style="color:var(--green)">-</span></div>
  <div class="status-item">Shot: <span id="status-screenshot-age" style="color:var(--green)">-</span></div>
  <div class="status-item">Turn: <span id="status-turn" style="color:var(--green)">-</span></div>
  <div class="status-item">Cost: $<span id="status-cost" style="color:var(--amber)">-</span></div>
  <div class="status-item">Sessions: <span id="status-sessions" style="color:var(--green)">-</span></div>
  <div class="status-item" style="color:#333;margin-left:auto" id="refresh-indicator">5s</div>
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
    <div class="hero" id="hero">
      <img id="hero-img" src="/screenshots/screenshot.png" alt="Latest game screenshot" onclick="openLightbox(this.src)">
      <div class="caption">
        <span id="hero-caption">Latest agent view</span>
        <span id="hero-time">-</span>
      </div>
    </div>
    <div class="grid-3">
      <div class="card">
        <h2>Player Status</h2>
        <div id="player-stats"><div class="empty">Waiting...</div></div>
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
    <div class="card full" style="margin-top:14px">
      <h2>Dataset Stats</h2>
      <div id="dataset-stats"><div class="empty">No dataset recorded yet</div></div>
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
// === Tab switching ===
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    // Load tab-specific data on first view
    if (tab.dataset.tab === 'prompt' && !promptLoaded) loadPrompt();
    if (tab.dataset.tab === 'sessions' && !sessNotesLoaded) { loadSessionNotes(); loadDatasetStats(); }
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
  if (e.key === 'Escape') {
    closeDrawer();
    document.getElementById('lightbox').classList.remove('active');
  }
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

const typeMap = {1:'player',2:'npc',3:'mob',4:'item',player:'player',mob:'mob',npc:'npc',item:'item'};

// === Build activity HTML ===
function buildActivityHTML(events, withDetails) {
  if (!events || !events.length) return '<div class="empty">No events yet</div>';
  let html = '';
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const id = 'ev-' + i;
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

// === Lazy-loaded tabs ===
let promptLoaded = false;
let sessNotesLoaded = false;
let rawLoaded = false;

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
    sessNotesLoaded = true;
  } catch(e) {}
}

async function loadDatasetStats() {
  try {
    const data = await (await fetch('/api/dataset-stats')).json();
    if (data.sessions && data.sessions.length > 0) {
      let html = '<div class="stat"><span class="stat-label">Total Steps</span><span class="stat-value">' + data.total_steps + '</span></div>';
      html += '<div class="stat"><span class="stat-label">Total Reward</span><span class="stat-value">' + data.total_reward + '</span></div>';
      for (const s of data.sessions) {
        html += '<div class="stat"><span class="stat-label">' + esc(s.name) + '</span><span class="stat-value">' + s.steps + ' steps, reward=' + s.total_reward + '</span></div>';
      }
      document.getElementById('dataset-stats').innerHTML = html;
    }
  } catch(e) {}
}

async function loadRawData() {
  try {
    const [prog, gs, cmd] = await Promise.all([
      fetch('/api/raw?file=progress').then(r => r.json()),
      fetch('/api/raw?file=game_state').then(r => r.json()),
      fetch('/api/raw?file=claude_md').then(r => r.json()),
    ]);
    document.getElementById('raw-progress').textContent = JSON.stringify(JSON.parse(prog.content || '{}'), null, 2);
    const gsObj = JSON.parse(gs.content || '{}');
    document.getElementById('raw-game-state').textContent = JSON.stringify(gsObj, null, 2);
    document.getElementById('raw-claude-md').innerHTML = simpleMarkdown(esc(cmd.content || ''));
    rawLoaded = true;
  } catch(e) {
    // individual panels may fail, that's ok
    rawLoaded = true;
  }
}

// === Load session detail in drawer ===
async function openSession(name) {
  openDrawer('Loading ' + name + '...', '<div class="empty pulse">Parsing session log...</div>');
  try {
    const data = await (await fetch('/api/session-detail?name=' + encodeURIComponent(name))).json();
    let html = '<div style="margin-bottom:12px">';
    html += '<div class="stat"><span class="stat-label">Model</span><span class="stat-value">' + esc(data.model) + '</span></div>';
    html += '<div class="stat"><span class="stat-label">Turns</span><span class="stat-value">' + data.turn + '</span></div>';
    html += '<div class="stat"><span class="stat-label">Cost</span><span class="stat-value">$' + data.cost_usd + '</span></div>';
    html += '</div>';
    html += buildActivityHTML(data.events, true);
    document.getElementById('drawer-title').textContent = data.name;
    document.getElementById('drawer-body').innerHTML = html;
  } catch(e) {
    document.getElementById('drawer-body').innerHTML = '<div class="empty">Error loading session</div>';
  }
}

// === Main refresh loop ===
async function refresh() {
  // Live status
  try {
    const live = await (await fetch('/api/live')).json();

    const setDot = (dotId, textId, on, onLabel, offLabel) => {
      document.getElementById(dotId).className = 'dot ' + (on ? 'green' : 'red');
      const el = document.getElementById(textId);
      el.textContent = on ? onLabel : offLabel;
      el.style.color = on ? 'var(--green)' : 'var(--red)';
    };
    setDot('dot-agent', 'status-agent', live.agent_running, 'PLAYING', 'STOPPED');
    setDot('dot-server', 'status-server', live.game_server_up, 'ONLINE', 'DOWN');
    setDot('dot-observer', 'status-observer', live.ws_observer_running, 'RUNNING', 'STOPPED');

    const gsAge = live.game_state_age_seconds;
    const gsEl = document.getElementById('status-gs-age');
    if (gsAge < 0) { gsEl.textContent = 'none'; gsEl.style.color = 'var(--dim)'; }
    else if (gsAge < 30) { gsEl.textContent = humanTime(gsAge); gsEl.style.color = 'var(--green)'; }
    else if (gsAge < 120) { gsEl.textContent = humanTime(gsAge); gsEl.style.color = 'var(--amber)'; }
    else { gsEl.textContent = humanTime(gsAge); gsEl.style.color = 'var(--red)'; }

    document.getElementById('status-sessions').textContent = live.total_sessions;
    document.getElementById('status-screenshot-age').textContent = humanTime(live.screenshot_age_seconds);
    document.getElementById('hero-time').textContent = live.screenshot_time || '-';
  } catch(e) {}

  // Activity feed (both overview compact + full tab)
  try {
    const activity = await (await fetch('/api/activity')).json();
    document.getElementById('status-turn').textContent = activity.turn || '0';
    document.getElementById('status-cost').textContent = (activity.cost_usd || 0).toFixed(2);
    const logName = activity.log_file || '-';
    document.getElementById('activity-log-name').textContent = logName;
    document.getElementById('activity-log-name-full').textContent = logName;

    if (activity.events && activity.events.length > 0) {
      // Overview: last 25, no details
      const overviewHtml = buildActivityHTML(activity.events.slice(-25), false);
      const feed1 = document.getElementById('activity-feed-overview');
      feed1.innerHTML = overviewHtml;
      feed1.scrollTop = feed1.scrollHeight;

      // Full tab: all events with expandable details
      const fullHtml = buildActivityHTML(activity.events, true);
      const feed2 = document.getElementById('activity-feed-full');
      feed2.innerHTML = fullHtml;
      feed2.scrollTop = feed2.scrollHeight;
    }
  } catch(e) {}

  // Game state
  try {
    const gs = await (await fetch('/api/game-state')).json();
    const entities = gs.nearby_entities || [];
    document.getElementById('entity-count').textContent = entities.length;

    if (entities.length > 0) {
      let ehtml = '<table class="entity-table"><tr><th>Name</th><th>Type</th><th>Lvl</th><th>HP</th><th>Pos</th></tr>';
      for (const e of entities) {
        const name = esc(e.name || e.id || '?');
        const tc = typeMap[e.type] || '';
        const tn = typeMap[e.type] || String(e.type);
        const hp = e.hitPoints ?? e.hp ?? '';
        const hpMax = e.maxHitPoints ?? e.max_hp ?? e.hp_max ?? '';
        let hpBar = '';
        if (hp !== '' && hpMax !== '' && hpMax > 0) {
          const pct = Math.max(0, Math.min(100, Math.round(hp / hpMax * 100)));
          const cls = pct > 60 ? 'high' : pct > 25 ? 'mid' : 'low';
          hpBar = '<div class="hp-bar-bg"><div class="hp-bar-fill ' + cls + '" style="width:' + pct + '%"></div></div> <span style="font-size:9px">' + hp + '/' + hpMax + '</span>';
        }
        const pos = (e.x !== undefined && e.y !== undefined) ? e.x + ',' + e.y : '';
        ehtml += '<tr><td>' + name + '</td><td><span class="entity-type ' + tc + '">' + tn + '</span></td><td>' + (e.level||'') + '</td><td>' + hpBar + '</td><td style="color:var(--dim);font-size:9px">' + pos + '</td></tr>';
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

  // Progress state
  try {
    const state = await (await fetch('/api/state')).json();
    if (state && Object.keys(state).length > 0) {
      let html = '';
      const fields = [
        ['Level', state.level],
        ['XP', state.xp_estimate],
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

      let mhtml = '';
      const quests_s = (state.quests_started||[]);
      const quests_c = (state.quests_completed||[]);
      mhtml += '<div class="stat"><span class="stat-label">Quests Started</span><span class="stat-value">' + (quests_s.length ? quests_s.join(', ') : 'none') + '</span></div>';
      mhtml += '<div class="stat"><span class="stat-label">Quests Done</span><span class="stat-value">' + (quests_c.length ? quests_c.join(', ') : 'none') + '</span></div>';
      if (state.notes) mhtml += '<div class="stat"><span class="stat-label">Notes</span><span class="stat-value" style="max-width:200px;text-align:right;font-size:10px">' + esc(state.notes).substring(0,150) + '</span></div>';
      document.getElementById('mission-stats').innerHTML = mhtml;
    }
  } catch(e) {}

  // Hero image
  document.getElementById('hero-img').src = '/screenshots/screenshot.png?t=' + Date.now();

  // Screenshots (world tab)
  try {
    const shots = await (await fetch('/api/screenshots')).json();
    document.getElementById('shot-count').textContent = shots.length;
    if (shots.length > 0) {
      let html = '';
      for (const s of shots.slice(0, 20)) {
        html += '<div class="thumb" onclick="openLightbox(\'/screenshots/' + s.name + '?t=' + Date.now() + '\')">'
          + '<img src="/screenshots/' + s.name + '?t=' + Date.now() + '" alt="' + esc(s.name) + '" loading="lazy">'
          + '<div class="meta">' + esc(s.name) + '<br>' + s.time + '</div></div>';
      }
      document.getElementById('gallery').innerHTML = html;
    }
  } catch(e) {}

  // Sessions list
  try {
    const sessions = await (await fetch('/api/sessions')).json();
    if (sessions.length > 0) {
      let html = '';
      for (const s of sessions) {
        html += '<div class="session-entry" onclick="openSession(\'' + esc(s.name) + '\')">'
          + '<span class="name">' + esc(s.name) + '</span>'
          + '<span class="time">' + s.time + '</span>'
          + '<span class="size">' + sizeStr(s.size) + '</span>'
          + '<span class="arrow">&rarr;</span></div>';
      }
      document.getElementById('sessions-list').innerHTML = html;
    }
  } catch(e) {}

  // Tick refresh indicator
  const ri = document.getElementById('refresh-indicator');
  ri.style.color = 'var(--green)';
  setTimeout(() => ri.style.color = '#333', 500);
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", 8080), DashboardHandler)
    print(f"Dashboard running at http://0.0.0.0:8080")
    server.serve_forever()
