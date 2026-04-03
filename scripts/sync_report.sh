#!/usr/bin/env bash
# Auto-export training data report and push to GitHub Gist.
# Runs via cron every 10 minutes. Safe to run concurrently (uses lockfile).
set -euo pipefail

LOCK="/tmp/kaetram-sync-report.lock"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GIST_ID="$(cat "$PROJECT_DIR/.gist_id" 2>/dev/null || echo "")"
REPORT="/tmp/kaetram-export/report.json"

# Skip if already running
if [ -f "$LOCK" ]; then
    # Stale lock check (older than 5 min)
    if [ "$(find "$LOCK" -mmin +5 2>/dev/null)" ]; then
        rm -f "$LOCK"
    else
        exit 0
    fi
fi
trap "rm -f $LOCK" EXIT
touch "$LOCK"

# Generate report
cd "$PROJECT_DIR"
.venv/bin/python3 scripts/export_report.py > /dev/null 2>&1

# Push to gist (if ID exists and report was generated)
if [ -n "$GIST_ID" ] && [ -f "$REPORT" ]; then
    gh gist edit "$GIST_ID" "$REPORT" > /dev/null 2>&1
fi
