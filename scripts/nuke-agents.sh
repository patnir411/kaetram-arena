#!/usr/bin/env bash
# Nuclear kill: destroy ALL data-collection agent processes. State preserved.
# Eval lanes (ports 9061/9071) and the e2e test lane (9191) are deliberately
# spared — see scripts/_kill_helpers.sh for the scoping rules.
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
# shellcheck source=scripts/_kill_helpers.sh
source "$SCRIPT_DIR/_kill_helpers.sh"

echo "=== NUKING data-collection agent processes (eval/test lanes spared) ==="

# TERM-then-KILL: send SIGTERM first so Kaetram game servers flush player
# state to Mongo (they only autosave on graceful shutdown) and in-flight model
# SSE streams have a chance to wind down. After a short grace window,
# SIGKILL anything still alive.

# Orchestrator + tmux wrapper — kill orchestrator first so it stops respawning
# children. TERM is enough; the supervisor exits quickly on its own.
pkill -TERM -f "python3 orchestrate.py" 2>/dev/null || true
tmux kill-session -t datacol 2>/dev/null || true

# Disable abort-on-error for the teardown itself — a single non-zero kill must
# never skip the remaining phases (esp. Phase 2 SIGKILL). Stays off through the
# cosmetic report below; the script exits 0 explicitly at the end.
set +e

# ── Phase 1: SIGTERM (graceful) ──
kill_scoped "claude -p"           TERM
kill_scoped "codex.*exec"         TERM
kill_scoped "gemini -p"           TERM
kill_scoped "opencode run"        TERM
kill_scoped "timeout .* opencode" TERM
kill_scoped "mcp_game_server.py"  TERM
kill_scoped "playwright/driver"   TERM
kill_scoped "playwright-mcp"      TERM
kill_scoped "npm exec @playwright" TERM
kill_scoped_chrome_pgroup         TERM
kill_scoped "play.sh"             TERM
kill_scoped "play_qwen.py"        TERM
kill_scoped "game_driver.py"      TERM

# Game servers on data-collection ports — TERM lets them flush autosave.
for port in "${KAETRAM_DATA_PORTS[@]}"; do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
done

# Grace window for autosave + SSE drain. 2.5s is enough for Mongo writes
# without making "stop" feel slow.
sleep 2.5

# ── Phase 2: SIGKILL (forceful — anything that ignored TERM) ──
pkill -9 -f "python3 orchestrate.py" 2>/dev/null || true
kill_scoped "claude -p"           KILL
kill_scoped "codex.*exec"         KILL
kill_scoped "gemini -p"           KILL
kill_scoped "opencode run"        KILL
kill_scoped "timeout .* opencode" KILL
kill_scoped "mcp_game_server.py"  KILL
kill_scoped "playwright/driver"   KILL
kill_scoped "playwright-mcp"      KILL
kill_scoped "npm exec @playwright" KILL
kill_scoped_chrome_pgroup         KILL
kill_scoped "play.sh"             KILL
kill_scoped "play_qwen.py"        KILL
kill_scoped "game_driver.py"      KILL
for port in "${KAETRAM_DATA_PORTS[@]}"; do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
done

# Livestream pipeline — Xvfb/ffmpeg have no state to flush, go straight to KILL.
kill_kaetram_livestream KILL
rm -rf /tmp/hls/agent_* 2>/dev/null || true
# Stay non-strict through the report below — it's cosmetic, and a stray
# non-zero there must not mask a successful teardown with a failing exit code.

sleep 0.5

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
echo "  Xvfb:            $(pgrep -c -f \"$KAETRAM_XVFB_PATTERN\" 2>/dev/null || echo 0)"
echo "  ffmpeg x11grab:  $(pgrep -c -f \"$KAETRAM_FFMPEG_PATTERN\" 2>/dev/null || echo 0)"
echo ""
echo "State preserved. Use ./scripts/resume-agent.sh to restart."
exit 0
