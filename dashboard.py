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


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == "/" or self.path == "/index.html":
                self.send_dashboard()
            elif self.path == "/api/state":
                self.send_json_state()
            elif self.path == "/api/sessions":
                self.send_sessions()
            elif self.path == "/api/screenshots":
                self.send_screenshot_list()
            elif self.path == "/api/live":
                self.send_live_status()
            elif self.path == "/api/activity":
                self.send_activity()
            elif self.path.startswith("/screenshots/"):
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
        raw = self.path.split("?")[0]  # strip query params
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
        for img in images[:30]:
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

    def send_live_status(self):
        """Return live agent status — is claude running, latest screenshot age, etc."""
        # Check if claude process is running
        try:
            result = subprocess.run(["pgrep", "-f", "play.sh"], capture_output=True, text=True, timeout=3)
            agent_running = result.returncode == 0
        except Exception:
            agent_running = False

        # Check latest screenshot age
        screenshot = os.path.join(STATE_DIR, "screenshot.png")
        screenshot_age = -1
        screenshot_time = ""
        if os.path.isfile(screenshot):
            mtime = os.path.getmtime(screenshot)
            screenshot_age = int(datetime.now().timestamp() - mtime)
            screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

        # Check game server
        try:
            result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=3)
            game_server_up = ":9000" in result.stdout and ":9001" in result.stdout
        except Exception:
            game_server_up = False

        # Count sessions from logs
        logs = glob.glob(os.path.join(LOG_DIR, "session_*.log"))
        total_sessions = len(logs)

        # Get highlights
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
            "game_server_up": game_server_up,
            "screenshot_age_seconds": screenshot_age,
            "screenshot_time": screenshot_time,
            "total_sessions": total_sessions,
            "highlights": highlights[-10:],
        })

    def send_activity(self):
        """Parse the most recent stream-json log file for live agent activity."""
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
                                # Shorten playwright tool names
                                tool = tool.replace("mcp__playwright__", "")
                                inp = c.get("input", {})
                                summary = ""
                                if "code" in inp:
                                    code = inp["code"][:120]
                                    summary = code.split("return ")[1].split("'")[1] if "return '" in code else code[:80]
                                elif "command" in inp:
                                    summary = inp["command"][:80]
                                elif "url" in inp:
                                    summary = inp["url"][:80]
                                elif "file_path" in inp:
                                    summary = inp["file_path"].split("/")[-1]
                                turn += 1
                                events.append({"turn": turn, "type": "tool", "tool": tool, "summary": sanitize(summary)})
                            elif ct == "text":
                                text = c.get("text", "")[:200]
                                if text.strip():
                                    events.append({"turn": turn, "type": "text", "text": sanitize(text)})

                    elif t == "result":
                        cost_usd = obj.get("total_cost_usd", 0)

        except Exception:
            pass

        self._send_json({
            "events": events[-30:],
            "turn": turn,
            "cost_usd": round(cost_usd, 4),
            "log_file": os.path.basename(latest),
        })

    def send_sessions(self):
        logs = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True)
        entries = []
        for log in logs[:20]:
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


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<title>Kaetram AI Agent — Live Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg: #0a0a0a; --card: #111; --border: #222; --green: #00ff41; --amber: #ffaa00; --red: #ff4141; --blue: #00aaff; --dim: #555; --text: #ccc; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace; background: var(--bg); color: var(--text); }

  /* Header */
  header { background: #0d0d0d; border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
  header h1 { color: var(--green); font-size: 20px; letter-spacing: 1px; }
  header h1 span { font-weight: normal; color: var(--dim); font-size: 14px; }
  .header-links { display: flex; gap: 10px; }
  .header-links a { color: var(--blue); text-decoration: none; border: 1px solid #333; padding: 4px 12px; border-radius: 4px; font-size: 12px; transition: all 0.2s; }
  .header-links a:hover { border-color: var(--green); color: var(--green); }

  /* Status bar */
  .status-bar { background: #0d0d0d; border-bottom: 1px solid var(--border); padding: 8px 24px; display: flex; gap: 24px; font-size: 12px; flex-wrap: wrap; }
  .status-item { display: flex; align-items: center; gap: 6px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot.green { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.red { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.amber { background: var(--amber); box-shadow: 0 0 6px var(--amber); }

  /* Main layout */
  main { max-width: 1400px; margin: 0 auto; padding: 20px; }

  /* Hero screenshot */
  .hero { margin-bottom: 20px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .hero img { width: 100%; max-height: 500px; object-fit: contain; background: #000; display: block; cursor: pointer; }
  .hero .caption { padding: 10px 16px; font-size: 12px; color: var(--dim); display: flex; justify-content: space-between; }

  /* Grid */
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }

  /* Cards */
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card h2 { color: var(--amber); font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
  .card.full { grid-column: 1 / -1; }

  .stat { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #1a1a1a; font-size: 13px; }
  .stat:last-child { border-bottom: none; }
  .stat-label { color: var(--dim); }
  .stat-value { color: var(--green); font-weight: bold; }

  /* Screenshot gallery */
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
  .thumb { background: #0d0d0d; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; transition: border-color 0.2s; }
  .thumb:hover { border-color: var(--green); }
  .thumb img { width: 100%; display: block; cursor: pointer; }
  .thumb .meta { padding: 6px 10px; font-size: 10px; color: var(--dim); }

  /* Session log */
  .session-entry { padding: 5px 0; border-bottom: 1px solid #1a1a1a; font-size: 12px; display: flex; justify-content: space-between; }
  .session-entry .name { color: var(--text); }
  .session-entry .time { color: var(--dim); }
  .session-entry .size { color: var(--amber); min-width: 60px; text-align: right; }

  /* Activity feed */
  .activity-event { padding: 4px 10px; margin-bottom: 2px; font-size: 11px; border-left: 2px solid #333; font-family: monospace; }
  .activity-event .turn-num { color: var(--dim); margin-right: 6px; }
  .activity-event .tool-name { color: var(--blue); font-weight: bold; }
  .activity-event .summary { color: var(--text); margin-left: 6px; }
  .activity-event.text-event { border-left-color: var(--green); }
  .activity-event.text-event .agent-text { color: var(--green); }
  .activity-feed { max-height: 400px; overflow-y: auto; }

  /* Lightbox */
  .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.95); z-index: 100; justify-content: center; align-items: center; cursor: pointer; }
  .modal.active { display: flex; }
  .modal img { max-width: 95vw; max-height: 95vh; }

  .empty { color: #333; font-style: italic; padding: 20px; text-align: center; }
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
</head>
<body>

<header>
  <h1>KAETRAM AI AGENT <span>// autonomous MMORPG player</span></h1>
  <div class="header-links">
    <a href="http://__GAME_HOST__:9000" target="_blank">Play Game</a>
    <a href="/api/state">API: State</a>
    <a href="/api/live">API: Live</a>
    <a href="https://github.com/patnir411/kaetram-arena" target="_blank">GitHub</a>
  </div>
</header>

<div class="status-bar">
  <div class="status-item"><span class="dot amber" id="dot-agent"></span> Agent: <span id="status-agent">checking...</span></div>
  <div class="status-item"><span class="dot amber" id="dot-server"></span> Game Server: <span id="status-server">checking...</span></div>
  <div class="status-item">Sessions: <span id="status-sessions" style="color:var(--green)">-</span></div>
  <div class="status-item">Screenshot: <span id="status-screenshot-age" style="color:var(--green)">-</span></div>
  <div class="status-item">Turn: <span id="status-turn" style="color:var(--green)">-</span></div>
  <div class="status-item">Cost: $<span id="status-cost" style="color:var(--amber)">-</span></div>
  <div class="status-item refresh-info" style="color:#333">Refreshes every 5s</div>
</div>

<main>
  <div class="hero" id="hero">
    <img id="hero-img" src="/screenshots/screenshot.png" alt="Latest game screenshot" onclick="openLightbox(this.src)">
    <div class="caption">
      <span id="hero-caption">Latest agent view</span>
      <span id="hero-time">-</span>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Player Status</h2>
      <div id="player-stats"><div class="empty">Waiting for agent data...</div></div>
    </div>
    <div class="card">
      <h2>Mission Progress</h2>
      <div id="mission-stats"><div class="empty">Waiting for agent data...</div></div>
    </div>
  </div>

  <div class="card full" style="margin-bottom:16px" id="activity-card">
    <h2>Live Activity Feed</h2>
    <div class="activity-feed" id="activity-feed"><div class="empty">Waiting for agent activity...</div></div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Screenshot History</h2>
      <div class="gallery" id="gallery"><div class="empty">No screenshots yet</div></div>
    </div>
    <div class="card">
      <h2>Session Log</h2>
      <div id="sessions"><div class="empty">No sessions yet</div></div>
    </div>
  </div>
</main>

<div class="modal" id="lightbox" onclick="this.classList.remove('active')">
  <img id="lightbox-img" src="">
</div>

<script>
function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('active');
}

function humanTime(seconds) {
  if (seconds < 0) return 'never';
  if (seconds < 60) return seconds + 's ago';
  if (seconds < 3600) return Math.floor(seconds/60) + 'm ago';
  return Math.floor(seconds/3600) + 'h ago';
}

async function refresh() {
  // Live status
  try {
    const live = await (await fetch('/api/live')).json();

    const agentDot = document.getElementById('dot-agent');
    const agentText = document.getElementById('status-agent');
    if (live.agent_running) {
      agentDot.className = 'dot green';
      agentText.textContent = 'PLAYING';
      agentText.style.color = 'var(--green)';
    } else {
      agentDot.className = 'dot red';
      agentText.textContent = 'STOPPED';
      agentText.style.color = 'var(--red)';
    }

    const serverDot = document.getElementById('dot-server');
    const serverText = document.getElementById('status-server');
    if (live.game_server_up) {
      serverDot.className = 'dot green';
      serverText.textContent = 'ONLINE';
      serverText.style.color = 'var(--green)';
    } else {
      serverDot.className = 'dot red';
      serverText.textContent = 'DOWN';
      serverText.style.color = 'var(--red)';
    }

    document.getElementById('status-sessions').textContent = live.total_sessions;
    document.getElementById('status-screenshot-age').textContent = humanTime(live.screenshot_age_seconds);
    document.getElementById('hero-time').textContent = live.screenshot_time || '-';

  } catch(e) {}

  // Activity feed
  try {
    const activity = await (await fetch('/api/activity')).json();
    document.getElementById('status-turn').textContent = activity.turn || '0';
    document.getElementById('status-cost').textContent = (activity.cost_usd || 0).toFixed(2);
    if (activity.events && activity.events.length > 0) {
      let ahtml = '';
      for (const ev of activity.events) {
        if (ev.type === 'tool') {
          ahtml += '<div class="activity-event"><span class="turn-num">#' + ev.turn + '</span><span class="tool-name">' + ev.tool + '</span><span class="summary">' + (ev.summary || '').replace(/</g,'&lt;') + '</span></div>';
        } else if (ev.type === 'text') {
          ahtml += '<div class="activity-event text-event"><span class="turn-num">#' + ev.turn + '</span><span class="agent-text">' + (ev.text || '').replace(/</g,'&lt;') + '</span></div>';
        }
      }
      const feed = document.getElementById('activity-feed');
      feed.innerHTML = ahtml;
      feed.scrollTop = feed.scrollHeight;
    }
  } catch(e) {}

  // State
  try {
    const state = await (await fetch('/api/state')).json();
    if (state && Object.keys(state).length > 0) {
      const name = state.character?.name || state.login?.actual_username || '-';
      const level = state.character?.level || state.level || '-';
      const hp = state.character?.hp || '-';
      const hpMax = state.character?.hp_max || '-';
      const loc = state.location || state.coordinates || state.world?.location || '-';

      document.getElementById('player-stats').innerHTML =
        '<div class="stat"><span class="stat-label">Name</span><span class="stat-value">' + name + '</span></div>' +
        '<div class="stat"><span class="stat-label">Level</span><span class="stat-value">' + level + '</span></div>' +
        '<div class="stat"><span class="stat-label">HP</span><span class="stat-value">' + hp + ' / ' + hpMax + '</span></div>' +
        '<div class="stat"><span class="stat-label">Location</span><span class="stat-value">' + loc + '</span></div>';

      const sessions = state.sessions || '-';
      const milestone = state.milestone || '-';
      const notes = state.notes || '-';
      document.getElementById('mission-stats').innerHTML =
        '<div class="stat"><span class="stat-label">Sessions</span><span class="stat-value">' + sessions + '</span></div>' +
        '<div class="stat"><span class="stat-label">Milestone</span><span class="stat-value">' + milestone + '</span></div>' +
        '<div class="stat"><span class="stat-label">Notes</span><span class="stat-value" style="max-width:280px;text-align:right;font-size:11px">' + (notes.length > 150 ? notes.substring(0,150) + '...' : notes) + '</span></div>';
    }
  } catch(e) {}

  // Hero image cache bust
  document.getElementById('hero-img').src = '/screenshots/screenshot.png?t=' + Date.now();

  // Screenshots
  try {
    const shots = await (await fetch('/api/screenshots')).json();
    if (shots.length > 0) {
      let html = '';
      for (const s of shots.slice(0, 12)) {
        html += '<div class="thumb"><img src="/screenshots/' + s.name + '?t=' + Date.now() + '" alt="' + s.name + '" onclick="openLightbox(this.src)" loading="lazy"><div class="meta">' + s.name + ' | ' + s.time + '</div></div>';
      }
      document.getElementById('gallery').innerHTML = html;
    }
  } catch(e) {}

  // Sessions
  try {
    const sessions = await (await fetch('/api/sessions')).json();
    if (sessions.length > 0) {
      let html = '';
      for (const s of sessions.slice(0, 20)) {
        const sizeStr = s.size > 0 ? (s.size > 1024 ? Math.round(s.size/1024) + ' KB' : s.size + ' B') : 'running...';
        html += '<div class="session-entry"><span class="name">' + s.name + '</span><span class="time">' + s.time + '</span><span class="size">' + sizeStr + '</span></div>';
      }
      document.getElementById('sessions').innerHTML = html;
    }
  } catch(e) {}
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
