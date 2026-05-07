# Dataset Regeneration Plan (r7 → r8)

> **Status: HISTORICAL (2026-04-28).** This is the r7→r8 regeneration runbook from
> 2026-04-09. r8 shipped, r9 followed. r10 was rebuilt 2026-05-06 from the post-Core-3
> Claude corpus (see `dataset/qwen_sft/README.md` and `research/experiments/training-runs.md`).
> All actionable steps below are completed and the open questions (§10) are all resolved.
> Preserved for reference on how dataset rebuilds are sequenced when needed again.

---

Author: Claude (agent run 2026-04-09)
Status: Draft — awaiting sister agents' patches to land in `extract_turns.py`
and `convert_to_qwen.py`, and human go/no-go on Open Questions (section 10).

This plan regenerates the Qwen3.5 9B SFT dataset at `dataset/qwen_sft/` and
(optionally) the KTO dataset at `dataset/qwen_kto/` after the data-pipeline
patches land. It is read-only until explicitly executed — the r7 Modal
training run is still live.

---

## 1. What changed (context)

The r7 SFT dataset was built by an `extract_turns.py` + `convert_to_qwen.py`
pair that silently dropped a large fraction of real agent behavior. Two bugs:

**Bug A — tool drift.** `extract_turns.py` only recognized a fixed list of
MCP tool names. The MCP server grew seven new tools after that list was
frozen, and every call to one of them was silently discarded:

- `accept_quest`
- `gather`
- `loot`
- `buy_item`
- `drop_item`
- `clear_combat`
- `query_quest`

Approximate impact in r7 (from the currently-shipped `dataset/qwen_sft/train.json`
tool-call distribution):

```
navigate:        5754
attack:          3032
cancel_nav:      2260
interact_npc:    2183
warp:            1993
stuck_reset:     1565
move:            1425
click_tile:       820
equip_item:       412
set_attack_style: 381
eat_food:         350
talk_npc:         301
respawn:          251
accept_quest:       8   <-- only 8 (the MCP tool was dropped; these 8 are
                            the legacy `quest_accept` button-click tool)
gather:             0   <-- completely missing
loot:               0
buy_item:           0
drop_item:          0
clear_combat:       0
query_quest:        0
```

Every one of the missing tools is a first-class action in `mcp_game_server.py`
and is used heavily by the agents in production (the user's count: ~3,794
dropped calls plus the mislabelled `accept_quest` path).

**Bug B — quest_opened always false.** `extract_turns.py` produced
`action_result` blobs in memory and `convert_to_qwen.py` fabricated synthetic
tool result text from those blobs. The fabricated result for `interact_npc`
hardcoded `quest_opened: false` regardless of the real browser response, so
all 574 real quest-opening NPC interactions show up in the dataset as
failures. The model learned that interacting with a quest NPC never
progresses a quest, which is the opposite of the truth.

**Fixes (landing now, by sister agents):**

1. `extract_turns.py` is taught the 7 new tools — their MCP names
   (`mcp__kaetram__accept_quest`, `mcp__kaetram__gather`, etc.) map to
   friendly action types and get canonicalized into `<action>...</action>`
   strings.
2. `extract_turns.py` persists the raw tool-result text from the log as
   `action_result_raw` on each turn, so the downstream converter has a
   source of truth instead of reinventing the result.
3. `convert_to_qwen.py` prefers `turn["action_result_raw"]` when present,
   and only falls back to synthetic stub text for very old turns without
   that field.
4. `convert_to_qwen.py` adds TOOL_DEFINITIONS for the 7 new tools so the
   system prompt tool list and the dataset tool list agree.
5. `score_sessions.py` `nav_cancel` drift fix (unrelated but shipping in
   the same batch) — changes how cancel_nav is scored for KTO labelling.

The net effect is that r8 will contain thousands more real tool calls
(7 new action classes), real tool-result strings (not fabricated ones), and
truthful `quest_opened: true` events for NPC interactions that actually
opened a quest.

**r7 today:** corrupted baseline. ~7,069 train+val records (6,423 train,
646 val), missing entire action classes, lying about quest progression.

**r8 goal:** same raw session corpus, reprocessed through the patched
pipeline. No new data collection required.

**Why regenerate now:** r8 is the first clean training run on the fixed
data and the first point where train/val loss and downstream eval are
directly comparable to r7 without a confound.

