#!/usr/bin/env python3
"""
qwen_dashboard.py — Lightweight livestream dashboard for the Qwen agent.

Serves on port 8082 (separate from main dashboard on 8080).
Shows near-realtime MJPEG stream of the agent's browser + game state overlay + action log.

Usage:
    python3 qwen_dashboard.py
    python3 qwen_dashboard.py --port 8082 --sandbox /tmp/kaetram_agent_4
"""

import argparse
import json
import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8082
DEFAULT_SANDBOX = "/tmp/kaetram_agent_4"

# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Qwen Agent — Live</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0a0a; color: #e0e0e0; font-family: 'JetBrains Mono', 'Fira Code', monospace; overflow: hidden; }

  .container { display: grid; grid-template-columns: 1fr 360px; grid-template-rows: 1fr; height: 100vh; }

  /* Main stream */
  .stream-panel { position: relative; background: #000; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .stream-panel img { max-width: 100%%; max-height: 100%%; object-fit: contain; }
  .no-signal { color: #555; font-size: 24px; position: absolute; }

  /* Overlay bar */
  .overlay { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.85);
    padding: 10px 16px; display: flex; gap: 20px; align-items: center; font-size: 13px; z-index: 10; }
  .overlay .stat { display: flex; align-items: center; gap: 6px; }
  .overlay .label { color: #888; }
  .overlay .value { color: #4fc3f7; font-weight: bold; }
  .overlay .hp-bar { width: 120px; height: 10px; background: #333; border-radius: 5px; overflow: hidden; }
  .overlay .hp-fill { height: 100%%; background: #4caf50; transition: width 0.3s; }
  .overlay .hp-fill.low { background: #f44336; }
  .overlay .hp-fill.mid { background: #ff9800; }
  .model-badge { background: #7c4dff; color: #fff; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: bold; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%%; display: inline-block; }
  .status-dot.live { background: #4caf50; animation: pulse 1.5s infinite; }
  .status-dot.stale { background: #f44336; }
  @keyframes pulse { 0%%,100%% { opacity: 1; } 50%% { opacity: 0.4; } }

  /* Side panel */
  .side-panel { background: #111; border-left: 1px solid #222; display: flex; flex-direction: column; overflow: hidden; }
  .panel-header { padding: 12px 16px; border-bottom: 1px solid #222; font-size: 14px; font-weight: bold; color: #7c4dff; }
  .thinking-panel { padding: 12px 16px; border-bottom: 1px solid #222; max-height: 200px; overflow-y: auto; }
  .thinking-panel .think-label { color: #888; font-size: 11px; margin-bottom: 4px; }
  .thinking-panel .think-text { color: #ccc; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }

  .action-log { flex: 1; overflow-y: auto; padding: 8px 0; }
  .action-entry { padding: 6px 16px; border-bottom: 1px solid #1a1a1a; font-size: 12px; }
  .action-entry .turn-num { color: #555; margin-right: 8px; }
  .action-entry .action-text { color: #81c784; }
  .action-entry .action-text.memory { color: #ce93d8; }
  .action-entry .action-text.heal { color: #ef5350; }
  .action-entry .action-text.warp { color: #4fc3f7; }
  .action-entry .pos { color: #666; font-size: 11px; }
  .action-entry .result { color: #555; font-size: 11px; }

  .memory-panel { padding: 12px 16px; border-top: 1px solid #222; max-height: 150px; overflow-y: auto; }
  .memory-panel .mem-label { color: #888; font-size: 11px; margin-bottom: 4px; }
  .memory-panel .mem-text { color: #aaa; font-size: 11px; line-height: 1.4; white-space: pre-wrap; }
</style>
</head>
<body>
<div class="container">
  <!-- Stream -->
  <div class="stream-panel">
    <img id="stream" src="/stream" alt="Game stream">
    <div class="no-signal" id="no-signal" style="display:none;">NO SIGNAL</div>
    <div class="overlay" id="overlay">
      <span class="status-dot live" id="status-dot"></span>
      <span class="model-badge">QWEN 3.5-9B</span>
      <div class="stat"><span class="label">HP</span>
        <div class="hp-bar"><div class="hp-fill" id="hp-fill" style="width:100%%"></div></div>
        <span class="value" id="hp-text">?/?</span>
      </div>
      <div class="stat"><span class="label">LVL</span><span class="value" id="level">?</span></div>
      <div class="stat"><span class="label">POS</span><span class="value" id="position">?,?</span></div>
      <div class="stat"><span class="label">TURN</span><span class="value" id="turn">0</span></div>
    </div>
  </div>

  <!-- Side panel -->
  <div class="side-panel">
    <div class="panel-header">Qwen Agent — Live Feed</div>
    <div class="thinking-panel" id="thinking-panel">
      <div class="think-label">THINKING</div>
      <div class="think-text" id="think-text">Waiting for agent...</div>
    </div>
    <div class="action-log" id="action-log"></div>
    <div class="memory-panel" id="memory-panel">
      <div class="mem-label">MEMORY (progress.json)</div>
      <div class="mem-text" id="mem-text">No memory yet</div>
    </div>
  </div>
</div>

<script>
const LOG_MAX = 100;

async function pollState() {
  try {
    const res = await fetch('/state');
    if (!res.ok) return;
    const data = await res.json();

    // Update overlay
    if (data.game_state) {
      const gs = data.game_state;
      const ps = gs.player_stats || {};
      const pp = gs.player_position || {};
      const hp = ps.hp || 0, maxHp = ps.max_hp || 1;
      const pct = Math.round(hp / maxHp * 100);

      document.getElementById('hp-text').textContent = hp + '/' + maxHp;
      const fill = document.getElementById('hp-fill');
      fill.style.width = pct + '%%';
      fill.className = 'hp-fill' + (pct < 30 ? ' low' : pct < 60 ? ' mid' : '');
      document.getElementById('level').textContent = ps.level || '?';
      document.getElementById('position').textContent = (pp.x||'?') + ',' + (pp.y||'?');
    }

    // Status dot
    const dot = document.getElementById('status-dot');
    dot.className = 'status-dot ' + (data.screenshot_age < 30 ? 'live' : 'stale');

    // Turn count
    if (data.turn !== undefined) {
      document.getElementById('turn').textContent = data.turn;
    }

    // Thinking
    if (data.reasoning) {
      document.getElementById('think-text').textContent = data.reasoning.slice(0, 500);
    }

    // Action log
    if (data.actions && data.actions.length > 0) {
      const log = document.getElementById('action-log');
      // Only add new actions
      const current = log.children.length;
      for (let i = current; i < data.actions.length; i++) {
        const a = data.actions[i];
        const div = document.createElement('div');
        div.className = 'action-entry';
        let cls = 'action-text';
        if (a.action && a.action.includes('memory')) cls += ' memory';
        else if (a.action && a.action.includes('heal')) cls += ' heal';
        else if (a.action && a.action.includes('warp')) cls += ' warp';
        div.innerHTML = '<span class="turn-num">#' + a.turn + '</span>'
          + '<span class="' + cls + '">' + (a.action||'?') + '</span>'
          + (a.pos ? ' <span class="pos">(' + a.pos + ')</span>' : '')
          + (a.result ? ' <span class="result">' + a.result + '</span>' : '');
        log.appendChild(div);
      }
      // Auto-scroll
      log.scrollTop = log.scrollHeight;
      // Trim old entries
      while (log.children.length > LOG_MAX) log.removeChild(log.firstChild);
    }

    // Memory
    if (data.memory) {
      const m = data.memory;
      let text = 'Level: ' + (m.level||'?') + ' | Sessions: ' + (m.sessions||0);
      if (m.next_objective) text += '\\nObjective: ' + m.next_objective;
      if (m.notes) text += '\\nNotes: ' + m.notes;
      if (m.active_quests && m.active_quests.length) text += '\\nQuests: ' + JSON.stringify(m.active_quests);
      document.getElementById('mem-text').textContent = text;
    }

  } catch(e) {}
}

// Poll state every 1s
setInterval(pollState, 1000);
pollState();

// Handle stream errors (show no-signal)
const img = document.getElementById('stream');
img.onerror = () => {
  document.getElementById('no-signal').style.display = 'block';
  img.style.display = 'none';
  setTimeout(() => { img.src = '/stream?' + Date.now(); img.style.display = 'block'; }, 3000);
};
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class QwenDashboardHandler(BaseHTTPRequestHandler):
    sandbox = DEFAULT_SANDBOX

    def log_message(self, format, *args):
        pass  # Suppress default access logs

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/stream":
            self._serve_mjpeg_stream()
        elif self.path == "/state":
            self._serve_state()
        elif self.path == "/screenshot":
            self._serve_screenshot()
        else:
            self.send_error(404)

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def _serve_mjpeg_stream(self):
        """MJPEG stream — pushes new frames whenever the screenshot file changes."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

        ss_path = os.path.join(self.sandbox, "state", "live_screen.png")
        last_mtime = 0

        try:
            while True:
                try:
                    if os.path.exists(ss_path):
                        mtime = os.path.getmtime(ss_path)
                        if mtime != last_mtime:
                            with open(ss_path, "rb") as f:
                                frame = f.read()
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/png\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n".encode())
                            self.wfile.write(b"\r\n")
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                            last_mtime = mtime
                except (FileNotFoundError, PermissionError):
                    pass
                time.sleep(0.3)  # ~3 FPS check rate
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_screenshot(self):
        """Single screenshot (fallback for non-MJPEG clients)."""
        ss_path = os.path.join(self.sandbox, "state", "live_screen.png")
        if os.path.exists(ss_path):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(ss_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "No screenshot yet")

    def _serve_state(self):
        """JSON endpoint with game state, actions, memory, and metadata."""
        state_dir = os.path.join(self.sandbox, "state")
        log_dir = os.path.join(self.sandbox, "logs")

        result = {
            "screenshot_age": 9999,
            "turn": 0,
            "reasoning": "",
            "actions": [],
            "game_state": {},
            "memory": {},
        }

        # Game state
        gs_path = os.path.join(state_dir, "game_state.json")
        if os.path.exists(gs_path):
            try:
                with open(gs_path) as f:
                    result["game_state"] = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass

        # Screenshot age
        ss_path = os.path.join(state_dir, "live_screen.png")
        if os.path.exists(ss_path):
            result["screenshot_age"] = time.time() - os.path.getmtime(ss_path)

        # Memory
        mem_path = os.path.join(state_dir, "progress.json")
        if os.path.exists(mem_path):
            try:
                with open(mem_path) as f:
                    result["memory"] = json.load(f)
            except (json.JSONDecodeError, ValueError):
                pass

        # Parse latest log file for actions and reasoning
        if os.path.isdir(log_dir):
            logs = sorted(Path(log_dir).glob("*.log"), key=os.path.getmtime)
            if logs:
                latest_log = logs[-1]
                actions = []
                reasoning = ""
                try:
                    with open(latest_log) as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                turn = entry.get("turn", 0)
                                action = entry.get("action", "")
                                pos = entry.get("player_position", {})
                                pos_str = f"{pos.get('x','?')},{pos.get('y','?')}" if pos else ""
                                actions.append({
                                    "turn": turn,
                                    "action": action,
                                    "pos": pos_str,
                                    "result": entry.get("result", ""),
                                })
                                if entry.get("reasoning"):
                                    reasoning = entry["reasoning"]
                                result["turn"] = turn
                            except json.JSONDecodeError:
                                continue
                except (FileNotFoundError, PermissionError):
                    pass
                result["actions"] = actions[-100:]  # Last 100
                result["reasoning"] = reasoning

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a separate thread (needed for MJPEG streaming)."""
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    parser = argparse.ArgumentParser(description="Qwen Agent Livestream Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("--sandbox", default=DEFAULT_SANDBOX, help=f"Agent sandbox dir (default: {DEFAULT_SANDBOX})")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    args = parser.parse_args()

    # Ensure sandbox exists
    os.makedirs(os.path.join(args.sandbox, "state"), exist_ok=True)
    os.makedirs(os.path.join(args.sandbox, "logs"), exist_ok=True)

    QwenDashboardHandler.sandbox = args.sandbox

    server = ThreadedHTTPServer((args.host, args.port), QwenDashboardHandler)
    print(f"Qwen Dashboard: http://{args.host}:{args.port}")
    print(f"Sandbox: {args.sandbox}")
    print(f"Stream: http://{args.host}:{args.port}/stream")
    print(f"State API: http://{args.host}:{args.port}/state")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
