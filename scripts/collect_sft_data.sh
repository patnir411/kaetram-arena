#!/usr/bin/env bash
# collect_sft_data.sh — End-to-end SFT data collection pipeline
#
# Usage:
#   ./scripts/collect_sft_data.sh [N_AGENTS] [HOURS]
#   ./scripts/collect_sft_data.sh 4 8        # 4 agents for 8 hours
#   ./scripts/collect_sft_data.sh 2           # 2 agents, run until ctrl-c
#   ./scripts/collect_sft_data.sh             # defaults: 4 agents, no time limit
#
# Steps:
#   1. Check that the shared Kaetram client is running on port 9000
#   2. Launch orchestrate.py with N agents (each gets its own server)
#   3. On completion, run extract_turns.py on all collected logs
#   4. Run convert_to_qwen.py to produce final SFT dataset
#   5. Print stats

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

N_AGENTS="${1:-4}"
HOURS="${2:-}"

echo "=== Kaetram SFT Data Collection Pipeline ==="
echo "  Agents: $N_AGENTS"
echo "  Hours: ${HOURS:-unlimited}"
echo ""

# Step 1: Check shared client on port 9000
echo "--- Step 1: Checking Kaetram client on port 9000 ---"
if curl -s --max-time 2 http://localhost:9000 >/dev/null 2>&1; then
  echo "  Client is running on port 9000."
else
  echo "  WARNING: Kaetram client not detected on port 9000."
  echo "  The game client serves static assets. Agents will try localhost:9000."
  echo "  Start it with: ./scripts/start-kaetram.sh"
  echo ""
  read -p "  Continue anyway? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
  fi
fi
echo ""

# Step 2: Run orchestrator
echo "--- Step 2: Running orchestrator ($N_AGENTS agents) ---"
ORCH_ARGS="--agents $N_AGENTS"
if [ -n "$HOURS" ]; then
  ORCH_ARGS="$ORCH_ARGS --hours $HOURS"
fi

python3 orchestrate.py $ORCH_ARGS
echo ""

# Step 3: Extract turns from all collected logs
echo "--- Step 3: Extracting turns from session logs ---"
RAW_DIR="$PROJECT_DIR/dataset/raw"
EXTRACTED_DIR="$PROJECT_DIR/dataset/extracted"

if [ -d "$RAW_DIR" ]; then
  for agent_dir in "$RAW_DIR"/agent_*/logs; do
    if [ -d "$agent_dir" ]; then
      echo "  Processing $agent_dir ..."
      python3 extract_turns.py --log-dir "$agent_dir" --output-dir "$EXTRACTED_DIR" --no-frames
    fi
  done
fi

# Also process any logs in the main logs/ directory
if [ -d "$PROJECT_DIR/logs" ]; then
  echo "  Processing logs/ ..."
  python3 extract_turns.py --log-dir "$PROJECT_DIR/logs" --output-dir "$EXTRACTED_DIR" --no-frames
fi
echo ""

# Step 4: Convert to Qwen3.5 SFT format
echo "--- Step 4: Converting to Qwen3.5 9B SFT format ---"
python3 convert_to_qwen.py --input "$EXTRACTED_DIR" --output "$PROJECT_DIR/dataset/qwen_sft"
echo ""

# Step 5: Print stats
echo "--- Step 5: Dataset Summary ---"
TRAIN="$PROJECT_DIR/dataset/qwen_sft/train.json"
VAL="$PROJECT_DIR/dataset/qwen_sft/val.json"

if [ -f "$TRAIN" ]; then
  TRAIN_COUNT=$(python3 -c "import json; print(len(json.load(open('$TRAIN'))))")
  VAL_COUNT=$(python3 -c "import json; print(len(json.load(open('$VAL'))))")
  echo "  Train examples: $TRAIN_COUNT"
  echo "  Val examples:   $VAL_COUNT"
  echo "  Total:          $((TRAIN_COUNT + VAL_COUNT))"
  echo "  Output:         $PROJECT_DIR/dataset/qwen_sft/"
else
  echo "  No dataset produced. Check logs for errors."
fi

echo ""
echo "=== Pipeline complete ==="
