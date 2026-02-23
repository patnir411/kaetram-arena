#!/usr/bin/env python3
"""Live dashboard for Kaetram AI Agent — serves on port 8080."""

import http.server
import json
import os
import glob
import mimetypes
from datetime import datetime

STATE_DIR = os.path.expanduser("~/projects/kaetram-agent/state")
LOG_DIR = os.path.expanduser("~/projects/kaetram-agent/logs")
RECORDINGS_DIR = os.path.expanduser("~/projects/kaetram-agent/recordings")

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_dashboard()
        elif self.path == "/api/state":
            self.send_json_state()
        elif self.path == "/api/sessions":
            self.send_sessions()
        elif self.path == "/api/screenshots":
            self.send_screenshot_list()
        elif self.path.startswith("/screenshots/"):
            self.send_screenshot_file()
        else:
            self.send_error(404)

    def send_screenshot_file(self):
        """Serve screenshot images from state dir."""
        filename = os.path.basename(self.path.split("/")[-1])
        # Only allow image files
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            self.send_error(403)
            return
        filepath = os.path.join(STATE_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(filepath)
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    def send_screenshot_list(self):
        """Return list of screenshot filenames."""
        images = []
        for ext in ('*.png', '*.jpg', '*.jpeg', '*.webp'):
            images.extend(glob.glob(os.path.join(STATE_DIR, ext)))
        images.sort(key=os.path.getmtime, reverse=True)
        result = []
        for img in images[:20]:
            name = os.path.basename(img)
            mtime = datetime.fromtimestamp(os.path.getmtime(img)).strftime("%Y-%m-%d %H:%M:%S")
            result.append({"name": name, "time": mtime, "size": os.path.getsize(img)})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def send_dashboard(self):
        host = self.headers.get('Host', 'localhost').split(':')[0]
        html = f"""<!DOCTYPE html>
<html>
<head>
<title>Kaetram AI Agent Dashboard</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Courier New', monospace; background: #0a0a0a; color: #00ff41; padding: 20px; }}
  h1 {{ color: #00ff41; border-bottom: 2px solid #00ff41; padding-bottom: 10px; margin-bottom: 10px; font-size: 24px; }}
  h2 {{ color: #ffaa00; margin: 15px 0 10px; font-size: 18px; }}
  .topbar {{ color: #888; font-size: 12px; margin-bottom: 20px; display: flex; gap: 15px; flex-wrap: wrap; align-items: center; }}
  .topbar a {{ color: #00aaff; text-decoration: none; border: 1px solid #333; padding: 3px 8px; border-radius: 4px; }}
  .topbar a:hover {{ border-color: #00ff41; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
  @media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  .card {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 15px; }}
  .card.full {{ grid-column: 1 / -1; }}
  .stat {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #222; }}
  .stat-label {{ color: #888; }}
  .stat-value {{ color: #00ff41; font-weight: bold; }}
  pre {{ background: #0d0d0d; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; }}
  .log-entry {{ padding: 4px 0; border-bottom: 1px solid #1a1a1a; font-size: 13px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 8px; }}
  .badge.online {{ background: #003300; color: #00ff41; border: 1px solid #00ff41; }}
  .badge.offline {{ background: #330000; color: #ff4141; border: 1px solid #ff4141; }}
  .badge.idle {{ background: #332200; color: #ffaa00; border: 1px solid #ffaa00; }}
  .screenshots {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px; }}
  .screenshot-card {{ background: #0d0d0d; border: 1px solid #333; border-radius: 6px; overflow: hidden; }}
  .screenshot-card img {{ width: 100%; display: block; cursor: pointer; }}
  .screenshot-card img:hover {{ opacity: 0.85; }}
  .screenshot-card .meta {{ padding: 8px 10px; font-size: 11px; color: #888; }}
  .screenshot-desc {{ padding: 5px 10px 10px; font-size: 12px; color: #aaa; }}
  .modal {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 100; justify-content: center; align-items: center; cursor: pointer; }}
  .modal.active {{ display: flex; }}
  .modal img {{ max-width: 95vw; max-height: 95vh; border: 2px solid #333; }}
  .no-data {{ color: #666; font-style: italic; padding: 20px; text-align: center; }}
</style>
</head>
<body>
<h1>Kaetram AI Agent <span class="badge idle" id="status-badge">LOADING</span></h1>
<div class="topbar">
  <span>Auto-refreshes every 10s</span>
  <a href="/api/state">Raw JSON</a>
  <a href="/api/screenshots">Screenshot API</a>
  <a href="http://{host}:9000" target="_blank">Play Kaetram</a>
</div>

<div class="grid">
  <div class="card">
    <h2>Player Status</h2>
    <div id="player-stats"><div class="no-data">No agent session yet</div></div>
  </div>
  <div class="card">
    <h2>Session Info</h2>
    <div id="session-stats"><div class="no-data">No agent session yet</div></div>
  </div>
  <div class="card full">
    <h2>Agent Screenshots</h2>
    <div class="screenshots" id="screenshots"><div class="no-data">No screenshots yet</div></div>
  </div>
  <div class="card full">
    <h2>Observations</h2>
    <pre id="observations">No observations yet</pre>
  </div>
  <div class="card full">
    <h2>Files</h2>
    <div id="sessions"><div class="no-data">No session files yet</div></div>
  </div>
</div>

<div class="modal" id="lightbox" onclick="this.classList.remove('active')">
  <img id="lightbox-img" src="">
</div>

<script>
function openLightbox(src) {{
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('active');
}}

async function refresh() {{
  // State data
  try {{
    const res = await fetch('/api/state');
    const data = await res.json();

    if (Object.keys(data).length === 0) {{
      document.getElementById('status-badge').className = 'badge idle';
      document.getElementById('status-badge').textContent = 'NO DATA';
    }} else {{
      document.getElementById('status-badge').className = 'badge online';
      document.getElementById('status-badge').textContent = 'LIVE';

      const p = data.player || {{}};
      const c = data.character || {{}};
      document.getElementById('player-stats').innerHTML = `
        <div class="stat"><span class="stat-label">Name</span><span class="stat-value">${{p.name || c.name || data.login?.actual_username || data.actual_guest_name || 'Unknown'}}</span></div>
        <div class="stat"><span class="stat-label">Level</span><span class="stat-value">${{p.level || c.level || '?'}}</span></div>
        <div class="stat"><span class="stat-label">HP</span><span class="stat-value">${{p.hp || c.hp || '?'}} / ${{p.hp_max || c.hp_max || '?'}}</span></div>
        <div class="stat"><span class="stat-label">MP</span><span class="stat-value">${{p.mp || c.mana || '?'}} / ${{p.mp_max || c.mana_max || '?'}}</span></div>
        <div class="stat"><span class="stat-label">Location</span><span class="stat-value">${{data.game_state?.location || data.world?.location || '?'}}</span></div>
      `;

      const s = (typeof data.session === 'object') ? data.session : {{}};
      document.getElementById('session-stats').innerHTML = `
        <div class="stat"><span class="stat-label">Session</span><span class="stat-value">${{s.session || data.session || data.sessions || '?'}}</span></div>
        <div class="stat"><span class="stat-label">Game</span><span class="stat-value">${{s.game || data.game || 'Kaetram'}} ${{s.version || data.version || ''}}</span></div>
        <div class="stat"><span class="stat-label">Login</span><span class="stat-value">${{data.login?.status || data.login_method || '?'}}</span></div>
        <div class="stat"><span class="stat-label">Updated</span><span class="stat-value">${{s.timestamp || data.timestamp || '?'}}</span></div>
        <div class="stat"><span class="stat-label">Events</span><span class="stat-value">${{(data.world?.events_active || data.game_state?.active_events || []).join(', ') || 'none'}}</span></div>
      `;

      // Observations with screenshot descriptions
      const obs = data.observations || {{}};
      document.getElementById('observations').textContent = JSON.stringify(obs, null, 2);
    }}
  }} catch(e) {{
    document.getElementById('status-badge').className = 'badge offline';
    document.getElementById('status-badge').textContent = 'ERROR';
  }}

  // Screenshots
  try {{
    const res2 = await fetch('/api/screenshots');
    const shots = await res2.json();
    if (shots.length > 0) {{
      let html = '';
      for (const s of shots) {{
        html += `<div class="screenshot-card">
          <img src="/screenshots/${{s.name}}" alt="${{s.name}}" onclick="openLightbox(this.src)" loading="lazy">
          <div class="meta">${{s.name}} | ${{s.time}} | ${{Math.round(s.size/1024)}}KB</div>
        </div>`;
      }}
      document.getElementById('screenshots').innerHTML = html;
    }}
  }} catch(e) {{}}

  // Session files
  try {{
    const res3 = await fetch('/api/sessions');
    const sessions = await res3.json();
    let html = '';
    for (const s of sessions.slice(0, 15)) {{
      html += `<div class="log-entry">${{s}}</div>`;
    }}
    document.getElementById('sessions').innerHTML = html || '<div class="no-data">No session files yet</div>';
  }} catch(e) {{}}
}}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def send_json_state(self):
        state_files = sorted(glob.glob(os.path.join(STATE_DIR, "*.json")), key=os.path.getmtime, reverse=True)
        data = {}
        for f in state_files:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    break
            except:
                continue
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def send_sessions(self):
        logs = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True)
        sessions = []
        for log in logs[:10]:
            name = os.path.basename(log)
            size = os.path.getsize(log)
            mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
            sessions.append(f"[{mtime}] {name} ({size} bytes)")
        states = sorted(glob.glob(os.path.join(STATE_DIR, "*.json")), key=os.path.getmtime, reverse=True)
        for s in states[:5]:
            name = os.path.basename(s)
            mtime = datetime.fromtimestamp(os.path.getmtime(s)).strftime("%Y-%m-%d %H:%M:%S")
            sessions.append(f"[{mtime}] {name}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(sorted(sessions, reverse=True)).encode())

    def send_screenshot_list(self):
        images = []
        for ext in ('*.png', '*.jpg', '*.jpeg', '*.webp'):
            images.extend(glob.glob(os.path.join(STATE_DIR, ext)))
        images.sort(key=os.path.getmtime, reverse=True)
        result = []
        for img in images[:20]:
            name = os.path.basename(img)
            mtime = datetime.fromtimestamp(os.path.getmtime(img)).strftime("%Y-%m-%d %H:%M:%S")
            result.append({"name": name, "time": mtime, "size": os.path.getsize(img)})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", 8080), DashboardHandler)
    print(f"Dashboard running at http://0.0.0.0:8080")
    server.serve_forever()
