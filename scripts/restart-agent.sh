#!/usr/bin/env bash
# Restart the multi-agent training run.
#
# What it does:
#   1. Kills the running orchestrator + all claude agent processes
#   2. Kills game server processes (orchestrator restarts them)
#   3. Preserves session logs in dataset/raw/ (training data)
#   4. Clears transient state (screenshots, game_state, progress) per agent sandbox
#   5. Restarts orchestrator in the "datacol" tmux session
#   6. Ensures dashboard is running on :8080
#
# Usage:
#   ./scripts/restart-agent.sh              # 4 agents, 24 hours (defaults)
#   ./scripts/restart-agent.sh 2            # 2 agents, 24 hours
#   ./scripts/restart-agent.sh 4 8          # 4 agents, 8 hours
#   ./scripts/restart-agent.sh 4 0          # 4 agents, no time limit
#   ./scripts/restart-agent.sh --aggressive 1 --methodical 1 --curious 1 --efficient 1
#   ./scripts/restart-agent.sh --aggressive 2 --efficient 2 --hours 0

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Defaults
N_AGENTS=""
HOURS="24"
N_AGGRESSIVE=""
N_METHODICAL=""
N_CURIOUS=""
N_EFFICIENT=""

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --aggressive)  N_AGGRESSIVE="$2"; shift 2;;
    --methodical)  N_METHODICAL="$2"; shift 2;;
    --curious)     N_CURIOUS="$2"; shift 2;;
    --efficient)   N_EFFICIENT="$2"; shift 2;;
    --hours)     HOURS="$2"; shift 2;;
    *)
      # Positional: first=agents, second=hours
      if [ -z "$N_AGENTS" ]; then N_AGENTS="$1"
      else HOURS="$1"; fi
      shift;;
  esac
done

# Determine total agent count for cleanup and orchestrator
HAS_PERSONALITY=false
PERSONALITY_ARGS=""
TOTAL_AGENTS=0
for p in aggressive methodical curious efficient; do
  eval "count=\$N_$(echo $p | tr '[:lower:]' '[:upper:]')"
  if [ -n "$count" ] && [ "$count" -gt 0 ]; then
    HAS_PERSONALITY=true
    PERSONALITY_ARGS="$PERSONALITY_ARGS --$p $count"
    TOTAL_AGENTS=$((TOTAL_AGENTS + count))
  fi
done

if ! $HAS_PERSONALITY; then
  N_AGENTS="${N_AGENTS:-4}"
  TOTAL_AGENTS="$N_AGENTS"
fi

echo "=== Restarting Kaetram training run ==="
if $HAS_PERSONALITY; then
  [ -n "$N_AGGRESSIVE" ] && [ "$N_AGGRESSIVE" -gt 0 ] && echo "  Aggressive:  $N_AGGRESSIVE"
  [ -n "$N_METHODICAL" ] && [ "$N_METHODICAL" -gt 0 ] && echo "  Methodical:  $N_METHODICAL"
  [ -n "$N_CURIOUS" ] && [ "$N_CURIOUS" -gt 0 ] && echo "  Curious:     $N_CURIOUS"
  [ -n "$N_EFFICIENT" ] && [ "$N_EFFICIENT" -gt 0 ] && echo "  Efficient:   $N_EFFICIENT"
  echo "  Total:    $TOTAL_AGENTS"
else
  echo "  Agents: $TOTAL_AGENTS (round-robin personalities)"
fi
echo "  Hours:  ${HOURS}"
echo ""

# ── Step 1: Kill orchestrator + agents ──
echo "Stopping orchestrator and agents..."
# Kill orchestrate.py process specifically (not tmux/shell wrappers)
pkill -f "python3 orchestrate.py" 2>/dev/null || true
sleep 1
# Kill the datacol tmux session (holds shell wrappers)
tmux kill-session -t datacol 2>/dev/null || true
# Kill any remaining claude -p agent processes
pkill -f "claude.*-p.*IMPORTANT.*play the game" 2>/dev/null || true
# Also kill single-agent mode processes
pkill -f "play.sh" 2>/dev/null || true
pkill -f "claude -p.*Login" 2>/dev/null || true
sleep 2

# ── Step 2: Kill game server instances (not the client on 9000) ──
echo "Stopping game servers (preserving client on :9000)..."
for port in $(seq 9001 10 9071); do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "  Killed server on :$port (PID $pid)"
  fi
done
sleep 1

