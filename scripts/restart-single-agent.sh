#!/usr/bin/env bash
# Restart a single agent within a running orchestration.
#
# Kills the agent's CLI process so the orchestrator auto-restarts it.
# Optionally resets sandbox state and/or changes personality/harness.
#
# Usage:
#   ./scripts/restart-single-agent.sh 2                    # restart agent 2
#   ./scripts/restart-single-agent.sh 2 --reset             # reset state (fresh level 1)
#   ./scripts/restart-single-agent.sh 2 --codex             # switch to codex harness
#   ./scripts/restart-single-agent.sh 2 --claude             # switch to claude harness
#   ./scripts/restart-single-agent.sh 2 --personality grinder
#   ./scripts/restart-single-agent.sh 2 --reset --codex --personality explorer_tinkerer

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Parse args
AGENT_ID=""
RESET=false
NEW_HARNESS=""
NEW_PERSONALITY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset)       RESET=true; shift;;
    --claude)      NEW_HARNESS="claude"; shift;;
    --codex)       NEW_HARNESS="codex"; shift;;
    --gemini)      NEW_HARNESS="gemini"; shift;;
    --opencode)    NEW_HARNESS="opencode"; shift;;
    --personality) NEW_PERSONALITY="$2"; shift 2;;
    -h|--help)
      echo "Usage: $0 <agent_id> [--reset] [--claude|--codex|--gemini|--opencode] [--personality <name>]"
      echo ""
      echo "  agent_id         Agent number (0-7)"
      echo "  --reset          Clear sandbox state + reset DB (fresh level 1)"
      echo "  --claude         Switch agent to Claude CLI"
      echo "  --codex           Switch agent to Codex CLI"
      echo "  --gemini          Switch agent to Gemini CLI"
      echo "  --opencode        Switch agent to OpenCode CLI (NVIDIA Qwen free API)"
      echo "  --personality X  Change personality (grinder/completionist/explorer_tinkerer)"
      exit 0;;
    *)
      if [ -z "$AGENT_ID" ] && [[ "$1" =~ ^[0-9]+$ ]]; then
        AGENT_ID="$1"
      else
        echo "Unknown argument: $1" >&2; exit 1
      fi
      shift;;
  esac
done

if [ -z "$AGENT_ID" ]; then
  echo "ERROR: agent_id required. Usage: $0 <agent_id> [--reset] [--claude|--codex|--gemini|--opencode] [--personality <name>]" >&2
  exit 1
fi

SANDBOX="/tmp/kaetram_agent_$AGENT_ID"
METADATA="$SANDBOX/metadata.json"

if [ ! -d "$SANDBOX" ]; then
  echo "ERROR: No sandbox at $SANDBOX — agent $AGENT_ID doesn't exist" >&2
  exit 1
fi

# Read current metadata
if [ -f "$METADATA" ]; then
  CUR_USERNAME=$(python3 -c "import json; print(json.load(open('$METADATA')).get('username','Agent$AGENT_ID'))" 2>/dev/null || echo "Agent$AGENT_ID")
  CUR_HARNESS=$(python3 -c "import json; print(json.load(open('$METADATA')).get('harness','claude'))" 2>/dev/null || echo "claude")
  CUR_PERSONALITY=$(python3 -c "import json; print(json.load(open('$METADATA')).get('personality','grinder'))" 2>/dev/null || echo "grinder")
  CUR_PORT=$(python3 -c "import json; print(json.load(open('$METADATA')).get('server_port', $((9001 + AGENT_ID * 10))))" 2>/dev/null || echo "$((9001 + AGENT_ID * 10))")
else
  CUR_USERNAME="Agent$AGENT_ID"
  CUR_HARNESS="claude"
  CUR_PERSONALITY="grinder"
  CUR_PORT=$((9001 + AGENT_ID * 10))
fi

# Default harness is current harness (or claude if not set)
HARNESS="${NEW_HARNESS:-${CUR_HARNESS:-claude}}"
PERSONALITY="${NEW_PERSONALITY:-$CUR_PERSONALITY}"

# Validate personality
if [ -n "$NEW_PERSONALITY" ]; then
  case "$NEW_PERSONALITY" in
    grinder|completionist|explorer_tinkerer) ;;
    *) echo "ERROR: Invalid personality '$NEW_PERSONALITY'. Use: grinder, completionist, explorer_tinkerer" >&2; exit 1;;
  esac
fi

echo "=== Restarting Agent $AGENT_ID ==="
echo "  Username:    $CUR_USERNAME"
echo "  Harness:     $CUR_HARNESS → $HARNESS"
echo "  Personality: $CUR_PERSONALITY → $PERSONALITY"
echo "  Server port: $CUR_PORT"
[ "$RESET" = true ] && echo "  Reset:       YES (fresh level 1)"
echo ""

