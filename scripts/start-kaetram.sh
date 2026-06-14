#!/usr/bin/env bash
# Start Kaetram game server (requires Node 20 — uWS.js incompatible with Node 24)
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

NVM_SH="$HOME/.nvm/nvm.sh"
[ -f "$NVM_SH" ] || NVM_SH="$(brew --prefix nvm 2>/dev/null)/nvm.sh"
if [ ! -f "$NVM_SH" ]; then
  echo "ERROR: nvm.sh not found (looked in ~/.nvm and brew). Install nvm first." >&2
  exit 1
fi
# nvm sets a non-zero $? in some helper paths under `set -e`; source defensively.
# shellcheck disable=SC1090
source "$NVM_SH"
# uWS.js (Kaetram's WS layer) only builds on Node 16/18/20 — Node 24/25 crashes
# at runtime. Abort loudly if Node 20 isn't available rather than silently
# starting on an incompatible version.
if ! nvm use 20; then
  echo "ERROR: 'nvm use 20' failed. Kaetram needs Node 20 (uWS.js is incompatible" >&2
  echo "       with Node 24/25). Install it:  nvm install 20" >&2
  exit 1
fi

KAETRAM_DIR="$HOME/projects/Kaetram-Open"
if [ ! -d "$KAETRAM_DIR" ]; then
  echo "ERROR: Kaetram-Open not found at $KAETRAM_DIR" >&2
  exit 1
fi
cd "$KAETRAM_DIR"
export ACCEPT_LICENSE=true
export SKIP_DATABASE=false
exec yarn start
