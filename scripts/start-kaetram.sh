#!/usr/bin/env bash
# Start Kaetram game server (requires Node 20 — uWS.js incompatible with Node 24)
set -euo pipefail
source ~/.nvm/nvm.sh
nvm use 20
cd ~/projects/Kaetram-Open
export ACCEPT_LICENSE=true
export SKIP_DATABASE=true
yarn start
