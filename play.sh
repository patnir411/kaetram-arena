#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop
set -euo pipefail
unset CLAUDECODE

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$PROJECT_DIR/state/progress.json"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"

# Parse personality flag
PERSONALITY=""
for arg in "$@"; do
  case "$arg" in
    --aggressive)  PERSONALITY="aggressive";;
    --methodical)  PERSONALITY="methodical";;
    --curious)     PERSONALITY="curious";;
    --efficient)   PERSONALITY="efficient";;
  esac
done
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=150
PAUSE_BETWEEN=10

mkdir -p "$LOG_DIR" "$PROJECT_DIR/state"

if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"level":1,"active_quests":[],"completed_quests":[],"inventory_summary":[],"kills_this_session":0,"next_objective":"accept quests from NPCs","notes":"fresh start"}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Session $SESSION starting at $(date) ==="

  SYSTEM=$(sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
               -e "s|__USERNAME__|ClaudeBot|g" \
               -e "s|__SERVER_PORT__||g" \
               "$SYSTEM_PROMPT_FILE")

  # Inject game knowledge block (before personality so agent reads world context first)
  if [ -f "$PROJECT_DIR/prompts/game_knowledge.md" ]; then
    GFILE="$PROJECT_DIR/prompts/game_knowledge.md"
  else
    GFILE=""
  fi

  # Inject personality block
  if [ -n "$PERSONALITY" ] && [ -f "$PROJECT_DIR/prompts/personalities/${PERSONALITY}.md" ]; then
    PFILE="$PROJECT_DIR/prompts/personalities/${PERSONALITY}.md"
  else
    PFILE=""
  fi

  SYSTEM=$(python3 -c "
import sys
s = sys.stdin.read()
gfile = '$GFILE'
pfile = '$PFILE'
g = open(gfile).read() if gfile else ''
p = open(pfile).read() if pfile else ''
s = s.replace('__GAME_KNOWLEDGE_BLOCK__', g)
s = s.replace('__PERSONALITY_BLOCK__', p)
sys.stdout.write(s)
" <<< "$SYSTEM")

  # Read previous progress and include in prompt
  PROGRESS=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

  # Read game state if available (written by the observe step's page.evaluate() call)
  GAME_STATE=""
  if [ -f "$PROJECT_DIR/state/game_state.json" ]; then
    GAME_STATE=$(python3 -c "
import json, sys
d = json.load(open('$PROJECT_DIR/state/game_state.json'))
d['nearby_entities'] = d.get('nearby_entities', [])[:15]
d['inventory'] = d.get('inventory', [])[:15]
d['quests'] = d.get('quests', [])[:10]
d['achievements'] = d.get('achievements', [])[:10]
print(json.dumps(d, separators=(',',':')))
" 2>/dev/null || echo "")
  fi

  GAME_STATE_BLOCK=""
  if [ -n "$GAME_STATE" ]; then
    GAME_STATE_BLOCK="
Previous game state (from last observe step):
${GAME_STATE}
Use nearest_mob.click_x/click_y to click on targets. Use player_position for spatial awareness."
  fi

  PROMPT="IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. Your ONLY job is to play the game via the browser. Start IMMEDIATELY with the login code block in your system instructions.

Session #${SESSION}. Your previous progress: ${PROGRESS}
${GAME_STATE_BLOCK}
Follow your system instructions exactly. Load tools, then login, then run the OBSERVE-ACT loop: kill mobs, progress quests, explore. Write progress.json before session ends."

  # Run from isolated dir to prevent claude from reading this project's CLAUDE.md
  SANDBOX="/tmp/kaetram_session_${SESSION}_$$"
  mkdir -p "$SANDBOX"
  cp "$PROJECT_DIR/.mcp.json" "$SANDBOX/.mcp.json"
  (cd "$SANDBOX" && claude -p "$PROMPT" \
    --model sonnet \
    --max-turns "$MAX_TURNS" \
    --append-system-prompt "$SYSTEM" \
    --dangerously-skip-permissions \
    --disallowedTools "Glob Grep Agent Edit WebFetch WebSearch Write Skill mcp__playwright__browser_evaluate mcp__playwright__browser_snapshot mcp__playwright__browser_console_messages mcp__playwright__browser_take_screenshot mcp__playwright__browser_click" \
    --output-format stream-json \
    --verbose) \
    2>&1 | tee "$LOG_FILE" || true

  rm -rf "$SANDBOX"

  # Auto-extract last game state from session log (no agent Bash call needed)
  python3 -c "
import json
last_state = None
for line in open('$LOG_FILE'):
    try:
        obj = json.loads(line)
        # browser_run_code results contain game state JSON as a string
        msg = obj.get('message', {})
        for block in msg.get('content', []):
            text = block.get('text', '') if isinstance(block, dict) else ''
            if 'player_position' in text and 'nearby_entities' in text:
                last_state = text
    except: pass
if last_state:
    try:
        d = json.loads(last_state)
        with open('$PROJECT_DIR/state/game_state.json', 'w') as f:
            json.dump(d, f, separators=(',',':'))
    except: pass
" 2>/dev/null || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
