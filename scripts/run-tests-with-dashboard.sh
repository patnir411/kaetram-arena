#!/usr/bin/env bash
# Wrap pytest so terminal-launched runs surface in the dashboard's Tests tab.
# Usage: scripts/run-tests-with-dashboard.sh [pytest args...]
#
# Headed video is NOT supported via this shim — that requires the dashboard's
# Xvfb/ffmpeg lifecycle. Use the dashboard "Run" button for live video.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
run_id="cli_$(date +%Y%m%d_%H%M%S)"
run_dir="/tmp/test_runs/$run_id"
mkdir -p "$run_dir"

cat > "$run_dir/meta.json" <<EOF
{"source":"cli","run_id":"$run_id","started_at":$(date +%s),"headed":false,"suite":null,"markers":null,"exit_code":null,"finished_at":null,"cancelled":false}
EOF

# Best-effort dashboard hello so the run appears in the tab immediately.
curl -s -m 1 -X POST -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$run_id\",\"event\":\"run_started\",\"payload\":{\"run_id\":\"$run_id\",\"headed\":false,\"suite\":null}}" \
  http://127.0.0.1:${DASHBOARD_HTTP_PORT:-8080}/ingest/test_event >/dev/null 2>&1 || true

set +e
DASHBOARD_TEST_RUN_DIR="$run_dir" \
  "$PROJECT_DIR/.venv/bin/python3" -m pytest \
    -p tests.dashboard_progress_plugin \
    --junit-xml="$run_dir/junit.xml" \
    "$@" 2>&1 | tee "$run_dir/log.txt"
exit_code=${PIPESTATUS[0]}
set -e

# Backfill final meta — no reaper thread for CLI runs.
"$PROJECT_DIR/.venv/bin/python3" - <<EOF
import json, time, pathlib
p = pathlib.Path("$run_dir/meta.json")
m = json.loads(p.read_text())
m["finished_at"] = time.time()
m["exit_code"] = $exit_code
p.write_text(json.dumps(m, indent=2))
EOF

# Tell the dashboard we're done.
curl -s -m 1 -X POST -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$run_id\",\"event\":\"session_finish\",\"payload\":{\"exit_code\":$exit_code,\"cancelled\":false}}" \
  http://127.0.0.1:${DASHBOARD_HTTP_PORT:-8080}/ingest/test_event >/dev/null 2>&1 || true

exit $exit_code