---

## 2. Current data state (VM, 2026-04-09)

Captured live from `patnir41@34.28.111.6:~/projects/kaetram-agent`.

| Thing | Value |
|---|---|
| Raw sessions agent_0 | 485 logs |
| Raw sessions agent_1 | 454 logs |
| Raw sessions agent_2 | 456 logs |
| Raw sessions agent_3 | 0 (agent_3 unused) |
| **Total raw sessions** | **1,395 session logs** |
| Extracted sessions agent_0 | 218 |
| Extracted sessions agent_1 | 217 |
| Extracted sessions agent_2 | 215 |
| **Total extracted sessions** | **650** |
| `dataset/raw/` disk | 960 MB |
| `dataset/extracted/` disk | 170 MB |
| `dataset/qwen_sft/` disk | 120 MB |
| `dataset/qwen_kto/` disk | 72 MB |
| `dataset/qwen_sft/train.json` | 6,423 records |
| `dataset/qwen_sft/val.json` | 646 records |
| `dataset/qwen_kto/train.json` | 2,771 records |
| `dataset/qwen_kto/val.json` | 273 records |
| VM disk free | 14 GB of 49 GB (72% used) |

**Note on the gap between raw and extracted.** 1,395 raw sessions produced
only 650 extracted session directories. This is because many raw sessions
are short (< 2 turns), truncated, or Codex logs that `extract_turns.py`
currently short-circuits (see the VM diff to `_parse_codex_events`, which
has been temporarily disabled and returns `[]` pending Codex log
validation). None of that needs to change for r8.

**KTO metadata (from `dataset/qwen_kto/metadata.json`):**

```
sessions:               338
desirable_sessions:     135
undesirable_sessions:   102
neutral_sessions:       101
train_sessions:         201
val_sessions:            21
train_desirable:       2035
train_undesirable:      736
window_size:              5
stride:                   2
positive_window_floor:  0.45
negative_window_ceiling: 0.60
desirable_top_pct:      0.40
undesirable_bottom_pct: 0.30
```

Rebuilding KTO is optional for r8 SFT but it **is** affected: session scores
depend on action counts, and the `score_sessions.py` fix for `nav_cancel`
drift plus the newly-visible actions (gather/loot/buy_item/accept_quest)
will shift the distribution. See section 5 step 7.

---

## 3. Pre-flight checks

All must be true before running the regeneration commands in section 5.

1. **r7 Modal training.** As of this writing `pgrep -f modal` on the VM
   shows the training process alive (pid `2602245`, launched from tmux
   session `research`). **Do NOT stop it.** The patches do not touch
   `/checkpoints/` and do not touch the on-disk dataset while it runs,
   but confirm before pulling patches that the training job is still
   reading the dataset (it should have streamed its copy into the Modal
   volume at job start — verify this, because if Modal is still reading
   `dataset/qwen_sft/train.json` from the VM mid-run, any `mv` will break
   it).

   Check: `modal app list | grep kaetram` and inspect the running app's
   mount spec. Expected: the dataset is uploaded once at app start, not
   streamed.

2. **Patch branch state.** The sister agents are editing
   `extract_turns.py`, `convert_to_qwen.py`, and `score_sessions.py` on
   separate branches. Before running regeneration, either:
   - those branches are merged to `main` and the VM has pulled `main`, OR
   - the VM has `git fetch`'d and checked out the patch branch directly,
     OR
   - the patches are applied on the laptop and rsync'd to the VM.

   Pick ONE path and write it into the runbook; do not mix.

3. **Sync direction.** The laptop currently holds a working copy with
   most of the fixes already present (the laptop `extract_turns.py` has
   `MCP_TOOL_NAME_MAP` entries for all 7 new tools and emits
   `action_result_raw`; the laptop `convert_to_qwen.py` has TOOL_DEFINITIONS
   for all 7 new tools and prefers `action_result_raw`). The VM `main`
   branch does NOT have any of this — `grep -c "action_result_raw"
   extract_turns.py` on the VM returns 0.

   So the substantive change between "VM main" and "patched" is identical
   to the laptop working copy. Decide whether r8 runs on the VM (preferred,
   since the raw logs live there) or the laptop (only if we rsync
   `dataset/raw/` down, 960 MB).

