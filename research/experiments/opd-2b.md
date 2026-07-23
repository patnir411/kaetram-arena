# OPD rounds 1–3: 4B teacher → base-2B student (the small-model pivot)

**Status:** rounds 1–3 complete + evaluated, June 7–14 2026 (branch `feat/r11-opd-tinker`).
**Historical observation:** project notes report a monotone Core-3 sequence of base 12 → r1 12 →
r2 15 → **r3 18**, with one complete run per cell. Round 2 ran without the recovery affordance;
round 3 combined new weights with recovery. The June raw session trees, immutable manifests,
checkpoint/configuration digests, and gameplay seeds are not preserved in this repository, so the
sequence cannot establish a causal weights effect, exact harness parity, or a +3/+2/+1 effect
decomposition. Within the reported runs, every round-3 agent completed Foresting and Herbalist's
Desperation where the reported base agents completed only Foresting. Round 3 broke the stage-2 wall and fixed the format defect via the harness recovery
affordance — but Rick's Roll stayed 0/4 (over-determined: a displaced fishing tool gates link 1
before cooking is reached, with the cook-incompetent teacher a wall behind it). The reported
round-2-weights-plus-recovery result (17/30) was also a single unmatched historical run; it is
suggestive, not a controlled effect estimate.
We label 18/30 a weights-plus-interface result and don't lean
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

- Training (174 steps = ceil(5,564/32), lr 5e-5) ran on the **pure-torch Gated-DeltaNet
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

### Execution wins (binomial-significant on 5,526 vs 6,183 calls)

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

**The historical probe motivates an in-context copy-prior hypothesis.** It reports positive
teacher-over-student advantage for one malformed continuation, but does not score canonical and
malformed candidates in the same context. The paired artifact needed to establish wrong-signed
teacher preference is absent. A generative check (the 4B emits the correct form in its own runs)
still does not by itself certify the teacher as a per-token grader. Impact is bounded: stage *turn-ins* advance via
plain `interact_npc` with items in inventory (server-side consume) — the parameter only gates
initial accepts. The defect's arc across rounds: round 2 masks advantages on the malformed
spans (containment, no cure), round 3 grades them under a canonicalized teacher context
(regresses the emission in the reported run), and a harness recovery affordance executes the
dropped call at generation time (see round 3).

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
  objectives can *increase* probability on worse actions. The present historical probe does not
  establish a wrong-direction candidate preference, so it cannot choose abstention over weighting.
- **Our defect's nearest named relative** is KAT's "KL agreement trap"
  ([2606.09471](https://arxiv.org/abs/2606.09471), June 8): the teacher locally agrees
  with degraded student prefixes, yielding no corrective signal. Our case is the sharper
  candidate only if a paired study shows that the teacher prefers the malformed continuation over
  the canonical one. The present notes report positive teacher-over-student advantage for the
  malformed candidate, not preference reversal. Related framings include CCOPD, SOD, KAT, the OPD survey's "flawed prefix
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
at runtime (reported 3.3% recovery rate; source logs are not packaged).

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

### Descriptive cross-run comparison; no causal decomposition

The historical reports give base 12/30, round 2 at 15/30 without recovery, round-2 weights with
recovery at 17/30, and round-3 weights with recovery at 18/30. These are one unmatched run per
cell. Their raw bundles, exact checkpoint/render parity, gameplay seeds, and immutable
configuration manifests are absent, so subtracting the scores does **not** identify weights,
recovery, or interaction effects. In the reported recovery-off round-2 run, the Herbalist wall
fell 3/3 across clustered prompt variants; that is the strongest descriptive observation, not
three independent replications or a pure-weights estimate.

The project also reports **round-2 weights with the recovery affordance on**
(`run_20260613_214956`) at **17/30** (grinder 6, completionist 6, explorer 5). The notes also
report that r3 reached Herbalist stage 2 in **~2.0h vs r2's ~4.5h** and produced
**~40% more turns per hour (511 vs 366)**. Without the underlying bundles and matched replicated
runs, neither the score differences nor the throughput difference can be attributed to a single
lever.

We therefore label 18/30 a **weights-plus-interface** result. The recovery-off round-2 passage is
the least-confounded historical comparison, but it is not a pure-weights effect estimate.

### Rick's Roll: 0/4 — a three-link execution failure, not missing intent

The agents *understood and attempted* the full chain (52 navigate-to-Rick, 5 interact_npc("Rick"),
12 door-(379,388) attempts; all quote the walkthrough verbatim). The chain dies at **link 1
(fishing)**: the **fishing pole is never equipped** (Foresting's logs displaced it from slot 2;
0 pole-equipped observes across all agents), so every shrimp gather returns `items_gained:
"none"` and zero shrimp enter inventory — cook/door/Rick never become reachable. Compounding:
**craft_item = 0 across all 200+ post-Herbalist sessions** (zero cook transfer — matching the
cook-incompetent 4B exactly, the pre-registered KAT-agreement-trap prediction), and the
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

### Historical recovery behavior (source logs not packaged)

Historical notes report 405 rewritten sessions with one `[format]` marker and no later marker;
90% reportedly fire at turn 2. The source logs and raw pre-rewrite emissions are absent, so this
cannot establish model self-correction, zero relapse, or the true malformation denominator. The
reported 335 malformed emissions and 405 markers use different units.

### What round 3 established (the clean separation)

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

**Framing:** the r3 18/30 is a **weights + harness recovery** arm — env-changed, not
pure-weights like base/r1/r2 — so we label it as such. The recovery-off round-2 report is the
least-confounded historical comparison, but exact parity is unavailable. For the r3 arm specifically, the binding
constraint on the last stages was the format defect — a model–environment interface failure
three attempted weights-side interventions had not reached in the reported runs — which is why
18/30 is honestly weights-plus-interface.

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

### Proposed Round 4 (not yet run)

1. **Privileged-context grading (Plan B)** for Rick's — Claude's successful cook/door trajectory
   in the *teacher's* grading context (OPSD/π-Distill, published), since the same-family teacher
   can't grade what it can't do.
2. **Two cheap harness fixes the logs demand**: auto-re-equip/surface the fishing pole (link-1
   gate), and a session note that carries route-progress for un-accepted target quests.
3. **Promote tool-recovery to a permanent affordance** (it is that load-bearing), tracking the
   pure-weights defect rate separately.

*Counter provenance: the historical notes report 405 recovery markers (`[format]` results);
`n_malformed_emit` (335) is a lower bound — the harness rewrites assistant content to canonical
before logging, so it counts only malformed text that survived recovery. The analyzer
(`scripts/log_analysis/`) decodes the `[format]` result prefix, detects plain-string validation
errors (`is_error`), and tracks the malformed-spam class via `n_malformed_emit`.*
