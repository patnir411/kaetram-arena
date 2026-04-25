#!/usr/bin/env bash
# Run eval harness for base + r9-sft in parallel.
# Each model gets its own game server, username, and sandbox.
#
# Usage:
#   ./scripts/run-eval.sh                    # 3 episodes, scenario D
#   ./scripts/run-eval.sh --episodes 5       # 5 episodes
#   ./scripts/run-eval.sh --scenario A       # Rat Grind (100 turns)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

EPISODES=3
SCENARIO=D
PERSONALITY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --episodes)     EPISODES="$2"; shift 2;;
    --scenario)     SCENARIO="$2"; shift 2;;
    --personality)  PERSONALITY="$2"; shift 2;;
    *)              shift;;
  esac
done

PERS_FLAG=""
[ -n "$PERSONALITY" ] && PERS_FLAG="--personality $PERSONALITY"

# ── Run directory (timestamped, preserves history) ──
RUN_TAG="$(date +%Y%m%d_%H%M%S)"
[ -n "$PERSONALITY" ] && RUN_TAG="${RUN_TAG}_${PERSONALITY}"
RUN_DIR="$PROJECT_DIR/dataset/eval/runs/${RUN_TAG}"
mkdir -p "$RUN_DIR"

# ── Cleanup ──
echo "Cleaning up previous eval runs..."
pkill -9 -f "eval_harness" 2>/dev/null || true
pkill -9 -f "play_qwen" 2>/dev/null || true
pkill -9 -f "mcp_game_server" 2>/dev/null || true
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true
pkill -9 -f "playwright/driver" 2>/dev/null || true
BASE_GS_PID=$(ss -tlnp 2>/dev/null | grep ":9071" | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "$BASE_GS_PID" ] && kill -9 "$BASE_GS_PID" 2>/dev/null || true
sleep 2

# Clean eval sandboxes (temp data only — results are preserved in runs/)
rm -rf /tmp/kaetram_eval_*

# Reset eval player data in MongoDB
source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
python3 -c "
from pymongo import MongoClient
c = MongoClient('localhost', 27017)
db = c['kaetram_devlopment']
for username in ['evalbotsft', 'evalbotbase']:
    for col in ['player_info','player_skills','player_equipment','player_inventory','player_bank','player_quests','player_achievements','player_statistics','player_abilities']:
        db[col].delete_many({'username': username})
print('  Eval player data cleared')
"

# ── Ensure game servers ──
# Port 9061 (r9-sft eval — distinct from agent_0-5 ports).
# NODE_ENV=eval pins MONGODB_DATABASE=kaetram_eval (see Kaetram-Open/.env.eval)
# so eval rows don't interleave with the data-collection ClaudeBot* rows in
# kaetram_devlopment. dotenv-extended layers .env.{NODE_ENV} on top of .env.
if ! ss -tlnp 2>/dev/null | grep -q ":9061 "; then
  echo "Starting game server on port 9061 (r9-sft eval, db=kaetram_eval)..."
  (source "$HOME/.nvm/nvm.sh" && nvm use 20 --silent && cd ~/projects/Kaetram-Open/packages/server && \
   NODE_ENV=eval ACCEPT_LICENSE=true SKIP_DATABASE=false exec node --enable-source-maps dist/main.js --port 9061) &
  for i in $(seq 1 30); do ss -tlnp 2>/dev/null | grep -q ":9061 " && break; sleep 1; done
fi

# Port 9071 (base eval — distinct from agent_0-5 ports). Same NODE_ENV=eval lane.
if ! ss -tlnp 2>/dev/null | grep -q ":9071 "; then
  echo "Starting game server on port 9071 (base eval, db=kaetram_eval)..."
  (source "$HOME/.nvm/nvm.sh" && nvm use 20 --silent && cd ~/projects/Kaetram-Open/packages/server && \
   NODE_ENV=eval ACCEPT_LICENSE=true SKIP_DATABASE=false exec node --enable-source-maps dist/main.js --port 9071) &
  for i in $(seq 1 60); do
    if ss -tlnp 2>/dev/null | grep -q ":9071 "; then
      echo "  Game server ready on 9071 (${i}s)"
      break
    fi
    sleep 1
  done
fi

