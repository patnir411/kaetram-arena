#!/usr/bin/env bash
# Nuclear kill: destroy ALL agent processes. No questions, no preservation.
set -euo pipefail

echo "=== NUKING all agent processes ==="

# Kill orchestrator
pkill -9 -f "python3 orchestrate.py" 2>/dev/null || true
tmux kill-session -t datacol 2>/dev/null || true

# Kill ALL agent CLI processes (Claude, Codex, Gemini, OpenCode)
pkill -9 -f "claude -p" 2>/dev/null || true
pkill -9 -f "codex.*exec" 2>/dev/null || true
pkill -9 -f "gemini -p" 2>/dev/null || true
pkill -9 -f "opencode run" 2>/dev/null || true
# Kill the bash `timeout` wrappers too (they keep their child alive on SIGTERM)
pkill -9 -f "timeout .* opencode" 2>/dev/null || true

# Kill ALL MCP game servers
pkill -9 -f "mcp_game_server.py" 2>/dev/null || true

# Kill ALL Playwright (every form)
pkill -9 -f "playwright/driver" 2>/dev/null || true
pkill -9 -f "playwright-mcp" 2>/dev/null || true
pkill -9 -f "npm exec @playwright" 2>/dev/null || true

# Kill ALL Chrome headless
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true

# Livestream pipeline: kill per-agent Xvfb (display 99..108) and the
# matching ffmpeg x11grab encoders, then wipe HLS segment dirs.
pkill -9 -f "Xvfb :9[0-9]" 2>/dev/null || true
pkill -9 -f "Xvfb :10[0-9]" 2>/dev/null || true
pkill -9 -f "ffmpeg.*x11grab" 2>/dev/null || true
rm -rf /tmp/hls/agent_* 2>/dev/null || true

# Kill game servers on agent ports (not client :9000)
for port in $(seq 9001 10 9071); do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null || true
done

# Kill play scripts
pkill -9 -f "play.sh" 2>/dev/null || true
pkill -9 -f "play_qwen.py" 2>/dev/null || true
pkill -9 -f "game_driver.py" 2>/dev/null || true

sleep 2

# Report
echo ""
echo "Survivors (should be 0):"
echo "  claude -p: $(pgrep -c -f 'claude -p' 2>/dev/null || echo 0)"
echo "  codex exec: $(pgrep -c -f 'codex.*exec' 2>/dev/null || echo 0)"
echo "  MCP servers: $(pgrep -c -f 'mcp_game_server' 2>/dev/null || echo 0)"
echo "  Playwright: $(pgrep -c -f 'playwright/driver' 2>/dev/null || echo 0)"
echo "  Chrome: $(pgrep -c -f 'chrome-headless-shell' 2>/dev/null || echo 0)"
echo "  Xvfb: $(pgrep -c -f 'Xvfb :' 2>/dev/null || echo 0)"
echo "  ffmpeg x11grab: $(pgrep -c -f 'ffmpeg.*x11grab' 2>/dev/null || echo 0)"
echo ""
echo "State preserved. Use ./scripts/resume-agent.sh to restart."