# ── Step 3: Reset MongoDB player data (fresh characters) ──
MONGO_CONTAINER="kaetram-mongo"
MONGO_DB="kaetram_devlopment"
COLLECTIONS=(player_info player_skills player_equipment player_inventory player_bank player_quests player_achievements player_statistics player_abilities)

if docker ps --format '{{.Names}}' | grep -q "^${MONGO_CONTAINER}$"; then
  USER_JS_ARRAY=""
  for i in $(seq 0 $((TOTAL_AGENTS - 1))); do
    [ -n "$USER_JS_ARRAY" ] && USER_JS_ARRAY="${USER_JS_ARRAY},"
    USER_JS_ARRAY="${USER_JS_ARRAY}'claudebot${i}'"
  done

  echo "Resetting player data in MongoDB..."
  for coll in "${COLLECTIONS[@]}"; do
    result=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
      var r = db.'"$coll"'.deleteMany({username: {$in: ['"$USER_JS_ARRAY"']}});
      print(r.deletedCount);
    ' 2>/dev/null)
    [ "$result" != "0" ] && echo "  ${coll}: deleted ${result}"
  done
  echo "  Players will start fresh on login."
else
  echo "WARNING: MongoDB container not running — skipping DB reset"
fi
echo ""

# ── Step 4: Preserve logs, clear transient state ──
echo "Clearing agent sandbox state (logs preserved)..."
for i in $(seq 0 $((TOTAL_AGENTS - 1))); do
  sandbox="/tmp/kaetram_agent_$i/state"
  if [ -d "$sandbox" ]; then
    rm -f "$sandbox/screenshot.png" \
          "$sandbox/live_screen.png" \
          "$sandbox/game_state.json" \
          "$sandbox/progress.json" \
          "$sandbox/.session_counter"
    find "$sandbox" -name "*.png" -delete 2>/dev/null || true
    echo "  Cleared /tmp/kaetram_agent_$i/state/"
  fi
done

# Also clear single-agent state
rm -f "$PROJECT_DIR/state/screenshot.png" \
      "$PROJECT_DIR/state/live_screen.png" \
      "$PROJECT_DIR/state/game_state.json"

# Count preserved logs
LOG_COUNT=$(find "$PROJECT_DIR/dataset/raw" -name "session_*.log" 2>/dev/null | wc -l)
echo "  Preserved $LOG_COUNT session logs in dataset/raw/"
echo ""

# ── Step 5: Ensure Kaetram client is running on :9000 ──
if ! ss -tlnp "sport = :9000" 2>/dev/null | grep -q 9000; then
  echo "WARNING: Kaetram client not running on :9000"
  echo "  Start it first:  ./scripts/start-kaetram.sh"
  echo "  (run in the 'kaetram' tmux session)"
  echo ""
fi

# ── Step 6: Restart dashboard if not running ──
if ! ss -tlnp "sport = :8080" 2>/dev/null | grep -q 8080; then
  echo "Starting dashboard on :8080..."
  cd "$PROJECT_DIR"
  nohup python3 dashboard.py > /tmp/dashboard.log 2>&1 &
  echo "  Dashboard PID: $!"
else
  echo "Dashboard already running on :8080"
fi

# ── Step 7: Launch orchestrator in datacol tmux session ──
echo "Launching orchestrator ($TOTAL_AGENTS agents, $HOURS hours)..."

if $HAS_PERSONALITY; then
  ORCH_CMD="cd $PROJECT_DIR && python3 orchestrate.py $PERSONALITY_ARGS"
else
  ORCH_CMD="cd $PROJECT_DIR && python3 orchestrate.py --agents $N_AGENTS"
fi
if [ "$HOURS" != "0" ]; then
  ORCH_CMD="$ORCH_CMD --hours $HOURS"
fi
ORCH_CMD="$ORCH_CMD 2>&1 | tee /tmp/orchestrate.log"

# Send to existing datacol session, or create one
if tmux has-session -t datacol 2>/dev/null; then
  # Send Ctrl-C first to clear any leftover prompt, then the command
  tmux send-keys -t datacol C-c 2>/dev/null || true
  sleep 0.5
  tmux send-keys -t datacol "$ORCH_CMD" Enter
else
  tmux new-session -d -s datacol -c "$PROJECT_DIR" "$ORCH_CMD"
fi

echo ""
echo "=== Training run restarted ==="
echo "  Orchestrator: tmux attach -t datacol"
echo "  Dashboard:    http://localhost:8080"
echo "  Logs:         $PROJECT_DIR/dataset/raw/agent_*/logs/"
echo ""
echo "  Monitor: tail -f /tmp/orchestrate.log"