# ── Step 1: Kill the agent's CLI process + its MCP server ──
echo "Killing agent $AGENT_ID process..."
# Kill CLI processes scoped to this agent's sandbox or username
pkill -f "claude.*-p.*$SANDBOX\|claude.*-p.*$CUR_USERNAME" 2>/dev/null || true
pkill -f "codex.*$CUR_USERNAME" 2>/dev/null || true
# Kill MCP server + Playwright + Chromium for this agent
# The agent CLI is the process group leader (spawned with setsid by orchestrator),
# so find its children to identify the right MCP server
CLI_PID=$(pgrep -f "claude.*-p.*$CUR_USERNAME\|codex.*$CUR_USERNAME\|gemini.*$CUR_USERNAME\|opencode.*$CUR_USERNAME" 2>/dev/null | head -1 || true)
if [ -n "$CLI_PID" ]; then
  # Kill the entire process group (CLI + MCP + Playwright + Chromium)
  PGID=$(ps -o pgid= -p "$CLI_PID" 2>/dev/null | tr -d ' ')
  if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then
    kill -- -"$PGID" 2>/dev/null || true
    echo "  Killed process group $PGID (CLI + MCP + browser)"
  fi
fi
# Fallback: kill any MCP servers whose parent was the agent CLI
MCP_PIDS=$(pgrep -f "mcp_game_server.py" 2>/dev/null || true)
if [ -n "$MCP_PIDS" ]; then
  for pid in $MCP_PIDS; do
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    # Check if parent is dead (orphaned under init) — likely ours
    if [ "$ppid" = "1" ]; then
      # Check if it's using this agent's port
      if grep -q "KAETRAM_PORT.*$CUR_PORT" "/proc/$pid/environ" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        echo "  Killed orphaned MCP server (PID $pid)"
      fi
    fi
  done
fi
sleep 2

# ── Always clear session counter so agent starts fresh session ──
# (This ensures orchestrator increments the counter and starts a new session,
#  rather than resuming an incomplete one)
STATE_DIR="$SANDBOX/state"
rm -f "$STATE_DIR/.session_counter"
echo "  Cleared session counter — agent will start fresh session on restart"

# ── Step 2: Update metadata if harness or personality changed ──
if [ "$HARNESS" != "$CUR_HARNESS" ] || [ "$PERSONALITY" != "$CUR_PERSONALITY" ]; then
  # Compute new username based on harness
  case "$HARNESS" in
    codex)
      NEW_USERNAME="CodexBot$AGENT_ID"
      MODEL="gpt-5.4"
      ;;
    gemini)
      NEW_USERNAME="GeminiBot$AGENT_ID"
      MODEL="gemini-2.5-flash"
      ;;
    opencode)
      NEW_USERNAME="OpenCodeBot$AGENT_ID"
      MODEL="${OPENCODE_MODEL:-nvidia/qwen/qwen3-coder-480b-a35b-instruct}"
      ;;
    *)
      # claude or default
      NEW_USERNAME="ClaudeBot$AGENT_ID"
      MODEL="sonnet"
      ;;
  esac

  echo "Updating metadata..."
  python3 -c "
import json
meta = json.load(open('$METADATA')) if True else {}
meta['harness'] = '$HARNESS'
meta['model'] = '$MODEL'
meta['personality'] = '$PERSONALITY'
meta['mode'] = '$PERSONALITY'
meta['username'] = '$NEW_USERNAME'
json.dump(meta, open('$METADATA', 'w'))
print(f'  harness={meta[\"harness\"]}, personality={meta[\"personality\"]}, username={meta[\"username\"]}')
"
fi

# ── Step 3: Reset state if requested ──
if [ "$RESET" = true ]; then
  echo "Resetting sandbox state..."
  STATE_DIR="$SANDBOX/state"
  rm -f "$STATE_DIR/screenshot.png" \
        "$STATE_DIR/live_screen.png" \
        "$STATE_DIR/game_state.json" \
        "$STATE_DIR/.session_counter"
  find "$STATE_DIR" -name "*.png" -delete 2>/dev/null || true

  # Reset MongoDB player data
  MONGO_CONTAINER="kaetram-mongo"
  MONGO_DB="kaetram_devlopment"
  COLLECTIONS=(player_info player_skills player_equipment player_inventory player_bank player_quests player_achievements player_statistics player_abilities)

  # Determine which usernames to clear (all possible bot types for this agent ID)
  USERNAMES="'claudebot${AGENT_ID}','codexbot${AGENT_ID}','geminibot${AGENT_ID}','opencodebot${AGENT_ID}'"

  if docker ps --format '{{.Names}}' | grep -q "^${MONGO_CONTAINER}$"; then
    echo "Resetting MongoDB for agent $AGENT_ID..."
    for coll in "${COLLECTIONS[@]}"; do
      result=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
        var r = db.'"$coll"'.deleteMany({username: {$in: ['"$USERNAMES"']}});
        print(r.deletedCount);
      ' 2>/dev/null)
      [ "$result" != "0" ] && echo "  ${coll}: deleted ${result}"
    done
    echo "  Player will start fresh on login."
  else
    echo "  WARNING: MongoDB container not running — skipping DB reset"
  fi
fi

echo ""
echo "=== Agent $AGENT_ID killed — orchestrator will auto-restart it ==="
echo "  Monitor: tail -f /tmp/orchestrate.log"
