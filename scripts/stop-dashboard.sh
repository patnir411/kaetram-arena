#!/usr/bin/env bash
# Stop the dashboard on :8080. Kills only the process bound to
# DASHBOARD_HTTP_PORT (default 8080) — never a bare-name pkill.
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


PORT="${DASHBOARD_HTTP_PORT:-8080}"

# Kill only the process bound to our port (8080) — never a bare-name pkill.
PORT_PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
if [ -n "$PORT_PID" ]; then
  kill "$PORT_PID" 2>/dev/null || true
  for _ in $(seq 1 10); do
    ss -tlnp 2>/dev/null | grep -q ":${PORT} " || break
    sleep 0.3
  done
  ss -tlnp 2>/dev/null | grep -q ":${PORT} " && kill -9 "$PORT_PID" 2>/dev/null || true
fi

# Verify
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
  echo "WARNING: Port ${PORT} still in use" >&2
else
  echo "Dashboard stopped"
fi
