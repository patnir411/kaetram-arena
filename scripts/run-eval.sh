#!/usr/bin/env bash
# Run eval harness for base + r10-sft in parallel.
# Each model gets its own game server, username, and sandbox.
#
# Usage:
#   ./scripts/run-eval.sh                    # 3 episodes, scenario D
#   ./scripts/run-eval.sh --episodes 5       # 5 episodes
#   ./scripts/run-eval.sh --scenario A       # Rat Grind (5 minutes)
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
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# NODE_ENV=eval pins both game servers to this database. Export the same value
# for eval_harness DB snapshots and the pre-run reset.
export KAETRAM_MONGO_DB="kaetram_eval"

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
# A timestamp collision must fail instead of reusing artifacts from another run.
mkdir "$RUN_DIR"

# ── Cleanup ──
# Scope every kill to the eval lane — never a bare pkill on the generic names
# mcp_game_server.py / chrome-headless-shell / playwright/driver:
#   - eval_harness.py is kaetram-unique → safe to match by name.
#   - play_qwen.py is kaetram-unique AND carries `--sandbox /tmp/kaetram_eval_*`
#     in its cmdline → match the eval sandbox specifically (not a bare match,
#     which would also hit data-collection play_qwen agents).
#   - mcp_game_server / chrome / playwright orphans are reaped ONLY when their
#     process env (KAETRAM_STATE_DIR=.../kaetram_eval...) marks them as eval
#     children — see reap_eval_orphans(). cmdline has no sandbox marker, so env
#     is the only safe discriminator.
#   - eval game servers are killed by their dedicated ports (9061/9071).
echo "Cleaning up previous eval runs..."
pkill -9 -f "eval_harness" 2>/dev/null || true
# procps pkill -f uses ERE; scope to the eval sandbox so data-collection
# play_qwen agents (--sandbox /tmp/kaetram_agent_*) are NOT matched.
pkill -9 -f "play_qwen.py.*--sandbox /tmp/kaetram_eval" 2>/dev/null || true