# ── Ensure dashboard ──
if ! pgrep -f "python3 dashboard.py" > /dev/null 2>&1; then
  "$SCRIPT_DIR/start-dashboard.sh"
fi

# ── Launch evals in parallel ──
echo ""
echo "Starting eval: $EPISODES episodes × 2 models, scenario $SCENARIO"
echo "  Run dir: $RUN_DIR"
echo ""

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "r9-sft=https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1" \
  --episodes "$EPISODES" --scenario "$SCENARIO" \
  --username evalbotSFT --server-port 9061 --output-dir "$RUN_DIR" $PERS_FLAG \
  > /tmp/eval_r9sft.log 2>&1 &
SFT_PID=$!
echo "  r9-SFT eval started (PID $SFT_PID, log: /tmp/eval_r9sft.log, personality: ${PERSONALITY:-none})"

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "base=https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1" \
  --episodes "$EPISODES" --scenario "$SCENARIO" \
  --username evalbotBase --server-port 9071 --output-dir "$RUN_DIR" $PERS_FLAG \
  > /tmp/eval_base.log 2>&1 &
BASE_PID=$!
echo "  Base eval started (PID $BASE_PID, log: /tmp/eval_base.log, personality: ${PERSONALITY:-none})"

# ── Watchdog (background) ──
PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/scripts/eval_watchdog.py" \
  --run-dir "$RUN_DIR" \
  --episodes "$EPISODES" \
  --kill-on-failure \
  --model "r9-sft=https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1,/tmp/kaetram_eval_r9-sft,9061" \
  --model "base=https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1,/tmp/kaetram_eval_base,9071" \
  > "/tmp/eval_watchdog_${RUN_TAG}.log" 2>&1 &
WATCHDOG_PID=$!
echo "  Watchdog started (PID $WATCHDOG_PID, log: /tmp/eval_watchdog_${RUN_TAG}.log)"

# Symlink latest for dashboard
ln -sfn "$RUN_DIR" "$PROJECT_DIR/dataset/eval/latest"

echo ""
echo "Both evals running in parallel."
echo "  Dashboard: http://localhost:8080 (Eval tab — live side-by-side + metrics)"
echo "  Logs: tail -f /tmp/eval_r9sft.log"
echo "        tail -f /tmp/eval_base.log"
echo "        tail -f /tmp/eval_watchdog_${RUN_TAG}.log"
echo ""
echo "Stop: pkill -f eval_harness"

# ── Monitor loop ──
while kill -0 $SFT_PID 2>/dev/null || kill -0 $BASE_PID 2>/dev/null; do
  sleep 30
  SFT_STATUS="running"
  BASE_STATUS="running"
  kill -0 $SFT_PID 2>/dev/null || SFT_STATUS="done (rc=$(wait $SFT_PID 2>/dev/null; echo $?))"
  kill -0 $BASE_PID 2>/dev/null || BASE_STATUS="done (rc=$(wait $BASE_PID 2>/dev/null; echo $?))"

  SFT_EP=0; BASE_EP=0
  [ -f "$RUN_DIR/r9-sft/results.json" ] && SFT_EP=$(python3 -c "import json; print(len([e for e in json.load(open('$RUN_DIR/r9-sft/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)
  [ -f "$RUN_DIR/base/results.json" ] && BASE_EP=$(python3 -c "import json; print(len([e for e in json.load(open('$RUN_DIR/base/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)

  echo "[$(date +%H:%M)] r9-sft: $SFT_STATUS ($SFT_EP/$EPISODES eps) | base: $BASE_STATUS ($BASE_EP/$EPISODES eps)"
done

# ── Cleanup eval game servers ──
for EVAL_PORT in 9071 9061; do
  GS_PID=$(ss -tlnp 2>/dev/null | grep ":${EVAL_PORT}" | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "$GS_PID" ] && kill "$GS_PID" 2>/dev/null || true
done

echo ""
echo "EVAL COMPLETE"
echo "  Run dir: $RUN_DIR"
echo "  Results: $RUN_DIR/r9-sft/results.json"
echo "           $RUN_DIR/base/results.json"
echo "  Symlink: dataset/eval/latest → $RUN_DIR"
echo ""
echo "Compare: python3 eval_compare.py $RUN_DIR/base/results.json $RUN_DIR/r9-sft/results.json"
echo "History: ls dataset/eval/runs/"
