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
OPENCODE_MODEL=""
# Manual two-arg parse so --opencode-model <id> works alongside the bare flags.
while [ $# -gt 0 ]; do
  case "$1" in
    --completionist)        PERSONALITY="completionist"; shift;;
    --grinder)              PERSONALITY="grinder"; shift;;
    --explorer_tinkerer)    PERSONALITY="explorer_tinkerer"; shift;;
    --explorer)             PERSONALITY="explorer_tinkerer"; shift;;  # short form
    --codex)       HARNESS="codex"; shift;;
    --gemini)      HARNESS="gemini"; shift;;
    --opencode)    HARNESS="opencode"; shift;;
    --opencode-model) OPENCODE_MODEL="$2"; shift 2;;
    *) shift;;
  esac
done
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=150
PAUSE_BETWEEN=10

# Set username based on harness — opencode splits by model family so the
# in-game name + Mongo row mirror the orchestrator's bot-prefix convention
# (cli_adapter.opencode_bot_prefix).
case "$HARNESS" in
  codex)    BOT_USERNAME="CodexBot";;
  gemini)   BOT_USERNAME="GeminiBot";;
  opencode)
    MODEL_LC="$(echo "$OPENCODE_MODEL" | tr '[:upper:]' '[:lower:]')"
    case "$MODEL_LC" in
      *qwen*)     BOT_USERNAME="BigQwenBot";;
      *grok*)     BOT_USERNAME="GrokBot";;
      *deepseek*) BOT_USERNAME="DeepSeekBot";;
      *)          BOT_USERNAME="OpenCodeBot";;
    esac
    ;;
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

# ── Cleanup trap ──────────────────────────────────────────────────────────
# Without this, a Ctrl-C (or kill) mid-session leaked the per-session sandbox
# AND its spawned children (the harness CLI's process group: MCP server +
# Playwright + Chromium, plus the opencode watchdog). Orphaned Chromium/MCP
# are the heaviest contributors to the load-cascade class we're hardening
# against. CUR_SANDBOX is updated each iteration; CHILD_PIDS collects any
# backgrounded children (opencode + watchdog) so we can reap them too.
CUR_SANDBOX=""
CHILD_PIDS=()
cleanup() {
  trap - INT TERM EXIT
  # Kill backgrounded children's process groups (opencode run + ctx watchdog).
  for _pid in "${CHILD_PIDS[@]:-}"; do
    [ -n "$_pid" ] || continue
    _pgid=$(ps -o pgid= -p "$_pid" 2>/dev/null | tr -d ' ' || true)
    if [ -n "$_pgid" ] && [ "$_pgid" != "0" ]; then
      kill -- -"$_pgid" 2>/dev/null || true
    else
      kill "$_pid" 2>/dev/null || true
    fi
  done
  # Remove the in-flight session sandbox (resolved + bounded to /tmp prefix).
  if [ -n "$CUR_SANDBOX" ] && [ -d "$CUR_SANDBOX" ]; then
    case "$CUR_SANDBOX" in
      /tmp/kaetram_session_*) rm -rf "$CUR_SANDBOX" 2>/dev/null || true;;
    esac
  fi
}
trap cleanup INT TERM EXIT

# Fast-fail backoff state for the outer respawn loop. A session that exits
# almost immediately (auth failure, MCP can't reach the game server, etc.)
# must NOT be respawned every PAUSE_BETWEEN seconds — that's the uncapped
# respawn loop that storms MCP+Chromium. opencode has its own 429 backoff
# below; this guards the claude/codex/gemini paths.
CONSECUTIVE_FAST_FAILS=0
MIN_HEALTHY_SECS=60   # matches orchestrate.py _MIN_HEALTHY_UPTIME
MAX_BACKOFF=300

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"
  SESSION_STARTED_AT=$(date +%s)
  CHILD_PIDS=()
  INNER_BACKOFF_DONE=0   # set when a harness path already slept (e.g. opencode 429)

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

  # Bootstrap user message — single source of truth (bootstrap.py), same as
  # orchestrate.py and play_qwen.py. Keeps dev-loop input identical to the
  # collection path that generates training data.
  PROMPT=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from bootstrap import build_orchestrate_bootstrap
