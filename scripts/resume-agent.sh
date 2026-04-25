#!/usr/bin/env bash
# Resume the multi-agent training run from where it was stopped.
#
# What it does:
#   1. Detects how many agents have preserved state in /tmp/kaetram_agent_*/
#   2. Ensures Kaetram client is running on :9000
#   3. Starts dashboard if not running
#   4. Launches orchestrate.py (which reads .session_counter)
#
# Usage:
#   ./scripts/resume-agent.sh                                    # resume all agents (default mode)
#   ./scripts/resume-agent.sh --grinder 1 --completionist 1 --explorer 1
#   ./scripts/resume-agent.sh --hours 8                          # resume with time limit

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_kill_helpers.sh
source "$SCRIPT_DIR/_kill_helpers.sh"

# Parse args (same flags as restart-agent.sh)
N_GRINDER=""
N_COMPLETIONIST=""
N_EXPLORER_TINKERER=""
HOURS=""
N_CLAUDE=""
N_CODEX=""
N_GEMINI=""
N_OPENCODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --grinder)            N_GRINDER="$2"; shift 2;;
    --completionist)      N_COMPLETIONIST="$2"; shift 2;;
    --explorer-tinkerer|--explorer)  N_EXPLORER_TINKERER="$2"; shift 2;;
    --hours)       HOURS="$2"; shift 2;;
    --claude)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_CLAUDE="$2"; shift 2
      else
        N_CLAUDE="-1"; shift
      fi
      ;;
    --codex)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_CODEX="$2"; shift 2
      else
        N_CODEX="-1"; shift
      fi
      ;;
    --gemini)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_GEMINI="$2"; shift 2
      else
        N_GEMINI="-1"; shift
      fi
      ;;
    --opencode)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_OPENCODE="$2"; shift 2
      else
        N_OPENCODE="-1"; shift
      fi
      ;;
    *) shift;;
  esac
done

# ── Step 1: Check if orchestrator is already running ──
if pgrep -f "python3 orchestrate.py" > /dev/null 2>&1; then
  echo "ERROR: Orchestrator is already running (PID $(pgrep -f 'python3 orchestrate.py'))."
  echo "  Stop it first: ./scripts/nuke-agents.sh"
  exit 1
fi

# ── Step 1b: Clean up orphaned processes from previous runs ──
# All scoped to data-collection sandboxes — eval/test lanes are spared.
kill_scoped "claude -p"            TERM
kill_scoped "codex.*exec"          TERM
kill_scoped "gemini.*-p"           TERM
kill_scoped "opencode run"         TERM
kill_scoped "timeout .* opencode"  TERM
kill_scoped "play.sh"              TERM
kill_scoped "play_qwen.py"         TERM
sleep 2
kill_scoped "claude -p"            KILL
kill_scoped "codex.*exec"          KILL
kill_scoped "gemini.*-p"           KILL
# MCP + Playwright + game_driver — scoped.
kill_scoped "mcp_game_server.py"     TERM
kill_scoped "playwright/driver/node" TERM
kill_scoped "npm exec @playwright"   TERM
kill_scoped "playwright-mcp"         TERM
kill_scoped "game_driver.py"         TERM
kill_scoped_chrome_pgroup TERM
sleep 1
kill_scoped "mcp_game_server.py"     KILL
kill_scoped "playwright/driver/node" KILL
kill_scoped "npm exec @playwright"   KILL
kill_scoped "playwright-mcp"         KILL
kill_scoped_chrome_pgroup KILL
# Stale game servers on data-collection ports only (never 9061/9071/9191).
for port in "${KAETRAM_DATA_PORTS[@]}"; do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
  fi
done

# ── Step 2: Detect agents with preserved state ──
DETECTED=0
for i in 0 1 2 3 4 5 6 7; do
  if [ -f "/tmp/kaetram_agent_$i/state/.session_counter" ]; then
    DETECTED=$((DETECTED + 1))
  fi
done

if [ "$DETECTED" -eq 0 ]; then
  echo "ERROR: No preserved agent state found in /tmp/kaetram_agent_*/."
  echo "  Nothing to resume. Use ./scripts/restart-agent.sh to start fresh."
  exit 1
fi

# Determine agent count from personality flags or detected state
HAS_PERSONALITY=false
PERSONALITY_ARGS=""
PERSONALITY_TOTAL=0
declare -A _PERSONALITY_FLAGS=(
  [grinder]=N_GRINDER
  [completionist]=N_COMPLETIONIST
  [explorer-tinkerer]=N_EXPLORER_TINKERER
)
for p in grinder completionist explorer-tinkerer; do
  var="${_PERSONALITY_FLAGS[$p]}"
  count="${!var}"
  if [ -n "$count" ] && [ "$count" -gt 0 ]; then
    HAS_PERSONALITY=true
    PERSONALITY_ARGS="$PERSONALITY_ARGS --$p $count"
    PERSONALITY_TOTAL=$((PERSONALITY_TOTAL + count))
  fi