4. **VM uncommitted changes.** The VM has uncommitted modifications to:
   - `cli_adapter.py`
   - `extract_turns.py` (Codex extraction disabled, 45-line diff)
   - `orchestrate.py`
   - `play.sh`
   - `scripts/nuke-agents.sh`
   - `scripts/reset-state.sh`
   - `scripts/resume-agent.sh`

   The `extract_turns.py` VM-local diff only disables Codex parsing
   (`_parse_codex_events` returns `[]`) and is orthogonal to the bug
   fixes. However, if the sister agents' patch lands on top of the VM's
   local diff, we need to either:
   - `git stash` the VM diff, `git pull`, re-apply the Codex disable,
     commit it, then run, OR
   - Merge the patch branch into the VM's dirty tree manually.

   **Recommended**: commit the VM's Codex-disable change first (it's a
   legitimate safety guard, not a hack), push as its own PR, then pull the
   sister-agent patches cleanly.

5. **Dashboard / live agents.** Confirm no data collection is running
   (`pgrep -f orchestrate.py`). If agents are actively writing to
   `dataset/raw/`, pause them for the duration of re-extraction —
   `extract_turns.py` globs `session_*.log`, so a half-written session
   will get re-extracted on the next run as well, which is fine, but new
   logs landing mid-extraction are a distraction.

6. **Backup disk budget.** The r7 `qwen_sft/` is 120 MB and `qwen_kto/` is
   72 MB. Backups fit easily in 14 GB free. A full `dataset/extracted/`
   backup (170 MB) is also cheap. Do all three.

---

## 4. Backup strategy

Everything is a rename, not a copy. Renames are atomic on the same
filesystem and preserve inode linkage for anything that may still be
reading the files.

```bash
cd ~/projects/kaetram-agent
mv dataset/qwen_sft   dataset/qwen_sft_r7
mv dataset/qwen_kto   dataset/qwen_kto_r7
mv dataset/extracted  dataset/extracted_r7
```

Rules:

- **Never delete the r7 backups until r8 eval has landed and r8 is
  promoted.** They are the baseline for all eval comparisons. Delete only
  after the ICLR paper is submitted, or move them to cold storage.
- **Optional: checkpoint the backup to GCS.** See section 10, open
  question 2.
- Do not rename `dataset/raw/` — raw session logs are the ground truth
  and must never move.

After renaming, the regeneration step writes to the original
`dataset/qwen_sft/`, `dataset/qwen_kto/`, and `dataset/extracted/` paths
so no downstream consumer (training scripts, dashboard, research docs)
needs to change its file paths.

---

## 5. Regeneration steps

All commands are run from `~/projects/kaetram-agent` on the VM
(`patnir41@34.28.111.6`) in a single tmux pane. Do NOT parallelise;
extract + convert are fast (minutes) and ordering matters.

### Step 1 — apply patches

Pick ONE of the two paths depending on Open Question 4:

**Path A: patches merged to `main`** (preferred)

```bash
cd ~/projects/kaetram-agent
git fetch origin
# Commit or stash the VM-local Codex-disable diff first
git status
git add cli_adapter.py extract_turns.py orchestrate.py play.sh \
        scripts/nuke-agents.sh scripts/reset-state.sh scripts/resume-agent.sh
git commit -m "chore(vm): local ops tweaks — Codex disable, nuke script"
git push origin HEAD:vm-ops-local   # pushes to a side branch, NOT main
git pull --rebase origin main       # pulls sister-agent patches
```

**Path B: patches on a feature branch, not yet merged**

```bash
cd ~/projects/kaetram-agent
git stash push -m "vm-local-ops"
git fetch origin
git checkout <patch-branch-name>
# Verify the three files have the expected fixes:
grep -c "mcp__kaetram__accept_quest" extract_turns.py   # expect >= 1
grep -c "action_result_raw"          extract_turns.py   # expect >= 1
grep -c "action_result_raw"          convert_to_qwen.py # expect >= 1
git stash pop                         # only if no conflicts
```

In either case, sanity check the patched files with:

```bash
python3 -c "import ast; ast.parse(open('extract_turns.py').read()); ast.parse(open('convert_to_qwen.py').read()); print('OK')"
```

### Step 2 — back up the r7 artefacts

