#!/usr/bin/env bash
# High-quality trajectory collection using Claude as the player.
#
# This is the EXPENSIVE option — uses Claude API for real chain-of-thought.
# Each session costs ~$0.50-2.00 but produces gold-standard training data.
#
# The key difference from play.sh: this version instruments every action
# to save structured (screenshot, thought, action) tuples.
#
# Usage:
#   ./selfplay/claude_player.sh                  # 10 sessions
#   ./selfplay/claude_player.sh --sessions 50    # 50 sessions
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
OUTPUT_DIR="$PROJECT_DIR/trajectories"
MAX_TURNS=25
PAUSE_BETWEEN=10
SESSIONS=10

while [[ $# -gt 0 ]]; do
  case $1 in
    --sessions) SESSIONS="$2"; shift 2 ;;
    --output)   OUTPUT_DIR="$2"; shift 2 ;;
    --turns)    MAX_TURNS="$2"; shift 2 ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

mkdir -p "$OUTPUT_DIR"

SYSTEM=$(cat "$SYSTEM_PROMPT_FILE")

# Augmented system prompt: instructs Claude to save trajectory data
TRAJECTORY_INSTRUCTIONS="
## TRAJECTORY RECORDING (CRITICAL — do this for EVERY action)

You are collecting training data. For EVERY action you take, you MUST:

1. Take a BEFORE screenshot: save to /home/user/kaetram-arena/trajectories/claude_session_\${SESSION}/step_NNNN_before.png
2. Write your THOUGHT (what you see, why you're acting) to the trajectory log
3. Execute the ACTION
4. Take an AFTER screenshot: save to /home/user/kaetram-arena/trajectories/claude_session_\${SESSION}/step_NNNN_after.png
5. Append a line to /home/user/kaetram-arena/trajectories/claude_session_\${SESSION}/trajectory.jsonl:

{\"step\": N, \"before_screenshot\": \"step_NNNN_before.png\", \"after_screenshot\": \"step_NNNN_after.png\", \"action_type\": \"click|key_hold|key_press|type_text\", \"action_params\": {\"x\": 100, \"y\": 200}, \"thought\": \"Your reasoning here\", \"timestamp\": 0.0}

Action types:
- click: {\"x\": N, \"y\": N}
- key_hold: {\"key\": \"w\", \"duration_ms\": 2000}
- key_press: {\"key\": \"Enter\"}
- type_text: {\"text\": \"/teleport 188 157\"}

Be DETAILED in your thoughts — describe what you see, your HP, nearby entities, and WHY you chose this action.
This data will be used to train an AI model to play the game.

Start step counter at 0. Create the episode directory first:
mkdir -p /home/user/kaetram-arena/trajectories/claude_session_\${SESSION}
"

for SESSION in $(seq 1 "$SESSIONS"); do
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  EPISODE_ID="claude_session_${SESSION}_${TIMESTAMP}"

  echo "=== Claude Session $SESSION/$SESSIONS [$EPISODE_ID] ==="

  PROMPT="You are collecting gameplay training data. Session #${SESSION}.

Create the output directory:
mkdir -p /home/user/kaetram-arena/trajectories/${EPISODE_ID}

Then play Kaetram at http://localhost:9000, recording every action as specified.
Save all screenshots and trajectory data to /home/user/kaetram-arena/trajectories/${EPISODE_ID}/

After finishing, write metadata to /home/user/kaetram-arena/trajectories/${EPISODE_ID}/metadata.json:
{\"episode_id\": \"${EPISODE_ID}\", \"agent_type\": \"claude_sonnet\", \"total_steps\": N, \"session\": ${SESSION}}

Play the game and collect data!"

  claude -p "$PROMPT" \
    --model sonnet \
    --max-turns "$MAX_TURNS" \
    --append-system-prompt "${SYSTEM}

${TRAJECTORY_INSTRUCTIONS}" \
    2>&1 || true

  echo "=== Session $SESSION ended ==="
  sleep "$PAUSE_BETWEEN"
done

echo
echo "Done! $SESSIONS Claude sessions collected in $OUTPUT_DIR"
echo "Convert to training format: python3 $SCRIPT_DIR/convert.py --input $OUTPUT_DIR --output training_data"
