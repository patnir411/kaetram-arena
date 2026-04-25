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

TEST_PORT="${TEST_PORT:-9191}"
KAETRAM_DIR="$HOME/projects/Kaetram-Open"
SERVER_DIR="$KAETRAM_DIR/packages/server"
LOG_DIR="/tmp/kaetram_test"
LOG_FILE="$LOG_DIR/gameserver_${TEST_PORT}.log"

mkdir -p "$LOG_DIR"

NVM_SH="$HOME/.nvm/nvm.sh"
[ -f "$NVM_SH" ] || NVM_SH="$(brew --prefix nvm 2>/dev/null)/nvm.sh"
# shellcheck disable=SC1090
source "$NVM_SH"
nvm use 20 --silent

if [ ! -f "$SERVER_DIR/dist/main.js" ]; then
  echo "ERROR: $SERVER_DIR/dist/main.js missing — run 'yarn build' in $KAETRAM_DIR first" >&2
  exit 1
fi

if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${TEST_PORT}\$"; then
  echo "ERROR: port ${TEST_PORT} already in use — kill the existing listener or pick another TEST_PORT" >&2
  exit 1
fi

# Kaetram derives apiPort = --port + 1 when API_ENABLED=true (currently false
# in .env.defaults, so dormant — but reserve +1 anyway so a future config flip
# doesn't silently double-bind). See packages/server/src/args.ts:36.
API_PORT=$((TEST_PORT + 1))
if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${API_PORT}\$"; then
  echo "ERROR: api port ${API_PORT} (TEST_PORT+1) already in use — Kaetram reserves it for apiPort" >&2
  exit 1
fi

export NODE_ENV=e2e
export ACCEPT_LICENSE=true
export SKIP_DATABASE=false

cd "$SERVER_DIR"
echo "[start-test-kaetram] starting on :${TEST_PORT} (NODE_ENV=e2e, db=kaetram_e2e)"
echo "[start-test-kaetram] log: $LOG_FILE"
exec node --enable-source-maps dist/main.js --port "$TEST_PORT" 2>&1 | tee -a "$LOG_FILE"