```bash
cd ~/projects/kaetram-agent
mv dataset/qwen_sft   dataset/qwen_sft_r7
mv dataset/qwen_kto   dataset/qwen_kto_r7
mv dataset/extracted  dataset/extracted_r7
mkdir -p dataset/extracted dataset/qwen_sft dataset/qwen_kto
```

Verify:

```bash
ls -lh dataset/qwen_sft_r7/ dataset/qwen_kto_r7/ dataset/extracted_r7/
du -sh dataset/raw/ dataset/extracted_r7/ dataset/qwen_sft_r7/ dataset/qwen_kto_r7/
```

### Step 3 — re-extract turns from raw logs

```bash
cd ~/projects/kaetram-agent
for a in 0 1 2; do
  python3 extract_turns.py \
    --log-dir  dataset/raw/agent_$a/logs \  # logs/ symlink resolves to latest run dir
    --output-dir dataset/extracted
done
```

Note: `extract_turns.py` does NOT accept `--no-frames` on the VM (that
flag is not in its argparse; see help output). The VM help text shows
only `--log-dir`, `--log-file`, `--output-dir`. If the sister-agent patch
adds `--no-frames`, use it; otherwise just run as above. Frame
extraction is no longer part of the pipeline.

Also: `extract_turns.py` takes `--log-dir` as a *flat* directory of
`session_*.log` files, not a recursive tree. That is why `collect_sft_data.sh`
loops over `agent_*/runs/` (or `agent_*/logs/` via symlink). Do the same here.

Expected output:

```
  session_X_YYYYMMDD_HHMMSS.log: N turns
  ...
Total: T turns from L logs -> dataset/extracted
```

Record `T` (total turns) as `T_extract`. Compare to r7's extracted turn
count (see section 6 verification).

### Step 4 — convert to Qwen SFT format

```bash
python3 convert_to_qwen.py \
  --input  dataset/extracted \
  --output dataset/qwen_sft
```

Default mode is `mixed`, default format is `sft`, default `val-ratio` is
0.1. Keep all defaults to match r7 exactly. The `--seed` default must
also match the r7 run — if the r7 command line set an explicit seed,
repeat it here so that the train/val split is deterministic for
comparison. If r7 used the default, do the same.

### Step 5 — first-look sanity check

The verification queries in section 6 are the authoritative gate. Do at
least the following before moving on:

```bash
python3 - <<'PY'
import json, collections
d = json.load(open("dataset/qwen_sft/train.json"))
v = json.load(open("dataset/qwen_sft/val.json"))
print(f"train records: {len(d)}")
print(f"val records:   {len(v)}")
c = collections.Counter()
for rec in d + v:
    for m in rec["messages"]:
        if m.get("role") == "assistant" and "tool_calls" in m:
            for tc in m["tool_calls"]:
                c[tc["function"]["name"]] += 1
for k, n in c.most_common():
    print(f"  {k}: {n}")
PY
```

**Pass criteria:**

1. All 7 new tools appear with non-zero counts: `accept_quest`, `gather`,
   `loot`, `buy_item`, `drop_item`, `clear_combat`, `query_quest`.
2. `accept_quest` is much larger than 8 (probably in the hundreds or low
   thousands).
3. Total record count is >= 7,069 (the r7 total) and ideally a few
   hundred higher. If it's lower, something is wrong — the new tools
   should only *add* turns, never remove them.
4. Existing tool counts (navigate, attack, interact_npc, etc.) are
   roughly the same magnitude as r7 (section 1 table). A >10% drop in
   any of them is a red flag.

**Fail criteria → abort and investigate, do not ship.**

### Step 6 — promote qwen_sft (no atomic rename needed)

Because Step 2 already renamed the r7 directory, `dataset/qwen_sft/`
already points at the new artefacts. There is nothing to promote. Skip.

(This step is here so the numbering matches earlier drafts.)

### Step 7 — rebuild KTO (optional but recommended)

The KTO dataset is downstream of `extract_turns.py` and `score_sessions.py`,
both of which have shifted. Session scores will change because:

- `score_sessions.py` `nav_cancel` drift fix changes the cancel_nav weight.
- The newly-visible `accept_quest` / `gather` / `loot` / `buy_item` calls
  add to the "productive action" tally.
- The newly-truthful `quest_opened: true` events flip some sessions from
  undesirable to desirable.

Rebuild:

