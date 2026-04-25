#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop — supports Claude Code, Codex, Gemini, OpenCode CLIs
set -euo pipefail
unset CLAUDECODE

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"

# Parse flags
PERSONALITY=""
HARNESS="claude"
CLAUDE_MODEL="sonnet"
CODEX_MODEL="gpt-5.4"
GEMINI_MODEL="gemini-3-flash-preview"
for arg in "$@"; do
  case "$arg" in
    # Capability-focused archetypes (only supported set)
    --completionist)        PERSONALITY="completionist";;
    --grinder)              PERSONALITY="grinder";;
    --explorer_tinkerer)    PERSONALITY="explorer_tinkerer";;
    --explorer)             PERSONALITY="explorer_tinkerer";;  # short form
    --codex)       HARNESS="codex";;
    --gemini)      HARNESS="gemini";;
    --opencode)    HARNESS="opencode";;
  esac
done
LOG_DIR="$PROJECT_DIR/logs"
STATE_FILE="$PROJECT_DIR/state/progress.json"
MAX_TURNS=150
PAUSE_BETWEEN=10

# Set username based on harness
case "$HARNESS" in
  codex)    BOT_USERNAME="CodexBot";;
  gemini)   BOT_USERNAME="GeminiBot";;
  opencode) BOT_USERNAME="OpenCodeBot";;
  *)        BOT_USERNAME="ClaudeBot";;
esac

# Check for required CLI
case "$HARNESS" in
  codex)
    if ! command -v codex &>/dev/null; then
      echo "ERROR: codex CLI not found. Install with: npm install -g @openai/codex"
      exit 1
    fi
    echo "Using Codex CLI (model: $CODEX_MODEL)"
    ;;
  gemini)
    if ! command -v gemini &>/dev/null; then
      echo "ERROR: gemini CLI not found. Install with: npm install -g @google/gemini-cli"
      exit 1
    fi
    echo "Using Gemini CLI (model: $GEMINI_MODEL)"
    ;;
  opencode)
    if ! command -v opencode &>/dev/null; then
      echo "ERROR: opencode CLI not found. Install with: npm install -g opencode"
      exit 1
    fi
    echo "Using OpenCode CLI (model: opencode default)"
    ;;
  *)
    echo "Using Claude Code CLI (model: $CLAUDE_MODEL)"
    ;;
esac

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
               -e "s|__USERNAME__|${BOT_USERNAME}|g" \
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

  PROMPT="IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. Your ONLY job is to play the game via the MCP tools. The MCP server auto-connects to the game. Start IMMEDIATELY by calling observe.

Session #${SESSION}. Your previous progress: ${PROGRESS}
${GAME_STATE_BLOCK}
Follow your system instructions exactly. Call observe, then run the OBSERVE-ACT loop: kill mobs, progress quests, explore."

  # Codex exec is one-shot — needs explicit instruction to keep looping
  if [ "$HARNESS" = "codex" ]; then
    PROMPT="${PROMPT}

You must keep playing continuously — call tools in a loop for the ENTIRE session. After every action, call observe again and pick the next action. Do NOT stop after login. Do NOT stop after one action. Keep calling tools: observe → decide → act → observe → decide → act, hundreds of times. Never output a final message or conclude — just keep playing until the process is killed."
  fi

  # Run from isolated dir to prevent the CLI from reading this project's CLAUDE.md / AGENTS.md
  SANDBOX="/tmp/kaetram_session_${SESSION}_$$"
  mkdir -p "$SANDBOX"

  case "$HARNESS" in
    codex)
      # Codex: write system prompt file + AGENTS.md, init git repo
      echo "$SYSTEM" > "$SANDBOX/AGENTS.md"
      echo "$SYSTEM" > "$SANDBOX/system_prompt.md"
      git -C "$SANDBOX" init -q

      # Configure kaetram MCP server + stop hook per-session via CODEX_HOME isolation
      mkdir -p "$SANDBOX/.codex" "$SANDBOX/state"
      # Copy auth credentials so sandbox can authenticate with OpenAI
      [ -f "$HOME/.codex/auth.json" ] && cp "$HOME/.codex/auth.json" "$SANDBOX/.codex/auth.json"
      cat > "$SANDBOX/.codex/config.toml" <<TOML
model = "$CODEX_MODEL"
model_reasoning_effort = "medium"

[features]
codex_hooks = true

[mcp_servers.kaetram]
command = "${PROJECT_DIR}/.venv/bin/python3"
args = ["${PROJECT_DIR}/mcp_game_server.py"]
tool_timeout_sec = 60
startup_timeout_sec = 30

