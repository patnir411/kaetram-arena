#!/usr/bin/env bash
set -u
PROJECT=/home/patnir41/projects/kaetram-agent
KAETRAM=/home/patnir41/projects/Kaetram-Open
LOG=/tmp/chainer.log
RUNS=${1:-3}

cd "$PROJECT"
echo "[chainer $(date -u +%FT%TZ)] started — will wait for current run, patch strawberry drop, build+commit+push Kaetram-Open, then chain $RUNS × 4h runs" >> "$LOG"

while pgrep -f "python3 orchestrate.py" >/dev/null; do
  sleep 60
done
echo "[chainer $(date -u +%FT%TZ)] current orchestrator exited" >> "$LOG"

echo "[chainer $(date -u +%FT%TZ)] applying strawberry drop-rate patch" >> "$LOG"
python3 - <<'PY' >> "$LOG" 2>&1
import json, pathlib
p = pathlib.Path('/home/patnir41/projects/Kaetram-Open/packages/server/data/tables.json')
data = json.loads(p.read_text())
changed = False
for d in data['fruits']['drops']:
    if d['key'] == 'strawberry' and d['chance'] != 100000:
        d['chance'] = 100000
        changed = True
if changed:
    p.write_text(json.dumps(data, indent=4) + '\n')
    print('[chainer] strawberry chance 8000 -> 100000')
else:
    print('[chainer] strawberry chance already patched, skipping')
PY

source "$HOME/.nvm/nvm.sh"
nvm use 20 >> "$LOG" 2>&1
cd "$KAETRAM"
echo "[chainer $(date -u +%FT%TZ)] yarn build (Kaetram-Open)" >> "$LOG"
yarn build >> "$LOG" 2>&1
BUILD_RC=$?
if [ $BUILD_RC -ne 0 ]; then
  echo "[chainer $(date -u +%FT%TZ)] yarn build FAILED rc=$BUILD_RC — chained runs will use OLD build" >> "$LOG"
fi

git add packages/server/data/tables.json >> "$LOG" 2>&1
if git diff --cached --quiet; then
  echo "[chainer $(date -u +%FT%TZ)] no staged diff (already committed?), skipping commit/push" >> "$LOG"
else
  git commit -m "fix(data): bump strawberry drop chance 8000->100000

Raises effective strawberry drop rate from ~0.8%/kill to ~10%/kill for mobs
with 'fruits' drop table. Unblocks agent Scavenger quest (turn-in needs 2
strawberries; previously ~250 kills expected to acquire both)." >> "$LOG" 2>&1
  git push origin develop >> "$LOG" 2>&1
fi

cd "$PROJECT"

for i in $(seq 1 "$RUNS"); do
  while pgrep -f "python3 orchestrate.py" >/dev/null; do
    sleep 60
  done
  echo "[chainer $(date -u +%FT%TZ)] launching chained run $i/$RUNS" >> "$LOG"
  ./scripts/restart-agent.sh --claude 3 --grinder 1 --completionist 1 --explorer 1 --hours 4 >> "$LOG" 2>&1
  sleep 120
done
echo "[chainer $(date -u +%FT%TZ)] all $RUNS chained runs launched, chainer exiting" >> "$LOG"
