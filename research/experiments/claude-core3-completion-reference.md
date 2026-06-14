# Claude (Sonnet) Core-3 Completion Reference

**Purpose.** A safekeeping record of *how the Claude teacher policy completed all
three Core-3 quests* — the behavioral target the distilled Qwen student (and the
better-prompted base teacher) is trying to reproduce. Captures per-quest timing,
the winning tool sequences, key coordinates/quantities, and the failure modes,
so we can compare student runs against a known-good trajectory.

Source: `scripts/log_analysis/analyze.py --run <id> quest|metrics` over the
checked-in Claude session logs under `dataset/raw/agent_*/runs/`.

---

## Reference runs (Claude harness)

Every live (non-archived) Claude run, fresh-from-L1 vs resume, with per-agent
Core-3 stages (`/10`; ✅ = that agent finished all three):

| Run | Start | Dur | Start type | grinder / completionist / explorer | All-3? |
|-----|-------|-----|-----------|-------------------------------------|--------|
| **`run_20260505_150033`** | 05-05 3:00 PM | 6h | fresh (s1) | 7 / **10 ✅** / **10 ✅** | 2 agents |
| `run_20260505_214542` | 05-05 9:45 PM | 6h | fresh (s1) | 7 / 7 / **10 ✅** | 1 agent |
| `run_20260522_164529` | 05-22 4:45 PM | 3h | fresh (s1) | 6 / 7 / 7 | none |
| `run_20260504_172157` | 05-04 5:21 PM | 3h | fresh (s1) | 7 / 6 / 4 | none |
| `run_20260504_140418` | 05-04 2:04 PM | 3h | fresh (s1) | 7 / 6 / 7 | none |
| `run_20260504_221206` | 05-04 10:12 PM | 3h | **resume (s8)** | +3 / +4 / +6 *(Δ)* | **3 agents (cumulative)** |

**Corrected claim (fact-checked across all 6 live runs):**
- **No fresh-from-Level-1 3-hour run has ever finished all three.** In every fresh
  3h run (`164529`, `172157`, `140418`) agents topped out at Foresting +
  Herbalist's (4–7/10); **Rick's Roll was never completed in a fresh 3h window.**
- **Genuine fresh all-three completions exist only in the 6-hour runs**
  (`150033`: completionist + explorer; `214542`: explorer).