```bash
python3 score_sessions.py \
  --input  dataset/extracted \
  --output dataset/qwen_kto/session_scores.json

python3 build_kto_dataset.py \
  --input  dataset/extracted \
  --scores dataset/qwen_kto/session_scores.json \
  --output dataset/qwen_kto

python3 inspect_kto_dataset.py --dataset dataset/qwen_kto --n 3
```

Keep the flag defaults from r7's KTO build (per metadata: `window_size=5`,
`stride=2`, `positive_window_floor=0.45`, `negative_window_ceiling=0.60`,
`desirable_top_pct=0.40`, `undesirable_bottom_pct=0.30`, `keep_personality=false`).
If the sister-agent patches add new flags, do NOT set them yet — hold
them for an r9 experiment.

**If the user wants to skip KTO for now** (because Niral is running the
KTO full run on r7), back up `dataset/qwen_kto_r7` and leave
`dataset/qwen_kto/` as an empty placeholder until the r7 KTO run is done.
See Open Question 1.

---

## 6. Verification queries

All queries are copy-paste ready, assuming `cd ~/projects/kaetram-agent`.

### 6.1 — total record counts

```bash
python3 - <<'PY'
import json
for split in ("train", "val"):
    for root in ("dataset/qwen_sft_r7", "dataset/qwen_sft"):
        try:
            n = len(json.load(open(f"{root}/{split}.json")))
            print(f"{root}/{split}.json: {n}")
        except FileNotFoundError:
            print(f"{root}/{split}.json: MISSING")
PY
```

Expect r8 train >= 6,423, r8 val >= 646, with total increase driven by
the new tools. If the increase is > ~15% be skeptical — that would
suggest duplicate extraction.

### 6.2 — tool call distribution (the headline)

```bash
python3 - <<'PY'
import json, collections
for label, root in (("r7", "dataset/qwen_sft_r7"), ("r8", "dataset/qwen_sft")):
    d = json.load(open(f"{root}/train.json"))
    v = json.load(open(f"{root}/val.json"))
    c = collections.Counter()
    for rec in d + v:
        for m in rec["messages"]:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    c[tc["function"]["name"]] += 1
    print(f"=== {label} ===")
    for k, n in c.most_common():
        print(f"  {k:>18}: {n}")
    print()
PY
```

Expected delta for r8 vs r7: positive counts for `gather`, `loot`,
`buy_item`, `drop_item`, `clear_combat`, `query_quest` (all 0 in r7);
`accept_quest` many hundreds higher than 8; everything else within ~10%
of r7.

### 6.3 — interact_npc results with quest_opened=true (Bug B proof)

```bash
python3 - <<'PY'
import json, re
for label, root in (("r7", "dataset/qwen_sft_r7"), ("r8", "dataset/qwen_sft")):
    d = json.load(open(f"{root}/train.json")) + json.load(open(f"{root}/val.json"))
    total_interact = 0
    quest_opened_true = 0
    for rec in d:
        msgs = rec["messages"]
        for i, m in enumerate(msgs):
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    if tc["function"]["name"] == "interact_npc":
                        total_interact += 1
                        # the following tool message is the result
                        if i + 1 < len(msgs) and msgs[i + 1].get("role") == "tool":
                            content = msgs[i + 1].get("content", "") or ""
                            if '"quest_opened": true' in content or '"quest_opened":true' in content:
                                quest_opened_true += 1
    print(f"{label}: interact_npc={total_interact}, quest_opened=true={quest_opened_true}")
PY
```

