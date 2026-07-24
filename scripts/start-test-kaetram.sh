#!/usr/bin/env bash
# Start the isolated e2e-test Kaetram game server.
#
# Listens on TEST_PORT (default 9191) — separate from the data-collection
# agent ports (9001/9011/9021) so e2e tests can run without nuking the
# datacol session. Loads .env then layers .env.e2e (NODE_ENV=e2e), which
# pins MONGODB_DATABASE=kaetram_e2e for db isolation as well.
#
# Usage:
#   ./scripts/start-test-kaetram.sh                # foreground, port 9191
#   TEST_PORT=9291 ./scripts/start-test-kaetram.sh # custom port (must leave +1 free)
#
# The static client on :9000 (started elsewhere) is reused — the WS URL
# is rewritten per-MCP-subprocess via KAETRAM_PORT (see mcp_server/core.py).

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


TEST_PORT="${TEST_PORT:-9191}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
KAETRAM_DIR="${KAETRAM_GAME_DIR:-$HOME/projects/Kaetram-Open}"
SERVER_DIR="$KAETRAM_DIR/packages/server"
LOG_DIR="/tmp/kaetram_test"
LOG_FILE="$LOG_DIR/gameserver_${TEST_PORT}.log"

mkdir -p "$LOG_DIR"

NODE_BIN="${KAETRAM_NODE_BINARY:-$(command -v node || true)}"
if [ -z "$NODE_BIN" ]; then
  echo "ERROR: Node.js missing — put Node 20 on PATH or set KAETRAM_NODE_BINARY" >&2
  exit 1
fi
NODE_VERSION="$("$NODE_BIN" --version 2>/dev/null || true)"
case "$NODE_VERSION" in
  v20.*) ;;
  *)
    echo "ERROR: Kaetram requires Node 20; $NODE_BIN reported '${NODE_VERSION:-unknown}'" >&2
    exit 1
    ;;
esac

port_is_open() {
  if python3 "$PROJECT_DIR/port_probe.py" --host 127.0.0.1 --port "$1"; then
    return 0
  else
    probe_status=$?
    if [ "$probe_status" -eq 1 ]; then
      return 1
    fi
    echo "ERROR: port probe failed for $1 (exit $probe_status)" >&2
    exit 1
  fi
}

if [ ! -f "$SERVER_DIR/dist/main.js" ]; then
  echo "ERROR: $SERVER_DIR/dist/main.js missing — run 'yarn build' in $KAETRAM_DIR first" >&2
  exit 1
fi

if port_is_open "$TEST_PORT"; then
  echo "ERROR: port ${TEST_PORT} already in use — kill the existing listener or pick another TEST_PORT" >&2
  exit 1
fi

# Kaetram derives apiPort = --port + 1 when API_ENABLED=true (currently false
# in .env.defaults, so dormant — but reserve +1 anyway so a future config flip
# doesn't silently double-bind). See packages/server/src/args.ts:36.
API_PORT=$((TEST_PORT + 1))
if port_is_open "$API_PORT"; then
  echo "ERROR: api port ${API_PORT} (TEST_PORT+1) already in use — Kaetram reserves it for apiPort" >&2
  exit 1
fi

export NODE_ENV=e2e
export ACCEPT_LICENSE=true
export SKIP_DATABASE=false

cd "$SERVER_DIR"
echo "[start-test-kaetram] starting on :${TEST_PORT} (NODE_ENV=e2e, db=kaetram_e2e)"
echo "[start-test-kaetram] log: $LOG_FILE"
exec "$NODE_BIN" --enable-source-maps dist/main.js --port "$TEST_PORT" 2>&1 | tee -a "$LOG_FILE"
