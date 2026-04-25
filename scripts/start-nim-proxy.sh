#!/usr/bin/env bash
# Start the NIM SSE-rewriting proxy on 127.0.0.1:8889 (or $NIM_PROXY_PORT).
#
# Idempotent and concurrency-safe:
#   - If a proxy is already listening on the port, exit 0 (do nothing).
#     This means a sibling orchestrator's proxy is left untouched.
#   - If the PID file points at a dead pid, clean it up and start fresh.
#   - We never `pkill -f scripts/nim_proxy.py` blindly — that footgun used
#     to kill a sibling orchestrator's proxy mid-stream.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${NIM_PROXY_PORT:-8889}"
LOG="/tmp/nim_proxy.log"
PID_FILE="/tmp/nim_proxy.pid"

# Already listening? Treat as success — another caller manages it.
if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
  pid="$(cat "$PID_FILE" 2>/dev/null || echo "?")"
  echo "NIM proxy already listening on 127.0.0.1:$PORT (pid $pid)"
  exit 0
fi

# Stale PID file pointing at a dead pid → clean up and proceed.
if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && ! kill -0 "$old_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
  elif [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    # Process exists but isn't bound to the port yet (e.g. mid-startup).
    # Wait briefly; if it binds, exit 0; otherwise abort to avoid two procs.
    for _ in $(seq 1 10); do
      if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
        echo "NIM proxy bound by existing pid $old_pid on :$PORT"
        exit 0
      fi
      sleep 0.2
    done
    echo "ERROR: PID $old_pid is alive but port :$PORT not bound — refusing to spawn a duplicate." >&2
    echo "  Investigate the existing process or remove $PID_FILE manually." >&2
    exit 1
  fi
fi

cd "$PROJECT_DIR"
NIM_PROXY_PORT="$PORT" nohup "$PROJECT_DIR/.venv/bin/python3" \
  "$PROJECT_DIR/scripts/nim_proxy.py" >> "$LOG" 2>&1 &
new_pid=$!
echo "$new_pid" > "$PID_FILE"

# Wait up to 5s for the port to bind.
for _ in $(seq 1 20); do
  if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
    echo "NIM proxy listening on 127.0.0.1:$PORT (pid $new_pid, log: $LOG)"
    exit 0
  fi
  sleep 0.25
done

# Failed to bind — clean up.
kill "$new_pid" 2>/dev/null || true
rm -f "$PID_FILE"
echo "ERROR: NIM proxy failed to bind on :$PORT — see $LOG" >&2
tail -20 "$LOG" >&2
exit 1
