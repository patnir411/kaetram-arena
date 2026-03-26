#!/usr/bin/env bash
# play_opencode.sh — Kaetram agent loop using OpenCode + finetuned Qwen3.5-9B
#
# Uses OpenCode with Playwright MCP and a Modal vLLM endpoint.
# Writes to its own sandbox (/tmp/kaetram_agent_N/) so the dashboard
# shows it alongside Claude agents without interference.
#
# Usage:
#   ./play_opencode.sh                          # defaults: agent_4, QwenBot
#   ./play_opencode.sh --agent-id 5 --username QwenBot2
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
GAME_KNOWLEDGE_FILE="$PROJECT_DIR/prompts/game_knowledge.md"

# Defaults
AGENT_ID=4
USERNAME="QwenBot"
SERVER_PORT=""
MAX_SESSION_SECONDS=$((150 * 30))  # ~75 minutes per session
PAUSE_BETWEEN=10

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-id)   AGENT_ID="$2"; shift 2;;
    --username)   USERNAME="$2"; shift 2;;
    --server-port) SERVER_PORT="$2"; shift 2;;
    --hours)      MAX_SESSION_SECONDS=$(($2 * 3600)); shift 2;;
    *)            shift;;
  esac
done

# Sandbox setup — separate from Claude agents
SANDBOX="/tmp/kaetram_agent_${AGENT_ID}"
STATE_DIR="$SANDBOX/state"
LOG_DIR="$SANDBOX/logs"
STATE_FILE="$STATE_DIR/progress.json"

mkdir -p "$STATE_DIR" "$LOG_DIR"

# Write metadata so dashboard shows this agent with a distinct badge
cat > "$SANDBOX/metadata.json" << EOF
{
  "personality": "qwen",
  "username": "$USERNAME",
  "agent_id": $AGENT_ID,
  "model": "Qwen3.5-9B (finetuned)",
  "harness": "opencode"
}
EOF

# Init progress.json if missing
if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"level":1,"active_quests":[],"completed_quests":[],"inventory_summary":[],"kills_this_session":0,"next_objective":"accept quests from NPCs","notes":"fresh start"}' > "$STATE_FILE"
fi

# Build system prompt (same substitution as play.sh)
build_system_prompt() {
  local SYSTEM
  SYSTEM=$(sed -e "s|__PROJECT_DIR__|${SANDBOX}|g" \
               -e "s|__USERNAME__|${USERNAME}|g" \
               -e "s|__SERVER_PORT__|${SERVER_PORT}|g" \
               "$SYSTEM_PROMPT_FILE")

  # Remove personality placeholder (Qwen was trained on mixed personalities)
  SYSTEM=$(echo "$SYSTEM" | sed 's/__PERSONALITY_BLOCK__//g')

  # Append game knowledge
  if [ -f "$GAME_KNOWLEDGE_FILE" ]; then
    GAME_KNOWLEDGE=$(cat "$GAME_KNOWLEDGE_FILE")
    SYSTEM=$(echo "$SYSTEM" | sed "s|__GAME_KNOWLEDGE_BLOCK__|${GAME_KNOWLEDGE}|g")
  fi

  echo "$SYSTEM"
}

# Session loop
SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Qwen Session $SESSION starting at $(date) ==="
  echo "    Agent ID: $AGENT_ID | Username: $USERNAME | Sandbox: $SANDBOX"

  SYSTEM=$(build_system_prompt)
  PROGRESS=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

  PROMPT="Session #${SESSION}. Your previous progress: ${PROGRESS}

Navigate to http://localhost:9000 and log in as ${USERNAME} with password password123. Then inject the state extractor and begin playing."

  # Run OpenCode with timeout
  # MODAL_ENDPOINT_URL is the base URL for the OpenAI-compatible API
  # OpenCode reads it via the "env" field in opencode.json provider config
  export PATH="$HOME/.opencode/bin:$PATH"
  export MODAL_ENDPOINT_URL="https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1"
  timeout "${MAX_SESSION_SECONDS}s" opencode run \
    --model modal/kaetram \
    --dir "$SANDBOX" \
    "$PROMPT" \
    2>&1 | tee "$LOG_FILE" || true

  echo "=== Qwen Session $SESSION ended at $(date) ==="
  sleep "$PAUSE_BETWEEN"
done
