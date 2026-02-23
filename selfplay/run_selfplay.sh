#!/usr/bin/env bash
# Orchestrate self-play data collection at scale.
#
# Usage:
#   ./selfplay/run_selfplay.sh                    # defaults: 100 episodes, mixed strategy
#   ./selfplay/run_selfplay.sh --episodes 1000    # bulk collection
#   ./selfplay/run_selfplay.sh --parallel 4       # 4 browser instances
#   ./selfplay/run_selfplay.sh --annotate         # also run post-hoc annotation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults
EPISODES=100
STEPS_PER_EP=100
STRATEGY="mixed"
PARALLEL=1
HEADLESS="--headless"
ANNOTATE=false
ANNOTATION_PROVIDER="local"
ANNOTATION_MODEL=""
GAME_URL="http://localhost:9000"
OUTPUT_DIR="$PROJECT_DIR/trajectories"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --episodes)     EPISODES="$2"; shift 2 ;;
    --steps)        STEPS_PER_EP="$2"; shift 2 ;;
    --strategy)     STRATEGY="$2"; shift 2 ;;
    --parallel)     PARALLEL="$2"; shift 2 ;;
    --headed)       HEADLESS=""; shift ;;
    --annotate)     ANNOTATE=true; shift ;;
    --provider)     ANNOTATION_PROVIDER="$2"; shift 2 ;;
    --model)        ANNOTATION_MODEL="$2"; shift 2 ;;
    --game-url)     GAME_URL="$2"; shift 2 ;;
    --output)       OUTPUT_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================="
echo "  Kaetram Self-Play Data Collection"
echo "========================================="
echo "Episodes:    $EPISODES"
echo "Steps/ep:    $STEPS_PER_EP"
echo "Strategy:    $STRATEGY"
echo "Parallel:    $PARALLEL"
echo "Headless:    $([ -n "$HEADLESS" ] && echo yes || echo no)"
echo "Game URL:    $GAME_URL"
echo "Output:      $OUTPUT_DIR"
echo "Annotate:    $ANNOTATE"
echo "========================================="
echo

# Check game server is running
if ! curl -s "$GAME_URL" > /dev/null 2>&1; then
  echo "WARNING: Game server at $GAME_URL is not responding."
  echo "Start it with: bash $PROJECT_DIR/scripts/start-kaetram.sh"
  echo "Continuing anyway (will fail if server doesn't start)..."
  echo
fi

# Ensure playwright is installed
python3 -c "from playwright.sync_api import sync_playwright" 2>/dev/null || {
  echo "Installing playwright..."
  pip install playwright
  python3 -m playwright install chromium
}

mkdir -p "$OUTPUT_DIR"

# Run collection
if [ "$PARALLEL" -le 1 ]; then
  # Single process
  python3 "$SCRIPT_DIR/bot.py" \
    --episodes "$EPISODES" \
    --steps "$STEPS_PER_EP" \
    --strategy "$STRATEGY" \
    --output "$(basename "$OUTPUT_DIR")" \
    --game-url "$GAME_URL" \
    $HEADLESS
else
  # Parallel: split episodes across workers
  EPISODES_PER_WORKER=$(( (EPISODES + PARALLEL - 1) / PARALLEL ))
  PIDS=()

  for i in $(seq 0 $((PARALLEL - 1))); do
    WORKER_EPISODES=$EPISODES_PER_WORKER
    # Last worker gets remainder
    if [ $i -eq $((PARALLEL - 1)) ]; then
      WORKER_EPISODES=$(( EPISODES - i * EPISODES_PER_WORKER ))
      [ "$WORKER_EPISODES" -le 0 ] && continue
    fi

    echo "Starting worker $i ($WORKER_EPISODES episodes)..."
    python3 "$SCRIPT_DIR/bot.py" \
      --episodes "$WORKER_EPISODES" \
      --steps "$STEPS_PER_EP" \
      --strategy "$STRATEGY" \
      --output "$(basename "$OUTPUT_DIR")" \
      --game-url "$GAME_URL" \
      $HEADLESS \
      &
    PIDS+=($!)
  done

  echo "Waiting for ${#PIDS[@]} workers..."
  for pid in "${PIDS[@]}"; do
    wait "$pid" || echo "Worker $pid failed"
  done
fi

# Count results
EPISODE_COUNT=$(find "$OUTPUT_DIR" -name "trajectory.jsonl" | wc -l)
echo
echo "Collection complete: $EPISODE_COUNT episodes in $OUTPUT_DIR"

# Optional: post-hoc annotation
if [ "$ANNOTATE" = true ]; then
  echo
  echo "Running post-hoc annotation..."
  ANNOTATED_DIR="$PROJECT_DIR/trajectories_annotated"

  MODEL_FLAG=""
  [ -n "$ANNOTATION_MODEL" ] && MODEL_FLAG="--model $ANNOTATION_MODEL"

  python3 "$SCRIPT_DIR/annotate.py" \
    --input "$OUTPUT_DIR" \
    --output "$ANNOTATED_DIR" \
    --provider "$ANNOTATION_PROVIDER" \
    $MODEL_FLAG

  echo "Annotated trajectories: $ANNOTATED_DIR"
fi

# Convert to training format
echo
echo "Converting to training format..."
TRAINING_DIR="$PROJECT_DIR/training_data"

python3 "$SCRIPT_DIR/convert.py" \
  --input "$OUTPUT_DIR" \
  --output "$TRAINING_DIR" \
  --format chatml

echo
echo "========================================="
echo "  DONE"
echo "========================================="
echo "Raw trajectories:  $OUTPUT_DIR ($EPISODE_COUNT episodes)"
echo "Training data:     $TRAINING_DIR"
echo
echo "Next steps:"
echo "  1. Review samples:  head $TRAINING_DIR/train/samples.jsonl | python3 -m json.tool"
echo "  2. Annotate (optional):  python3 $SCRIPT_DIR/annotate.py --input $OUTPUT_DIR --output trajectories_annotated --provider local"
echo "  3. Train:  See training/ directory for fine-tuning scripts"