done

if $HAS_PERSONALITY; then
  N_AGENTS="$PERSONALITY_TOTAL"
else
  N_AGENTS="$DETECTED"
fi

echo "=== Resuming Kaetram training run ==="
if $HAS_PERSONALITY; then
  [ -n "$N_GRINDER" ] && [ "$N_GRINDER" -gt 0 ] && echo "  Grinder:            $N_GRINDER"
  [ -n "$N_COMPLETIONIST" ] && [ "$N_COMPLETIONIST" -gt 0 ] && echo "  Completionist:      $N_COMPLETIONIST"
  [ -n "$N_EXPLORER_TINKERER" ] && [ "$N_EXPLORER_TINKERER" -gt 0 ] && echo "  Explorer/Tinkerer:  $N_EXPLORER_TINKERER"
  echo "  Total:       $N_AGENTS"
else
  echo "  Agents to resume: $N_AGENTS (detected $DETECTED with state)"
fi
echo ""

# Show what we're resuming
for i in $(seq 0 $((N_AGENTS - 1))); do
  SANDBOX="/tmp/kaetram_agent_$i/state"
  COUNTER="$SANDBOX/.session_counter"
  if [ -f "$COUNTER" ]; then
    SESSION=$(cat "$COUNTER" 2>/dev/null || echo "0")
    echo "  Agent $i: resuming from session #$SESSION"
  else
    echo "  Agent $i: no state (will start fresh)"
  fi
done
echo ""

# ── Step 3: Ensure Kaetram client is running on :9000 ──
if ! ss -tlnp "sport = :9000" 2>/dev/null | grep -q 9000; then
  echo "WARNING: Kaetram client not running on :9000"
  echo "  Start it first:  ./scripts/start-kaetram.sh"
  echo "  (run in the 'kaetram' tmux session)"
  echo ""
fi

# ── Step 4: Start dashboard if not running ──
if ! ss -tlnp "sport = :8080" 2>/dev/null | grep -q 8080; then
  echo "Starting dashboard on :8080..."
  cd "$PROJECT_DIR"
  nohup .venv/bin/python3 dashboard.py > /tmp/dashboard.log 2>&1 &
  echo "  Dashboard PID: $!"
else
  echo "Dashboard already running on :8080"
fi

# ── Step 5: Launch orchestrator in datacol tmux session ──
# Build orchestrator command with personality flags
ORCH_ARGS=""
if $HAS_PERSONALITY; then
  ORCH_ARGS="$PERSONALITY_ARGS"
  echo "Launching orchestrator ($N_AGENTS agents with personalities, ${HOURS:-no} time limit)..."
else
  ORCH_ARGS="--agents $N_AGENTS"
  echo "Launching orchestrator ($N_AGENTS agents round-robin, ${HOURS:-no} time limit)..."
fi
if [ -n "$HOURS" ]; then
  ORCH_ARGS="$ORCH_ARGS --hours $HOURS"
fi
[ -n "$N_CLAUDE" ] && ORCH_ARGS="$ORCH_ARGS --claude $N_CLAUDE"
[ -n "$N_CODEX" ] && ORCH_ARGS="$ORCH_ARGS --codex $N_CODEX"
[ -n "$N_GEMINI" ] && ORCH_ARGS="$ORCH_ARGS --gemini $N_GEMINI"
[ -n "$N_OPENCODE" ] && ORCH_ARGS="$ORCH_ARGS --opencode $N_OPENCODE"

ORCH_CMD="cd $PROJECT_DIR && python3 orchestrate.py $ORCH_ARGS 2>&1 | tee /tmp/orchestrate.log"

if tmux has-session -t datacol 2>/dev/null; then
  tmux send-keys -t datacol C-c 2>/dev/null || true
  sleep 0.5
  tmux send-keys -t datacol "$ORCH_CMD" Enter
else
  tmux new-session -d -s datacol -c "$PROJECT_DIR" "$ORCH_CMD"
fi

echo ""
echo "=== Training run resumed ==="
echo "  Orchestrator: tmux attach -t datacol"
echo "  Dashboard:    http://localhost:8080"
echo "  Logs:         $PROJECT_DIR/dataset/raw/agent_*/logs/"
echo ""
echo "  Monitor: tail -f /tmp/orchestrate.log"