Expected: r7 shows `quest_opened=true: 0` (Bug B). r8 shows a positive
number, ideally in the hundreds (the user's estimate: 574 real events).

### 6.4 — assistant reasoning length distribution

```bash
python3 - <<'PY'
import json, statistics
for label, root in (("r7", "dataset/qwen_sft_r7"), ("r8", "dataset/qwen_sft")):
    d = json.load(open(f"{root}/train.json"))
    lens = []
    for rec in d:
        for m in rec["messages"]:
            if m.get("role") == "assistant":
                c = m.get("content") or ""
                if c.strip():
                    lens.append(len(c))
    if lens:
        print(f"{label}: n={len(lens)} mean={statistics.mean(lens):.0f} median={statistics.median(lens):.0f} p95={sorted(lens)[int(len(lens)*0.95)]}")
PY
```

Reasoning length distributions should be nearly identical across r7 and
r8 (same raw logs, same `<think>` blocks). A large shift suggests the
converter is doing something unexpected.

### 6.5 — no empty / malformed records

```bash
python3 - <<'PY'
import json
for root in ("dataset/qwen_sft", "dataset/qwen_sft_r7"):
    for split in ("train", "val"):
        d = json.load(open(f"{root}/{split}.json"))
        empty = sum(1 for r in d if not r.get("messages"))
        no_user = sum(1 for r in d if not any(m.get("role") == "user" for m in r["messages"]))
        no_asst = sum(1 for r in d if not any(m.get("role") == "assistant" for m in r["messages"]))
        print(f"{root}/{split}: total={len(d)} empty={empty} no_user={no_user} no_asst={no_asst}")
PY
```

Expected: 0 for all three counters in r8. Any non-zero → blocker.

### 6.6 — system prompt tool list matches TOOL_DEFINITIONS

The r7 metadata shows 15 tools in the system prompt. r8 must show 22
(15 + 7 new).

```bash
python3 - <<'PY'
import json
for root in ("dataset/qwen_sft_r7", "dataset/qwen_sft"):
    md = json.load(open(f"{root}/metadata.json"))
    names = [t["function"]["name"] for t in md.get("tools", [])]
    print(f"{root}: {len(names)} tools -> {sorted(names)}")
PY
```

---

## 7. Rollback plan

All r7 artefacts are preserved intact in the `_r7` sibling directories.
Rollback is a three-line shell operation.

### 7.1 — full rollback (nuke r8, restore r7)

```bash
cd ~/projects/kaetram-agent
rm -rf dataset/qwen_sft dataset/qwen_kto dataset/extracted
mv dataset/qwen_sft_r7  dataset/qwen_sft
mv dataset/qwen_kto_r7  dataset/qwen_kto
mv dataset/extracted_r7 dataset/extracted
```

### 7.2 — partial rollback (keep extracted, redo conversion only)

Useful if Step 4 (`convert_to_qwen.py`) produced a bad dataset but Step
3 (`extract_turns.py`) is fine. The extracted turns are expensive to
regenerate; don't throw them away unless Step 3 itself needs a fix.

```bash
rm -rf dataset/qwen_sft
mkdir  dataset/qwen_sft
python3 convert_to_qwen.py --input dataset/extracted --output dataset/qwen_sft
```

### 7.3 — when to roll back

- Verification 6.1 shows total records *decreased* vs r7.
- Verification 6.2 shows missing existing tools (drop of >10% in
  `navigate`, `attack`, `interact_npc`, etc.).
- Verification 6.3 shows `quest_opened: true` count *decreased* vs r7.
- Verification 6.5 shows any non-zero empty / no_user / no_asst counts.
- r8 training run produces worse eval metrics than r7 by a clear margin
  (section 9). Rollback + investigate; do NOT ship r9 on top of a broken
  r8.

### 7.4 — when NOT to roll back

- r8 total record count grew by ~5-15% — that is the expected effect of
  Bug A being fixed.
- Tool call distribution shows roughly the same magnitude for existing
  tools but new tools appearing — expected.
- KTO dataset desirable/undesirable split shifted slightly — expected,
  because the scoring fix is shipping in the same batch.

---

## 8. Training launch plan (r8)

Goal: isolate the effect of the data fix. One and only one thing changes
between r7 and r8 — the dataset. Nothing else.

1. Bump the experiment name in `finetune/train_modal.py`:

   ```python
   # Line 102:
   EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r8"
   ```

2. Leave every other hyperparameter untouched. In particular:
   - base model
   - rsLoRA setting (r7 reverted rsLoRA in commit `685f649` — keep it reverted)
   - LR, batch size, epochs, LoRA rank, alpha, dropout, target modules
   - chat template
   - personality label settings
   - Modal timeout (kept at 18h for safety)
   - paraphrase augmentation flag
3. Commit the bump:

   ```bash
   git checkout -b train/r8-data-fix
   git add finetune/train_modal.py
   git commit -m "train: bump experiment name to r8 for data-fix run"
   git push -u origin train/r8-data-fix
   ```

4. Launch:

   ```bash
   tmux new -s r8
   cd ~/projects/kaetram-agent
   modal run finetune/train_modal.py
   ```

5. Expected duration: comparable to r7 (r7 ran ~14h+, Modal timeout is
   18h). r8 has marginally more turns, so expect marginally longer — but
   no blowout. If it crosses 17h, investigate.

6. Do NOT delete r7 checkpoints. Keep `/checkpoints/kaetram-qwen3.5-9b-r7/`
   on the Modal volume for eval comparison.

**Critical:** do not launch r8 until Open Question 1 is resolved. If
Niral's KTO full run on r7 is still in flight, launching r8 simultaneously
may contend for Modal credits / H100 availability.

---

## 9. Eval comparison protocol (r7 vs r8)

Two axes of comparison; both are required to declare r8 a success.

### 9.1 — loss-based (cheap, automatic)

Pull `training_metrics.json` for both runs from the Modal volume:

```bash
modal volume get kaetram-checkpoints /checkpoints/kaetram-qwen3.5-9b-r7/training_metrics.json ./r7_metrics.json
modal volume get kaetram-checkpoints /checkpoints/kaetram-qwen3.5-9b-r8/training_metrics.json ./r8_metrics.json
```

Compare:
- final train loss
- final val loss
- loss curve shape (any divergence, any sudden drops)
- best-val-loss checkpoint step

**Expected:** r8 val loss is lower than r7 val loss or approximately
equal. A lower val loss is the cheapest evidence that the data fix
helped. An equal val loss is acceptable; a higher val loss means
something is wrong.

### 9.2 — behavioral (expensive, authoritative)

Run both models through the standard eval harness on the GPU VM
(`73.173.11.56:1738`) with the finetuned model loaded in Ollama. Held-out
set is N live gameplay sessions per model (N >= 20 for statistical
power).

Metrics (same as existing dashboard + session log analysis):
- **XP/hour** — primary. Higher is better.
- **Quest completion rate** — the Bug B fix targets this directly. Major
  jump expected.
- **Death rate** — should not increase. A large increase would suggest
  the model is now over-confident about interact_npc and walking into
  traps.
- **Tool diversity** — count of distinct tools used per session. Should
  increase for r8 because the new tools are now in-vocabulary.
- **`accept_quest` call rate** — direct measure of whether the model
  learned to use the new tool.
- **`gather` / `loot` call rate** — same.

### 9.3 — promotion decision

- r8 **promoted** → if loss-based eval passes AND behavioral eval shows
  improvement in at least quest completion rate and XP/hour without a
  significant regression in death rate. Document in
  `research/experiments/training-runs.md` per the CLAUDE.md maintenance
  rule.
- r8 **rejected** → roll back per section 7, open a Linear issue, decide
  whether the bug is in the patches or in the data assumptions.
- r8 **inconclusive** (equal or slightly better) → promote anyway.
  Equal-quality on fixed data is still better than corrupted data, and
  any r9 experiments need to start from a clean baseline.

---

## 10. Open questions for the user

1. **r8 vs Niral's KTO run ordering.** Should r8 SFT training be
   launched immediately after dataset regeneration, or should we wait for
   Niral's full KTO run on r7 to finish first (to avoid Modal H100
   contention and to keep r7 as the last "untouched" baseline)?
   Recommendation: wait for KTO to finish, then launch r8. But this is
   the user's call.

