#!/usr/bin/env bash
# Auto-export training data report and push to GitHub Gist.
# Runs via cron every 10 minutes. Safe to run concurrently (uses lockfile).
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


LOCK="/tmp/kaetram-sync-report.lock"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GIST_ID="$(cat "$PROJECT_DIR/.gist_id" 2>/dev/null || echo "")"
REPORT="/tmp/kaetram-export/report.json"

# Reap a stale lock first (older than 5 min → a previous run died holding it).
if [ -f "$LOCK" ] && [ -n "$(find "$LOCK" -mmin +5 2>/dev/null)" ]; then
    rm -f "$LOCK"
fi

# Acquire atomically: `set -C` (noclobber) makes the redirect fail if the lock
# already exists, closing the check-then-touch race between concurrent crons.
if ! (set -C; : > "$LOCK") 2>/dev/null; then
    exit 0
fi
# Single-quote so $LOCK is expanded at trap-fire time, not trap-set time.
trap 'rm -f "$LOCK"' EXIT

# Generate report
cd "$PROJECT_DIR"
.venv/bin/python3 scripts/export_report.py > /dev/null 2>&1

# Push to gist (if ID exists and report was generated)
if [ -n "$GIST_ID" ] && [ -f "$REPORT" ]; then
    gh gist edit "$GIST_ID" "$REPORT" > /dev/null 2>&1
fi