# Reap mcp_game_server / chrome / playwright orphans that belong to a kaetram
# EVAL sandbox, identified via /proc/<pid>/environ (KAETRAM_STATE_DIR pointing
# at /tmp/kaetram_eval_*). The live data-collection lane carries a different
# KAETRAM_STATE_DIR, so it is untouched.
reap_eval_orphans() {
  local pid environ
  for pid in $(pgrep -f 'mcp_game_server|chrome-headless-shell|playwright/driver' 2>/dev/null || true); do
    [ -r "/proc/$pid/environ" ] || continue
    # NUL-delimited env; match our eval sandbox marker only.
    if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
         | grep -q '^KAETRAM_STATE_DIR=/tmp/kaetram_eval_'; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

SFT_PID=""
BASE_PID=""
WATCHDOG_PID=""

cleanup_on_signal() {
  local exit_code="$1"
  local child_pid eval_port server_pid
  trap - INT TERM
  set +e
  for child_pid in "$WATCHDOG_PID" "$SFT_PID" "$BASE_PID"; do
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
      kill "$child_pid" 2>/dev/null
    fi
  done
  for child_pid in "$WATCHDOG_PID" "$SFT_PID" "$BASE_PID"; do
    [ -n "$child_pid" ] && wait "$child_pid" 2>/dev/null
  done
  for eval_port in 9071 9061; do
    server_pid=$(ss -tlnp 2>/dev/null | grep ":${eval_port} " \
      | grep -oP 'pid=\K[0-9]+' | head -1 || true)
    [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null
  done
  reap_eval_orphans
  exit "$exit_code"
}

trap 'cleanup_on_signal 130' INT
trap 'cleanup_on_signal 143' TERM
reap_eval_orphans

# Kill BOTH eval game servers by their dedicated ports (9061 r10-sft, 9071 base).
for EVAL_PORT in 9061 9071; do
  GS_PID=$(ss -tlnp 2>/dev/null | grep ":${EVAL_PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "$GS_PID" ] && kill -9 "$GS_PID" 2>/dev/null || true
done
sleep 2

# Clean eval sandboxes (temp data only — results are preserved in runs/)
rm -rf /tmp/kaetram_eval_*

# Reset eval player data in MongoDB
source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
export KAETRAM_MONGO_DB="kaetram_eval"
PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -c "
import os
from eval_harness import MONGO_COLLECTIONS
from pymongo import MongoClient
c = MongoClient('localhost', 27017)
db = c[os.environ['KAETRAM_MONGO_DB']]
for username in ['evalbotsft', 'evalbotbase']:
    for col in MONGO_COLLECTIONS:
        db[col].delete_many({'username': username})
print('  Eval player data cleared')
"

# ── Ensure game servers ──
# Port 9061 (r10-sft eval — distinct from agent_0-5 ports).
# NODE_ENV=eval pins MONGODB_DATABASE=kaetram_eval (see Kaetram-Open/.env.eval)
# so eval rows don't interleave with the data-collection ClaudeBot* rows in
# kaetram_devlopment. dotenv-extended layers .env.{NODE_ENV} on top of .env.
if ! ss -tlnp 2>/dev/null | grep -q ":9061 "; then
  echo "Starting game server on port 9061 (r10-sft eval, db=kaetram_eval)..."
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
# Probe by port (8080), not by process name.
if ! ss -tlnp 2>/dev/null | grep -q ":${DASHBOARD_HTTP_PORT:-8080} "; then
  "$SCRIPT_DIR/start-dashboard.sh"
fi

# ── Resolve Modal endpoints from env (like cli_adapter / eval_harness) ──
MODAL_WS="${MODAL_WORKSPACE:-workspace}"
SFT_ENDPOINT="${KAETRAM_QWEN_SFT_ENDPOINT:-https://${MODAL_WS}--kaetram-qwen-serve-inference-serve.modal.run/v1}"
BASE_ENDPOINT="${KAETRAM_QWEN_BASE_ENDPOINT:-https://${MODAL_WS}--kaetram-qwen-base-inference-serve.modal.run/v1}"

# ── Launch evals in parallel ──
echo ""
echo "Starting eval: $EPISODES episodes × 2 models, scenario $SCENARIO"
echo "  Run dir: $RUN_DIR"
echo "  Endpoints: r10-sft=$SFT_ENDPOINT  base=$BASE_ENDPOINT"
echo ""

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "r10-sft=$SFT_ENDPOINT" \
  --episodes "$EPISODES" --scenario "$SCENARIO" \
  --username evalbotSFT --server-port 9061 --output-dir "$RUN_DIR" $PERS_FLAG \
  > /tmp/eval_r10sft.log 2>&1 &
SFT_PID=$!
echo "  r10-SFT eval started (PID $SFT_PID, log: /tmp/eval_r10sft.log, personality: ${PERSONALITY:-none})"

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "base=$BASE_ENDPOINT" \
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
  --health-timeout 90 --health-fail-threshold 3 \
  --model "r10-sft=$SFT_ENDPOINT,/tmp/kaetram_eval_r10-sft,9061" \
  --model "base=$BASE_ENDPOINT,/tmp/kaetram_eval_base,9071" \
  > "/tmp/eval_watchdog_${RUN_TAG}.log" 2>&1 &
WATCHDOG_PID=$!
echo "  Watchdog started (PID $WATCHDOG_PID, log: /tmp/eval_watchdog_${RUN_TAG}.log)"

echo ""
echo "Both evals running in parallel."
echo "  Dashboard: http://localhost:8080 (Eval tab — live side-by-side + metrics)"
echo "  Logs: tail -f /tmp/eval_r10sft.log"
echo "        tail -f /tmp/eval_base.log"
echo "        tail -f /tmp/eval_watchdog_${RUN_TAG}.log"
echo ""
echo "Stop: pkill -f eval_harness"

# ── Monitor loop ──
while kill -0 $SFT_PID 2>/dev/null || kill -0 $BASE_PID 2>/dev/null; do
  sleep 30
  SFT_STATUS="running"
  BASE_STATUS="running"
  kill -0 $SFT_PID 2>/dev/null || SFT_STATUS="done"
  kill -0 $BASE_PID 2>/dev/null || BASE_STATUS="done"

  SFT_EP=0; BASE_EP=0
  [ -f "$RUN_DIR/r10-sft/results.json" ] && SFT_EP=$(python3 -c "import json; print(len([e for e in json.load(open('$RUN_DIR/r10-sft/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)
  [ -f "$RUN_DIR/base/results.json" ] && BASE_EP=$(python3 -c "import json; print(len([e for e in json.load(open('$RUN_DIR/base/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)

  echo "[$(date +%H:%M)] r10-sft: $SFT_STATUS ($SFT_EP/$EPISODES eps) | base: $BASE_STATUS ($BASE_EP/$EPISODES eps)"
done

# Reap each child exactly once and retain its real exit status.
SFT_RC=0
BASE_RC=0
wait "$SFT_PID" || SFT_RC=$?
wait "$BASE_PID" || BASE_RC=$?

if kill -0 "$WATCHDOG_PID" 2>/dev/null; then
  kill "$WATCHDOG_PID" 2>/dev/null || true
fi
wait "$WATCHDOG_PID" 2>/dev/null || true

# ── Cleanup eval game servers ──
# Trailing space on the port match so ":9061 " can't substring-match ":90610".
for EVAL_PORT in 9071 9061; do
  GS_PID=$(ss -tlnp 2>/dev/null | grep ":${EVAL_PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "$GS_PID" ] && kill "$GS_PID" 2>/dev/null || true
done

validate_arm() {
  local model_name="$1"
  local result_path="$RUN_DIR/$model_name/results.json"
  python3 "$PROJECT_DIR/scripts/validate_eval_results.py" \
    --results "$result_path" \
    --model "$model_name" \
    --episodes "$EPISODES" \
    --scenario "$SCENARIO"
}

VALIDATION_RC=0
validate_arm "r10-sft" || VALIDATION_RC=1
validate_arm "base" || VALIDATION_RC=1

if [ "$SFT_RC" -ne 0 ] || [ "$BASE_RC" -ne 0 ] || [ "$VALIDATION_RC" -ne 0 ]; then
  echo ""
  echo "EVAL FAILED"
  echo "  r10-sft exit: $SFT_RC"
  echo "  base exit:    $BASE_RC"
  echo "  Run preserved for diagnosis: $RUN_DIR"
  echo "  dataset/eval/latest-run.txt was not changed"
  exit 1
fi

# Promote only a complete, validated paired run. The helper validates that the
# target is a real direct child of dataset/eval/runs and atomically replaces a
# regular text pointer; directory symlinks are prohibited by runtime isolation.
LATEST_RUN_RELATIVE=$(
  python3 "$PROJECT_DIR/dashboard/eval_latest.py" promote \
    --eval-dir "$PROJECT_DIR/dataset/eval" \
    --run-dir "$RUN_DIR"
)

echo ""
echo "EVAL COMPLETE"
echo "  Run dir: $RUN_DIR"
echo "  Results: $RUN_DIR/r10-sft/results.json"
echo "           $RUN_DIR/base/results.json"
echo "  Pointer: dataset/eval/latest-run.txt → $LATEST_RUN_RELATIVE"
echo ""
echo "Compare: python3 eval_compare.py $RUN_DIR/base/results.json $RUN_DIR/r10-sft/results.json"
echo "History: ls dataset/eval/runs/"