2. **GCS checkpoint for the r7 backup.** Should
   `dataset/qwen_sft_r7/`, `dataset/qwen_kto_r7/`, and
   `dataset/extracted_r7/` be uploaded to GCS (or a Modal volume) for
   long-term cold storage? Pro: survives VM destruction. Con: ~362 MB
   and we still have local copies. Recommendation: yes, belt-and-braces,
   ~ free.

3. **r8 hyperparameter doc.** Should a new
   `research/experiments/r8-hyperparameters.md` be written, or do we
   piggy-back on `research/experiments/r7-hyperparameters.md` and just
   add a "data-fix update" section? Recommendation: piggy-back — r8 is
   explicitly the "same hparams, fixed data" run, and a separate doc
   would invite drift.

4. **Patch merge timing.** Should the sister agents' patches merge to
   `main` immediately (so they are the default for future data
   collection) or wait until r7 eval lands (so r7 is reproducible from
   `main` at a known SHA)? Recommendation: merge to a `data-fix`
   integration branch now, open a PR to `main` now, but do not squash-
   merge until r8 trains and r7 eval is captured. Tag the pre-merge SHA
   as `r7-baseline` so the corrupted pipeline is always reproducible for
   anyone who wants to re-derive r7 from raw logs later.

5. **Do we want r8 KTO or just r8 SFT?** Building qwen_kto is in section
   5 step 7 as optional. Default plan is yes, rebuild it, to keep
   everything aligned. If the user wants to split the experiments and
   keep r7 KTO alive, skip that step and leave `dataset/qwen_kto_r7/` as
   the canonical KTO source.

