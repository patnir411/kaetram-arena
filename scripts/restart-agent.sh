#!/usr/bin/env bash
# Restart the multi-agent training run.
#
# What it does:
#   1. Kills the running orchestrator + all claude agent processes
#   2. Kills game server processes (orchestrator restarts them)
#   3. Preserves session logs in dataset/raw/ (training data)
#   4. Clears transient state (game_state, progress) per agent sandbox
#   5. Restarts orchestrator in the "datacol" tmux session
#   6. Ensures dashboard is running on :8080
#
# Usage:
#   ./scripts/restart-agent.sh              # 3 agents, 24 hours (defaults — one per archetype)
#   ./scripts/restart-agent.sh 2            # 2 agents, 24 hours
#   ./scripts/restart-agent.sh 3 8          # 3 agents, 8 hours
#   ./scripts/restart-agent.sh 3 0          # 3 agents, no time limit
#   ./scripts/restart-agent.sh --grinder 1 --completionist 1 --explorer 1
#   ./scripts/restart-agent.sh --opencode --opencode-model qwen3.5-35a3b --hours 3
#   ./scripts/restart-agent.sh --qwen-sft 3 --grinder 1 --completionist 1 --explorer 1
#   ./scripts/restart-agent.sh --qwen-base 3 --grinder 1 --completionist 1 --explorer 1
#   ./scripts/restart-agent.sh --qwen-sft 1 --qwen-base 1   # mixed A/B run
#
# OpenCode model aliases (resolve to opencode.template.json model IDs):
#   grok-4-1-fast     | qwen3.5-35a3b   | qwen3.5-397a17b
#   qwen3-80a3b       | deepseek-v4-flash | deepseek-v4-pro
# (or pass a fully-qualified provider/model ID directly)

set -euo pipefail

# ── --help / -h guard (auto-injected) ────────────────────────────────────────
for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done


PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_kill_helpers.sh
source "$SCRIPT_DIR/_kill_helpers.sh"

# Defaults
N_AGENTS=""
HOURS="24"
MAX_BUDGET=""
N_GRINDER=""
N_COMPLETIONIST=""
N_EXPLORER_TINKERER=""
N_CLAUDE=""
N_CODEX=""
N_GEMINI=""
N_OPENCODE=""
N_QWEN_SFT=""
N_QWEN_BASE=""
OPENCODE_MODEL=""

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --grinder)            N_GRINDER="$2"; shift 2;;
    --completionist)      N_COMPLETIONIST="$2"; shift 2;;
    --explorer-tinkerer|--explorer)  N_EXPLORER_TINKERER="$2"; shift 2;;
    --hours)       HOURS="$2"; shift 2;;
    --max-budget-usd) MAX_BUDGET="$2"; shift 2;;
    --opencode-model) OPENCODE_MODEL="$2"; shift 2;;
    --claude)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_CLAUDE="$2"; shift 2
      else
        N_CLAUDE="-1"; shift  # bare --claude = all agents
      fi
      ;;
    --codex)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_CODEX="$2"; shift 2
      else
        N_CODEX="-1"; shift  # bare --codex = all agents
      fi
      ;;
    --gemini)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_GEMINI="$2"; shift 2
      else
        N_GEMINI="-1"; shift  # bare --gemini = all agents
      fi
      ;;
    --opencode)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_OPENCODE="$2"; shift 2
      else
        N_OPENCODE="-1"; shift  # bare --opencode = all agents
      fi
      ;;
    --qwen-sft)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_QWEN_SFT="$2"; shift 2
      else
        N_QWEN_SFT="-1"; shift  # bare --qwen-sft = all agents
      fi
      ;;
    --qwen-base)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        N_QWEN_BASE="$2"; shift 2
      else
        N_QWEN_BASE="-1"; shift  # bare --qwen-base = all agents
      fi
      ;;
    *)
      # Positional: first=agents, second=hours
      if [ -z "$N_AGENTS" ]; then N_AGENTS="$1"
      else HOURS="$1"; fi
      shift;;
  esac
done