- **`run_20260504_221206` is the one exception that looks like a 3h all-three —
  but it's a resume**, not a fresh run: its session counter starts at **#8**
  (timestamped ~4h after the run's nominal start), and this segment added only
  **+3/+4/+6 new stages** onto progress inherited from earlier same-day segments.
  Its "all three finished" accumulated across the full continuous May-4 play, not
  a single 3h window — which is why its `core3_stages` is a small delta, not 10.
- **So Rick's Roll is the long pole**: it is the only Core-3 quest never finished
  inside a fresh 3h run; it only completes given ~6h fresh, or cumulatively across
  resumed segments.

> Scope: this covers the 6 **live** Claude runs. 192 older run dirs under
> `dataset/raw/_archive/claude/` (April, pre-current-Core-3 framing, mostly short
> smoke tests) are out-of-corpus and `analyze.py` does not parse them — they were
> not machine-scored here.

All per-quest timing below is from the cleanest fresh full-completion run,
`run_20260505_150033` (T0 = run start; elapsed approximate from session-file
timestamps; `sN #M` = session N, turn M). The 10/10 agents there ran
**1,400–2,300 tool calls over 10–16 sessions** — completion is a long, navigation-
heavy grind, not fast or cheap.

---

## Per-quest playbook

### 1. Foresting — easy, fast, no gate (done in session 1, ~15–30 min)

Winning sequence: **equip `bronzeaxe` (slot 0) → `gather("Oak")` to 10 logs →
`interact_npc(Forester)` turn-in → 10 more logs → turn in again.** Forester at
**(216, 114)**. Two turn-ins (10 logs each). No skill gate.

| Agent | 0→1 | →2 | →3 (finish) | Elapsed |
|-------|-----|----|-----|---------|
| completionist | s1 #28 `gather(Oak)` | s1 #50 `gather(Oak)` | s1 #52 `interact_npc(Forester)` | **~T+15m** |
| explorer | s1 #13 `interact_npc(Forester)` | s1 #52 `gather(Oak)` | s1 #54 `interact_npc(Forester)` | **~T+25m** |

Teacher reasoning at turn-in: *"I have 12 logs and I'm on stage 2 … more than 10,
so I should turn in now."* Clean, near error-free (≤1 error each).

### 2. Herbalist's Desperation — accept, then GRIND the Foraging gate (done ~T+1.5–2h)

This is the quest the gate-handling prompt rule is about. The teacher **accepts
the quest despite the Foraging-5 gate, then grinds Foraging and forages the
ingredients** — it does NOT bail on the gate.

Winning sequence: **reach Herby (`warp`/`navigate`) → accept → grind Foraging to
L5 → gather `3× Blue Lily, 2× Paprika, 2–3× Tomato` → `interact_npc(Herby Mc.
Herb)` turn-in.** Herby at **(333, 281)**; Blue Lily bushes ~(327,288)/(325,291),
tomato ~(324,295).

| Agent | 0→1 | →2 | →3 (finish) | Elapsed |
|-------|-----|----|-----|---------|
| completionist | s2 #123 `navigate` | s3 #21 `navigate` | s3 #108 `warp(mudwich)` | **~T+1h20m** |
| explorer | s6 #86 `navigate` | s7 #20 `navigate` | s7 #33 `interact_npc(Herby)` | **~T+2h05m** |
| grinder | s2 #76 `warp(mudwich)` | — | s5 #65 `interact_npc(Herby)` | ~T+2h |

Teacher reasoning right after accepting (grinder): *"Herbalist's Desperation
accepted! … Now I need to: 1. Grind Foraging to Level 5 (currently Level 1) 2.
Gather 3× Blue Lily, 2× Paprika, 2× Tomato 3. …"* — i.e. **gate = grind, not
abandon.** Heavy `gather` (39–173 calls). Errors while active: BFS_NO_PATH,
COMBAT_BLOCKED_WARP, MOB_NOT_FOUND (mobs aggro the route to Herby).

### 3. Rick's Roll — the long pole: fish→cook→2 turn-ins→DOOR PUZZLE→Lena (done ~T+3–4.7h)

The hardest and longest. Four stages: (1) accept + cook shrimp, (2) first Rick
turn-in, (3) navigate a **door puzzle**, (4) reach **Lena** for the final turn-in.

Winning sequence: **accept (`warp mudwich`/`lakesworld`) → fish 5 shrimp near
(324, 360) → `craft_item` cook to `cookedshrimp` → `interact_npc(Rick)` **twice**
at (1088, 833) (the double turn-in) → `navigate` the stage-2 door route through
(260, 229) (with `warp(crullfield)` assists) → `interact_npc(Lena)` at (455, 924).**

| Agent | 0→1 | →2 | →3 | →4 (finish) | Elapsed |
|-------|-----|----|----|-----|---------|
| completionist | s5 #50 `warp(mudwich)` | s8 #146 `interact_npc(Rick)` | s9 #48 `navigate` | s9 #60 `interact_npc(Lena)` | **~T+4h40m** |
| explorer | s7 #67 `warp(mudwich)` | s9 #68 `interact_npc(Rick)` | s9 #114 `navigate` | s9 #129 `interact_npc(Lena)` | **~T+3h05m** |
| grinder | s5 #128 `warp(lakesworld)` | — stuck 1/4 — | | ✗ never | DNF |

This stage dominates the whole run's error budget: completionist logged
**68 BFS_NO_PATH + 11 STATION_UNREACHABLE** on Rick's; the grinder spent **412
observes / 389 navigates / 115 BFS_NO_PATH** and **never solved the door route**
(stuck at 1/4). The door puzzle (the (260, 229) door) is the single
hardest obstacle in Core-3 — even the successful agents brute-forced it with
dozens of navigate/warp retries.

---

## Notes / lessons for student comparison

- **Order in practice ≠ identical per archetype.** Completionist did Foresting +
  Herbalist's fast (both by ~T+1h20m) but Rick's took until ~T+4h40m. Explorer
  wandered early (Herbalist's not until s6/~T+1h45m) but then cleared Herbalist's
  + Rick's quickly, finishing all three by ~T+3h05m.
- **Foresting is a gimme** (session 1, no gate). If a student can't finish
  Foresting, it's a harness/navigation problem, not difficulty.
- **Herbalist's is a "grind the gate" quest, not a "switch away" quest.** The
  teacher always accepts and grinds Foraging to 5. A student that bails on
  `live_gate_status.gated:true` here will never complete it — this is exactly the
  contradiction fixed in the prompt (skill blocker ⇒ grind; quest/achievement
  blocker ⇒ switch).
- **Rick's Roll is the completion bottleneck** and needs ~3–4.5h of wall-clock +
  the door-puzzle navigation. No **fresh-from-L1** 3-hour run has finished it
  (only the 6h fresh runs, or cumulatively across resumed segments — see the run
  table above). Two sub-skills it requires that the others don't: `craft_item`
  (cook shrimp) and multi-warp door routing. The grinder's failure mode is always
  the door route (BFS_NO_PATH
  storm), never the fishing/cooking.
- **Completion is expensive.** 10/10 took 1,400–2,300 tool calls / 10–16 sessions
  / ~$60–84 of Claude. Useful when setting expectations for student parity.

---

*Generated from `analyze.py` over checked-in Claude logs. To refresh:
`python3 scripts/log_analysis/analyze.py --run run_20260505_150033 quest`.*
