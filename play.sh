#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop
# Runs Claude Code (Sonnet) in headless mode with Playwright MCP
set -euo pipefail

PROJECT_DIR="$HOME/projects/kaetram-agent"
STATE_FILE="$PROJECT_DIR/state/progress.json"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=25
PAUSE_BETWEEN=10

mkdir -p "$LOG_DIR" "$PROJECT_DIR/state"

# Initialize state
if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"milestone":"not_started","level":0,"notes":""}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Session $SESSION starting at $(date) ==="

  STATE=$(cat "$STATE_FILE")
  SYSTEM=$(cat "$SYSTEM_PROMPT_FILE")

  PROMPT="You are the Kaetram gameplay agent. Session #${SESSION}.

Previous state:
${STATE}

INSTRUCTIONS:
1. Navigate browser to http://localhost:9000
2. Take a screenshot to assess the current state
3. If you see a login screen, type 'ClaudeBot' in the name field and click Play
4. Play the game following your system instructions
5. After your actions, update ${STATE_FILE} with a JSON summary:
   {\"sessions\": ${SESSION}, \"milestone\": \"<achievement>\", \"level\": <n>, \"notes\": \"<what happened>\"}
6. If something exciting happens, append to ${PROJECT_DIR}/state/highlights.jsonl:
   {\"session\": ${SESSION}, \"type\": \"<death|levelup|loot|quest>\", \"desc\": \"<what happened>\"}

Play aggressively. Narrate with dark humor. Take screenshots constantly."

  claude -p "$PROMPT" \
    --model sonnet \
    --max-turns "$MAX_TURNS" \
    --append-system-prompt "$SYSTEM" \
    2>&1 | tee "$LOG_FILE" || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
