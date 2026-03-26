#!/usr/bin/env bash
# play_qwen.sh — Session loop for the custom Qwen harness
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
GAME_KNOWLEDGE_FILE="$PROJECT_DIR/prompts/game_knowledge.md"

# Defaults
AGENT_ID=4
USERNAME="QwenBot"
SERVER_PORT=""
MAX_TURNS=300
PAUSE_BETWEEN=10
ENDPOINT="https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-id)    AGENT_ID="$2"; shift 2;;
    --username)    USERNAME="$2"; shift 2;;
    --server-port) SERVER_PORT="$2"; shift 2;;
    --max-turns)   MAX_TURNS="$2"; shift 2;;
    --endpoint)    ENDPOINT="$2"; shift 2;;
    *)             shift;;
  esac
done

# Sandbox setup
SANDBOX="/tmp/kaetram_agent_${AGENT_ID}"
STATE_DIR="$SANDBOX/state"
LOG_DIR="$SANDBOX/logs"
STATE_FILE="$STATE_DIR/progress.json"

mkdir -p "$STATE_DIR" "$LOG_DIR"

# Write metadata for dashboard
cat > "$SANDBOX/metadata.json" << EOF
{
  "personality": "qwen",
  "username": "$USERNAME",
  "agent_id": $AGENT_ID,
  "model": "Qwen3.5-9B (finetuned)",
  "harness": "play_qwen.py"
}
EOF

# Init progress.json if missing
if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"level":1,"active_quests":[],"completed_quests":[],"inventory_summary":[],"kills_this_session":0,"next_objective":"accept quests from NPCs","notes":"fresh start"}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  echo "=== Qwen Session $SESSION starting at $(date) ==="

  # Build system prompt with substitutions using Python (sed breaks on special chars)
  SYSTEM_TMP=$(mktemp)
  python3 -c "
import sys
text = open('$SYSTEM_PROMPT_FILE').read()
text = text.replace('__PROJECT_DIR__', '$SANDBOX')
text = text.replace('__USERNAME__', '$USERNAME')
text = text.replace('__SERVER_PORT__', '$SERVER_PORT')
text = text.replace('__PERSONALITY_BLOCK__', '')
try:
    gk = open('$GAME_KNOWLEDGE_FILE').read()
    text = text.replace('__GAME_KNOWLEDGE_BLOCK__', gk)
except: pass
open('$SYSTEM_TMP', 'w').write(text)
"

  PROGRESS=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

  PROMPT="IMPORTANT: Do NOT search for files or explore the filesystem. Your ONLY job is to play the game via the browser. Start IMMEDIATELY with the login code block in your system instructions.

Session #${SESSION}. Your previous progress: ${PROGRESS}

Follow your system instructions exactly. Login, then run the OBSERVE-ACT loop: kill mobs, progress quests, explore. Write progress.json before session ends."

  # Run harness
  source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
  python3 "$PROJECT_DIR/play_qwen.py" \
    --endpoint "$ENDPOINT" \
    --model kaetram \
    --system-prompt "$SYSTEM_TMP" \
    --user-prompt "$PROMPT" \
    --sandbox "$SANDBOX" \
    --max-turns "$MAX_TURNS" \
    || true

  rm -f "$SYSTEM_TMP"

  echo "=== Qwen Session $SESSION ended at $(date) ==="
  sleep "$PAUSE_BETWEEN"
done
