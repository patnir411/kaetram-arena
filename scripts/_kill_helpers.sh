# Shared scoping helpers for nuke/restart/resume scripts.
#
# Why: broad `pkill -f <pattern>` matches any process on the host whose
# cmdline contains the pattern — including in-flight eval lanes that share
# the same MCP/Playwright/Chrome binaries. This file defines `kill_scoped`
# which only kills pids it can prove belong to data-collection runs.
#
# A pid is treated as data-collection if any of:
#   1. its cmdline contains a sandbox prefix (/tmp/kaetram_agent_<N>)
#   2. its environ has HOME or KAETRAM_STATE_DIR pointing at a sandbox
#   3. an ancestor (up to 5 hops) is orchestrate.py or play.sh
#   4. it holds a TCP listener on a data-collection game-server port
# AND it does NOT hold a listener on a protected port (eval/test).
#
# Source this file at the top of nuke-agents.sh / restart-agent.sh /
# resume-agent.sh, then call:  kill_scoped <pgrep-pattern> [signal]

KAETRAM_SANDBOX_PREFIX="/tmp/kaetram_agent_"
# Data-collection game-server ports (orchestrate.py PORT_STRIDE=10, max 6 slots).
KAETRAM_DATA_PORTS=(9001 9011 9021 9031 9041 9051)
# Protected: eval lanes (9061/9071) and the e2e test lane (9191). Never touch.
KAETRAM_PROTECTED_PORTS=(9061 9071 9191)

_ks_cmdline() {
  [ -r "/proc/$1/cmdline" ] || return 1
  tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null
}

_ks_environ_has() {
  [ -r "/proc/$1/environ" ] || return 1
  tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | grep -q "$2"
}

_ks_holds_protected_port() {
  local pid="$1" port
  for port in "${KAETRAM_PROTECTED_PORTS[@]}"; do
    if ss -tlnp "sport = :$port" 2>/dev/null | grep -q "pid=$pid,"; then
      return 0
    fi
  done
  return 1
}

_ks_holds_data_port() {
  local pid="$1" port
  for port in "${KAETRAM_DATA_PORTS[@]}"; do
    if ss -tlnp "sport = :$port" 2>/dev/null | grep -q "pid=$pid,"; then
      return 0
    fi
  done
  return 1
}

_ks_is_data_collection() {
  local pid="$1" cmd
  cmd="$(_ks_cmdline "$pid" 2>/dev/null || true)"
  [ -n "$cmd" ] || return 1

  # 1) Sandbox path appears anywhere in cmdline (covers MCP --extractor,
  #    opencode --dir, claude HOME=..., etc.).
  if printf '%s' "$cmd" | grep -q "$KAETRAM_SANDBOX_PREFIX"; then
    return 0
  fi
  # 2) Environment variables point at a sandbox.
  if _ks_environ_has "$pid" "^HOME=$KAETRAM_SANDBOX_PREFIX"; then return 0; fi
  if _ks_environ_has "$pid" "^KAETRAM_STATE_DIR=$KAETRAM_SANDBOX_PREFIX"; then return 0; fi
  # 2b) Cwd points at a sandbox. Catches opencode subprocesses launched by
  #     orchestrate.py with cwd=$SANDBOX but no --dir flag and no KAETRAM_*
  #     env (orchestrate doesn't set KAETRAM_STATE_DIR for the harness env).
  #     Without this, ancestor walk (#4) fails after orchestrate is killed
  #     first and opencode gets reparented to init.
  local cwd_link
  cwd_link="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  if [ -n "$cwd_link" ] && printf '%s' "$cwd_link" | grep -q "^$KAETRAM_SANDBOX_PREFIX"; then
    return 0
  fi
  # 3) Holds a data-collection port directly.
  if _ks_holds_data_port "$pid"; then return 0; fi
  # 4) Walk up to 5 ancestors looking for orchestrate.py / play.sh.
  local cur="$pid" ppid pcmd
  for _ in 1 2 3 4 5; do
    ppid="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ' || true)"
    if [ -z "$ppid" ] || [ "$ppid" = "1" ] || [ "$ppid" = "0" ]; then
      break
    fi
    pcmd="$(_ks_cmdline "$ppid" 2>/dev/null || true)"
    if printf '%s' "$pcmd" | grep -qE "(orchestrate\.py|/play\.sh)"; then
      return 0
    fi
    cur="$ppid"
  done
  return 1
}

# kill_scoped <pgrep-pattern> [signal]
# Default signal is TERM. Use 'KILL' for SIGKILL.
kill_scoped() {
  local pattern="$1" sig="${2:-TERM}" pid
  for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
    [ -n "$pid" ] || continue
    if _ks_holds_protected_port "$pid"; then continue; fi
    if _ks_is_data_collection "$pid"; then
      kill "-$sig" "$pid" 2>/dev/null || true
    fi
  done
}

# Wipe transient sandbox state for an agent's fresh-start orchestration.
# Single source of truth so reset paths can't drift apart.
#
# Removes (transient state):
#   state/game_state.json    — last observe snapshot for dashboard
#   state/.session_counter   — session number; absent → next session = #1
#
# Preserves (config + debugging):
#   metadata.json, .mcp.json, opencode.json, AGENTS.md
#   state/mcp_server.log, gameserver_*.log, xvfb_*.log, ffmpeg_*.log
#
# Usage: clear_sandbox_state_reset <agent_id>
clear_sandbox_state_reset() {
  local agent_id="$1"
  local sandbox="/tmp/kaetram_agent_${agent_id}"
  [ -d "$sandbox/state" ] || return 0
  rm -f "$sandbox/state/game_state.json" \
        "$sandbox/state/.session_counter"
}

# Kaetram livestream displays: Xvfb :99 + agent_id (:99..:108); :198 is the test lane.
KAETRAM_XVFB_PATTERN='Xvfb :(99|10[0-8])\b'
KAETRAM_FFMPEG_PATTERN='ffmpeg.*x11grab.*-i :(99|10[0-8])\b'

kill_kaetram_livestream() {
  local sig="${1:-KILL}"
  pkill "-$sig" -f "$KAETRAM_FFMPEG_PATTERN" 2>/dev/null || true
  pkill "-$sig" -f "$KAETRAM_XVFB_PATTERN" 2>/dev/null || true
}

# Kill the process group for any data-collection chrome-headless-shell pid.
# Chrome spawns in its own pgid, so this catches all renderers/zygotes too.
kill_scoped_chrome_pgroup() {
  local sig="${1:-TERM}" pid pgid
  for pid in $(pgrep -f "chrome-headless-shell" 2>/dev/null || true); do
    [ -n "$pid" ] || continue
    if _ks_holds_protected_port "$pid"; then continue; fi
    if _ks_is_data_collection "$pid"; then
      pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
      [ -n "$pgid" ] && [ "$pgid" != "0" ] && kill "-$sig" -- -"$pgid" 2>/dev/null || true
    fi
  done
}
