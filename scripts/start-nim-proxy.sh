#!/usr/bin/env bash
# Start (or restart) the NIM SSE-rewriting proxy on 127.0.0.1:8889.
# Daemonized; logs to /tmp/nim_proxy.log.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${NIM_PROXY_PORT:-8889}"
LOG="/tmp/nim_proxy.log"
PID_FILE="/tmp/nim_proxy.pid"

# Kill any previous instance on the same port.
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  sleep 1
fi
pkill -f "scripts/nim_proxy.py" 2>/dev/null || true
sleep 0.5

cd "$PROJECT_DIR"
NIM_PROXY_PORT="$PORT" nohup "$PROJECT_DIR/.venv/bin/python3" \
  "$PROJECT_DIR/scripts/nim_proxy.py" >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"

# Wait up to 5s for the port to bind.
for _ in $(seq 1 20); do
  if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
    echo "NIM proxy listening on 127.0.0.1:$PORT (pid $(cat $PID_FILE), log: $LOG)"
    exit 0
  fi
  sleep 0.25
done

echo "ERROR: NIM proxy failed to bind on :$PORT — see $LOG" >&2
tail -20 "$LOG" >&2
exit 1