6. **Codex log re-enable.** The VM-local diff disables Codex extraction
   in `extract_turns.py`. When should Codex logs be validated and
   re-enabled? This is out of scope for r8 but worth tracking — right
   now Codex sessions are being collected (`tmux ls` shows
   `codex-harness`) and silently dropped from the dataset.

---

## Appendix A — file paths touched / not touched

**Touched during regeneration (rename or rewrite):**

- `/home/patnir41/projects/kaetram-agent/dataset/extracted/` (rename → `extracted_r7`; rewritten by Step 3)
- `/home/patnir41/projects/kaetram-agent/dataset/qwen_sft/` (rename → `qwen_sft_r7`; rewritten by Step 4)
- `/home/patnir41/projects/kaetram-agent/dataset/qwen_kto/` (rename → `qwen_kto_r7`; rewritten by Step 7)
- `/home/patnir41/projects/kaetram-agent/finetune/train_modal.py` (one-line bump in Step 8)

**Not touched (read-only):**

- `/home/patnir41/projects/kaetram-agent/dataset/raw/` — source of truth, never mutated
- Modal volume `/checkpoints/kaetram-qwen3.5-9b-r7/` — r7 model artefacts
- All MCP server code (`mcp_game_server.py`, `state_extractor.js`) — no
  schema changes required
- `prompts/system.md` and `prompts/game_knowledge.md` — r8 uses the same
  prompts as r7. Tool list is part of `convert_to_qwen.py` TOOL_DEFINITIONS,
  not `system.md`.

## Appendix B — quick-reference runbook (no prose)

```bash
# on the VM, after sister patches are confirmed on disk
cd ~/projects/kaetram-agent

# 0. confirm patches present
grep -c mcp__kaetram__accept_quest extract_turns.py    # >= 1
grep -c action_result_raw          extract_turns.py    # >= 1
grep -c action_result_raw          convert_to_qwen.py  # >= 1
python3 -c "import ast; ast.parse(open('extract_turns.py').read()); ast.parse(open('convert_to_qwen.py').read()); print('OK')"

# 1. backup
mv dataset/qwen_sft   dataset/qwen_sft_r7
mv dataset/qwen_kto   dataset/qwen_kto_r7
mv dataset/extracted  dataset/extracted_r7
mkdir -p dataset/extracted dataset/qwen_sft dataset/qwen_kto

# 2. re-extract
for a in 0 1 2; do
  python3 extract_turns.py --log-dir dataset/raw/agent_$a/logs --output-dir dataset/extracted  # logs/ symlink → latest run
done

# 3. convert
python3 convert_to_qwen.py --input dataset/extracted --output dataset/qwen_sft

# 4. verify (see section 6 for full queries)
python3 -c "
import json, collections
d = json.load(open('dataset/qwen_sft/train.json'))
v = json.load(open('dataset/qwen_sft/val.json'))
print(f'train={len(d)} val={len(v)}')
c = collections.Counter()
for rec in d+v:
  for m in rec['messages']:
    if m.get('role')=='assistant' and 'tool_calls' in m:
      for tc in m['tool_calls']:
        c[tc['function']['name']] += 1
for k,n in c.most_common(): print(f'  {k}: {n}')
"

# 5. KTO rebuild
python3 score_sessions.py  --input dataset/extracted --output dataset/qwen_kto/session_scores.json
python3 build_kto_dataset.py --input dataset/extracted --scores dataset/qwen_kto/session_scores.json --output dataset/qwen_kto
python3 inspect_kto_dataset.py --dataset dataset/qwen_kto --n 3

# 6. train r8 (after human go/no-go)
# edit finetune/train_modal.py: EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r8"
tmux new -s r8
modal run finetune/train_modal.py
```
