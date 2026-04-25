#!/usr/bin/env bash
# Nuclear kill: destroy ALL data-collection agent processes. State preserved.
# Eval lanes (ports 9061/9071) and the e2e test lane (9191) are deliberately
# spared — see scripts/_kill_helpers.sh for the scoping rules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_kill_helpers.sh
source "$SCRIPT_DIR/_kill_helpers.sh"

echo "=== NUKING data-collection agent processes (eval/test lanes spared) ==="

# Orchestrator + tmux wrapper.
pkill -9 -f "python3 orchestrate.py" 2>/dev/null || true
tmux kill-session -t datacol 2>/dev/null || true

# Agent CLI processes — scoped to data-collection sandboxes.
kill_scoped "claude -p"           KILL
kill_scoped "codex.*exec"         KILL
kill_scoped "gemini -p"           KILL
kill_scoped "opencode run"        KILL
kill_scoped "timeout .* opencode" KILL

# MCP game servers — scoped (eval MCP processes share the same binary).
kill_scoped "mcp_game_server.py"  KILL

# Playwright drivers — scoped.
kill_scoped "playwright/driver"   KILL
kill_scoped "playwright-mcp"      KILL
kill_scoped "npm exec @playwright" KILL

# Chrome — scoped via pgid so renderers/zygotes go too.
kill_scoped_chrome_pgroup KILL

# Livestream pipeline: per-agent Xvfb + ffmpeg are unique to data collection
# (eval lanes don't use them), but still scope by display range to be safe.
# Displays 99..108 map 1:1 to agent slots 0..9.
pkill -9 -f "Xvfb :9[0-9]" 2>/dev/null || true
pkill -9 -f "Xvfb :10[0-9]" 2>/dev/null || true
pkill -9 -f "ffmpeg.*x11grab" 2>/dev/null || true
rm -rf /tmp/hls/agent_* 2>/dev/null || true

# Game servers on data-collection ports only (NEVER 9061/9071/9191).
for port in "${KAETRAM_DATA_PORTS[@]}"; do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
done

# play.sh / play_qwen.py / game_driver.py — scoped.
kill_scoped "play.sh"        KILL
kill_scoped "play_qwen.py"   KILL
kill_scoped "game_driver.py" KILL

sleep 2

# Report
echo ""
echo "Survivors (data-collection only — eval/test pids are excluded):"
remaining() {
  local n=0 pid
  for pid in $(pgrep -f "$1" 2>/dev/null || true); do
    if _ks_is_data_collection "$pid" && ! _ks_holds_protected_port "$pid"; then
      n=$((n+1))
    fi
  done
  echo $n
}
echo "  claude -p:       $(remaining 'claude -p')"
echo "  codex exec:      $(remaining 'codex.*exec')"
echo "  MCP servers:     $(remaining 'mcp_game_server')"
echo "  Playwright:      $(remaining 'playwright/driver')"
echo "  Chrome:          $(remaining 'chrome-headless-shell')"
echo "  Xvfb:            $(pgrep -c -f 'Xvfb :' 2>/dev/null || echo 0)"
echo "  ffmpeg x11grab:  $(pgrep -c -f 'ffmpeg.*x11grab' 2>/dev/null || echo 0)"
echo ""
echo "State preserved. Use ./scripts/resume-agent.sh to restart."
