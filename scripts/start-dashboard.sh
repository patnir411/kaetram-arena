#!/usr/bin/env bash
# Start the dashboard on :8080 (DASHBOARD_HTTP_PORT). Kills only the process
# bound to that port. Idempotent: won't spawn a second if the port is held.
set -euo pipefail

# ── --help / -h guard (auto-injected) ────────────────────────────────────────
for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${DASHBOARD_HTTP_PORT:-8080}"

# Kill only the process bound to our port (8080) — never a bare-name pkill.
PORT_PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
if [ -n "$PORT_PID" ]; then
  kill "$PORT_PID" 2>/dev/null || true
  # Give it a moment to release the port, then SIGKILL if still bound.
  for _ in $(seq 1 10); do
    ss -tlnp 2>/dev/null | grep -q ":${PORT} " || break
    sleep 0.3
  done
  ss -tlnp 2>/dev/null | grep -q ":${PORT} " && kill -9 "$PORT_PID" 2>/dev/null || true
fi

# Wait for port to free (idempotency: a second `start` must not double-spawn).
for _ in $(seq 1 10); do
  ss -tlnp 2>/dev/null | grep -q ":${PORT} " || break
  sleep 1
done
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "ERROR: port ${PORT} still in use after kill — refusing to spawn a second dashboard" >&2
  exit 1
fi

source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
if [ ! -f "$PROJECT_DIR/dashboard.py" ]; then
  echo "ERROR: $PROJECT_DIR/dashboard.py not found" >&2
  exit 1
fi
nohup python3 "$PROJECT_DIR/dashboard.py" > /tmp/dashboard.log 2>&1 &
NEW_PID=$!

# Verify readiness: the port must bind AND the HTTP server must answer, rather
# than assuming the nohup succeeded. Bail early if the process already died.
for _ in $(seq 1 10); do
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    echo "ERROR: dashboard process (pid $NEW_PID) exited during startup. Check /tmp/dashboard.log" >&2
    exit 1
  fi
  if curl -fsS -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    echo "Dashboard running on :${PORT} (pid $NEW_PID)"
    exit 0
  fi
  sleep 1
done

echo "WARNING: Dashboard may not have started (port ${PORT} did not answer). Check /tmp/dashboard.log" >&2
exit 1
