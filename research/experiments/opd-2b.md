# OPD rounds 1–3: 4B teacher → base-2B student (the small-model pivot)

**Status:** rounds 1–3 complete + evaluated, June 7–14 2026 (branch `feat/r11-opd-tinker`).
**Headline: OPD took the base 2B from 12/30 to 18/30 (+50%) — a clear step beyond base. Every
agent completes two Core-3 chains unseeded (Foresting AND Herbalist's Desperation) where base
completes one; the Herbalist wall base passed 0/3 falls 3/3.** The Core-3 arc is monotone:
base 12 → r1 12 → r2 15 → **r3 18**. The competence is **weights-driven** — round 2 (15/30) is
the cleanest piece, a weights-only result on the same harness base ran on (no affordance). Over
base, the +6 decomposes as **+3 pure weights (r2, the wall) / +2 harness recovery / +1 r3
weights** (the +1 is within n=1 noise — the r3 weight update's reliable payoff is speed). Round 3 broke the stage-2 wall and fixed the format defect via the harness recovery
affordance — but Rick's Roll stayed 0/4 (over-determined: a displaced fishing tool gates link 1
before cooking is reached, with the cook-incompetent teacher a wall behind it [superseded 2026-07-17: the teacher forms/attempts cook — the wall is seeding + door/nav execution, not cooking incompetence; see the live Rick's probe]). The r2→r3 ablation (round-2 weights + recovery = 17/30) shows the harness
carried ~2 of those 3 stages and the r3 weight update ~1, whose distinct payoff is ~2× faster
progression — so **harness → stages, weights → speed describes the r2→r3 step only; over base,
weights are the competence engine**. We label 18/30 a weights-plus-interface result and don't lean
on nominally edging the 4B teacher (18 vs 17, with a recovery fix the teacher lacked). Supersedes
the 9B
instantiation of the OPD plan in [r11-direction.md](r11-direction.md) ("Where r11 sits");
companion to [r11-probing.md](r11-probing.md) (the 9B feasibility probes) and
[r11-27b-sanity-check.md](r11-27b-sanity-check.md) (the upward capacity check). Operational
state machine for the running chain: `dataset/opd_2b/ROUND1_RUNBOOK.md`.

## The pivot (June 7)

[r11-probing.md](r11-probing.md) established that the R11 scaffold alone recovers r10 from 2/30
to 10–12/30 — base's lower band. That reframed the r10-repair lane: we are confident OPD could
finish the recovery, but it would be expensive (9B teacher + 9B student serving plus H100
training per round) and the success case is uninteresting — a student restored to
≈base+scaffold level just answers "why not use base?" The 9B lane was parked with its round-1
data built but never trained (`dataset/opd_r11/round1/records.jsonl`, 1,074 records; trainer
`finetune/train_opd_modal.py`).

The question worth the compute is whether OPD can **instill** capability — measurably move a
student that genuinely lacks it, rather than restore one that regressed. That needs a student
sitting clearly below the teacher with headroom on the same benchmark, which the size-ladder
runs provided.

## The size ladder (the scaffold transfers down)

Same r11 scaffold, prompts, 16K gate, `KAETRAM_OBSERVE_COMPACT`, 3 archetypes — only the served
weights change. Core-3 stages /30 (`analyze.py --run <id> metrics`, summed over agents):

| Model (instruct, non-SFT) | Run(s) | Duration | Core-3 /30 |
|---|---|---|---|
| 27B (`serve_modal_27b.py`) | run_20260606_205254 | ~4h | 15 — capacity isn't the lever ([r11-27b-sanity-check.md](r11-27b-sanity-check.md)) |
| 9B (reference envelope, May 28–Jun 4) | — | 3–6h | 12–19 |
| 4B (`serve_modal_4b.py`) | run_20260607_150306 / run_20260607_190204 | 3h / 5h | 12 / **17** |
| 2B (`serve_modal_2b.py`) | run_20260608_003106 / run_20260608_185339 | 5h / 6h | 13 / 12 |

4B plays inside the 9B band. The 2B plateaus ~5 stages below 4B's best (12 vs 17). Foresting is
solved at every size and none cook (Rick's Roll is untouched at every size), but the **Herbalist
wall is the 2B-specific one** — the 2B stalls at stage 1 (its 4/10-per-agent ceiling) while
4B/9B/27B all clear it. Capacity barely moves the score *above* the 2B (27B's 15 sits inside the
9B's 12–19), so the gap that matters is the 4B's 17 vs the 2B's 12. So the
experiment is **teacher = scaffolded 4B, student = base 2B**: a real capability gap, same
family, and roughly an order of magnitude cheaper per round than the 9B lane — cheap rounds are
what an iterative OPD loop needs. (This held once the GDN kernels were fixed: r2/r3 build+train
ran ~$5–10 vs the ~$200 projected for a 9B round; round 1's ~$71 train was an artifact of the
broken-kernel 3× retrain, not the steady-state economics — see Costs.)

`scripts/opd/teacher_diag.py` sanity-checks the teacher choice before training: it scores the
same (context, action) pairs from the 2B's own rollouts on the 2B/4B/9B `/v1/score` endpoints
and compares teacher support and reverse-KL by depth tercile and tool verb.

## Program-wide comparison (every run, grouped by harness era)

Measured via `analyze.py --run <id> metrics/tools`, summed over the 3 archetype agents. **Only the
R11 block is a controlled comparison** (identical scaffold, varying capacity); the pre-R11 rows
ran on different harnesses — Claude doubly so (different model + the Claude Code CLI, May 5,
pre-R11-scaffold). Quest columns = agents (of 3) completing each chain; Core-3 = new stages reached
(duration-robust). Error % = game-state failure rate (high for Claude/r3 because they attempt the
hardest content). Durations differ (3–6h), so calls/deaths aren't duration-normalized.

**Quest progression & scale:**

| Harness era | Model | Core-3 /30 (g/c/e) | Foresting | Herbalist | Rick's | dur | calls | err% | deaths |
|---|---|---|---|---|---|---|---|---|---|
| pre-R11 · Claude Code | Claude Sonnet | 27 (7/10/10) | 3/3 | 3/3 | 2/3 | 5.9h | 5,605 | 12.9 | 114 |
| pre-R11 · r10-era | base-9B | 7 (1/3/3) | 2/3 | 0/3 | 0/3 | 3h | 1,738 | 5.1 | 36 |
| pre-R11 · r10-era | r10-SFT 9B | 2 (1/0/1) | 0/3 | 0/3 | 0/3 | 3h | 1,610 | 13.3 | 62 |
| R11 scaffold | 27B | 15 (6/4/5) | 3/3 | 1/3 | 0/3 | 4.2h | 2,324 | 3.4 | 146 |
| R11 scaffold | base-9B | 19 (7/6/6) | 3/3 | 3/3 | 0/3 | 5h | 2,844 | 3.4 | 145 |
| R11 scaffold | 4B (teacher) | 17 (6/6/5) | 3/3 | 2/3 | 0/3 | 5h | 2,140 | 4.3 | 105 |
| R11 scaffold | base-2B (student) | 12 (4/4/4) | 3/3 | 0/3 | 0/3 | 6h | 6,183 | 8.6 | 142 |
| R11 + recovery | **OPD-r3 2B** | **18** (6/6/6) | 3/3 | 3/3 | 0/3 | 6h | 8,563 | 13.0 | 183 |

**Tool-use signature (% of calls):**

| Harness era | Model | observe | navigate | attack | gather | query_quest | interact_npc | turns/s |
|---|---|---|---|---|---|---|---|---|
| pre-R11 · Claude | Claude Sonnet | 28.2 | 23.6 | 18.7 | 11.3 | 1.5 | 3.2 | 151† |
| pre-R11 · r10 | base-9B | 39.5 | 5.2 | 15.5 | 4.3 | 4.1 | 8.7 | 3.7 |
| pre-R11 · r10 | r10-SFT 9B | 32.7 | **27.9** | 13.9 | 5.5 | 2.1 | 2.7 | 3.7 |
| R11 | 27B | 31.9 | 22.0 | 2.8 | 17.7 | 8.8 | 1.5 | 8.2 |
| R11 | base-9B | 36.5 | 13.6 | 10.0 | 12.9 | 11.4 | 2.1 | 6.1 |
| R11 | 4B (teacher) | 36.1 | 9.1 | 9.1 | 20.8 | 8.7 | 1.4 | 6.1 |
| R11 | base-2B | 26.6 | 14.3 | 17.9 | 17.9 | 5.3 | 3.8 | 9.1 |
| R11+rec | OPD-r3 2B | 30.3 | 21.2 | 10.1 | 11.4 | 13.3 | 3.1 | 8.0 |

†Claude's turns/session is `maxSessionTurns`-bounded (39 sessions), not comparable to the Qwen
16K-rollover sessions — use tool calls as the cross-harness scale. **Three things the tables show
at once:** the harness lift (same base-9B 7 pre-R11 → 19 R11), the SFT regression (r10-era base 7
→ r10-SFT 2, with navigate amplified to 27.9% and decision verbs suppressed — the corpus-marginal
collapse), and the R11 capacity ladder (2B 12 / 4B 17 / 9B 19 / 27B 15, saturating above 9B) into
which OPD lifts the 2B (12 → 18). Claude (pre-R11, different harness) shows the game is winnable,
not a same-harness ceiling.

## Round-1 design

- **Data** (`scripts/opd/opd_2b_data.py`): the 2B student's own 6h-baseline rollouts
  (`run_20260608_185339`) re-scored by the 4B teacher → pre-tokenized records with per-token
  `advantages = -KL_COEF·(logp_student − logp_teacher)` over action tokens and
  `behavior_logprobs` for the IS ratio; `step_weight` 1.5 on early-session turns. Every 10th
  session held out to `heldout.jsonl` for the post-train gate. Round-1 build: **5,564 train /
  574 held-out records**, score_fail ~1%.
- **Trainer** (`finetune/train_opd_2b.py`): fresh LoRA on base `unsloth/Qwen3.5-2B` (init ==
  the policy that generated the rollouts), clipped importance-sampling reverse-KL, LM head
  applied only at action positions. Modal app `kaetram-qwen-2b-opd-finetune`, experiment id
  `kaetram-qwen3.5-2b-opd-r1`.
- **Gate** (`scripts/opd/opd_gate.py`): re-scores the trained student on held-out states. PASS =
  rKL reduction ≥ 30% and no degenerate completions. Diagnostic only — the play eval launches
  either way.
- **Eval**: `serve_modal_2b_opd.py` endpoint, model label `2b-opd-r1`, 3 archetypes, compact
  observes — apples-to-apples against the 6h 2B baseline (12/30; ~1,950–2,130 turns/agent;
  ~85–95 errors/1000 turns).

## Execution notes

- Training (174 steps = 5,564/32 exactly, lr 5e-5) ran on the **pure-torch Gated-DeltaNet
  fallback** (~168 s/step, ~8h — Qwen3.5-2B has 18/24 linear-attention layers and the
  fast-kernel paths were broken in our image: fla 0.5.0 refuses Triton ≥ 3.4 on Hopper,
  tilelang 0.1.11 SIGABRTs with a tvm::ffi TypeAttr double-registration). Adapter + merged at
  `kaetram-model-vol:/checkpoints/kaetram-qwen3.5-2b-opd-r1/`. The fast kernels (~10× per
  Qwen3.5 train) are resolved in round 2.

## Round-1 results (eval run_20260610_140358 vs baseline run_20260608_185339, June 10–11)

Validity gates all passed: both runs 6h / 3 archetypes / Mongo L1 reset / compact observes
(0/921 OPD logs contain ASCII_MAP) / zero rate limits / all agents alive; `run.meta.json`
model = `2b-opd-r1`.

### Headline: the style transferred, the competence didn't

Core-3 stages **12/30, identical to baseline** — every agent 4/10 with the same split
(Foresting 3/3 + Herbalist's Desperation 1/3, stuck at the same forage sub-stage). Underneath,
the student moved decisively onto the teacher's behavioral distribution:

| metric | base-2B | 2b-opd-r1 | 4B teacher |
|---|---|---|---|
| turns/session | 9.1 | **6.1** | 6.1 (exact match) |
| observe share | 26.6% | **35.5%** | 36.1% |
| navigate share | 14.3% | **9.4%** | 9.1% |
| attack share | 17.9% | **6.6%** | 9.1% (overshot) |
| query_quest share | 5.3% | **18.0%** | 8.7% (overshot 2×) |
| chars/turn | 411 | **1,248** | 864 (overshot) |
| total tool-error rate | 8.5% | **5.5%** | 4.3% |

Textbook reverse-KL signatures: exact convergence on the teacher's high-frequency modes,
mode-seeking amplification *past* the teacher on its preferences (query_quest, verbosity,
attack-suppression). The distillation was not a no-op and not Goodharted — the gate's
distribution movement transmuted into behavior.

### Execution wins (large call-level rate differences; 5,526 vs 6,183 calls — descriptive, calls within a handful of trajectories are autocorrelated, so no call-level significance is claimed [stat-hygiene edit 2026-07-13])

- **navigate errors 5.3% → 1.2%**; mix shifted navigate→warp (2.2%→5.0% share) — the BFS→warp
  rule base-2B never adopted.
- **eat_food errors 74% → 48%** (eat-inedible pathology halved).
- **MOB_NOT_FOUND 113 → 39**, and recovery changed: baseline's blind-retry
  (`MOB_NOT_FOUND → attack×37`) became re-grounding (`→ observe`).
- Foresting finished faster (final stage s38/s21/s40 vs s63/s27/s39); agents stay parked on
  the Core-3 frontier instead of drifting to side quests.

### Regressions

1. **Argument dropout (trained-in):** 8.3% of argument-requiring calls emit empty input
   (202 vs baseline 2; teacher 0/1,102), steady across run-quarters. The dominant span is one
   parameter (`accept_quest_offer`) written into the `<parameter=…>` key; SGLang salvages the
   verb but the argument is dropped, so the accept silently no-ops. The mechanism is a
   teacher-shared in-context preference, not a data-serialization artifact — see
   "Argument-dropout root cause" below.
2. **Deaths 142 → 172** (grinder 25→57): the student inherited the teacher's
   attack-suppression (attack calls 1,105→365, Health skill 23→11) without its danger sense.
3. **Session churn 681 → 921 sessions:** tripled verbosity fills the 16K window in ~6 turns;
   each rollover repays the bootstrap re-derivation tax, compounding the query_quest overshoot
   into 53.5% of all calls being information-gathering (vs 31.9% base, 44.9% teacher).

### The wall: visitation coupling, not teacher ceiling

Both arms reach Blue Lily Bush, pass the Foraging≥5 gate, and still mostly collect nothing —
ground truth in `Kaetram-Open/.../resourceskill.ts:226-248`: success is ~1-in-3 per loop tick
and one success depletes the bush. **The 4B teacher beats this wall in its own run** (2 of 3
agents finished Herbalist 3/3 around s76–s89 — precisely its 17/30 vs 12/30 edge). The student
did not inherit that competence for a structural reason: round-1 training data was base-2B
rollouts, which almost never visited lily-bush-at-Foraging≥5 states (20 ungated attempts in
6,183 calls). OPD only queries the teacher at student-visited states — the visitation-coupling
limit (L_OPD = E over d^πS) made concrete. The r1 policy now visits the wall far more (58
focused lily attempts; all three agents parked on Herbalist), so round-2 rollouts finally
query the teacher where it knows more.

One-line claim: *a single OPD round moved a 2B student measurably onto its 4B
teacher's behavioral distribution (tempo exact-matched, game-state error rate cut by a third,
navigation errors −77%) without converting it into task progress, because the competence gap
lives at states the round-1 data never visited — and it faithfully distilled a teacher-shared
in-context copy-prior into an 8.3% argument-dropout defect (see root cause below).* (Round 1
billed **~$111** — broken GDN kernels forced a 3× ~8h H100 retrain; once fixed, a round's
build+train is ~$5–10, eval serving aside. See Costs below.)

### The gate × behavior cross-table (mechanism consistency)

Post-train gate (re-captured 06-11, `dataset/opd_2b/round1/gate_r1_output.txt`): token-weighted
held-out |rKL| to the 4B dropped 0.4904 → 0.4369 = **+10.9% — a formal FAIL against the 30%
bar** (eval ran anyway per the diagnostic-only authorization; 0/3 degeneration). The
distribution-vs-behavior pairing is the round's most interesting result: a modest 10.9%
per-token movement on held-out (old-visitation) states compounded into the large behavioral
shifts above — and the per-verb signs line up. Every verb whose rKL dropped (attack −17%,
stuck_reset −23%, warp −13%, respawn −13%, navigate −11%) moved toward the teacher
behaviorally; **query_quest is the single verb whose rKL INCREASED (+20%) and exactly the verb
that behaviorally overshot the teacher 2×** — the student moved past the teacher and now
diverges from the other side. Mode-seeking amplification, visible in both spaces at once.
The lesson: a token-level KL gate on old-visitation states under-predicts behavioral
movement; the 30% bar measures the wrong quantity.

### What the per-token advantages reward: structure, not reasoning

A direct read of the round-1 held-out records (574 records = base-2B rollouts, each carrying
raw per-token `teacher_logprobs` + `student_base_logprobs`; advantage = teacher − student,
positive ⇒ OPD pushes the token up) shows the teacher does *not* reward reasoning prose — the
positive advantage concentrates on **tool-call structure**, the negative advantage on **prose**:

| pushed UP most | sum | pushed DOWN most | sum |
|---|---|---|---|
| `=` (`<function=`/`<parameter=` syntax) | +182 | `parameter` | −363 |
| `</tool_call>` | +52 | `.` `,` ` ` `:` ` (` | −250…−175 ea |
| ` observe`, `dialog`, `_state` (verb/field names) | +30…+42 | ` the` ` and` ` to` ` at` ` for` | −185…−138 ea |
| `</think>` (mean +0.212, **79% positive**) | +5 | `\n` | −178 |

Region means confirm it: reasoning-region tokens carry the *most negative* mean advantage
(−0.333), tool-call-region tokens are ~neutral (−0.039). This is textbook reverse-KL — the
base-2B is over-confident/peaky on its idiosyncratic prose wording (any one phrasing of "`.`"
scores negative against a teacher that spreads mass over many), while tool-call structure has
one correct form and scores sharply positive. It also lines up with the Stage-A pre-train
diagnostic (student over-confident on *every* verb → suppressed).

Consequences for how to read the round:
- **The dialect shift IS directly rewarded, the reasoning *length* is not.** The single token
  the teacher rewards inside the reasoning span is the `</think>` delimiter (+0.212, 79%
  positive) — the entry hook into the reasoning dialect. That cleanly explains the tag rate
  going 4% (base) → 99% (r1). It does **not** explain the ~4× growth in reasoning prose
  (base ~64% of turns carry reasoning content / median 195 chars on the tagged subset → r1
  ~100% / 793), because the prose tokens themselves are suppressed.
- **The length growth is a trajectory-level mode-seeking effect, not a per-token reward.** The
  student is pulled toward the teacher's high-probability *trajectories* ("reason, then emit a
  clean tool call" — the teacher reasons on 100% of its own turns); the teacher-forced grade on
  the *base's specific* reasoning tokens is negative (wrong phrasing), but the sequence-level
  pull is still toward reasoning-then-acting, with `</think>` as the landing token. This is the
  same mode-seeking framing as the gate × behavior cross-table above — stated at the token level.
- **Generator ≠ grader.** "The 4B prefers reasoning" is true of how it *generates* (100% of its
  own non-thinking turns reason — the closed-empty-`<think>` block weakly suppresses an
  RL-hardened reasoning prior; the 2B's weaker prior complies 64% of the time, and that
  ~36-point propensity gap is the inter-size capability difference) and **false** of how it
  *grades* token-by-token (rewards structure + `</think>`, suppresses prose). Conflating the two
  produces the wrong "teacher taught reasoning by rewarding it" story.

**The thinking regime — why the dangling `</think>`, and why it's valid.** The whole OPD pipeline
(base-2B rollouts, the 4B teacher's serve + `/score` grading, every r1–r3 eval, and the
`opd_2b_data._render` build) ran the Qwen3.5 template's **non-thinking default**: a closed-empty
`<think>\n\n</think>\n\n` generation prompt that says "reasoning is done, answer now." Qwen3.5's
RL-hardened reasoning prior leaks through that soft suppressor anyway — the model reasons in the
*content* channel and emits a vestigial close tag with no opener (the "dangling `</think>`"). The
tag rate is a reasoning-effort gauge, not a real block: base-2B 4% → 4B teacher 100% → distilled
r1/r2 99% → r3 12%. It is **cosmetic, not a correctness bug**: the tool-call parser ignores think
tags, train and serve use the identical verbatim-emission format, and the regime is held constant
across base/teacher/student, so every comparison is apples-to-apples and no result depends on it.
The regime was inherited from the template default, not chosen (whether thinking-*on* would help is
untested), and the 99%-vs-12% dangling split is also what let us confirm, after the fact, that the
pipeline ran non-thinking throughout.

### Argument-dropout root cause: the teacher shares the defect in-context

The 8.3% dropout is dominated by **one span family**: the model writes the boolean kwarg
into the parameter *key* — `<parameter=accept_quest_offer=True>` — which the parser drops,
so the accept silently no-ops. Eval run: **154 malformed vs 18 correct**; baseline: **0 vs
182**. Zero errors are logged (the intent never reaches the game), which is why it hides from
the error metrics and why quest acceptance lags (grinder accepts Herbalist ~5h vs
baseline ~2.5h — sampling luck on the ~11% correct rate). The corruption is confined to this
one parameter.

The mechanism is not the obvious candidates. Round-1 `interact_npc` training records (203
total, 154 carrying the accept param) all hold the correct form as raw student tokens — the
malformation was never imitated from data. In base-rollout contexts the mean advantage on the
correct `>` delimiter is **+0.74** (the teacher is *more* confident than the student in the
correct form), so training pushes the correct form *up*. And there is no reasoning-self-priming
association (malformed calls 40% primed vs correct 47%).

What surfaces it is a context flip in the grader. On a real r1-rollout malformed state scored
on both `/score` endpoints, at the divergence token the **4B teacher assigns −0.161 nats (~85%
probability) to the malformed `=True` continuation** vs the r1 student's −0.253 →
**advantage +0.09 toward the malformation**; the mirror probe on a correct-form state gives
+0.39 toward the correct `>`. Under teacher-forcing on the student's malformed prefix the
teacher follows the student into the attractor — the prompt documents the call Python-style as
`interact_npc(..., accept_quest_offer=True)`, and the `<parameter=` wire dialect primes the
assignment reading — even though it never emits the form generatively.

**The defect is the teacher's in-context copy-prior, faithfully distilled in round 1, and dense
reverse-KL cannot self-correct it on r1's own rollouts** (the per-token signal points the wrong
way). **Cross-grader generality (probe 2026-07-15):** the 2B-as-grader shows the same
mechanism — doc-literal canonicalization suppresses its endorsement of the malformed
continuations by median −1.455 nats (57% of states ≤ −0.2), vs the 4B's −1.21/86% — so the
copy-prior is at least family-general, a structural hazard of dense teacher-forced grading
primed by Python-style tool-doc literals, not a 4B idiosyncrasy. (9B grader untested — its
r10-era endpoint failed all /score calls; optional A100 retry.) General lesson: on-policy distillation transfers teacher failure modes that only manifest
under teacher-forcing; a generative check (the 4B emits the correct form in its own runs) does
not certify the teacher as a per-token grader. Impact is bounded: stage *turn-ins* advance via
plain `interact_npc` with items in inventory (server-side consume) — the parameter only gates
initial accepts. The defect's arc across rounds: round 2 masks advantages on the malformed
spans (containment, no cure), round 3 grades them under a canonicalized teacher context
(regresses the emission), and a harness recovery affordance finally cures it at generation
time (see round 3).

### Qualitative deep-read: six readers over r1 + base logs

Six Explore subagents read the actual sessions (every ~4th in full plus all
quest-transition neighborhoods) across `run_20260610_140358` + `run_20260610_222755` (r1)
and `run_20260608_185339` (base). Beyond the metrics:

- **The wall in the logs is three-layered and shared** (both models, all archetypes):
  the Foraging-5 gate (correctly *diagnosed* in reasoning, slowly executed — base
  completionist took ~27 sessions for Foraging 2→5), a **25/25 inventory deadlock** at the
  gather phase (base explorer burned 100+ sessions in it; the r1 grinder hit the identical
  state at s280; `drop_item` gets retried without strategy), and the hostile L42–54
  turn-in zone (the r1 completionist at L4–9 walked in, reasoned "too low-level to safely
  battle", and did not retreat). An inventory affordance is plausibly worth more Core-3
  stages than another training round.
- **Mechanism find** (live run, completionist s44): Herbalist stage 1→2 advanced via a
  plain `interact_npc` turn-in with the items held — `accept_quest_offer` is irrelevant to
  stage progression, only to initial accepts.
- **Behavioral narrowing:** r1 agents are rigidly Core-3-locked and ritualized ("Let me
  observe first to confirm…", full-state re-enumeration every turn; grounding degrades
  toward template-matching late in the run). Base drifted — sometimes productively
  (finished Anvil's Echoes, ran Scavenger to stage 2) — and out-leveled r1 in raw combat
  (grinder ~L78 vs ~L37). Distillation traded breadth for execution discipline.
- **Thinking dialect:** 99% of r1 turns reason in a dangling-`</think>` dialect
  (reasoning as plain content + spurious close tag; the opener lives in the
  closed-empty-think generation prompt), verbose with zero empty turns — base's 4%
  dialect universalized. Base: 97% of turns have no think structure (median 106 chars,
  ~32% effectively thoughtless), and its rare *long* reasoning (600+ tokens) is
  self-contradicting stream-of-consciousness that doesn't change the chosen action.
- **Error-taxonomy caveats** (affects reading `analyze.py` categories): a chunk of
  base's MOB_NOT_FOUND co-occur with `post_attack.killed=true` — the kill succeeded, the
  message is stale, the model re-attacks and inflates the count; r1's −65% is partly
  "doesn't re-attack dead mobs". Base's eat-inedible is mostly slot-shift after drops plus
  eating at full HP, not literal inedibles. r1's signature errors are instead
  missing-required-parameter calls that it self-corrects on the next turn.
- **Verdict:** r1 is the better agent for the data-collection objective — a better
  executor (fewer fumbles, deeper quest progress: first agents to reach Herbalist 2/3, in
  the live run) with the same strategic ceiling: neither model converts correct diagnosis
  into committed multi-session strategy. The 2B capacity story is unchanged; what OPD
  bought is execution discipline.

### June-2026 literature alignment

The recipe sits squarely inside the May–June 2026 consensus stack, with two
no-precedent elements that are ours to write up:

- **Selective token masking** of grader-unreliable spans is established practice:
  TrOPD ([2606.01249](https://arxiv.org/abs/2606.01249)) explicitly ablates masking vs
  clipping vs forward-KL for outlier tokens; Rock Tokens
  ([2605.09253](https://arxiv.org/abs/2605.09253)) masks persistent structural residuals;
  special-token masking is one of the "simple fixes" in
  [2603.25562](https://arxiv.org/abs/2603.25562); TA-OPD
  ([2605.26844](https://arxiv.org/abs/2605.26844)) trains on the teachable ~5% only.
  DistIL ([2606.05152](https://arxiv.org/abs/2606.05152)) supplies the theory: reverse-KL
  objectives can *increase* probability on worse actions — so abstention (zero), not
  down-weighting, is correct where the grader is provably wrong-direction.
- **Our defect's nearest named relative** is KAT's "KL agreement trap"
  ([2606.09471](https://arxiv.org/abs/2606.09471), June 8): the teacher locally agrees
  with degraded student prefixes, yielding no corrective signal. Our case is the sharper
  variant — the teacher actively *prefers* the malformed continuation (~85%) it never
  emits generatively. **The teacher-forcing copy-prior mechanism (ICL copy bias / induction
  heads surfacing inside the grader: [2410.01288](https://arxiv.org/abs/2410.01288),
  [2505.13514](https://arxiv.org/abs/2505.13514)) has no precedent in the distillation
  literature — novel observation.** Related framings: the OPD survey's "flawed prefix
  trap" ([2604.00626](https://arxiv.org/abs/2604.00626)), SKD's "inaccurate teacher
  feedback" ([2410.11325](https://arxiv.org/abs/2410.11325)), SG-OPD's verifier-teacher
  sign-gating ([2606.09304](https://arxiv.org/abs/2606.09304)).
- **Gate redesign** (off per-token KL, onto rollout-level behavior + format checks) is
  what KAT/TCOD ([2604.24005](https://arxiv.org/abs/2604.24005)) motivate; no published
  numeric round-over-round criterion exists — held-out behavioral plateau is as principled
  as anything in print.
- **Env-state seeding** is a reverse-curriculum state reset — a well-trodden RL idea:
  reset-along-a-demo (Salimans & Chen, [1812.03381](https://arxiv.org/abs/1812.03381)),
  Backplay ([1807.06919](https://arxiv.org/abs/1807.06919)), Go-Explore
  ([1901.10995](https://arxiv.org/abs/1901.10995)), plus the LLM-side analogues: BREAD's
  branched rollouts ([2506.17211](https://arxiv.org/abs/2506.17211)), Reset-50-50
  expert-state resets ([2502.10325](https://arxiv.org/abs/2502.10325), 82.0% vs 73.9%),
  TRB's visitation-shifting ([2605.31159](https://arxiv.org/abs/2605.31159)). **Our narrow
  contribution is the setting + mechanism: a write of persistent typed environment state (a
  DB row), for on-policy distillation rather than reward-RL — "Backplay for LLM tool-use
  distillation."** Watch-item from [2605.19433](https://arxiv.org/abs/2605.19433) (reversed
  exposure bias): teacher guidance quality at seeded states matters — our 3/3 behavioral wall
  passage suggests it holds here.
- **Iteration design** (init = merged r1 + fresh LoRA, fresh on-policy corpus) is the
  canonical DAgger loop (survey [2604.00626](https://arxiv.org/abs/2604.00626);
  DAgger-LLM at 4B/8B [2605.12913](https://arxiv.org/abs/2605.12913)); zero rollout-drift
  at round start is what f-OPD gates toward
  ([2605.17862](https://arxiv.org/abs/2605.17862)). Rethinking-OPD
  ([2604.13016](https://arxiv.org/abs/2604.13016)) documents our round-1 outcome as the
  expected failure mode when the teacher's delta isn't in the visited states ("style-level
  convergence without capability gain") and notes 2× capacity gaps are benign (<10×
  threshold). Round-3 options if instability shows: KAT rollout termination, TCOD horizon
  curriculum, POPD truncation ([2605.31490](https://arxiv.org/abs/2605.31490)).

## Round 2 (June 11–12): design, execution, results

**Design deltas from round 1** (all validated pre-spend): (1) `_emission_text` = `turn.text`
verbatim + `<|im_end|>` (the doubling re-synthesis deleted; dry-render 0 prefix violations,
0 doubled structures / 524 sampled states); (2) raw advantages restored (trainer ADV_CLAMP=3
— round-1 recipe); (3) **abstention masking**: advantages zeroed on malformed-param spans
(`<parameter=K=V>`; 197 spans ≈ the 154+42 known counts); (4) gate redesigned — hard verdict
on malformed-rate/degeneration/blow-up, held-out rKL (masked spans excluded)
directional-only; (5) **bucket-B seeded corpus slice**: `run_20260610_222755` (2.5h, agents
DB-seeded at Herbalist stage 1, Foraging 5 — all 3 passed the wall in seeded rollouts);
(6) init = merged r1 + fresh LoRA (init==generator); (7) fast GDN kernels
(fp32-three-way-parity-verified, 8.9×; train 220 steps ≈ 50 min vs round-1's 8h).
Design decisions: no env dispatch shim (eval parity), full-corpus interleave (~70/30
natural/seeded), raw+clamp advantages.

**Build**: 7,024 train / 825 heldout from 7,849 states, **zero failures of any kind**
(no score_fail/target_mismatch/prefix_mismatch); 177 spans / 1,422 tokens masked (0.06% of
action tokens; remaining ~20 spans sit in holdout). Fresh-state per-verb rKL ≈ **half of
round-1 levels** (eat_food +0.298→+0.149, Herbalist frontier +0.261→+0.136) — round 1
genuinely moved the policy toward the teacher on its own visitation distribution.

**Gate (r2, redesigned): PASS** — held-out rKL to teacher 0.4281→0.3662 (**+14.5%, every
verb improved** — incl. query_quest 0.396→0.324, reversing round-1's sign-flip), 0/10
degeneration, no blow-up. (Format check vacuous: no spot completion emitted parameter
blocks.) `dataset/opd_2b/round2/gate_r2_output.txt`.

**Eval (run_20260612_044933, 6h, unseeded, parity verified): Core-3 15/30 — 5/10 every
agent. All three agents passed the Herbalist stage-1 wall unseeded** (grinder 4.1h /
completionist 4.5h / explorer 5.0h) — a stage neither base nor r1 ever reached:
base 0/3 → r1 0/3 → **r2 3/3**; the first weights-driven Core-3 lift above
the scaffold floor. Accept milestones also compressed: all 3 accepted by 3.2h (base max
5.7h, r1 max 5.3h); correct-form accepts 26 vs 86 malformed (r1: 18 vs 154). Foresting
monotone faster (r2 0.4–0.6h < r1 0.5–0.8 < base 0.7–1.8). Stage 2 progress at run end:
paprika 1 + tomato 1 (grinder, via an opportunistic bush at (358,325)), tomato 3
(completionist — made the first-ever deliberate trip to the (298,300) paprika cluster but
nav-looped 6 tiles short), tomato 2 (explorer). Overshoot retreating: query_quest share
18.0→12.9% (teacher 8.7), chars/turn 1,248→1,116 (teacher 864); turns/session held at the
teacher's tempo (6.1→6.0).

**Qualitative mechanism of the passage** (six-figure deep-read, session cites in logs):
r2 agents rotate bushes after empty gathers, track the Foraging-5 gate explicitly, and use
gather auto-walk across the map; r1's failure at the same wall was a *premature-turn-in
attractor* (s269: standing distance-1 from the bush holding 2/3, walks 85 tiles to Herby
with an incomplete set) plus gate-blind retries at Foraging L1–4; base displaced into
combat (L59 with Foraging L4). Reasoning profile: r2 is still verbose and revision-heavy like
r1 (non-empty median ~558 chars vs base's ~195; ~59% of blocks carry revision churn) while
keeping state-grounding — the compression comes in round 3.

**Costs of round 2:**
1. **The malformed-call attractor mutated and persists.** Kwarg-in-key declined (154→86)
   but a NEW dominant form appeared: Python-call syntax inside the function tag —
   `<function=gather("Oak")>` — 0 (base) → 79 (r1) → **599 (r2)**; plus corrupted closing
   tags (`</number>`, `</script>`). Masking removes reinforcement but supplies no
   correction (the predicted limit); **the cure is the harness-side lever** that round 3 adds.
2. **Total failure rate** base 8.6% / r1 16.9% / r2 16.7%: the OPD models carry a
   *schema/validation* failure class (~0.1% base → ~11%) that the malformed calls produce and
   that roughly doubles their total failure rate, while the *game-state* error class improved
   across rounds (8.5→5.5→5.9%).
3. **Deaths 142→172→188** — but 78% of r2 death-positions are at Herby's tile (the L45–54
   turn-in zone): largely the *cost of attempting the quest* (turn-in survives death;
   agents name the danger and proceed — mission-rational, then occasionally "attack an Orc
   while dead").

**Validity notes**: n=1 runs per arm; archetypes are prompt variants (direction-consistency
3/3 is the replicate structure); known analyzer gaps (quest-transition verb attribution
shows the in-flight tool, not the cause — all three r2 passages were `interact_npc`
turn-ins; `gather items_gained` races the server and under-reports successes; eat_food
error class conflates HP_FULL/slot-shift with validation empties).

## Round 3 (June 12–13): counterfactual grading, full-ladder seeding, harness recovery — 18/30

Round 3 attacked the two open wounds from round 2 — the mutated malformed-call attractor and
the stalled Herbalist stage 2 — and aimed at 10/10. It produced the program's best result
(**18/30, 6/10 every agent**) and a clean separation of what training vs harness can each do.

### Design

1. **Counterfactual-canonicalized grading (Plan A, weights-side).** At parser-flagged
   records, the TEACHER scores the same student emission under a context whose system-prompt
   tool-doc literals are rewritten to canonical wire form — its clean-convention preference
   becomes a corrective negative advantage instead of round-1's +0.09 copy-prior endorsement. A
   pre-build **flip probe** set the scope empirically: canonicalizing the malformed *history*
   calls was a measured **null (0% flip)**, but rewriting the system prompt's Python-style
   tool-doc literals (`interact_npc(npc_name, accept_quest_offer=False)`) in the *teacher's
   grading copy* flipped **86% of flagged states at −1.21 nats** — so the build canonicalizes
   the doc-literals only (history left as-is; student/serving/eval copies untouched).
   Precedented: CCOPD ([2605.30251](https://arxiv.org/abs/2605.30251)), OPSD
   ([2601.18734](https://arxiv.org/abs/2601.18734)); the doc-literal canonicalization + the
   mixed-context advantage are ours to characterize.
2. **Full-ladder milestone seeding.** Two 2.5h collections seeded the 2B (`seed_milestones.py`,
   e2e-verified conftest kwargs) across every unsolved stage: Herbalist stage 2 (tomato +
   paprika sides), Rick's fishing (R3), cook-decision (R4), turn-in (R5), door (R6). All
   graded by the plain 4B.
3. **Build**: r2-eval rollouts + both seeded runs → **8,856 train / 1,040 heldout, 4 score
   failures**; 365 counterfactually-graded records (0 tokens masked — corrective grading
   replaced round-2 abstention). Train from merged r2, 277 steps. **Gate PASS** (rKL to
   teacher 0.3558→0.3330 = +6.4%, every verb improved; 0 degeneration).

### The two-attempt story: counterfactual grading alone REGRESSED; harness recovery fixed it

The first 6h eval (`run_20260613_105318`) was **stopped at 35 min**: counterfactual grading did
NOT suppress the malformed *emission* — it regressed it. 2/3 agents were paralyzed (57% / 76%
spam fraction), emitting `<function=query_quest("Foresting")>`-style calls the server can't
parse, which fall to text, never execute, and the model re-emits the unanswered call → spam
loop. **Key lesson: grader-endorsement-suppression (flip-probe-verified) did not transfer to
student-emission-suppression after training.** The defect lives in the policy's generation, and
a grading-context fix doesn't reach it.

The fix was a **harness affordance** (`KAETRAM_TOOL_RECOVERY`, env-gated): when the server
drops a malformed call, `play_qwen` recovers the executable call (`canonicalize.recover_tool_calls`,
99.5% coverage on real specimens), **rewrites history to a clean canonical assistant turn**
(severing the in-context copy prior at its source), executes it, and returns a loud `[format]`
correction note. The rerun (`run_20260613_112422`) with recovery on solved the paralysis
completely (3.3% recovery rate, **self-correcting** — see below).

### Results: 18/30, and the Herbalist stage-2 wall broke

| metric | base | r1 | r2 | **r3** | 4B |
|---|---|---|---|---|---|
| Core-3 /30 | 12 | 12 | 15 | **18** | 17 |
| total err % | 8.6 | 16.9 | 16.7 | **13.0** | 4.3 |
| — game-state err % | 8.5 | 5.5 | 5.9 | **7.9** | — |
| — validation/format err % | 0.1 | 11.5 | 10.7 | **5.1** | — |
| malformed emissions | 0 | 233 | 685 | **335** (405 recovered) | 0 |
| reasoning chars (median non-empty) | 195 | 793 | 558 | **243** | 864 |
| near-empty reasoning turns | — | 0% | 0% | **~30%** | — |
| revision-churn / block | 0.74 | 2.43 | 1.88 | **1.17** | — |
| `</think>` dialect | 4% | 99% | 99% | **12%** | — |
| deaths | 142 | 172 | 188 | 183 | 105 |

All three completed Herbalist 3/3 (2.7–4.0h, sessions ~181–205) — r2 had *zero*. Mechanism
(six-figure deep-read vs r2's stuck stage 2), three concrete differences:
- **Correct cluster coordinate.** r3 navigated to the *actual* Paprika Bush at **(358,325)**
  (deep in the hostile zone), not the prompt's stale (298/300,300). r2's completionist burned
  its whole budget walking toward unreachable (300,300), frozen at (303,296) repeating
  *"Still progressing. Let me continue…"* ~16× without re-observing.
- **Stall-recovery via adjacent re-targeting.** r3 hit the same 1-tile nav stall r2 died on,
  but re-targeted an adjacent tile (356,325) → arrived → gathered. r2 never made this move.
- **Tighter observe→act loop** (half-length reasoning → re-observes more often, catches frozen
  positions in 1–2 turns not 16). Gathered paprika **survives death**, so dying in the L45–54
  zone (10/7/10 deaths during the grind) didn't reset progress.

### Decomposing the gains: weights are the competence engine over base

The full **+6 over base (12→18)** decomposes into **+3 from weights alone** (round 2,
12→15, run *without* the recovery affordance — the Herbalist wall falls 3/3 unseeded), **+2 from
the harness recovery affordance**, and a residual **+1 from the r3 weight update that sits within
the n=1 noise band** (it may be ~0 stages; the r3 update's reliable payoff is speed, not stages).
So over base, weights contributed ~+3–4 of the +6 and the harness +2 — **weights are the larger
cumulative lever, and
they are what makes the 2B better than base** (the wall, visible with seeding off at eval).

> **SUPERSEDED (2026-07-12, E4):** the "+2 harness / +1 weights" split below was refuted by the
> r3-no-recovery run (18/30 — see "Hardening runs E1 + E4"). The r2→r3 stage gain is
> weights-driven; recovery's contribution is efficiency. Kept for provenance.

We isolate the harness/weights split *at the r2→r3 step* with the controlled ablation —
**round-2 weights with the recovery affordance on**, otherwise the identical 6h protocol
(`run_20260613_214956`). It scored **17/30** (grinder 6, completionist 6, explorer 5). So of the
three stages between r2 and r3, the harness affordance contributes **~2** and the r3 weight update
**~1** (within the n=1 band). The r3 weight update's distinct payoff is **speed**, not stages:
r3 reaches Herbalist stage 2 in **~2.0h vs r2's ~4.5h (~2× faster)** and runs **~40% more turns
per hour (511 vs 366)** — the dividend of its much shorter reasoning (below) and fewer nav stalls.

**Speed de-confound (added 2026-07-11, per the Codex cross-review objection that r3+recovery vs
r2-without-recovery conflates the two levers).** Recomputed per-agent time-to-Herbalist-stage-2
and turns/hour on all three arms, recovery now held constant in the r2-vs-r3 contrast:
r2 = 4.13/4.47/5.00h (mean 4.53h, ~367 t/h) → **r2+recovery = 3.17/3.96/4.17h (mean 3.77h,
~353 t/h)** → r3+recovery = 2.25/2.20/1.41h (mean 1.95h, ~512 t/h). Recovery alone buys −17%
time-to-wall and **zero throughput** on r2 weights; with recovery fixed, the r3 weight update
still halves time-to-wall (3.77→1.95h) and adds +45% turns/hour. **"Weights buy speed" survives
the de-confound** — the speed dividend is attributable to the r3 update, not the affordance.
(Script: session-timestamp scan for first observe with Herbalist stage ≥2; run IDs
run_20260612_044933 / run_20260613_214956 / run_20260613_112422.)

So **harness → stages, weights → speed describes the r2→r3 step only**; over base the order
reverses — weights instilled the competence, the harness unblocked a defect that was suppressing
it. We label 18/30 a **weights-plus-interface** result and rest the competence claim on round 2's
pure-weights wall passage, not on nominally edging the 4B teacher (18 vs 17, with a recovery fix
the teacher lacked).

### Rick's Roll: 0/4 — a three-link execution failure, not missing intent

The agents *understood and attempted* the full chain (52 navigate-to-Rick, 5 interact_npc("Rick"),
12 door-(379,388) attempts; all quote the walkthrough verbatim). The chain dies at **link 1
(fishing)**: the **fishing pole is never equipped** (Foresting's logs displaced it from slot 2;
0 pole-equipped observes across all agents), so every shrimp gather returns `items_gained:
"none"` and zero shrimp enter inventory — cook/door/Rick never become reachable. Compounding:
**craft_item = 0 across all 200+ post-Herbalist sessions** (zero cook transfer — matching the
cook-incompetent 4B exactly, the pre-registered KAT-agreement-trap prediction [the
"cook-incompetent" reading is superseded — see the 2026-07-17 live Rick's probe: the 4B and
base-2B both form/attempt the cook action; the 0 is reachability + seeding bugs]), and the
~217-tile cross-door traverse exceeds the ~8-turn session budget while the session note can't
carry route progress for an un-accepted quest. **Seeded-state grading instilled the declarative
plan but none of the procedural gates** a 2B can't clear: re-equip a displaced tool, cook
(no teacher signal), traverse a multi-session route.

### Thinking coherence: r3 sheds verbosity (the r2-vs-r3 token comparison)

Round 3's reasoning is a *third shape*, distinct from both base and r1/r2. The r2-vs-r3
contrast: non-empty reasoning median **558 → 243 chars** (more than halved); the share of turns
that are near-empty **0% → ~30%**; revision-churn **~59% → ~30%** of blocks (markers per block
1.88 → 1.17); and the dangling-`</think>` dialect **99% → 12%** (reverting to base's bare-prose
*form* while keeping r1/r2's coordinate grounding, 48% → 49%, far above base's 30%). The
aggregate chars/turn (496) looks like a regression toward base (411), but the decomposition
refutes it: when r3 reasons it is tight and decision-relevant (real coords + quest
have/remaining + next action in <370 chars), versus r2's 2000+-char loops re-litigating
blueberry-vs-bluelily without resolving and hallucinating coordinates (r2's fabricated
"(-46,-215)"); the low aggregate is the return of the near-empty turns, not degraded grounding.
Arc: base (tool-spam, no grounding) → r1 (maximal verbose churn) → r2 (still verbose and churny)
→ **r3 (concise, halved churn, grounding preserved)**. Brevity is overhead removal, and it
bought both more progress and ~2× the speed to the wall.

### Self-correction via harness recovery — the key mechanism, confirmed

405 sessions emitted a `[format]` note; **every one has exactly ONE note, zero relapse, 98.5%
clean afterward**; 90% fire at turn 2 (first call after the opening observe) and never again.
The malformation is a *session-opening artifact*, and the note — by rewriting history to clean
canonical exemplars + the correction text — durably fixes it. This is context-canonicalization
working as designed: deny the copy prior its malformed exemplars and the model stops copying.
The recovery also breaks the *compounding* (335 emissions vs r2's 685): without feedback,
malformed begets malformed; with it, the model self-corrects and the total stays bounded.

### What round 3 established (the clean separation)

> **PARTIALLY SUPERSEDED (2026-07-11/12/13):** bullet 2 is refuted by E4 (r3 weights without
> recovery reach 18/30 — recovery contains the *trajectory-level* attractor and buys efficiency;
> it does not gate stages, and the weights retain the session-opening defect: session-local
> correction, not a cure). Bullet 3's mechanism is reworded per the cook-grading probe (grades
> are ~state-insensitive globally; the Rick's null is over-determined, not a measured
> cook-specific grading hole). Bullet 1's causal reading awaits the ±seeding ablation
> (run_20260713_084905). See "Hardening runs E1 + E4" and "Cook-state grading probe" below.

- **Visitation-corrected weights instill competence over base** — the Herbalist wall base
  passed 0/3 falls 3/3 unseeded (round 2, pure weights, no affordance: 12→15). This is the core
  *better-than-base* result; round 3's stage-2 break extended it. Over base, weights led the +6
  (~+4 of 6).
- **A harness affordance dissolved what weights-side fixes could not** (the format
  defect: r1 created it, r2's masking contained-not-cured, r3's counterfactual grading
  *regressed* the emission; recovery + context-canonicalization fixed it instantly). This is
  the harness+weights co-evolution thesis made concrete — the program's clearest single lesson.
- **A teacher cannot grade in what it cannot do** (Rick's: the 4B never cooks → flat,
  weak seeded-state grades → 0 cook transfer, exactly as the pre-training diagnostic predicted).
  **SUPERSEDED (2026-07-17, live Rick's probe):** "the 4B never cooks" is a
  generative-non-occurrence artifact, not incompetence — base-2B emits the correct cook call
  21×/run when seeded at a station, the 4B cooks 7/20 (P-D), and the 0 transfer traces to four
  seeding/harness bugs (respawn-dungeon cook station, door-gated turn-in regions that reset the
  seed to spawn, seaside aggro, and a warp/never-step-on-doors executor that stalls the 4B too).
  See "Live seeded Rick's-Roll probe."

**Framing:** the r3 18/30 is a **weights + harness recovery** arm — env-changed, not
pure-weights like base/r1/r2 — so we label it as such. The *better-than-base* claim doesn't rest
on it: round 2's 15/30 is pure-weights (no affordance). For the r3 arm specifically, the binding
constraint on the last stages was the format defect — a model–environment interface failure
weights-only fixes provably could not reach — which is why 18/30 is honestly weights-plus-interface.

### Hardening runs E1 + E4 (2026-07-11/12): the factorial completes — weights carry the stages, recovery buys efficiency (and the June "paralysis" read was wrong)

Two missing factorial cells were run (6h protocol, e2-standard-**4** instance — June arms ran
e2-standard-8; E1 truncated to 6h after a silent supervisor death let it run 8.6h; E4 stopped
manually at 6h14m; both scored at the 6h boundary):

| cell (6h) | Core-3 /30 | wall | time-to-Herb-stage2 | notes |
|---|---|---|---|---|
| base-2B (June) | 12 | 0/3 | — | |
| **base+recovery (E1, run_20260711_065435)** | **12** | **1/3** | — | recovery fired **once** all run — a no-op on base |
| r2 (June) | 15 | 3/3 | 4.53h | |
| r2+recovery (June ablation) | 17 | 3/3 | 3.77h | |
| **r3 no-recovery (E4, run_20260711_153427)** | **18** | **3/3** | 3.62h | **~70% of turns are unexecuted spam** (5.7–6.1k turns vs ~1.8k calls/agent) |
| r3+recovery (June) | 18 | 3/3 | 1.95h | ~5% format-error turns |

Three revisions this forces:

1. **"Harness → stages at the r2→r3 step" is dead.** r3 weights WITHOUT recovery score 18/30 —
   the full r2→r3 stage gain (+3) is weights-driven. Recovery adds stages only at the cusp
   (r2: +2) and nothing at base (defect absent, affordance inert) or r3 (weights already
   carry). Report the six cells; do not decompose additively.
2. **Recovery's real contribution is efficiency, not stages:** at the r3 cell it converts ~70%
   wasted turns into ~5% and roughly halves time-to-wall (3.62h → 1.95h). The defect costs
   tokens and wall-clock, not stages, once the policy is competent.
3. **The June 13 "counterfactual grading paralyzed the agents" read was premature.** The
   stopped run's 57–76% spam at 35 min matches E4's steady-state ~70% — spam ≠ stage failure
   over a full 6h. The grading-side negative result stands (the emission regressed and the
   spam is real), but its *stage* cost was assumed from a 35-minute observation, not measured.

E1 bonus/caveats: recovery being inert makes E1 a de-facto second base replicate — and its
explorer passed the Herbalist wall within 6h, so base-family wall passage is now **1/6 pooled**
(vs r2's 3/3; descriptive only — agent-level Fisher on persona-runs is pseudoreplication, per
the 2026-07-13 stats audit; say "1 of 6 observed persona-runs," not "rarely"/significant).
E1's grinder lane ran under a 3-listener port collision on :9001 (E1's own orphaned server +
the yarn dev server at .env PORT=9001, both fixed 2026-07-11; dev server moved to :9900) — its
grinder score (3) is suspect and E1 deserves a cheap rerun. Whether June arms also ran with the
dev-server collision is an open protocol question to check before the paper freezes.

### The ±seeding falsifier (2026-07-13): natural-only r2 lands at BASE level — seeding was the difference-maker in this observed pair (suggestive, not categorical)

The matched ablation both reviews demanded: `2b-opd-r2-noseed` = round-2 retrained from the
identical merged-r1 init, identical build code (counterfactual grading disabled via
`OPD_BUILD_NO_CF` for r2 parity), natural r1-eval records only, resampled seed-42 to Arm B's
7,024 records / 220 steps. Gate: PASS at **+14.9% rKL — indistinguishable from the seeded arm's
+14.5%**, every verb improved. Eval `run_20260713_084905`, 6h protocol, unseeded, recovery off,
stopped at the 6h boundary (+3 min; port-collision-free harness, e2-standard-4 instance):

| arm (same init, same budget) | Core-3 /30 | wall | per-agent |
|---|---|---|---|
| r2 natural+seeded (June) | 15 | 3/3 | 5/5/5 |
| **r2 natural-only (E3′)** | **12** | **1/3** | g5 (stage 2) / c3 (never accepted) / e4 (stage 1) |

Readings:
1. **Without the seeded slice, round-2 training bought ZERO stages over base** (12/30 = base =
   r1) despite token-level KL movement indistinguishable from the seeded arm — the r1
   style-without-competence outcome repeated at round 2. In this observed pair, the seeded
   slice is the entire r2-over-base gain, and **KL movement predicts nothing about stages**
   (the strongest version yet of the gate-measures-the-wrong-quantity theme).
2. **Not categorical**: 3/3 vs 1/3 wall passage supports a reliability effect, not necessity
   (per the pre-registered branch reading); it sits near the base-family 1/6 background, each
   arm is one training run, and the two arms ran in different infrastructure eras. The
   replicated ±seeding study (≥3 train seeds/arm, contemporaneous, port-clean — planned for
   the restored e8) is what converts this into a causal claim.
3. Abstract wording (this branch; revised 2026-07-13 per Codex pass 3 — "determined" was too
   causal and risked implying teacher-alignment as mechanism): *"Holding initialization, record
   count and optimization budget fixed, the arm including milestone-reset rollouts reached
   15/30 with 3/3 wall passage, whereas the natural-only arm reached 12/30 with 1/3 passage,
   despite nearly identical held-out KL reductions (14.5% vs 14.9%). Aggregate KL movement did
   not predict task progress; training-state coverage was the manipulated difference that
   distinguished the two observed arms."* This wording survives every Arm-C outcome and does
   not claim teacher supervision generalized — E3′ is evidence that the seeded slice matters,
   not yet evidence for WHY (teacher-selective distillation vs reset-conditioned
   self-imitation vs marginal teacher regularization — the Arm-C control discriminates).

### Arm-C, the mechanism control (2026-07-14): uniform self-imitation MATCHES seeded OPD — the r2 competence lift is reset-conditioned self-imitation, not distillation

The decisive control from the Codex pass-3 design: `2b-opd-r2-uniform` = the EXACT r2 corpus
(seeded+natural, 7,024 records), same merged-r1 init, same IS-clipped trainer/steps/masks, with
every nonzero advantage replaced by the pre-registered constant c=0.4306 (= corpus mean |adv|,
matching initial update magnitude; manifest in `dataset/opd_2b/round2_uniform/`). Training
moved the policy comparably to OPD (ratios at clip, clip_frac 0.20–0.26). **Gate: rKL to
teacher UNCHANGED (−1.1%)** vs +14.5/+14.9% for the teacher-graded arms — the manipulation
verifiably erased teacher-direction. Eval `run_20260713_191230`, 6h protocol, port-clean,
stopped at boundary:

| arm (same init, same records-count, same steps) | corpus | advantages | KL gate | Core-3 | wall |
|---|---|---|---|---|---|
| r2 (June) | seeded+natural | teacher | +14.5% | 15 | 3/3 |
| E3′ noseed | natural only | teacher | +14.9% | 12 | 1/3 |
| **Arm-C** | **seeded+natural** | **uniform (no teacher)** | **−1.1%** | **15 (5/5/5)** | **3/3** |

Pre-registered outcome-table row 1 applies verbatim: **teacher grading was unnecessary for the
r2 lift under this recipe.** With seeding held fixed, removing all teacher-direction changes
nothing (15/30, 3/3, identical 5/5/5 profile); with teacher grading held fixed, removing the
seeded slice removes the entire lift (15→12, 3/3→1/3). The active ingredient is the seeded
rollouts themselves — the mechanism is **reset-conditioned self-imitation** (imitating the
student's own frontier-state behavior), not teacher-selective distillation. Do NOT say
"imitated only successes" — the corpus contains failures too, unfiltered.

Corollaries: (a) the KL-gate irrelevance is now maximal — the arm with ZERO movement toward
the teacher scored the same 15/30; (b) retro-explains r1 (self-imitation of natural rollouts
had nothing new to imitate → style only) and coheres with A2′ (weak state-conditional
advantage signal); (c) the paper's mechanism section recenters on self-imitation, with
teacher-grading effects confined to style/prior shaping (r1's documented transfers) and the
copy-prior defect; (d) missing 2×2 cell (natural+uniform) is predictably ≤12 and low-priority.

**Deep-audit addenda (2026-07-15, confirmatory log analysis — four independent analyses):**

*(v) Behavioral equivalence:* at the strategy level r2 and Arm-C are the SAME policy — tool-mix
total-variation distance 0.083, BELOW the within-arm agent-to-agent noise floor (0.10–0.12);
wall-passage mechanism verbatim-identical (same blueberry-grind gate arithmetic templates,
same bush rotation after empty gathers, same premature Herby visits, same stage-2 plateau);
milestone clocks match within per-agent scatter (wall μ 4.54h vs 4.49h). At the EMISSION level
they are cleanly different policies — error-category TV 0.615 vs ≤0.105 among the three
teacher-graded arms (r1/r2/E3′ cluster as a family), validation errors 107 vs 1.8 per 1,000
calls, dead tool-callish emissions 1,082 vs 1, hallucinated verbs 37 vs 2 — every difference
score-irrelevant, and all of it the teacher-graded family's defect fingerprint.
*(vi) Advantage anatomy (825 heldout records, 261k scored tokens):* 94% of |advantage| mass is
generic prose suppression, 5% generic structure endorsement; action-identity tokens (verb
names, argument values) carry ≈zero advantage (0.1–0.2% of mass; the gather verb-name at
seeded wall states gets mean −0.0000). Wall state-selectivity is NULL (gather@seeded
difference-in-differences −0.011, bootstrap CI [−0.036,+0.015]); the seeded-origin regression
coefficient is +0.014 (mild ENDORSEMENT of student behavior at wall states, not correction);
state variables add ≤0.3pp R² over (verb, turn-position, length) surface covariates, and 99%+
of advantage variance is within-record prose phrasing. Seeded and natural grading profiles are
near-identical — shifting the state distribution did not change what the teacher graded.

Original addenda: (i) *Efficiency channel
closed*: Arm-C matches r2 there too — time-to-Herb-stage-2 mean 4.48h vs r2's 4.53h,
throughput 376 vs 367 turns/h. Teacher grading contributed nothing measurable at round 2 on
stages, wall passage, speed, OR throughput. (ii) *Style panel* (full 6-arm comparison): no
style dimension is attributable to teacher-pull — uniform matched the teacher's tempo exactly
(6.0) and landed CLOSER to the teacher than r2 on the signature overshoots (query_quest 11.0%
vs 12.9% → teacher 8.7%; chars/turn 905 vs 1,116 → 864); the r2 "overshoot retreat" was a
retrain-on-r1-rollouts effect, not teacher-pull. (iii) **The one clean grading-specific
signature is the defect**: all three teacher-graded arms carry the ~11% malformed-call dialect
(r1 11.5% / r2 10.7% / E3′ 11.6% of calls) while Arm-C on the SAME corpus sits at **0.2% on
missing-required-field calls (12 vs 591) / 1.1% on n_malformed_emit (76 vs 685; metric labels
corrected per the 2026-07-17 verification sweep)** — a ~9× gap either way; the malformation
follows the gradient direction, not the data, closing the copy-prior causal loop at arm level. (iv) E3′ carries its own eat_food spam pathology (310/382
calls malformed no-ops), which inflates its raw turn counts. Full evidence ledger with
strength ratings, the against-side gaps (r1-uniform control never run; teacher-shaped init and
seeded-rollout generator upstream of every capable arm; n=1 everywhere), and the
gap-closing experiment menu: session records of 2026-07-15.
Caveats: n=1 per arm; E3′/Arm-C are contemporaneous same-infra arms (cleanest pair), June-r2
is the cross-era replicate of Arm-C's cell.

### M3, the natural+uniform cell (2026-07-15): 14/30, wall 2/3 — an honest wrinkle that narrows the seeding contrast

`2b-opd-r2-natuni` (eval run_20260715_090731): the E3′ natural-only corpus with UNIFORM
advantages (c=0.4289), merged-r1 init, 220 steps, 6h. **14/30 (5/5/4), wall 2/3, malformed
emissions 31** (vs E3′'s ~6,469-class spam). The "predicted ≤12" cell came in at 14. Three
consequences, stated honestly:
1. **The 2×2 is complete and the seeding contrast NARROWS**: seeded arms 6/6 wall passage
   (r2 3/3, Arm-C 3/3) vs natural-retrained arms 3/6 (E3′ 1/3, M3 2/3) vs base-family 1/6.
   Retraining on r1 rollouts helps somewhat regardless of the seeded slice (natural frontier
   visitation grew); seeding's residual claim is RELIABILITY (6/6 vs 3/6) and totals (15,15 vs
   12,14), not a categorical wall gate. The n=1 noise band Codex warned about is now visibly
   loud; M4's replication is the decider on seeding's true magnitude.
2. **Uniform BEAT teacher-graded on the identical natural corpus** (M3 14/30, 2/3 vs E3′
   12/30, 1/3) — consistent with the defect tax: E3′ carried the ~11% malformed dialect +
   eat_food spam; M3 is nearly clean (31 emissions). On natural corpora the teacher's gradient
   nets NEGATIVE.
3. Uniform-arm cleanliness replicates a fourth time (31 vs hundreds/thousands in every graded
   arm).

### The mechanical account of round-2 training (2026-07-17): what the update actually reinforced, with corpus counts

Full data-level dissection (heldout 1-in-10 sample for token-level; census raw logs for
behavior; report in session records). The operational meaning of "seeding trained the model":

1. **The trained-token composition**: seeded slice = 30.9% of scored tokens, 100% from
   wall-PASSING lanes (all 3 seeded agents passed in-rollout, early) — and only 22.5% of it
   is wall-era; **77.5% is post-passage stage-2 behavior the natural corpus contains ZERO
   of**. First-order distributional delta of the round = 0.3·(d_seeded − d_natural).
2. **What got its likelihood raised** (census, per-1000-call): ungated lily gathers ~12×
   (67 exemplars, 0 gated, vs ~13 natural), stage-2 target gathers 13×,
   **turn-in-at-≥3-lilies 0→3 — the only closing-move exemplars in the entire corpus, all
   three in the train split**, hostile-zone presence 2.1×. Honest negatives: bush rotation
   and gate-arithmetic reasoning are NOT seeded deltas (already r1-policy traits in both
   slices) — seeding placed existing skills at states where they connect to a viable action.
3. **Why reinforcing failures didn't hurt**: ~40% of seeded gradient mass is failed-outcome
   turns, but the "failures" are majority correct-policy-unlucky-dice (41% realized lily
   yield under the ~1/3-per-tick mechanic — empty-retry IS the optimal strategy); the ±30%
   trust region caps reinforcement of genuinely-bad modes that were already modal; failure
   and success exemplars occupy different states (premature turn-ins at 0–2 lilies, real
   ones at ≥3) so likelihood-raising both teaches the correct conditional.
4. **The teacher's weighting was flat where it mattered**: per-token mean |adv| seeded/natural
   ratio **0.999** (bootstrap CI [0.949, 1.045]); r2 trained with uniformly ~5.6% less total
   magnitude than Arm-C — the 15=15 tie cannot be an exposure artifact; sign structure and
   per-verb weights slice-flat. Confirms weight-pattern inertness at every measurable level.
5. **What natural corpora can/can't buy** (clean-r1 context): natural/base corpora contain
   ZERO passage exemplars and ≤1 successful lily gather, yet defect-free natural arms pass
   the wall 5/9 (M1 1/3, M3 2/3, clean-r1 2/3) — approach behavior + persistence passes a
   stochastic wall sometimes. Seeded arms are 6/6. **Seeding converts stochastic passage
   into dependable passage by making the closing sequence and the post-wall regime
   high-likelihood rather than merely reachable** — and the stage-2 continuation (+ its
   ~564k trained tokens) has no other corpus source.

One-line summary for the paper: *seeding trained the model in precisely the sense that
practice trains a player — it did not teach the moves; it arranged for the moves to be
performed where they mattered, and the recorder was the loss function.*

### Clean-r1, the discriminating arm (2026-07-17): defect ELIMINATED by the context-parity fix; clean grading lands at parity-to-slightly-positive vs uniform

`2b-opd-r1-clean` (eval run_20260716_215512): the r1 recipe with ONE repair — build/score
contexts rendered with the `tools=` block (byte-parity with serving; `OPD_BUILD_TOOLS_JSON`).
Same base init, same natural base-rollout corpus (5,575 records — parity contexts even
recovered the original build's ~1% score-fails), same trainer. Result: **14/30 (4/5/5), wall
2/3, malformed emissions 1 in 7,218 calls.**

| natural-corpus arm | config | Core-3 | wall | malformed |
|---|---|---|---|---|
| r1 (June) | graded, defect-exposing contexts | 12 | 0/3 | 233 |
| M1 | uniform (no teacher) | 13 | 1/3 | 0 |
| **clean-r1** | **graded, parity contexts** | **14** | **2/3** | **1** |

Readouts:
1. **The defect is eliminated at the training level by fixing OUR context bug** (1 vs 233
   emissions, ~base floor). The Seam-1 exposure is now causally confirmed end-to-end:
   same teacher, same corpus, same loss — only the gradient-time context changed.
2. **Defect-free grading ≥ uniform** (14 vs 13, wall 2/3 vs 1/3) on the identical corpus —
   the June-era "grades net negative on natural corpora" was entirely the self-inflicted
   defect; clean grades show a small POSITIVE edge (within n=1 noise). Flagship wording
   (final): *"under our defect-exposing configuration, teacher grades netted ≤0 stages vs
   uniform twins; with the configuration repaired, grading performs at parity to slightly
   positive (+1 stage, +1 wall passage at n=1) — 'grades carry no value' is not supported,
   'grades were never the primary competence channel' is."* The competence hierarchy stands:
   corpus/state-coverage effects (12→15 seeded) ≫ grade-direction effects (±1).
3. Natural-family wall rate is now 5/9 agent-passages across defect-free arms (M1 1/3, M3
   2/3, clean-r1 2/3) vs seeded 6/6 — seeding's reliability edge persists but the natural
   floor keeps rising as defect burden falls; M4 arbitrates the final magnitudes.
4. Protocol note: contexts are ~2.6K tokens longer under parity → slightly fewer turns fit
   per session; comparability with the June arms carries that caveat (M1 shares it not —
   M1 used the old contexts; the clean-r1-vs-M1 comparison is corpus-matched but not
   context-length-matched. The M4 clean-config replication resolves this cleanly.)

### Defect-sensitivity audit of all claims (2026-07-17): none invalidated; the flagship claim must be config-scoped; the tax could have masked positive grade value

Full claim-by-claim audit (all headline counts re-derived from raw logs — every one reproduced
exactly). Verdicts:

**Defect-independent (hold as-is):** size ladder; scaffold lift; r10 regression; r2's
pure-weights wall passage (paid ~11% tax and still passed — conservative); r1 execution wins
(measured on executed calls); KL-gate irrelevance (gates score fixed corpus text, masked spans
excluded; dissociations run both directions); Rick's over-determination; cook/A2′ probes;
speed de-confound; E4's 18/30 (**verified**: observe-delta scoring is server ground truth spam
cannot inflate; all 17 stage transitions traced to executed calls).

**Defect-taxed (config-scope the wording):**
- **"Grades never bought a stage" → "under our defect-exposing configuration, grades netted
  ≤0 stages vs uniform twins."** Per-pair tax accounting: pair-2's 2-stage gap (E3′ 12 vs M3
  14) is MECHANISTICALLY the defect (E3′'s completionist emitted 3,503 malformed turns and
  never accepted Herbalist — stood at Herby 3× with the panel open, accept param absent);
  pair-3 proxies disagree (throughput says clean-r2≈15; **r2+recovery=17 > Arm-C 15 is a real
  defect-neutralized measurement pointing POSITIVE**). Defect-adjusted graded total ∈ [59,63]
  vs uniform 59–60 — **the sign can flip; "grades carry no competence value" was never
  established.** The clean-r1 arm (in flight) is the discriminating experiment.
- Seeding reliability: observed 6/6 vs 3/6; defect-adjusted plausibly 6/6 vs ~4/6 (E3′'s miss
  is defect-attributed) — a reliability edge, not a gate.
- r1 style exhibit correction: query_quest's "2× behavioral overshoot" is ~⅔ defect retry
  loops (18.0% raw → **10.9% on defect-free calls**, teacher 8.7; after a dirty call the next
  verb is query_quest 52% vs 16% after clean). interact_npc overshoot entirely defect (clean
  = teacher exactly). chars/turn robust (1,195 clean vs 1,247). "Style transferred" survives;
  that one exhibit doesn't as written.
- 0.8B lane: reword to "under our defect-exposing configuration" — defect-transfer
  dose-response, not intrinsic-OPD-harm.
- M2 premise STRENGTHENED: June r1-policy seeded rollouts were richer in success density
  (3/3 passages, 344 gathers) not attempt volume (base: 757 gathers, 1/3) — malformed-retry
  inflation refuted as the source of r1's richer rollouts.

**Invalidated: none.** Reword list (7 items) + minimal follow-ups (clean-r1 in flight;
clean-r2 ~$10–30; Arm-C+recovery inertness check; M4 should use the clean config as default
with one legacy-config control seed, else it replicates the tax, not the mechanism) — full
audit in session records 2026-07-17.

### The defect-origin investigation (2026-07-16): web survey + internal forensics + a 2×2 generation probe — how much of the defect was OURS

Three-part investigation (external research agent; internal forensics over all runs, the
recovered pre-squash r1 build code, and heldout audits; a 2×2 generation probe,
`scripts/opd/defect_origin_probe.py`, 800 samples). Full agent reports in session records;
key numbers:

**External:** our wire format IS Qwen3.5's native (Qwen3-Coder XML lineage; fragility widely
documented — Ollama #14493, QwenLM/Qwen3.6 #125, vLLM #22975/#39056). presence_penalty=1.5 is
Qwen's GENERAL-tasks preset; their own tables recommend 0.0 for precise/structured work;
penalties corrupting repeated structural tokens documented since vLLM #1257 (2023).
Python-literal leakage into tool formats is a known cross-family phenomenon. UNREPORTED
anywhere: the kwarg-in-key variant; doc-literal priming of a teacher-forced grader (our
−1.2/−1.5-nat measurements); the generative-vs-teacher-forced contamination-channel
decomposition.

**Forensics (base = 0/6,149 malformed under byte-identical decode+prompt in production;
r1 corpus clean; defect appears in weights at r1-eval session 1):** presence penalty REFUTED
as seed (but families sit exclusively at penalized positions — param ≥2, close-tag triplets —
so it remains a plausible margin-tipper); our ~30 Python doc-literals dictate the surface
forms verbatim (F1 = our `accept_quest_offer=True` string, 6× in system.md; F2 verbs match
doc literals verb-for-verb); the malformed-vs-context correlation runs AGAINST penalty and
FOR copy-prior (prior malformed content in context: β=+0.38→+4.37, z to 17); the r1 "seam
records" bug (4.2% prefix violations misaligning advantages exactly at the tool-call opening)
was real, ours, fixed in r2, and did not inject malformed text; our parser's silent
laundering (F1 dropped errorlessly; F2 persisting verbatim, 67–79% self-priming) built the
entrenchment loop. Plus Seam 1: **the OPD build/score contexts were rendered WITHOUT the
`tools=` block** (r10-era comparability), so every gradient was computed on a context missing
the canonical-format reminder that generation always had.

**The 2×2 probe (20 states × 5 samples/cell, generation WITHOUT tools= — i.e., the
gradient-time context):**

| cell | r1 checkpoint | base-2B |
|---|---|---|
| pp1.5 + our py-docs | 5.0% malformed | **7.0% (all python-call!)** |
| pp0 + py-docs | 3.0% | 9.0% |
| pp1.5 + canonical docs | 1.0% | 0.0% |
| pp0 + canonical docs | **0.0%** | 1.0% |

Two decisive facts: (1) in the gradient-time context, **even BASE leaks Python-call forms at
7–9% when our Python docs are present** (0–1% with canonical docs; presence penalty ≈ no
effect) — reconciling with base's 0% in production logs, where the tools-block reminder was
present: **the training/scoring context we built (no tools block + Python docs) is precisely
the environment where the defect flourishes, and it differs from the serving context**;
(2) on the trained checkpoint, canonicalizing the docs collapses expression 5%→1%→0 — the
doc literals dominate; kwarg-in-key appears only in r1 cells (training-acquired, never in
base under any condition).

**Final causal account:** the VULNERABILITY is the method's (dense teacher-forced reverse-KL
distills the grader's copy-prior — a structural hazard, grader-general); the EXPOSURE was
ours (Python doc literals as the prime + tools-block-free gradient contexts where even base
misbehaves at 7–9%); the SURFACE FORMS were ours verbatim; the AMPLIFICATION was ours
(silent parser laundering, abstaining masking, verbatim retraining); the penalty preset is a
minor margin-tipper at most. Answer to "entirely caused by us": no single bug created it,
but remove our three artifacts (doc literals, tools-block asymmetry, silent laundering) and
this defect, in this form, very likely never happens. Fixes: docify the SERVED prompt;
render build/score contexts with tools= (or drop at serving for parity); keep recovery +
loud errors permanent; pp=0 for structured serving per Qwen's own tables; permanent prefix
tripwire.

### M6, the r3-uniform arm (2026-07-16): 17/30, wall 3/3 — the 15→18 step was also corpus, not grades; and the defect's second channel appears

`2b-opd-r3-uniform` (eval run_20260715_211431): the exact r3 corpus (r2-eval rollouts +
full-ladder seeded, 8,856 records) with uniform advantages, merged-r2 init, 6h clean run.
**17/30 (5/6/6), wall 3/3, two agents completed Herbalist 3/3** — vs r3+recovery 18 and
r3-no-recovery 18. The r2→r3 stage gain reproduces within n=1 noise without counterfactual
grading; the round-3 corpus (visitation), not the grading redesign, carried it.

**Defect refinement:** M6 logged 2,695 malformed emissions — the first heavily-affected
uniform arm. Its corpus text is defect-RICH (r2-eval rollouts, 685 emissions + mutated forms)
and its init (merged-r2) already carries the dialect; uniform imitation of contaminated data
propagates the defect. So the defect has TWO channels: (1) the teacher's wrong-signed
gradient CREATES it from clean corpora (r1: 0-malformed corpus → 233-emission policy;
cross-grader-confirmed copy-prior), and (2) plain imitation PRESERVES it once the corpus is
contaminated (M6). The four clean uniform arms all had clean-ish corpora; the pattern is
corpus-and-gradient, not gradient-only.

### THE COMPLETE MATCHED-PAIR FAMILY (July 16) — the teacher's grades never bought a stage

| corpus (init) | teacher-graded arm | uniform twin |
|---|---|---|
| natural base rollouts (base init) | r1: **12**, wall 0/3, mal 233 | M1: **13**, mal 0 |
| natural r1-eval (merged-r1) | E3′: **12**, wall 1/3, mal ~6.5k-class | M3: **14**, wall 2/3, mal 31 |
| natural+seeded r1-policy (merged-r1) | r2: **15**, wall 3/3, mal 685 | Arm-C: **15**, wall 3/3, mal 76 |
| r2-eval + full-ladder seeded (merged-r2) | r3-norec: **18**, wall 3/3, ~70% spam | M6: **17**, wall 3/3, mal 2,695 |
| base seeded rollouts (base init) | — | M2: **12**, wall 0/3, mal 0 |

Across four matched pairs: graded total 57, uniform total 59. **No teacher-graded arm beats
its uniform twin by more than n=1 noise anywhere in the program; on natural corpora the
grades net negative (defect tax).** Combined with M1 (discipline IS teacher-driven) and M2
(the discipline is what made seeded rollouts imitable), the final claim: **dense teacher
grading contributed execution discipline and defects to the weights, and its real service to
competence was indirect — building the executor whose frontier rollouts became the
curriculum. The grades never carried the game.**

### M2, the teacher-free end-to-end arm (2026-07-16): 12/30, wall 0/3 — the shortcut fails; the teacher matters ONE ROUND UPSTREAM

`2b-teacherfree` (eval run_20260715_151045): base init + BASE-generated seeded rollouts
(collection run_20260715_002620, 480 sessions, wall passages 1/3) + uniform advantages —
zero teacher signal anywhere in the lineage. Result: **12/30 (4/4/4), wall 0/3, malformed 0**
— exactly base's profile; the seeded self-imitation recipe did NOT transfer the wall when the
imitated rollouts came from base itself.

**The refined mechanism (the program's final form, each step controlled):**
1. r1 teacher-grading taught execution DISCIPLINE (M1: doesn't happen under uniform), zero
   stages, plus the defect.
2. The DISCIPLINED policy's seeded rollouts are rich (3/3 wall passages, June collection);
   BASE's seeded rollouts are poor (1/3 passages, July collection). Self-imitation of the
   rich corpus transfers the wall (Arm-C 15/30, 3/3); of the poor corpus, nothing (M2 12/30,
   0/3).
3. The teacher's per-token grades at round 2 add nothing (Arm-C) and net negative on natural
   corpora (M3 14 > E3′ 12 — the defect tax).

**Claim (final wording): the teacher's contribution is real but INDIRECT and one round
removed — dense teacher grading built the executor whose frontier experiences were worth
self-imitating; the grades themselves never carried the competence. Distillation acted as a
curriculum-builder, not a knowledge channel.** This rescues the two-stage OPD program from
"you never needed the teacher" (you did — for round 1's discipline) while preserving every
negative result about the grades-as-competence-channel.

Confounds, stated: M2's corpus is smaller (3,872 vs Arm-C's 7,024 records / ~121 vs 220
steps) and — the semantic variable — contains 1 wall passage vs the June seeded corpus's 3;
generating-policy quality and passage count are entangled with data volume here. A
passage-matched variant would disambiguate; the qualitative conclusion (base's seeded
rollouts are poorer imitation material, and that poverty traces to execution discipline the
teacher instilled) is supported by the collection-time passage rates independently of volume.

### M1, the r1-uniform control (2026-07-15): r1's execution discipline WAS teacher-driven — the attribution completes in three clean lines

`2b-opd-r1-uniform` (eval run_20260715_030342): uniform advantages (pre-registered c=0.4873)
on the exact round-1 corpus (5,564 base-rollout records), base init (== generator, as r1),
174 steps, 6h protocol. Result: **a near-no-op, as self-imitation of one's own on-policy
rollouts should be** — turns/session 8.7 (base 9.1, r1's teacher-exact 6.1), eat_food errors
77.8% (base 74.0, r1 48.3), MOB_NOT_FOUND 128 (base 113, r1 39), dangling-dialect 1% (base 3,
r1 100), navigate errors 3.6% (partial: base 5.3, r1 1.2), stages 13/30 (≈base),
**malformed emissions 0** (r1: 233 — every uniform arm is defect-free; every graded arm isn't).

**This falsifies the STRONG claim ("teacher grading contributed nothing measurable anywhere")
and completes the attribution with a control for each channel:**
- **Execution style/discipline ← the teacher's gradient** (r1's tempo/nav/re-grounding/dialect
  shifts do NOT reproduce under uniform; ledger gap A1 CLOSED, pro-teacher).
- **Task competence ← seeded self-imitation** (Arm-C = r2 with zero teacher signal; E3′ shows
  no competence without seeding even WITH the teacher).
- **The malformed-call defect ← the teacher's gradient** (0 in all three uniform arms trained
  so far vs 233/591/599-class counts in every graded arm).

The paper's three-transfers framing now has a controlled attribution per transfer — and the
decisive irony stands: the teacher taught the student how to move, look, and speak; it could
not teach it what to do; and its signature contribution to the wire format was its own defect.
Execution discipline was demonstrably NOT the competence carrier (r1 had all of it and +0
stages; Arm-C had none of it and +3).

### The five-arm decision-probe study (baseline_20260701; validated + adopted 2026-07-15)

`dataset/probes/baseline_20260701/` (branch `feat/social-dashboard-0_8b`; framework
`scripts/probes/{specs,runner,checks,report}.py`): 701 trials — five arms × seven seeded
single-decision probes × 20 trials, identical seed-digest-verified stimuli, standard 17-tool
surface, compact observes + programmatic note, ≤4-turn budget, intent-level (`pass_if` tool
rule) and world-effect (`pass_db`) scoring. **Validation (2026-07-15): PASS** — 699/701 scored
(2 seed_invalid excluded, 1 clean retry), one seed digest per probe across all arms, correct
endpoint per arm, uniform config; spot-checked scoring correct including both anomaly classes
(below). Caveats: 29 context-overflow terminals spread across arms (4B highest at 9); P-A
`pass_db` under-counts vs intent (stochastic gather yield); 2b×P-EQUIP has 19 trials; runner
provenance = the branch wip commit.

Pass rates (intent), arms × probes:

| probe | 0.8b | 0.8b-opd-r1 | 2b | 2b-opd-r3 | 4b |
|---|---|---|---|---|---|
| P-A gather-oak (quest active) | 11/20 | 6/20 | 14/20 | 17/20 | 18/20 |
| P-B two-step log turn-in | 5/20 | 4/20 | 10/20 | 12/20 | 19/20 |
| P-C gated-lily→pivot (cliff) | 6/20 | 5/20 | 7/20 | **1/20** | **2/20** |
| P-C2 lily at exactly L5 | 6/20 | 11/20 | 12/20 | 16/20 | 17/20 |
| P-D cook w/ rawshrimp | 1/20 | 0/20 | 0/20 | 1/20 | **7/20** |
| P-EQUIP pole before gather | 0/20 | 0/20 | 1/19 | 0/20 | 3/20 (**11/20** incl. reactive) |
| P-REC dungeon escape | 5/20 | 8/20 | 9/20 | 13/20 | 19/20 |

Findings the table adds to the program:
1. **Per-decision capability map, 4B vs 0.8B**: 4B ≫ 0.8B on 6/7 probes (recovery 19 vs 5,
   turn-in 19 vs 5) — the matched-state decision evidence reviewers asked for.
2. **P-EQUIP reactive-recovery split** (from the 8 intent=F/db=T 4B trials): the 4B equips the
   pole *reactively after a failed gather* 8/20 times on top of 3/20 proactive; students 0–1.
   Refines the Rick's link-1 story: the teacher recovers reactively, students not at all.
3. **P-D cook**: the 4B chooses cook 7/20 at the seeded cook state as a GENERATOR — the
   full-run "never cooks" is substantially reachability, not pure incompetence; students 0–1/20.
4. **P-C, the means-ends cliff — CORRECTED by forensics (2026-07-15, all 60 trials read):**
   the 7/20→1/20 "collapse" is **mostly a scoring artifact + partly a real, subtler seeding
   cost**. Artifact: the pass rule credits only an in-place `gather(blueberry|stump)` emission,
   but the seed location has no levelable node in `nearby` — so it rewards base's blind
   walkthrough-recitation/misnaming emissions (~5 of base's 7 "passes" are lexical accidents,
   e.g. calling the adjacent lily a blueberry bush) and punishes grounded TRAVEL pivots; r3
   verbalizes the correct bridge plan in ≥9/20 trials and navigates toward Mudwich blueberries
   (unscoreable in 6 turns); the un-OPD'd 4B fails the same way (2/20 despite executing the
   full bridge chain in ~7 trials, twice hallucinating Mudwich's coordinates); `pass_db` =
   0/20 for ALL arms. In the wild, r3 pivots BETTER than base at organic gate blocks (46% vs
   33% blueberry pivots within 3 actions; 0/13 vs 1/33 re-hammers). **Real cost (paper-worthy,
   the honest price of milestone seeding):** gate-PASSED-state training taught r3 a "gate
   already satisfied" prior at the lily decision point — 0/20 blueberry-first openings (base
   5/20), repeated unverified "Foraging L4 should be sufficient" claims — because the training
   distribution never contained pre-gate states there. Also visible: r3's malformed
   positional-arg syntax cost it 5/20 trials (17 malformed calls; base and 4B: 0). P-C needs a
   respec (travel-aware credit or a levelable node in `nearby`); under travel-aware credit
   r3 ≈ 5–6/20, 4B ≈ 8–9/20, base's genuine bridging ≈ 2/20 — a modest decline, not a cliff,
   plus the P-C2 rise (12→16) is the same axis: walkthrough-recitation prior traded for
   state-grounded target-gathering.
5. **0.8B OPD damage at decision level**: P-A 11→6, P-B 5→4 (worse); P-C2 6→11, P-REC 5→8
   (better) — and the failing trials are the malformed dialect in action (verified specimen:
   `<function=gather><parameter=resource_name>\nOak\n</` — corrupted close tag, argument lost,
   verb salvaged with empty input; the model KNOWS the action, the trained-in syntax drops it).

### The 0.8B lane (runs July 1–5, audited July 14): natural-rollout OPD DAMAGES the smallest student — the defect transfer dominates at low capacity

Three protocol-consistent 6h runs already existed on this VM (launched from the unmerged
`feat/social-dashboard-0_8b` branch, but verified clean: standard 17-tool surface, no
send_chat in tools or prompt text, zero `nearby.players` sightings = isolated worlds; corpus
built by the same round-agnostic pipeline, 4B teacher, natural rollouts, no seeding):

| arm | run | Core-3 /30 | wall | note |
|---|---|---|---|---|
| base-0.8B | run_20260701_125155 | **13** | 1/3 | on par with base-2B (12) — the size ladder FLATTENS at the bottom |
| 0.8b-opd-r1 (no recovery) | run_20260703_095207 | **3** | 0/3 | catastrophic: −10 vs base; Foresting itself breaks (1/2/0) |
| 0.8b-opd-r1 + recovery | run_20260705_093921 | **10** | 0/3 | recovery repairs most but NOT all — still below base |

Readings (they slot into the campaign narrative almost perfectly):
1. **Natural-rollout OPD does not improve 0.8B — it damages it.** Consistent with E3′ (natural
   OPD at 2B = +0 stages) plus the copy-prior defect transfer, whose COST is
   capacity-dependent: at 2B the weights push through 70% spam at full score (E4, 18/30); at
   0.8B the induced defect collapses the run (3/30) and even the recovery affordance recovers
   only 7 of the 10 lost stages. Purest instance of "OPD transfers execution defects" —
   and at small capacity the defect outweighs everything else.
2. **base-0.8B ≈ base-2B (13 vs 12)** extends the size-ladder saturation downward: on this
   benchmark, capacity shows only between 2B→4B (12→17).
3. Caveats: n=1 per arm; runs predate the 2026-07-11 port-collision fix, so their grinder
   lanes (agent_0) carry the same 3-listener ambiguity caveat as E1 — the base run's wall
   pass came from its grinder; e2-standard-4-era supervisor issues unaudited for these runs;
   branch working-tree provenance (exact code SHA of the build/train unconfirmed — the
   heldout + trial artifacts live on the branch under `dataset/opd_0_8b/`).
4. **The informative next 0.8B experiment is not another natural round** — it is the
   seeded ± teacher-direction pair (seeded corpus, uniform vs 4B-graded, recovery ON
   everywhere), which tests (a) whether reset-conditioned self-imitation transfers down to
   0.8B and (b) whether teacher-direction starts mattering when the student's own rollouts
   are weak — the capacity-conditional mechanism question Arm-C poses.

### Cook-state grading probe (2026-07-11): the "can't grade" claim needs rewording — grades are ~state-insensitive everywhere

Direct measurement of the Rick's-cook claim (`scripts/opd/cook_grade_probe.py`, 4B `/score`):
at 30 real cook-decision states (rawshrimp in inventory, from the r3 seeded collections) and 30
teacher-competent lily control states (Foraging≥5, Blue Lily Bush visible, r2 eval), score four
candidate continuations in an identical canonical wrapper and compare mean logprob/token.

| candidate | @cook states | @lily states |
|---|---|---|
| observe | **−0.126** | **−0.158** |
| craft_item cook | −0.417 | −0.451 |
| gather (oak / lily) | −0.861 | −0.949 |
| attack rat | −0.909 | −1.008 |

Median margin (correct − best distractor): cook −0.285 (0/30 positive); **control −0.879 (1/30
positive) — the control FAILED the same way**. Two readings, both load-bearing:

1. **The probe does not support "the teacher grades cook wrongly at cook states."** The teacher
   ranks the correct cook call *above* the other task verbs at cook states; what beats it is
   `observe` — everywhere. The failure to prefer the correct action is **global, not
   cook-specific** (the teacher-competent lily control shows the identical pattern, with
   gather-lily graded *below* craft-cook even at lily states). Reword the paper claim: Rick's
   cook null is over-determined (pole gate first; no successful cook rollouts in the seeded
   collections for reverse-KL to amplify) — not demonstrably "flat grades at cook states."
2. **Each candidate's grade is nearly state-INVARIANT** (cook −0.417 vs −0.451, observe −0.126
   vs −0.158 across conditions; per-state spread ≤~0.15 nats): at single-action granularity the
   dense per-token grade is dominated by candidate-intrinsic sequence probability (action-
   frequency prior + token surface), not by state-appropriateness. This extends the round-1
   "advantages reward structure, not reasoning" reading and supports the mechanism story that
   r2's competence transfer rode on **visitation change + amplification of the student's own
   seeded successes**, not on state-conditional action knowledge in the teacher's grades.

Caveats: candidates were bare tool calls (no reasoning prefix — state-conditioning may partly
live in grading reasoning tokens); mean-logprob comparisons across heterogeneous candidates
carry length/surface effects (the within-candidate cross-state comparison in (2) is robust to
this); single-action granularity only.

**Advantage-level follow-up (2026-07-13, A2′; same probe, r1 student scored alongside the 4B
so the metric is the actual OPD signal, teacher − student):** the state-invariance largely
DISSOLVES at the advantage level — candidate-intrinsic surface biases cancel between models.
observe's likelihood dominance cancels (advantage only +0.13–0.19 everywhere: the student
shares the prior). At teacher-competent lily states the correct gather-lily carries +0.411
(margin near parity, 5/30 positive); at cook states the correct cook call gets +0.190 while
WRONG task verbs get +0.82 (gather-oak) and +0.70 (attack-rat) — the gradient at cook states
actively favors wrong verbs, 0/30 positive margin. Control-minus-cook margin gap **+0.576
nats**. Consequences: (a) the raw-likelihood "grades are ~state-invariant" reading measured
the wrong quantity (as the Codex pass-3 review predicted) — retain it only as a fact about
likelihoods; (b) "the teacher can't grade what it can't do" is **partially rehabilitated in
its correct (advantage-level) formulation** — the training signal is materially
better-aligned at teacher-competent states; (c) it is still not clean state-conditional
tutoring anywhere (attack-rat tops lily states too), so the transfer-mechanism question
remains open and the Arm-C uniform-advantage control remains the decider. Probe caveats
carry over; n=30/30 states.

### Live seeded Rick's-Roll probe (2026-07-17): "can't cook" is refuted; the Rick's null is 4 seeding/harness bugs, not cooking incompetence

Direct behavioral test of the Rick's claim, prompted by the observation that the round-3
Rick's seeds were never verified to place the agent where intended. Single base-2B agents
(`kaetram-qwen-2b`, non-SFT) and one 4B agent (`kaetram-qwen-4b`) were seeded at the fixed
milestones (`seed_milestones.py`, branch `feat/ricks-roll-seeds`) and driven live through
`play_qwen`; MongoDB quest/inventory read directly for ground truth.

**Cooking capability is present — "can't cook" is a generative-non-occurrence artifact.** Seeded
on a cooking station with 5 rawshrimp (`r4_cook`), base-2B emitted the exactly-correct
`craft_item(cooking, cookedshrimp, 5)` and **attempted it 21 times in one run** (16 "Could not
reach cooking station"). It does not lack the cook action; it is blocked upstream of the stove.
Consistent with P-D (4B cooks 7/20 at the same seeded state) and the grading probe above.

**The Rick's-Roll null is over-determined by four concrete seeding/harness bugs, all found live,
none of them cooking capability:**
1. **`r4_cook` station was inside the respawn dungeon.** The (323,892) cooking station is 5
   tiles from `SPAWN_POINT` (328,892); base-2B read the seed tile as "respawn dungeon" and
   warped to Mudwich before cooking. Fixed → overworld station (411,866).
2. **Rick (1088,833) and Lena (455,924) are in door-gated regions that cannot be position-seeded.**
   A raw DB position write into those regions fails `intro()`→`setPosition`→`verifyCollision`,
   which calls `sendToSpawn()` (player.ts:705) at login (oldX/oldY = -1) → the agent materializes
   at `SPAWN_POINT` (328,892), not at the turn-in NPC. Confirmed twice for `r5_turnin`
   (`run_20260717_145155`, `_152528`): seeded (1088,832), observed at (328,892), then 28 failed
   navigates toward the unreachable Rick. This alone made the original round-3 `r5_turnin` seed
   inert. Reaching Rick/Lena requires *crossing* the door (379,388 / 260,229), not a position write.
3. **Rick's seaside is an L76-118 aggro wall** (13 darkwolf L118, 7 darkscorpion L101, 12
   darkskeleton L76, blackwizard aggro-range 6): a realistic-HP seed there would die on spawn
   anyway. Turn-in seeds now carry the conftest-R5 survival buffer (3039 HP / 15M Health-XP).
4. **Model execution, not seeds:** base-2B warps to Mudwich from unrecognized seed tiles, and
   **neither base-2B nor the 4B ever steps on a door** — seeded 1 tile from the stage-2 quest
   door (`r6_door`, (260,230), position verified to STICK), across 4 runs (2B grinder /
   2B completionist / 4B) **zero door-targeted navigates** occurred; agents instead grind mobs,
   eat the quest `seaweedroll`, or wander to the fishing spot (stage-1 behavior at stage 2).

**No live Rick's *stage* completion was obtained** — but the binding constraints are (2) gated-region
seeding and (4) door/nav execution, which **hold for the 4B teacher too**, not cooking. Overworld
seeds DO stick (r6_door (260,230) verified), so the seed mechanism is sound; the door-gated turn-in
NPCs need the pre-door-cross approach and a door-crossing/anti-warp harness affordance (the same
harness-affordance lever round 3 used for the format defect). This closes the loop the round-3
narrative left open: **"0 cook transfer" is seeding + harness/model-execution, exactly as the
2026-07-11 grading probe and P-D already implied — the "cook-incompetent teacher" framing below
(lines ~12/51/601/652) is superseded.** Seed fixes landed in `seed_milestones.py`; caveat: n=1
per cell, base-2B is a weak executor, and the pre-door Rick/Lena seeds are not yet built.

### Costs (billing-verified, `modal billing report`)

Ground-truth Modal spend for the three rounds, **including every failure and debugging detour**
(the same billing source used for r10's $360):

| Round | Train | 4B teacher grade | Student serve + gate + eval | **Round total** |
|---|---|---|---|---|
| **r1** | **$71** (3× retrain) | $7.80 | $18.30 (+ $14.33 base-2B rollout/build) | **$111** |
| **r2** | $3.87 (+ $1.67 kernel probe) | $4.33 | $22.74 | **$33** |
| **r3** | $4.57 | $5.13 | $6.51 | **$16** |
| | | | **r1–r3 total** | **$160** |

The split is the opposite of intuitive: **r1 was the most expensive round, not the cheapest.**
Its $71 training line is the whole story — the fast GDN kernels were broken (`fla` 0.5.0 refuses
Triton ≥ 3.4 on Hopper; `tilelang` SIGABRTs), so each run was the ~8h pure-torch fallback
(~168 s/step) **and it ran three times** (Modal preempted attempt 1 at step 84; attempt 2 had no
resume logic; attempt 3 finally checkpointed). The $1.67 GDN-kernel probe fixed this (8.9×), so
r2/r3 training dropped to ~$4–5 (50 min). **Clean marginal cost of a fast-kernel round is
~$5–10 to build+train; the 6h eval serving on L4 (~$6–23/round) is then the dominant recurring
line.** No paid-API cost enters the loop — both the 2B student rollouts and the 4B teacher are
self-hosted Qwen on Modal.

r1–r3 is ~44% of one r10 SFT cycle ($360). For context, the **pivot/scoping** that *selected*
the 4B→2B pair but is not part of the rounds cost a further ~$138 (size-ladder 27B sanity $18,
4B candidate evals $19, parked 9B OPD lane $18, r10-era 9B serving $30, a separate cold-start 2B
SFT lane $53) — so total Modal spend across the June 6–14 OPD work window was ~$298.

### Round 4 (evidenced, not yet run)

1. **Privileged-context grading (Plan B)** for Rick's — Claude's successful cook/door trajectory
   in the *teacher's* grading context (OPSD/π-Distill, published), since the same-family teacher
   can't grade what it can't do.
2. **Two cheap harness fixes the logs demand**: auto-re-equip/surface the fishing pole (link-1
   gate), and a session note that carries route-progress for un-accepted target quests.
3. **Promote tool-recovery to a permanent affordance** (it is that load-bearing), tracking the
   pure-weights defect rate separately.

*Counter provenance: 405 is the authoritative recovery count (`[format]` result markers);
`n_malformed_emit` (335) is a lower bound — the harness rewrites assistant content to canonical
before logging, so it counts only malformed text that survived recovery. The analyzer
(`scripts/log_analysis/`) decodes the `[format]` result prefix, detects plain-string validation
errors (`is_error`), and tracks the malformed-spam class via `n_malformed_emit`.*