print(build_orchestrate_bootstrap('$PERSONALITY' or None, $SESSION), end='')
")

  # Codex exec is one-shot — needs explicit instruction to keep looping
  if [ "$HARNESS" = "codex" ]; then
    PROMPT="${PROMPT}

You must keep playing continuously — call tools in a loop for the ENTIRE session. After every action, call observe again and pick the next action. Do NOT stop after login. Do NOT stop after one action. Keep calling tools: observe → decide → act → observe → decide → act, hundreds of times. Never output a final message or conclude — just keep playing until the process is killed."
  fi

  # Run from isolated dir to prevent the CLI from reading this project's CLAUDE.md / AGENTS.md
  SANDBOX="/tmp/kaetram_session_${SESSION}_$$"
  CUR_SANDBOX="$SANDBOX"   # expose to cleanup trap
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
KAETRAM_STATE_DIR = "${SANDBOX}/state"

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
        "KAETRAM_STATE_DIR": "${SANDBOX}/state"
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
        KAETRAM_STATE_DIR="$SANDBOX/state" \
        timeout "${TIMEOUT_SECS}s" opencode run \
          --format json \
          --dangerously-skip-permissions \
          --dir "$SANDBOX" \
          "$PROMPT") \
        2>&1 | tee "$LOG_FILE" &
      OPENCODE_BG_PID=$!
      CHILD_PIDS+=("$OPENCODE_BG_PID")   # reap on trap

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
      CHILD_PIDS+=("$WATCHDOG_PID")   # reap on trap

      wait "$OPENCODE_BG_PID" 2>/dev/null || true
      kill "$WATCHDOG_PID" 2>/dev/null || true
      wait "$WATCHDOG_PID" 2>/dev/null || true

      # Rate-limit backoff: a session that produced no step_finish events is
      # almost certainly an opencode 429 (or upstream auth failure). Size the
      # backoff by the 429 signal and sleep here, then mark INNER_BACKOFF_DONE
      # so the generic outer crash-loop guard doesn't sleep a second time.
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
        INNER_BACKOFF_DONE=1
      fi
      ;;

    *)
      # Claude: resolve .mcp.json template and pass via --mcp-config (bypasses project .mcp.json)
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          -e "s|__STATE_DIR__|${SANDBOX}/state|g" \
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
  CUR_SANDBOX=""

  echo "=== Session $SESSION ended at $(date) ==="

  # Crash-loop guard for the outer respawn loop. If this session died almost
  # immediately it almost certainly failed at launch (auth/MCP/server) — keep
  # respawning at PAUSE_BETWEEN and we storm MCP+Chromium (the 2026-05-29
  # cascade class). Back off exponentially on consecutive fast failures.
  SESSION_ELAPSED=$(( $(date +%s) - SESSION_STARTED_AT ))
  if [ "$SESSION_ELAPSED" -lt "$MIN_HEALTHY_SECS" ]; then
    CONSECUTIVE_FAST_FAILS=$((CONSECUTIVE_FAST_FAILS + 1))
  else
    CONSECUTIVE_FAST_FAILS=0
  fi
  if [ "$INNER_BACKOFF_DONE" -eq 1 ]; then
    echo "Respawning (harness already backed off this session)..."
  elif [ "$CONSECUTIVE_FAST_FAILS" -gt 0 ]; then
    BACKOFF=$(( PAUSE_BETWEEN * (1 << (CONSECUTIVE_FAST_FAILS - 1)) ))
    [ "$BACKOFF" -gt "$MAX_BACKOFF" ] && BACKOFF="$MAX_BACKOFF"
    echo "Session failed fast (${SESSION_ELAPSED}s, ${CONSECUTIVE_FAST_FAILS}x) — backing off ${BACKOFF}s before respawn..."
    sleep "$BACKOFF"
  else
    echo "Pausing ${PAUSE_BETWEEN}s before next session..."
    sleep "$PAUSE_BETWEEN"
  fi
done
