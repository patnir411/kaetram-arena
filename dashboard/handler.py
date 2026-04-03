"""HTTP request handler for the dashboard.

Routes requests to API endpoints, serves screenshots, and renders the dashboard template.
"""

import http.server
import json
import mimetypes
import os
import time
import urllib.parse

from dashboard.constants import PROJECT_DIR, STATE_DIR, MAX_AGENTS, SCREENSHOT_POLL_INTERVAL
from dashboard.api import APIMixin


def _load_template():
    """Load the HTML template from disk once at import time."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path) as f:
        return f.read()


# Cache template at import — no runtime file reads needed
_TEMPLATE = _load_template()


class DashboardHandler(APIMixin, http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/" or path == "/index.html":
                self.send_dashboard()
            elif path == "/favicon.ico":
                self.send_favicon()
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
            elif path == "/api/qwen-log":
                self.send_qwen_log()
            elif path == "/api/raw":
                which = qs.get("file", [None])[0]
                self.send_raw_file(which, qs)
            elif path == "/report.json":
                self.send_report_json()
            elif path.startswith("/stream/"):
                self.send_mjpeg_stream()
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

    # ── Report JSON (for Claude web fetch) ──

    def send_report_json(self):
        report_path = "/tmp/kaetram-export/report.json"
        # Auto-regenerate if stale (>5 min old) or missing
        try:
            import time as _t
            needs_regen = not os.path.exists(report_path) or (_t.time() - os.path.getmtime(report_path)) > 300
        except Exception:
            needs_regen = True
        if needs_regen:
            try:
                import subprocess
                project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                venv_py = os.path.join(project_dir, ".venv", "bin", "python3")
                script = os.path.join(project_dir, "scripts", "export_report.py")
                subprocess.run([venv_py, script], timeout=60, capture_output=True)
            except Exception:
                pass
        try:
            with open(report_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            fname = "kaetram_report_" + time.strftime("%Y-%m-%d") + ".json"
            self.send_header("Content-Disposition", f"attachment; filename={fname}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Report generation failed.")

    # ── Screenshot serving ──

    @staticmethod
    def _newest_screenshot(state_dir):
        """Return the path of the most recently modified screenshot in a state dir."""
        candidates = []
        for name in ("live_screen.png", "screenshot.png"):
            p = os.path.join(state_dir, name)
            if os.path.isfile(p):
                candidates.append((os.path.getmtime(p), p))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
        return os.path.join(state_dir, "screenshot.png")  # fallback (may 404)

    def send_screenshot_file(self):
        raw = self.path.split("?")[0]
        parts = raw.split("/")

        if len(parts) >= 4 and parts[2].startswith("agent_"):
            idx = parts[2].replace("agent_", "")
            filename = os.path.basename(parts[3])
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return self.send_error(403)
            state_dir = os.path.join("/tmp", f"kaetram_agent_{idx}", "state")
            if filename in ("live_screen.png", "screenshot.png"):
                filepath = self._newest_screenshot(state_dir)
            else:
                filepath = os.path.join(state_dir, filename)
        else:
            filename = os.path.basename(raw)
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return self.send_error(403)
            if filename in ("live_screen.png", "screenshot.png"):
                filepath = self._newest_screenshot(STATE_DIR)
            else:
                filepath = os.path.join(STATE_DIR, filename)

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

    def send_mjpeg_stream(self):
        """Serve MJPEG stream from an agent's live_screen.png."""
        raw = self.path.split("?")[0]
        parts = raw.strip("/").split("/")
        if len(parts) >= 2 and parts[1].startswith("agent_"):
            idx = parts[1].replace("agent_", "")
            ss_path = os.path.join("/tmp", f"kaetram_agent_{idx}", "state", "live_screen.png")
        else:
            ss_path = os.path.join(STATE_DIR, "live_screen.png")

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        last_mtime = 0
        try:
            while True:
                try:
                    if os.path.isfile(ss_path):
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
                except (FileNotFoundError, PermissionError, OSError):
                    pass
                time.sleep(SCREENSHOT_POLL_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_screenshot_list(self):
        import glob
        images = []
        for p in glob.glob(os.path.join(STATE_DIR, "*.png")):
            images.append((None, p))
        for i in range(MAX_AGENTS):
            agent_state = os.path.join("/tmp", f"kaetram_agent_{i}", "state")
            if os.path.isdir(agent_state):
                for p in glob.glob(os.path.join(agent_state, "*.png")):
                    images.append((i, p))
        images.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)
        from datetime import datetime
        result = []
        for agent_id, img in images[:50]:
            name = os.path.basename(img)
            mtime = datetime.fromtimestamp(os.path.getmtime(img)).strftime("%Y-%m-%d %H:%M:%S")
            entry = {"name": name, "time": mtime, "size": os.path.getsize(img)}
            if agent_id is not None:
                entry["agent"] = agent_id
            result.append(entry)
        self._send_json(result)

    # ── State dir resolution ──

    def _resolve_state_dir(self, qs):
        """Return state directory — either default or per-agent sandbox."""
        if qs:
            agent_id = qs.get("agent", [None])[0]
            if agent_id is not None:
                sandbox = os.path.join("/tmp", f"kaetram_agent_{agent_id}", "state")
                if os.path.isdir(sandbox):
                    return sandbox
        return STATE_DIR

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

    # ── Dashboard page ──

    def send_dashboard(self):
        host = self.headers.get('Host', 'localhost:8080')
        game_host = host.split(':')[0]
        html = _TEMPLATE.replace("__GAME_HOST__", game_host)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_favicon(self):
        filepath = os.path.join(PROJECT_DIR, "dashboard_favicon.png")
        if not os.path.isfile(filepath):
            return self.send_error(404)
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
