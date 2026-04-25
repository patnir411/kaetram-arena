#!/usr/bin/env bash
# Reset player data so agents start fresh on next login.
#
# Deletes all MongoDB records for all agent bots and clears sandbox state.
# Game servers must NOT be running — they cache player data in memory and
# would re-save stale state to DB on next autosave/logout.
#
# Usage:
#   ./scripts/reset-state.sh              # reset all 4 agents (default)
#   ./scripts/reset-state.sh 4            # reset agents 0-3
#   ./scripts/reset-state.sh 2            # reset agents 0-1 only
#   ./scripts/reset-state.sh 4 --force    # skip safety check (use with caution)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N_AGENTS="${1:-4}"
FORCE=false
[[ "${2:-}" == "--force" ]] && FORCE=true

MONGO_CONTAINER="kaetram-mongo"
MONGO_DB="kaetram_devlopment"

# All MongoDB collections that store per-player data
COLLECTIONS=(
  player_info
  player_skills
  player_equipment
  player_inventory
  player_bank
  player_quests
  player_achievements
  player_statistics
  player_abilities
)

echo "=== Reset Kaetram Agent State ==="
echo "  Agents to reset: 0 through $((N_AGENTS - 1))"
echo ""

# ── Safety check: refuse if agents or game servers are running ──
if [ "$FORCE" != "true" ]; then
  ORCH_COUNT=$(pgrep -c -f "python3 orchestrate.py" 2>/dev/null || echo 0)
  CLAUDE_COUNT=$(pgrep -c -f "claude -p" 2>/dev/null || echo 0)
  CODEX_COUNT=$(pgrep -c -f "codex.*exec" 2>/dev/null || echo 0)
  AGENT_COUNT=$((CLAUDE_COUNT + CODEX_COUNT))
  PLAY_COUNT=$(pgrep -c -f "play.sh" 2>/dev/null || echo 0)

  # Check game server ports
  SERVERS_UP=0
  for i in $(seq 0 $((N_AGENTS - 1))); do
    port=$((9001 + i * 10))
    if ss -tlnp "sport = :$port" 2>/dev/null | grep -q "$port"; then
      SERVERS_UP=$((SERVERS_UP + 1))
    fi
  done

  if [ "$ORCH_COUNT" -gt 0 ] || [ "$AGENT_COUNT" -gt 0 ] || [ "$PLAY_COUNT" -gt 0 ] || [ "$SERVERS_UP" -gt 0 ]; then
    echo "ERROR: Cannot reset while agents or game servers are running."
    echo "  Orchestrator processes: $ORCH_COUNT"
    echo "  Agent processes:        $AGENT_COUNT (Claude: $CLAUDE_COUNT, Codex: $CODEX_COUNT)"
    echo "  Game servers on ports:  $SERVERS_UP"
    echo ""
    echo "Stop them first:  ./scripts/nuke-agents.sh"
    echo "Or force reset:   ./scripts/reset-state.sh $N_AGENTS --force"
    exit 1
  fi
fi

# ── Check MongoDB is accessible ──
if ! docker ps --format '{{.Names}}' | grep -q "^${MONGO_CONTAINER}$"; then
  echo "ERROR: MongoDB container '$MONGO_CONTAINER' is not running."
  echo "  Start it: docker start $MONGO_CONTAINER"
  exit 1
fi

# ── Build username list (all harness types) ──
USERNAMES=()
USER_JS_ARRAY=""
for i in $(seq 0 $((N_AGENTS - 1))); do
  for prefix in claudebot codexbot geminibot opencodebot; do
    USERNAMES+=("${prefix}${i}")
    [ -n "$USER_JS_ARRAY" ] && USER_JS_ARRAY="${USER_JS_ARRAY},"
    USER_JS_ARRAY="${USER_JS_ARRAY}'${prefix}${i}'"
  done
done

echo "Players to reset: ${USERNAMES[*]}"
echo ""

# ── Show current state before reset ──
echo "Current DB state:"
docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
  var users = ['"$USER_JS_ARRAY"'];
  users.forEach(function(u) {
    var info = db.player_info.findOne({username: u}, {_id:0, username:1, x:1, y:1, hitPoints:1});
    var skills = db.player_skills.findOne({username: u});
    var skillSummary = "none";
    if (skills && skills.skills) {
      var active = skills.skills.filter(function(s) { return s.experience > 0; }).map(function(s) { return "type" + s.type + ":" + s.experience + "xp"; });
      if (active.length > 0) skillSummary = active.join(", ");
    }
    if (info) {
      print("  " + u + ": HP=" + (info.hitPoints||"?") + " pos=(" + info.x + "," + info.y + ") skills=[" + skillSummary + "]");
    } else {
      print("  " + u + ": not in DB");
    }
  });
' 2>/dev/null
echo ""

# ── Confirm ──
if [ "$FORCE" != "true" ]; then
  read -p "Delete all data for these players? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# ── Delete from all MongoDB collections ──
echo "Deleting from MongoDB..."
for coll in "${COLLECTIONS[@]}"; do
  result=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
    var r = db.'"$coll"'.deleteMany({username: {$in: ['"$USER_JS_ARRAY"']}});
    print(r.deletedCount);
  ' 2>/dev/null)
  echo "  ${coll}: deleted ${result} records"
done
echo ""

# ── Clear sandbox state ──
echo "Clearing sandbox state..."
for i in $(seq 0 $((N_AGENTS - 1))); do
  sandbox="/tmp/kaetram_agent_$i/state"
  if [ -d "$sandbox" ]; then
    rm -f "$sandbox/screenshot.png" \
          "$sandbox/live_screen.png" \
          "$sandbox/game_state.json" \
          "$sandbox/.session_counter"
    # Remove any extra screenshots agents may have created
    find "$sandbox" -name "*.png" -delete 2>/dev/null || true
    echo "  Cleared /tmp/kaetram_agent_$i/state/"
  else
    echo "  /tmp/kaetram_agent_$i/state/ does not exist (OK)"
  fi
done

# Also clear single-agent state
rm -f "$PROJECT_DIR/state/screenshot.png" \
      "$PROJECT_DIR/state/live_screen.png" \
      "$PROJECT_DIR/state/game_state.json"
echo ""

# ── Verify ──
echo "Verifying reset..."
REMAINING=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
  print(db.player_info.countDocuments({username: {$in: ['"$USER_JS_ARRAY"']}}));
' 2>/dev/null)
if [ "$REMAINING" = "0" ]; then
  echo "  All player data deleted successfully."
else
  echo "  WARNING: $REMAINING player_info records still remain!"
fi

echo ""
echo "=== Reset complete ==="
echo "  Next: ./scripts/restart-agent.sh $N_AGENTS"
echo "  Agents will create fresh Level 1 characters on login."