[mcp_servers.kaetram.env]
KAETRAM_PORT = ""
KAETRAM_USERNAME = "${BOT_USERNAME}"
KAETRAM_EXTRACTOR = "${PROJECT_DIR}/state_extractor.js"
KAETRAM_SCREENSHOT_DIR = "${SANDBOX}/state"

[projects."${SANDBOX}"]
trust_level = "trusted"
TOML

      # Stop Hook: forces Codex to keep playing instead of exiting after 1 turn
      cat > "$SANDBOX/.codex/hooks.json" <<HOOKJSON
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${PROJECT_DIR}/scripts/codex_stop_hook.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
HOOKJSON
      echo "0" > "$SANDBOX/.turn_counter"

      # Timeout = max_turns * 30s + 5min buffer for hook overhead
      TIMEOUT_SECS=$((MAX_TURNS * 30 + 300))
      (cd "$SANDBOX" && \
        CODEX_HOME="$SANDBOX/.codex" \
        CODEX_TURN_COUNTER="$SANDBOX/.turn_counter" \
        CODEX_MAX_TURNS="$MAX_TURNS" \
        timeout "${TIMEOUT_SECS}s" codex exec "$PROMPT" \
        --model "$CODEX_MODEL" \
        --dangerously-bypass-approvals-and-sandbox \
        --json \
        --enable codex_hooks \
        -c 'model_instructions_file="system_prompt.md"') \
        2>&1 | tee "$LOG_FILE" || true
      ;;

    gemini)
      # Gemini: write .gemini/settings.json with kaetram MCP server + GEMINI.md system prompt
      mkdir -p "$SANDBOX/.gemini" "$SANDBOX/state"
      cat > "$SANDBOX/.gemini/settings.json" <<GEMINIJSON
{
  "mcpServers": {
    "kaetram": {
      "command": "${PROJECT_DIR}/.venv/bin/python3",
      "args": ["${PROJECT_DIR}/mcp_game_server.py"],
      "trust": true,
      "env": {
        "KAETRAM_PORT": "",
        "KAETRAM_USERNAME": "${BOT_USERNAME}",
        "KAETRAM_EXTRACTOR": "${PROJECT_DIR}/state_extractor.js",
        "KAETRAM_SCREENSHOT_DIR": "${SANDBOX}/state"
      }
    }
  },
  "model": {
    "maxSessionTurns": ${MAX_TURNS}
  }
}
GEMINIJSON
      echo "$SYSTEM" > "$SANDBOX/.gemini/GEMINI.md"

      (cd "$SANDBOX" && gemini -p "$PROMPT" \
        -m "$GEMINI_MODEL" \
        --output-format stream-json \
        -y) \
        2>&1 | tee "$LOG_FILE" || true
      ;;

    opencode)
      # OpenCode: resolve opencode.template.json into the sandbox (its CWD-based
      # config lookup) so opencode picks up the kaetram MCP server with the
      # right venv + project paths. System prompt goes in AGENTS.md (opencode's
      # equivalent of claude's CLAUDE.md / codex's AGENTS.md). KAETRAM_* env
      # vars inherit via the child shell — we export inline rather than
      # hardcoding in the template, same pattern the Modal Qwen provider uses.
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          "$PROJECT_DIR/opencode.template.json" > "$SANDBOX/opencode.json"
      echo "$SYSTEM" > "$SANDBOX/AGENTS.md"

      mkdir -p "$SANDBOX/state"
      # opencode run is one-shot per invocation; the outer `while true` loop
      # drives session cadence like every other harness.
      TIMEOUT_SECS=$((MAX_TURNS * 45))
      (cd "$SANDBOX" && \
        KAETRAM_USERNAME="$BOT_USERNAME" \
        KAETRAM_EXTRACTOR="$PROJECT_DIR/state_extractor.js" \
        KAETRAM_SCREENSHOT_DIR="$SANDBOX/state" \
        timeout "${TIMEOUT_SECS}s" opencode run \
          --format json \
          --dangerously-skip-permissions \
          --dir "$SANDBOX" \
          "$PROMPT") \
        2>&1 | tee "$LOG_FILE" &
      OPENCODE_BG_PID=$!

      # Context watchdog: opencode rotates the same conversation across many
      # tool turns; if cumulative input tokens approach the model's window we
      # must end the session so the outer `while true` starts a fresh one.
      # Threshold 250k chosen to leave headroom under typical 256k/262k limits.
      OPENCODE_CTX_LIMIT="${OPENCODE_CTX_LIMIT:-250000}"
      (
        sleep 2
        while kill -0 "$OPENCODE_BG_PID" 2>/dev/null; do
          if [ -f "$LOG_FILE" ]; then
            max_ctx=$(grep '"type":"step_finish"' "$LOG_FILE" 2>/dev/null \
              | grep -oE '"total":[0-9]+' | awk -F: '{print $2}' \
              | sort -n | tail -1)
            if [ -n "$max_ctx" ] && [ "$max_ctx" -gt "$OPENCODE_CTX_LIMIT" ]; then
              echo "[ctx-watchdog] context ${max_ctx} > ${OPENCODE_CTX_LIMIT} — rotating session for $BOT_USERNAME" >&2
              pkill -TERM -f "opencode run.*--dir $SANDBOX" 2>/dev/null
              sleep 3
              pkill -KILL -f "opencode run.*--dir $SANDBOX" 2>/dev/null
              break
            fi
          fi
          sleep 5
        done
      ) &
      WATCHDOG_PID=$!

      wait "$OPENCODE_BG_PID" 2>/dev/null || true
      kill "$WATCHDOG_PID" 2>/dev/null || true
      wait "$WATCHDOG_PID" 2>/dev/null || true

      # Rate-limit backoff: a session that produced no step_finish events is
      # almost certainly an opencode 429 (or upstream auth failure). Without a
      # sleep, the outer `while true` immediately respawns and hammers the
      # endpoint. Detect short/empty sessions and back off before retrying.
      step_count=$(grep -c '"type":"step_finish"' "$LOG_FILE" 2>/dev/null || echo 0)
      if [ "${step_count:-0}" -lt 2 ]; then
        # Check opencode internal log for a 429 to size the backoff
        OC_LOG_DIR="$HOME/.local/share/opencode/log"
        backoff=30
        err_msg="empty session ($step_count step_finish events)"
        if [ -d "$OC_LOG_DIR" ]; then
          recent=$(ls -t "$OC_LOG_DIR"/*.log 2>/dev/null | head -1)
          if [ -n "$recent" ] && grep -q '"statusCode":429' "$recent" 2>/dev/null; then
            echo "[backoff] $BOT_USERNAME: NIM 429 detected — sleeping 120s before retry" >&2
            backoff=120
            err_msg="NVIDIA NIM HTTP 429 — rate limited, sleeping ${backoff}s"
          else
            echo "[backoff] $BOT_USERNAME: empty session ($step_count step_finish) — sleeping ${backoff}s" >&2
          fi
        fi
        # Emit a synthetic harness_error event into the session log so the
        # dashboard activity feed can surface this — opencode itself never
        # writes errors to the session log.
        ts_ms=$(date +%s%3N)
        printf '{"type":"harness_error","timestamp":%s,"error":%s,"backoff_secs":%s}\n' \
          "$ts_ms" "$(printf '%s' "$err_msg" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')" \
          "$backoff" >> "$LOG_FILE"
        sleep "$backoff"
      fi
      ;;

    *)
      # Claude: resolve .mcp.json template and pass via --mcp-config (bypasses project .mcp.json)
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          -e "s|__SCREENSHOT_DIR__|${SANDBOX}/state|g" \
          -e "s|__SERVER_PORT__||g" \
          -e "s|__USERNAME__|${BOT_USERNAME}|g" \
          "$PROJECT_DIR/.mcp.template.json" > "$SANDBOX/.mcp.json"
      (cd "$SANDBOX" && claude -p "$PROMPT" \
        --model "$CLAUDE_MODEL" \
        --max-turns "$MAX_TURNS" \
        --append-system-prompt "$SYSTEM" \
        --dangerously-skip-permissions \
        --disallowedTools "Glob Grep Agent Edit WebFetch WebSearch Write Skill" \
        --mcp-config "$SANDBOX/.mcp.json" \
        --strict-mcp-config \
        --output-format stream-json \
        --verbose) \
        2>&1 | tee "$LOG_FILE" || true
      ;;
  esac

  rm -rf "$SANDBOX"

  # Auto-extract last game state from session log using the CLI adapter
  python3 -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from cli_adapter import get_adapter
adapter = get_adapter(harness='$HARNESS')
state = adapter.parse_game_state_from_log(__import__('pathlib').Path('$LOG_FILE'))
if state:
    import json
    with open('$PROJECT_DIR/state/game_state.json', 'w') as f:
        f.write(state)
" 2>/dev/null || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
