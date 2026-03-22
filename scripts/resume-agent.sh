#!/usr/bin/env bash
# Resume the multi-agent training run from where it was stopped.
#
# What it does:
#   1. Detects how many agents have preserved state in /tmp/kaetram_agent_*/
#   2. Ensures Kaetram client is running on :9000
#   3. Starts dashboard if not running
#   4. Launches orchestrate.py (which reads .session_counter + progress.json)
#
# Usage:
#   ./scripts/resume-agent.sh                                    # resume all agents (default mode)
#   ./scripts/resume-agent.sh --aggressive 1 --methodical 1 --curious 1 --efficient 1
#   ./scripts/resume-agent.sh --hours 8                          # resume with time limit

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Parse args (same flags as restart-agent.sh)
N_AGGRESSIVE=""
N_METHODICAL=""
N_CURIOUS=""
N_EFFICIENT=""
HOURS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --aggressive)  N_AGGRESSIVE="$2"; shift 2;;
    --methodical)  N_METHODICAL="$2"; shift 2;;
    --curious)     N_CURIOUS="$2"; shift 2;;
    --efficient)   N_EFFICIENT="$2"; shift 2;;
    --hours)     HOURS="$2"; shift 2;;
    *) shift;;
  esac
done

# ── Step 1: Check if orchestrator is already running ──
if pgrep -f "python3 orchestrate.py" > /dev/null 2>&1; then
  echo "ERROR: Orchestrator is already running (PID $(pgrep -f 'python3 orchestrate.py'))."
  echo "  Stop it first: ./scripts/stop-agent.sh"
  exit 1
fi

# ── Step 2: Detect agents with preserved state ──
DETECTED=0
for i in 0 1 2 3 4 5 6 7; do
  if [ -f "/tmp/kaetram_agent_$i/state/progress.json" ]; then
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
for p in aggressive methodical curious efficient; do
  eval "count=\$N_$(echo $p | tr '[:lower:]' '[:upper:]')"
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
  [ -n "$N_WARRIOR" ] && [ "$N_WARRIOR" -gt 0 ] && echo "  Warrior:  $N_WARRIOR"
  [ -n "$N_GATHERER" ] && [ "$N_GATHERER" -gt 0 ] && echo "  Gatherer: $N_GATHERER"
  [ -n "$N_EXPLORER" ] && [ "$N_EXPLORER" -gt 0 ] && echo "  Explorer: $N_EXPLORER"
  [ -n "$N_QUESTER" ] && [ "$N_QUESTER" -gt 0 ] && echo "  Quester:  $N_QUESTER"
  echo "  Total:     $N_AGENTS"
else
  echo "  Agents to resume: $N_AGENTS (detected $DETECTED with state)"
fi
echo ""

# Show what we're resuming
for i in $(seq 0 $((N_AGENTS - 1))); do
  SANDBOX="/tmp/kaetram_agent_$i/state"
  PROGRESS="$SANDBOX/progress.json"
  COUNTER="$SANDBOX/.session_counter"
  if [ -f "$PROGRESS" ]; then
    SESSION=$(cat "$COUNTER" 2>/dev/null || echo "0")
    LEVEL=$(python3 -c "import json; print(json.load(open('$PROGRESS')).get('level', '?'))" 2>/dev/null || echo "?")
    echo "  Agent $i: resuming from session #$SESSION, level $LEVEL"
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
  nohup python3 dashboard.py > /tmp/dashboard.log 2>&1 &
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