# Determine total agent count for cleanup and orchestrator
HAS_PERSONALITY=false
PERSONALITY_ARGS=""
TOTAL_AGENTS=0
declare -A _PERSONALITY_FLAGS=(
  [grinder]=N_GRINDER
  [completionist]=N_COMPLETIONIST
  [explorer-tinkerer]=N_EXPLORER_TINKERER
)
for p in grinder completionist explorer-tinkerer; do
  var="${_PERSONALITY_FLAGS[$p]}"
  count="${!var}"
  if [ -n "$count" ] && [ "$count" -gt 0 ]; then
    HAS_PERSONALITY=true
    PERSONALITY_ARGS="$PERSONALITY_ARGS --$p $count"
    TOTAL_AGENTS=$((TOTAL_AGENTS + count))
  fi
done

if ! $HAS_PERSONALITY; then
  N_AGENTS="${N_AGENTS:-3}"
  TOTAL_AGENTS="$N_AGENTS"
fi

# ── Agent-count validation (mirrors orchestrate.py's 1-8 cap) ──
# The seed/cleanup loops below run BEFORE orchestrate.py launches, so an
# out-of-range value would drive Mongo seeding / sandbox loops with garbage
# before orchestrate's own guard could reject it. Validate up front.
if ! [[ "$TOTAL_AGENTS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: agent count must be a non-negative integer (got '$TOTAL_AGENTS')." >&2
  exit 1
fi
if [ "$TOTAL_AGENTS" -lt 1 ] || [ "$TOTAL_AGENTS" -gt 8 ]; then
  echo "ERROR: agent count must be 1-8 (got $TOTAL_AGENTS). The box has 8 vCPUs;" >&2
  echo "       each agent runs a game server + Xvfb + ffmpeg + Chromium." >&2
  exit 1
fi
# Oversubscription warning: 3 agents is the standard run on this 8-vCPU box.
# Running >4 here risks the CPU/IO load cascade that froze the VM on 2026-05-29.
if [ "$TOTAL_AGENTS" -gt 4 ]; then
  echo "WARNING: $TOTAL_AGENTS agents oversubscribes the 8-vCPU box (each agent =" >&2
  echo "         game server + Xvfb + ffmpeg + Chromium). Standard run is 3." >&2
fi

echo "=== Restarting Kaetram training run ==="
if $HAS_PERSONALITY; then
  [ -n "$N_GRINDER" ] && [ "$N_GRINDER" -gt 0 ] && echo "  Grinder:            $N_GRINDER"
  [ -n "$N_COMPLETIONIST" ] && [ "$N_COMPLETIONIST" -gt 0 ] && echo "  Completionist:      $N_COMPLETIONIST"
  [ -n "$N_EXPLORER_TINKERER" ] && [ "$N_EXPLORER_TINKERER" -gt 0 ] && echo "  Explorer/Tinkerer:  $N_EXPLORER_TINKERER"
  echo "  Total:    $TOTAL_AGENTS"
else
  echo "  Agents: $TOTAL_AGENTS (round-robin personalities)"
fi
# Show harness breakdown
HARNESS_DESC=""
[ -n "$N_CLAUDE" ] && [ "$N_CLAUDE" != "-1" ] && [ "$N_CLAUDE" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_CLAUDE Claude"
[ -n "$N_CODEX" ] && [ "$N_CODEX" != "-1" ] && [ "$N_CODEX" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_CODEX Codex"
[ -n "$N_GEMINI" ] && [ "$N_GEMINI" != "-1" ] && [ "$N_GEMINI" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_GEMINI Gemini"
[ -n "$N_OPENCODE" ] && [ "$N_OPENCODE" != "-1" ] && [ "$N_OPENCODE" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_OPENCODE OpenCode"
[ -n "$N_QWEN_SFT" ] && [ "$N_QWEN_SFT" != "-1" ] && [ "$N_QWEN_SFT" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_QWEN_SFT Qwen-SFT"
[ -n "$N_QWEN_BASE" ] && [ "$N_QWEN_BASE" != "-1" ] && [ "$N_QWEN_BASE" -gt 0 ] 2>/dev/null && HARNESS_DESC="${HARNESS_DESC}${HARNESS_DESC:+ + }$N_QWEN_BASE Qwen-Base"
[ "$N_CODEX" = "-1" ] && HARNESS_DESC="all Codex"
[ "$N_GEMINI" = "-1" ] && HARNESS_DESC="all Gemini"
[ "$N_OPENCODE" = "-1" ] && HARNESS_DESC="all OpenCode"
[ "$N_QWEN_SFT" = "-1" ] && HARNESS_DESC="all Qwen-SFT"
[ "$N_QWEN_BASE" = "-1" ] && HARNESS_DESC="all Qwen-Base"
[ -z "$HARNESS_DESC" ] && HARNESS_DESC="all Claude"
echo "  Harness: $HARNESS_DESC"
[ -n "$OPENCODE_MODEL" ] && echo "  OpenCode model: $OPENCODE_MODEL"
echo "  Hours:  ${HOURS}"
echo ""

# ── Step 1: Kill orchestrator + agents + MCP servers + browsers ──
# All kill_scoped calls below are gated by scripts/_kill_helpers.sh — they
# only target data-collection processes (sandbox /tmp/kaetram_agent_<N> or
# data-collection ports 9001..9051) and explicitly skip eval lanes
# (9061/9071) and the e2e test lane (9191).
echo "Stopping orchestrator and agents..."
# Kill orchestrate.py process specifically (not tmux/shell wrappers)
pkill -f "python3 orchestrate.py" 2>/dev/null || true
sleep 1
# Kill the datacol tmux session (holds shell wrappers)
tmux kill-session -t datacol 2>/dev/null || true
# Disable abort-on-error for the teardown — one non-zero kill must not skip
# the SIGKILL rounds or livestream cleanup. Re-enabled after.
set +e
# SIGTERM round (scoped)
kill_scoped "claude -p"            TERM
kill_scoped "codex.*exec"          TERM
kill_scoped "gemini.*-p"           TERM
kill_scoped "opencode run"         TERM
kill_scoped "timeout .* opencode"  TERM
kill_scoped "play.sh"              TERM
kill_scoped "play_qwen.py"         TERM
sleep 2
# SIGKILL round
kill_scoped "claude -p"            KILL
kill_scoped "opencode run"         KILL
kill_scoped "timeout .* opencode"  KILL
# MCP servers, Playwright, game_driver — scoped.
kill_scoped "mcp_game_server.py"   TERM
kill_scoped "playwright/driver/node" TERM
kill_scoped "npm exec @playwright" TERM
kill_scoped "playwright-mcp"       TERM
kill_scoped "game_driver.py"       TERM
# Chrome process groups — scoped via pgid.
kill_scoped_chrome_pgroup TERM
sleep 2
# Force-kill any surviving MCP/Playwright/Chrome processes (still scoped).
kill_scoped "mcp_game_server.py"     KILL
kill_scoped "playwright/driver/node" KILL
kill_scoped "npm exec @playwright"   KILL
kill_scoped "playwright-mcp"         KILL
kill_scoped_chrome_pgroup KILL

# ── Livestream pipeline cleanup: Xvfb + ffmpeg + HLS segments ──
kill_kaetram_livestream KILL
rm -rf /tmp/hls/agent_* 2>/dev/null || true
set -e  # teardown done; restore strict mode for setup below
mkdir -p /tmp/hls 2>/dev/null || true
for i in $(seq 0 $((TOTAL_AGENTS - 1))); do
  mkdir -p "/tmp/hls/agent_$i" 2>/dev/null || true
done

# ── Step 2: Kill game server instances (data-collection ports only) ──
# Never touch :9000 (client), :9061/:9071 (eval lanes), or :9191 (e2e tests).
echo "Stopping data-collection game servers (preserving client/eval/test)..."
for port in "${KAETRAM_DATA_PORTS[@]}"; do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "  Killed server on :$port (PID $pid)"
  fi
done
sleep 1

# ── Step 3: Reset MongoDB player data (fresh characters) ──
MONGO_CONTAINER="kaetram-mongo"
MONGO_DB="kaetram_devlopment"
COLLECTIONS=(player_info player_skills player_equipment player_inventory player_bank player_quests player_achievements player_statistics player_abilities)

if docker ps --format '{{.Names}}' | grep -q "^${MONGO_CONTAINER}$"; then
  USER_JS_ARRAY=""
  for i in $(seq 0 $((TOTAL_AGENTS - 1))); do
    [ -n "$USER_JS_ARRAY" ] && USER_JS_ARRAY="${USER_JS_ARRAY},"
    USER_JS_ARRAY="${USER_JS_ARRAY}'claudebot${i}','codexbot${i}','geminibot${i}','opencodebot${i}','bigqwenbot${i}','grokbot${i}','deepseekbot${i}'"
  done
  # Qwen personality-based usernames (not numeric — see orchestrate.py
  # qwen_username_map). Always include all three so a Qwen run with any
  # personality mix gets a clean Mongo state on restart.
  USER_JS_ARRAY="${USER_JS_ARRAY},'qwengrinder','qwencompletionist','qwenexplorer'"

  echo "Resetting player data in MongoDB..."
  for coll in "${COLLECTIONS[@]}"; do
    result=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
      var r = db.'"$coll"'.deleteMany({username: {$in: ['"$USER_JS_ARRAY"']}});
      print(r.deletedCount);
    ' 2>/dev/null)
    [ "$result" != "0" ] && echo "  ${coll}: deleted ${result}"
  done
  echo "  Players will start fresh on login."

  # Pre-seed accounts with the known bcrypt hash so the first login attempt
  # succeeds. Kaetram's client sometimes doesn't surface the "account not
  # found" error into #login-error-text fast enough for login.py's retry
  # loop to catch, so relying on register-on-fail is unreliable across
  # harnesses. Seeding every possible bot name for every active agent is
  # idempotent and cheap.
  echo "Seeding bot accounts (password=test)..."
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python3" - <<PYEOF
from tests.e2e.helpers.seed import seed_player
PREFIXES = ("claudebot", "codexbot", "geminibot", "opencodebot",
            "bigqwenbot", "grokbot", "deepseekbot")
QWEN_NAMES = ("qwengrinder", "qwencompletionist", "qwenexplorer")
n = 0
for i in range($TOTAL_AGENTS):
    for prefix in PREFIXES:
        seed_player(f"{prefix}{i}"); n += 1
for name in QWEN_NAMES:
    seed_player(name); n += 1
print(f"  Seeded {n} bot rows.")
PYEOF

  # Opt-in mid-quest seeding (OPD bucket-B collection): re-seed the Qwen rows
  # at the Herbalist stage-1 wall instead of post-tutorial vanilla.
  if [ -n "${KAETRAM_SEED_WALL:-}" ]; then
    echo "Re-seeding Qwen agents at the Herbalist wall (KAETRAM_SEED_WALL=$KAETRAM_SEED_WALL)..."
    PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python3" \
      "$PROJECT_DIR/scripts/opd/seed_herbalist_wall.py"
  fi

  # Opt-in milestone-ladder seeding (OPD): per-personality milestones,
  # lane set A, B, or C — see scripts/opd/seed_milestones.py.
  if [ -n "${KAETRAM_SEED_MILESTONES:-}" ]; then
    echo "Re-seeding Qwen agents at round-3 milestones (lane set $KAETRAM_SEED_MILESTONES)..."
    PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python3" \
      "$PROJECT_DIR/scripts/opd/seed_milestones.py" "$KAETRAM_SEED_MILESTONES"
  fi
else
  echo "WARNING: MongoDB container not running — skipping DB reset"
fi
echo ""

# ── Step 4: Preserve logs, clear transient state + stale sandbox files ──
echo "Clearing agent sandbox state (logs preserved)..."
for i in $(seq 0 $((TOTAL_AGENTS - 1))); do
  sandbox="/tmp/kaetram_agent_$i"
  clear_sandbox_state_reset "$i"
  # Clean stale files from previous architectures (old scripts, workarounds)
  rm -f "$sandbox"/*.js "$sandbox"/*.py "$sandbox"/package.json "$sandbox"/package-lock.json 2>/dev/null
  rm -rf "$sandbox"/node_modules "$sandbox"/ipc 2>/dev/null
  echo "  Cleared /tmp/kaetram_agent_$i/"
done

# Clean stale Claude Code project memory for agent sandboxes (prevents MCP bypass behavior)
rm -rf /home/user/.claude/projects/-tmp-kaetram-agent-*/memory/ 2>/dev/null && echo "  Cleared agent project memories"
# Kill orphaned Chrome/chromium processes from agent sandboxes (scoped).
kill_scoped_chrome_pgroup TERM
kill_scoped "chromium.*kaetram" TERM

# Also clear single-agent state
rm -f "$PROJECT_DIR/state/game_state.json"

# Count preserved logs
LOG_COUNT=$(find "$PROJECT_DIR/dataset/raw" -name "session_*.log" 2>/dev/null | wc -l)
echo "  Preserved $LOG_COUNT session logs in dataset/raw/"
echo ""

# ── Step 5: Ensure Kaetram client is running on :9000 ──
if ! ss -tlnp "sport = :9000" 2>/dev/null | grep -q 9000; then
  echo "WARNING: Kaetram client not running on :9000"
  echo "  Start it first:  ./scripts/start-kaetram.sh"
  echo "  (run in the 'kaetram' tmux session)"
  echo ""
fi

# ── Step 6: Restart dashboard if not running ──
if ! ss -tlnp "sport = :8080" 2>/dev/null | grep -q 8080; then
  echo "Starting dashboard on :8080..."
  cd "$PROJECT_DIR"
  nohup .venv/bin/python3 dashboard.py > /tmp/dashboard.log 2>&1 &
  echo "  Dashboard PID: $!"
else
  echo "Dashboard already running on :8080"
fi

# Ensure both reasoning-capture proxies are running for any opencode agents:
#   - NIM proxy (:8889) — flattens extraBody + rewrites reasoning_content for
#     NVIDIA NIM (Qwen thinking models).
#   - DeepSeek proxy (:8890) — same SSE rewrite pointed at api.deepseek.com,
#     because opencode 1.14.29's @ai-sdk/openai-compatible provider doesn't
#     read DeepSeek's delta.reasoning_content (issue #24097).
# Cheap when no opencode agents are launched, so always start.
if [ -n "$N_OPENCODE" ] && [ "$N_OPENCODE" != "0" ]; then
  if ! ss -lnt 'sport = :8889' | grep -q LISTEN; then
    echo "Starting NIM proxy on 127.0.0.1:8889 ..."
    "$PROJECT_DIR/scripts/start-nim-proxy.sh" || echo "  (NIM proxy start failed — Qwen reasoning won't surface)"
  else
    echo "NIM proxy already running on 127.0.0.1:8889"
  fi
  if ! ss -lnt 'sport = :8890' | grep -q LISTEN; then
    echo "Starting DeepSeek proxy on 127.0.0.1:8890 ..."
    "$PROJECT_DIR/scripts/start-deepseek-proxy.sh" || echo "  (DeepSeek proxy start failed — V4 reasoning won't surface)"
  else
    echo "DeepSeek proxy already running on 127.0.0.1:8890"
  fi
fi

# ── Step 7: Launch orchestrator in datacol tmux session ──
echo "Launching orchestrator ($TOTAL_AGENTS agents, $HOURS hours)..."

# Base Qwen runs enable observe compaction (drops the ASCII map from observe →
# more turns/session). Prefixed onto the python invocation so it survives the
# tmux → orchestrate → play_qwen → MCP chain.
ENV_PREFIX=""
if [ -n "$N_QWEN_BASE" ]; then
  ENV_PREFIX="KAETRAM_OBSERVE_COMPACT=1 "
  # Forward a custom base-model endpoint (e.g. the self-hosted Qwen3.5-27B
  # deploy) so it reaches orchestrate → play_qwen across the tmux boundary,
  # where a plain exported env var wouldn't reliably survive.
  [ -n "${KAETRAM_QWEN_BASE_ENDPOINT:-}" ] && \
    ENV_PREFIX="${ENV_PREFIX}KAETRAM_QWEN_BASE_ENDPOINT=${KAETRAM_QWEN_BASE_ENDPOINT} "
  # Variant label when the base endpoint serves a non-default model (e.g. 27B).
  [ -n "${KAETRAM_QWEN_BASE_MODEL:-}" ] && \
    ENV_PREFIX="${ENV_PREFIX}KAETRAM_QWEN_BASE_MODEL=${KAETRAM_QWEN_BASE_MODEL} "
fi
# Caller-forced observe compaction: --qwen-sft evals of 2B-family checkpoints must
# match the compact-observe shape of their --qwen-base baseline runs.
[ -z "$N_QWEN_BASE" ] && [ -n "${KAETRAM_OBSERVE_COMPACT:-}" ] && \
  ENV_PREFIX="${ENV_PREFIX}KAETRAM_OBSERVE_COMPACT=${KAETRAM_OBSERVE_COMPACT} "
[ -n "$N_QWEN_SFT" ] && [ -n "${KAETRAM_QWEN_SFT_ENDPOINT:-}" ] && \
  ENV_PREFIX="${ENV_PREFIX}KAETRAM_QWEN_SFT_ENDPOINT=${KAETRAM_QWEN_SFT_ENDPOINT} "
# Variant label when the SFT endpoint serves a non-default checkpoint (e.g. 2b-opd-r1).
[ -n "$N_QWEN_SFT" ] && [ -n "${KAETRAM_QWEN_SFT_MODEL:-}" ] && \
  ENV_PREFIX="${ENV_PREFIX}KAETRAM_QWEN_SFT_MODEL=${KAETRAM_QWEN_SFT_MODEL} "
# Harness-side recovery of malformed tool calls (play_qwen _TOOL_RECOVERY).
[ -n "${KAETRAM_TOOL_RECOVERY:-}" ] && \
  ENV_PREFIX="${ENV_PREFIX}KAETRAM_TOOL_RECOVERY=${KAETRAM_TOOL_RECOVERY} "
if $HAS_PERSONALITY; then
  ORCH_CMD="cd $PROJECT_DIR && ${ENV_PREFIX}python3 orchestrate.py $PERSONALITY_ARGS"
else
  ORCH_CMD="cd $PROJECT_DIR && ${ENV_PREFIX}python3 orchestrate.py --agents $N_AGENTS"
fi
if [ "$HOURS" != "0" ]; then
  ORCH_CMD="$ORCH_CMD --hours $HOURS"
fi
[ -n "$MAX_BUDGET" ] && ORCH_CMD="$ORCH_CMD --max-budget-usd $MAX_BUDGET"
[ -n "$N_CLAUDE" ] && ORCH_CMD="$ORCH_CMD --claude $N_CLAUDE"
[ -n "$N_CODEX" ] && ORCH_CMD="$ORCH_CMD --codex $N_CODEX"
[ -n "$N_GEMINI" ] && ORCH_CMD="$ORCH_CMD --gemini $N_GEMINI"
[ -n "$N_OPENCODE" ] && ORCH_CMD="$ORCH_CMD --opencode $N_OPENCODE"
[ -n "$N_QWEN_SFT" ] && ORCH_CMD="$ORCH_CMD --qwen-sft $N_QWEN_SFT"
[ -n "$N_QWEN_BASE" ] && ORCH_CMD="$ORCH_CMD --qwen-base $N_QWEN_BASE"
[ -n "$OPENCODE_MODEL" ] && ORCH_CMD="$ORCH_CMD --opencode-model $OPENCODE_MODEL"
ORCH_CMD="$ORCH_CMD 2>&1 | tee /tmp/orchestrate.log"

# Send to existing datacol session, or create one
if tmux has-session -t datacol 2>/dev/null; then
  # Send Ctrl-C first to clear any leftover prompt, then the command
  tmux send-keys -t datacol C-c 2>/dev/null || true
  sleep 0.5
  tmux send-keys -t datacol "$ORCH_CMD" Enter
else
  tmux new-session -d -s datacol -c "$PROJECT_DIR" "$ORCH_CMD"
fi

echo ""
echo "=== Training run restarted ==="
echo "  Orchestrator: tmux attach -t datacol"
echo "  Dashboard:    http://localhost:8080"
echo "  Logs:         $PROJECT_DIR/dataset/raw/agent_*/runs/"
echo ""
echo "  Monitor: tail -f /tmp/orchestrate.log"
