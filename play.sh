#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop
set -euo pipefail
unset CLAUDECODE

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$PROJECT_DIR/state/progress.json"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=100
PAUSE_BETWEEN=10

mkdir -p "$LOG_DIR" "$PROJECT_DIR/state"

if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"level":1,"xp_estimate":"0","quests_started":[],"quests_completed":[],"locations_visited":["mudwich"],"kills_this_session":0,"last_action":"none","notes":"fresh start"}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Session $SESSION starting at $(date) ==="

  SYSTEM=$(sed "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "$SYSTEM_PROMPT_FILE")

  # Read previous progress and include in prompt
  PROGRESS=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

  # Read real-time game state from ws_observer (optional — graceful if absent)
  GAME_STATE=""
  if [ -f "$PROJECT_DIR/state/game_state.json" ]; then
    GAME_STATE=$(python3 -c "
import json, sys
d = json.load(open('$PROJECT_DIR/state/game_state.json'))
ents = d.get('nearby_entities', [])[:15]  # cap at 15 to keep prompt short
d['nearby_entities'] = ents
print(json.dumps(d, separators=(',',':')))
" 2>/dev/null || echo "")
  fi

  GAME_STATE_BLOCK=""
  if [ -n "$GAME_STATE" ]; then
    GAME_STATE_BLOCK="
Current game state (real-time from ws_observer):
${GAME_STATE}
Use nearby_entities to find and target mobs directly by coordinate."
  fi

  PROMPT="Session #${SESSION}. Your previous progress: ${PROGRESS}
${GAME_STATE_BLOCK}
Follow your system instructions exactly. Phase 1: Run the login code block. Phase 2: Grind combat (kill rats/mobs, loot drops). Phase 3: Check quests if not started. Phase 4: Explore one new area. Phase 5: MANDATORY — write progress.json before session ends."

  claude -p "$PROMPT" \
    --model sonnet \
    --max-turns "$MAX_TURNS" \
    --append-system-prompt "$SYSTEM" \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --verbose \
    2>&1 | tee "$LOG_FILE" || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
