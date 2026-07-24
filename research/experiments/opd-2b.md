# OPD rounds 1–3: 4B teacher → base-2B student (the small-model pivot)

**Status:** rounds 1–3 complete + evaluated, June 7–14 2026 (branch `feat/r11-opd-tinker`).
**Historical observation:** project notes report a monotone Core-3 sequence of base 12 → r1 12 →
r2 15 → **r3 18**, with one complete run per cell. Round 2 ran without the recovery affordance;
round 3 combined new weights with recovery. The June raw session trees were later recovered
read-only and content-bound (`research/audits/historical-run-digests.json`), but immutable
manifests, checkpoint/configuration digests, and gameplay seeds are not preserved, so the
sequence cannot establish a causal weights effect, exact harness parity, or a +3/+2/+1 effect
decomposition. Within the reported runs, every round-3 agent completed Foresting and Herbalist's
Desperation where the reported base agents completed only Foresting. Round 3 was reported to
break the stage-2 wall while the harness recovery affordance was enabled; the retained evidence
does not establish that recovery causally fixed the format defect. Rick's Roll stayed 0/4
(over-determined: a displaced fishing tool gates link 1 before cooking is reached; later live
probes identify additional seeding, door, aggro, and navigation blockers but do not isolate
their effects). The reported
round-2-weights-plus-recovery result (17/30) was also a single unmatched historical run; it is
suggestive, not a controlled effect estimate.
We label 18/30 a weights-plus-interface result and don't lean
on nominally edging the 4B teacher (18 vs 17, with a recovery affordance the teacher lacked).
Supersedes
the 9B
instantiation of the OPD plan in [r11-direction.md](r11-direction.md) ("Where r11 sits");
companion to [r11-probing.md](r11-probing.md) (the 9B feasibility probes) and
[r11-27b-sanity-check.md](r11-27b-sanity-check.md) (the upward capacity check). Operational
state machine for the running chain: `dataset/opd_2b/ROUND1_RUNBOOK.md`.
The preregistered follow-up weights × recovery ablation and held-out
no-walkthrough protocol is documented in
[`opd-2b-factorial.md`](opd-2b-factorial.md).

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

**The historical probe motivates an in-context copy-prior hypothesis.** It reports positive
teacher-over-student advantage for one malformed continuation, but does not score canonical and
malformed candidates in the same context. The paired artifact needed to establish wrong-signed
teacher preference is absent. A generative check (the 4B emits the correct form in its own runs)
still does not by itself certify the teacher as a per-token grader. Later project notes report
a broader cross-grader canonicalization probe (2026-07-15): the **2B-as-grader shows the same
mechanism** — doc-literal canonicalization suppresses its endorsement of the malformed
continuations by median −1.455 nats (57% of states ≤ −0.2), vs the 4B's −1.21/86% — reported
as evidence the copy-prior is at least family-general (a structural hazard of dense
teacher-forced grading primed by Python-style tool-doc literals, not a 4B idiosyncrasy; 9B
grader untested — its r10-era endpoint failed all /score calls). The probe's source examples
and paired outputs are not packaged here; it remains supporting motivation rather than a
general mechanism result.
Impact is bounded: stage *turn-ins* advance via
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
   correction (the predicted limit); round 3 adds a harness-side recovery lever, whose separate
   effect remains unidentified in the historical record.
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

Round 3 targeted the two open wounds from round 2—the mutated malformed-call attractor and
the stalled Herbalist stage 2—and aimed at 10/10. It produced the program's best reported result
(**18/30, 6/10 every agent**), but because weights and recovery changed together it cannot
separate what training and the harness each contributed.

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

### The two-attempt story: counterfactual grading regressed; recovery-enabled rerun

The first 6h eval (`run_20260613_105318`) was **stopped at 35 min**: counterfactual grading did
NOT suppress the malformed *emission* — it regressed it. 2/3 agents were paralyzed (57% / 76%
spam fraction), emitting `<function=query_quest("Foresting")>`-style calls the server can't
parse, which fall to text, never execute, and the model re-emits the unanswered call → spam
loop. **Key lesson: grader-endorsement-suppression (flip-probe-verified) did not transfer to
student-emission-suppression after training.** The defect lives in the policy's generation, and
a grading-context fix doesn't reach it.

The intended runtime intervention was a **harness affordance** (`KAETRAM_TOOL_RECOVERY`,
env-gated): when the server
drops a malformed call, `play_qwen` recovers the executable call (`canonicalize.recover_tool_calls`,
99.5% coverage on real specimens), **rewrites history to a clean canonical assistant turn**
(severing the in-context copy prior at its source), executes it, and returns a loud `[format]`
correction note. In the reported rerun (`run_20260613_112422`), recovery was enabled and
paralysis was not reported (reported 3.3% recovery rate).
The rewritten run bundle has since been recovered and content-bound, but the
pre-rewrite emissions needed to regenerate recovery eligibility were not retained.
This temporal/runtime association does not establish a causal cure because raw pre-rewrite
emissions and an independently regenerated recovery count are unavailable.

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
cell. Their raw run directories were recovered read-only and bound to the external inventory
recorded in `research/audits/historical-run-digests.json`, but exact checkpoint/render parity,
gameplay seeds, reset receipts, and immutable configuration manifests are absent. The recovery
is not a public artifact. Therefore subtracting the scores does **not** identify weights,
recovery, or interaction effects. In the reported recovery-off round-2 run, the Herbalist wall
fell 3/3 across clustered prompt variants; that is the strongest descriptive observation, not
three independent replications or a pure-weights estimate.

The project also reports **round-2 weights with the recovery affordance on**
(`run_20260613_214956`) at **17/30** (grinder 6, completionist 6, explorer 5). The notes also
report that r3 reached Herbalist stage 2 in **~2.0h vs r2's ~4.5h** and produced
**~40% more turns per hour (511 vs 366)**. Even with the recovered bundles, without matched
replicated runs and exact execution parity neither the score differences nor the throughput
difference can be attributed to a single lever.

**Reported speed de-confound (2026-07-11, run per the Codex cross-review objection that
r3+recovery vs r2-without-recovery conflates the two levers).** Per-agent
time-to-Herbalist-stage-2 and turns/hour recomputed on all three arms, recovery held constant
in the r2-vs-r3 contrast: r2 = 4.13/4.47/5.00h (mean 4.53h, ~367 t/h) → r2+recovery =
3.17/3.96/4.17h (mean 3.77h, ~353 t/h) → r3+recovery = 2.25/2.20/1.41h (mean 1.95h, ~512 t/h).
In these one-run cells, recovery alone is associated with −17% time-to-wall and **zero
throughput change** on r2 weights; with recovery fixed, the r3 weight update is associated
with halved time-to-wall (3.77→1.95h) and +45% turns/hour. (Script: session-timestamp scan for
first observe with Herbalist stage ≥2; run IDs run_20260612_044933 / run_20260613_214956 /
run_20260613_112422. One run per cell — a descriptive association, not an effect estimate.)

We therefore label 18/30 a **weights-plus-interface** result. The recovery-off round-2 passage is
the least-confounded historical comparison, but it is not a pure-weights effect estimate.

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

### Historical recovery behavior (rewritten bundles recovered)

Historical notes report 405 rewritten sessions with one `[format]` marker and no later marker
each; 90% reportedly fire at turn 2 (the first call after the opening observe), with **98.5%
clean calls afterward** — read at the time as a session-opening artifact durably corrected by
the history rewrite (clean canonical exemplars + the correction text denying the copy prior its
malformed exemplars). The notes also report the rewrite breaking the *compounding* (335
emissions vs r2's 685 — without feedback, malformed begets malformed; with it the total stays
bounded). Recovered rewritten session bundles exist, but raw pre-rewrite emissions are absent
and the counts have not been independently regenerated. This cannot establish model
self-correction, zero relapse, or the true malformation denominator. The reported 335
malformed emissions and 405 markers use different units.

### Descriptive takeaways; causal separation still pending

- **A seeded-training round was followed by canonical-start wall passage.** Base and round 1
  report 0/3 clustered prompt variants across the Herbalist wall; round 2 reports 3/3 and 15/30
  with recovery off. This is one unmatched historical sequence, not evidence that seeding or
  weights alone caused the change.
- **Weights and runtime recovery are distinct intervention candidates.** Recovery can execute a
  dropped malformed call and canonicalize retained history, while the training rounds attempted
  weights-side changes. The unmatched 12/15/17/18 cells do not identify either main effect or an
  interaction.
- **A later Rick's probe weakens the cooking-null interpretation.** Both tested model sizes
  formed or attempted the cook action when seeded. The probe also exposed a respawn-dungeon
  station, door-gated position resets, seaside aggro, and failure to cross required doors.
  No live stage completion was obtained, so these are alternative blockers rather than an
  identified cause or evidence of transferred competence.

**Framing:** the r3 18/30 is a **weights + harness recovery** arm — env-changed, not
pure-weights like base/r1/r2 — so we label it as such. The recovery-off round-2 report is the
least-confounded historical comparison, but exact parity is unavailable. For the r3 arm specifically, the binding
constraint on the last stages was the format defect — a model–environment interface failure
three attempted weights-side interventions had not reached in the reported runs — which is why
18/30 is honestly weights-plus-interface.

### Hardening runs E1 + E4 (2026-07-11/12): additional observed factorial cells

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

Three descriptive updates:

1. The reported r3-without-recovery and r3-with-recovery runs both score 18/30. This rules out
   attributing that pair's numerical difference to recovery, but it does not identify a
   weights main effect across the cross-era r2/r3 runs.
2. At the two reported r3 cells, recovery is associated with fewer wasted turns (~70% to ~5%)
   and a shorter time-to-wall (3.62h to 1.95h). One run per cell does not establish an
   efficiency effect or a null stage effect.
3. The June 13 “counterfactual grading paralyzed the agents” interpretation was based on an
   early stopped run. The
   stopped run's reported 57–76% spam at 35 min is close to E4's reported ~70%. This weakens
   the early “paralysis” narrative, but neither a grading-side causal effect nor its stage cost
   is established.

E1 caveats: recovery reportedly fired once, but its different instance and port collision mean
it is not an independent matched base replicate. Its explorer passed the Herbalist wall within
6h, so the descriptive pooled base-family count is **1/6 persona-runs**
(vs r2's 3/3; descriptive only — agent-level Fisher on persona-runs is pseudoreplication, per
the 2026-07-13 stats audit; say "1 of 6 observed persona-runs," not "rarely"/significant).
E1's grinder lane ran under a 3-listener port collision on :9001 (E1's own orphaned server +
the yarn dev server at .env PORT=9001, both fixed 2026-07-11; dev server moved to :9900) — its
grinder score (3) is suspect and E1 deserves a cheap rerun. Whether June arms also ran with the
dev-server collision is an open protocol question to check before the paper freezes.

### The ±seeding observation (2026-07-13): natural-only r2 lands at the reported base score

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
1. **The natural-only run scores no higher than the reported base run** (12/30 = base =
   r1) despite a token-level KL summary close to the seeded arm's. In this one observed pair,
   adding the seeded slice is associated with the 12→15 difference, while aggregate KL
   movement does not track the observed stage totals. This is a hypothesis-generating
   contrast, not an effect estimate.
2. The observed clustered persona counts are 3/3 and 1/3. They are not independent policy
   replicates and therefore do not support a reliability-effect estimate. Each arm is one
   training run, and the runs used different infrastructure eras.
3. Bounded historical wording: *"Holding intended initialization, record count and
   optimization budget fixed, the arm including milestone-reset rollouts was reported at
   15/30 with 3/3 clustered wall passage, whereas the natural-only arm was reported at 12/30
   with 1/3, alongside similar aggregate KL summaries."* The pair motivates a replicated
   state-coverage experiment; neither it nor Arm-C discriminates teacher-selective
   distillation, reset-conditioned self-imitation, and other lineage/execution alternatives.

### Arm-C, a candidate mechanism control (2026-07-14): uniform self-imitation matches the reported seeded-OPD score

The intended control from the Codex pass-3 design: `2b-opd-r2-uniform` uses the reported r2 corpus
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

The uniform arm reproduces the single reported r2 score and clustered wall profile without
teacher-directed advantages. The natural-only arm is lower in a separate run. Together these
observations motivate a **reset-conditioned self-imitation hypothesis** (imitating the
student's own frontier-state behavior), but they do not identify a mechanism: each checkpoint
is represented by one training run, infrastructure eras differ, and the missing fully crossed
factorial leaves teacher direction, rollout source, and state coverage incompletely isolated.
The corpus also contains failures, so it is not success-only imitation.

Descriptively, the arm with a negative aggregate KL change still scores 15/30. This weakens
KL change as a surrogate endpoint in these runs and motivates testing self-imitation directly.
It does not retroactively explain r1, confine teacher effects to style, or license a prediction
for the missing natural+uniform cell.

**Retrospective addenda (2026-07-15, four analyses of the same logged runs):**

These analyses are neither independent replications nor confirmatory evidence. They are
maintainer-reported diagnostics over the same n=1 arm records.

*(v) Behavioral similarity:* in these logged runs, r2 and Arm-C have similar strategy summaries:
tool-mix total-variation distance 0.083, below the observed within-arm agent-to-agent range
(0.10–0.12);
wall-passage mechanism verbatim-identical (same blueberry-grind gate arithmetic templates,
same bush rotation after empty gathers, same premature Herby visits, same stage-2 plateau);
milestone clocks match within per-agent scatter (wall μ 4.54h vs 4.49h). At the EMISSION level
they are cleanly different policies — error-category TV 0.615 vs ≤0.105 among the three
teacher-graded arms (r1/r2/E3′ cluster as a family), validation errors 107 vs 1.8 per 1,000
calls, dead tool-callish emissions 1,082 vs 1, hallucinated verbs 37 vs 2 — every difference
not associated with a score difference in this pair, and concentrated in the
teacher-graded family's observed defect pattern.
*(vi) Advantage anatomy (825 heldout records, 261k scored tokens):* 94% of |advantage| mass is
generic prose suppression, 5% generic structure endorsement; action-identity tokens (verb
names, argument values) carry ≈zero advantage (0.1–0.2% of mass; the gather verb-name at
seeded wall states gets mean −0.0000). Wall state-selectivity is NULL (gather@seeded
difference-in-differences −0.011, bootstrap CI [−0.036,+0.015]); the seeded-origin regression
coefficient is +0.014 (mild ENDORSEMENT of student behavior at wall states, not correction);
state variables add ≤0.3pp R² over (verb, turn-position, length) surface covariates, and 99%+
of advantage variance is within-record prose phrasing. Seeded and natural grading profiles are
near-identical in this dataset; a replicated crossed study is needed to test whether state
distribution changes teacher grading.

Original addenda: (i) *Efficiency channel
not separated in this pair*: Arm-C and r2 report time-to-Herb-stage-2 means of 4.48h and
4.53h, with throughput 376 and 367 turns/h. No separation is observed on stages, wall passage,
speed, or throughput, but one run per arm cannot establish equivalence. (ii) *Style panel*
(full 6-arm comparison): the available summaries do not isolate a style dimension attributable
to teacher pull — uniform matched the teacher's reported tempo
(6.0) and is numerically closer to the teacher than r2 on the signature overshoots
(query_quest 11.0% vs 12.9% → teacher 8.7%; chars/turn 905 vs 1,116 → 864). These summaries
do not attribute the difference to retraining or teacher pull. (iii) **Malformed-call frequency
is associated with the reported graded arms**: all three carry the ~11% dialect
(r1 11.5% / r2 10.7% / E3′ 11.6% of calls) while Arm-C on the SAME corpus sits at **0.2% on
missing-required-field calls (12 vs 591) / 1.1% on n_malformed_emit (76 vs 685; metric labels
corrected per the 2026-07-17 verification sweep)** — a ~9× gap either way; the malformation
is associated with the teacher-graded arms rather than this uniform arm; this is compatible
with, but does not close, the copy-prior causal hypothesis. (iv) E3′ carries its own eat_food spam pathology (310/382
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
descriptive updates:
1. The reported four cells give seeded-arm counts of 6/6 clustered persona-runs
   (r2 3/3, Arm-C 3/3) vs natural-retrained arms 3/6 (E3′ 1/3, M3 2/3) vs base-family 1/6.
   The associated totals are 15/15 versus 12/14. These clustered, one-checkpoint cells do not
   show that retraining helps or estimate a seeding reliability effect; independent training
   replication is required.
2. The uniform run is numerically higher than the teacher-graded run on the intended identical
   natural corpus (M3 14/30, 2/3 vs E3′ 12/30, 1/3) and is much cleaner in the reported
   malformed-emission count. One run per arm cannot establish a negative teacher-gradient
   effect or attribute the difference to a defect tax.
3. The low malformed count repeats the descriptive pattern seen in other uniform runs; it is
   not an independent training replicate of a single arm.

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
3. **Why failure records may remain useful**: ~40% of seeded gradient mass is failed-outcome
   turns, and many “failures” are compatible with correct-policy unlucky dice (41% realized lily
   yield under the ~1/3-per-tick mechanic — empty-retry IS the optimal strategy); the ±30%
   trust region caps reinforcement of genuinely-bad modes that were already modal; failure
   and success exemplars occupy different states (premature turn-ins at 0–2 lilies, real
   ones at ≥3). This corpus anatomy suggests a testable conditional-learning explanation; it
   does not show what the trained policy learned.
4. **The teacher's weighting was flat where it mattered**: per-token mean |adv| seeded/natural
   ratio **0.999** (bootstrap CI [0.949, 1.045]); r2 trained with uniformly ~5.6% less total
   magnitude than Arm-C. The 15=15 observation is not explained by this scalar magnitude
   difference, but sign structure and per-verb weights remain candidates for replicated study.
5. **What natural corpora can/can't buy** (clean-r1 context): natural/base corpora contain
   ZERO passage exemplars and ≤1 successful lily gather, yet defect-free natural arms pass
   the wall 5/9 (M1 1/3, M3 2/3, clean-r1 2/3) — approach behavior + persistence passes a
   stochastic wall sometimes. Seeded arms are 6/6. The observed pattern is consistent with
   seeding increasing passage reliability by exposing the closing sequence and post-wall
   regime; independent training replicates are required to estimate that effect.

Working hypothesis: *milestone resets may help chiefly by placing the behavior policy where
useful closing sequences can be recorded and imitated.* This is not paper-ready result text.

### Clean-r1, a discriminating arm (2026-07-17): fewer defects and a small numerical edge

`2b-opd-r1-clean` (eval run_20260716_215512): the r1 recipe with ONE repair — build/score
contexts rendered with a `tools=` block (`OPD_BUILD_TOOLS_JSON` pointing at the live MCP
capture `scripts/opd/tool_defs.snapshot.json`; both the snapshot and the builder switch are
in-tree). The build predates the immutable-receipt machinery, so this historical arm cannot
establish exact train/serve parity by receipt.
Same base init, same natural base-rollout corpus (5,575 records — parity contexts even
recovered the original build's ~1% score-fails), same trainer. Result: **14/30 (4/5/5), wall
2/3, malformed emissions 1 in 7,218 calls.**

| natural-corpus arm | config | Core-3 | wall | malformed |
|---|---|---|---|---|
| r1 (June) | graded, defect-exposing contexts | 12 | 0/3 | 233 |
| M1 | uniform (no teacher) | 13 | 1/3 | 0 |
| **clean-r1** | **graded, parity contexts** | **14** | **2/3** | **1** |

Readouts:
1. **The repaired-context run reports far fewer defect emissions** (1 vs 233, near the
   reported base floor). Although teacher, corpus, and loss were intended to match, this is
   one run in a different execution era and therefore supports, but does not causally confirm,
   the Seam-1 exposure hypothesis.
2. **Defect-free grading ≥ uniform** (14 vs 13, wall 2/3 vs 1/3) on the identical corpus —
   the earlier “grades net negative” interpretation is not stable to the configuration repair;
   clean grades show a small positive numerical edge within n=1 noise. Bounded wording:
   *"under our defect-exposing configuration, teacher-graded arms scored no higher than
   uniform twins; with the configuration repaired, grading performs at parity to slightly
   positive (+1 stage, +1 wall passage at n=1) — 'grades carry no value' is not supported,
   'grades were never the primary competence channel' remains a hypothesis."* The observed
   between-run magnitudes motivate prioritizing corpus/state coverage, but do not establish a
   hierarchy of causal effects.
3. The pooled descriptive wall counts are 5/9 persona-runs across these natural-family arms
   and 6/6 across the seeded arms. Personas within a checkpoint are clustered, so these are
   not independent trials or a reliability-effect estimate.
4. Protocol note: contexts are ~2.6K tokens longer under parity → slightly fewer turns fit
   per session; comparability with the June arms carries that caveat (M1 shares it not —
   M1 used the old contexts; the clean-r1-vs-M1 comparison is corpus-matched but not
   context-length-matched. The M4 clean-config replication resolves this cleanly.)

### Maintainer defect-sensitivity retrospective (2026-07-17)

The relevant July raw bundles have since been recovered and content-bound, and their
six-hour Core-3 scores deterministically replay after exact input, clock, and
analysis-code verification in `research/audits/july-mechanism-results.json`.
The exact scripts and receipts for the
additional defect counts below are not preserved, so those diagnostics remain historical
audit notes rather than independently verified verdicts.

**Reported as less defect-sensitive:** size ladder; scaffold lift; r10 regression; r2's
pure-weights wall passage (paid ~11% tax and still passed — conservative); r1 execution wins
(measured on executed calls); KL-gate irrelevance (gates score fixed corpus text, masked spans
excluded; dissociations run both directions); Rick's over-determination; cook/A2′ probes;
speed de-confound; E4's 18/30 (**verified**: observe-delta scoring is server ground truth spam
cannot inflate; all 17 stage transitions traced to executed calls).

**Reported as defect-sensitive (configuration must be explicit):**
- The earlier “grades never bought a stage” slogan is unsupported. Under the reported
  defect-exposing configuration, one-run graded scores were no higher than their uniform
  counterparts. Pair-2's 2-stage gap (E3′ 12 vs M3
  14) is associated with a large defect-count difference (E3′'s completionist reportedly
  emitted 3,503 malformed turns and
  never accepted Herbalist — stood at Herby 3× with the panel open, accept param absent);
  pair-3 summaries disagree (a post-hoc clean-r2 estimate is ~15; the reported
  r2+recovery score is 17 vs Arm-C 15). A post-hoc defect-adjusted graded total
  was estimated at [59,63]
  vs uniform 59–60 — **the sign can flip; "grades carry no competence value" was never
  established.** The clean-r1 arm (in flight) is the discriminating experiment.
- Seeding reliability: observed 6/6 vs 3/6 clustered persona-runs. A post-hoc
  defect-adjusted count is not a valid effect estimate.
- r1 style exhibit correction: query_quest's "2× behavioral overshoot" is ~⅔ defect retry
  loops (18.0% raw → **10.9% on defect-free calls**, teacher 8.7; after a dirty call the next
  verb is query_quest 52% vs 16% after clean). The interact_npc overshoot disappears after
  excluding defect-marked calls. chars/turn is similar (1,195 clean vs 1,247).
  “Style transferred” remains descriptive;
  that one exhibit doesn't as written.
- 0.8B lane: reword to "under our defect-exposing configuration" — defect-transfer
  dose-response, not intrinsic-OPD-harm.
- M2 observation: June r1-policy seeded rollouts were richer in reported success density
  (3/3 passages, 344 gathers) not attempt volume (base: 757 gathers, 1/3) — malformed-retry
  inflation alone does not explain the reported difference.

No causal claim is upgraded by this retrospective. Historical reword list (7 items) and
suggested follow-ups (clean-r1 in flight;
clean-r2 ~$10–30; Arm-C+recovery inertness check; M4 should use the clean config as default
with one legacy-config control seed, else it replicates the tax, not the mechanism) — full
audit in session records 2026-07-17.

### Unverified maintainer report: defect-origin investigation (2026-07-16)

The raw probe outputs, exact source-run bundles, regression inputs, scorer responses, and
analysis receipt are not tracked in this checkout. All counts and interpretations in this
subsection are maintainer-reported, are not paper evidence, and must not be cited until an
immutable bundle passes the current validators. The reported three-part investigation used an
external research agent, internal forensics over runs and the recovered pre-squash r1 build
code, heldout audits, and a reported 800-sample 2×2 generation probe using
`scripts/opd/defect_origin_probe.py`. Full agent reports remain in untracked session records;
reported numbers:

**External:** our wire format IS Qwen3.5's native (Qwen3-Coder XML lineage; fragility widely
documented — Ollama #14493, QwenLM/Qwen3.6 #125, vLLM #22975/#39056). presence_penalty=1.5 is
Qwen's GENERAL-tasks preset; their own tables recommend 0.0 for precise/structured work;
penalties corrupting repeated structural tokens documented since vLLM #1257 (2023).
Python-literal leakage into tool formats is a known cross-family phenomenon. UNREPORTED
anywhere: the kwarg-in-key variant; doc-literal priming of a teacher-forced grader (our
−1.2/−1.5-nat measurements); the generative-vs-teacher-forced contamination-channel
decomposition.

**Maintainer-reported forensics:** base 0/6,149 malformed; an r1 corpus described as clean;
and defect emissions from the first r1 evaluation session. Reported specifics: the malformed
families sit exclusively at presence-penalized positions (param ≥2, close-tag triplets), yet
the malformed-vs-context regression runs AGAINST penalty and FOR copy-prior (prior malformed
content in context: **β=+0.38→+4.37, z to 17**); ~30 Python doc-literals dictate the surface
forms verbatim (F1 = the `accept_quest_offer=True` string, appearing 6× in system.md; F2 verbs
match doc literals verb-for-verb); the r1 "seam records" bug (4.2% prefix violations
misaligning advantages at the tool-call opening) was real, ours, fixed in r2, and did not
inject malformed text; and the parser's silent laundering (F1 dropped errorlessly; F2
persisting verbatim, 67–79% self-priming) built the entrenchment loop. The maintainer
interpreted these counts as evidence against presence penalty as the sole seed and as
compatible with Python doc-literal priming, context carryover, a prefix-seam bug, and parser
laundering. The report also states that historical build/score contexts omitted the
serving-time `tools=` block. None of those source artifacts or regression receipts is
available here, so the proposed chain remains a candidate explanation.

**The 2×2 probe (20 states × 5 samples/cell, generation WITHOUT tools= — i.e., the
gradient-time context):**

| cell | r1 checkpoint | base-2B |
|---|---|---|
| pp1.5 + our py-docs | 5.0% malformed | **7.0% (all python-call!)** |
| pp0 + py-docs | 3.0% | 9.0% |
| pp1.5 + canonical docs | 1.0% | 0.0% |
| pp0 + canonical docs | **0.0%** | 1.0% |

The maintainer reports two probe patterns: (1) in the tested gradient-time contexts, the
sampled base model emitted Python-call forms at
7–9% when our Python docs are present** (0–1% with canonical docs; presence penalty ≈ no
effect) — reconciling with base's 0% in production logs, where the tools-block reminder was
present. This makes the no-tools-block/Python-doc context a plausible exposure condition and
documents a train/serve difference; it does not by itself estimate the condition's causal
effect on training.
(2) on the trained checkpoint, the reported rate changes 5%→1%→0 after doc
canonicalization, while kwarg-in-key appears only in the reported r1 cells. Without the
bundle, these are unverified associations rather than evidence that doc literals dominate or
that training acquired the form.

**Candidate causal account:** dense teacher-forced reverse-KL may expose a copy-prior
vulnerability; Python doc literals and tools-block-free grading contexts are candidate
exposures; the observed surface forms copy those literals; and silent parser laundering,
abstention masking, and verbatim retraining could amplify them. Presence penalty appears
secondary in this probe. These links require a registered crossed intervention before being
claimed as causal. Engineering fixes remain warranted without that claim: docify the served prompt;
render build/score contexts with tools= (or drop at serving for parity); keep recovery +
loud errors permanent; pp=0 for structured serving per Qwen's own tables; permanent prefix
tripwire.

### M6, the r3-uniform arm (2026-07-16): 17/30, wall 3/3 — a corpus-channel hypothesis

`2b-opd-r3-uniform` (eval run_20260715_211431): the exact r3 corpus (r2-eval rollouts +
full-ladder seeded, 8,856 records) with uniform advantages, merged-r2 init, 6h clean run.
**17/30 (5/6/6), wall 3/3, two agents completed Herbalist 3/3** — vs the reported
r3+recovery and r3-no-recovery scores of 18. This is consistent with a corpus/visitation
channel, but does not identify it over grading or execution-era alternatives.

**Defect refinement:** M6 logged 2,695 malformed emissions — the first heavily-affected
uniform arm. Its corpus text is defect-RICH (r2-eval rollouts, 685 emissions + mutated forms)
and its init (merged-r2) already carries the dialect. The result is compatible with plain
imitation preserving contaminated forms. The broader pattern motivates two candidate
channels—teacher-gradient induction and corpus-level preservation—but the existing runs do
not isolate either one causally.

### The reported matched-pair family (July 16) — no replicated grade advantage established

| corpus (init) | teacher-graded arm | uniform twin |
|---|---|---|
| natural base rollouts (base init) | r1: **12**, wall 0/3, mal 233 | M1: **13**, mal 0 |
| natural r1-eval (merged-r1) | E3′: **12**, wall 1/3, mal ~6.5k-class | M3: **14**, wall 2/3, mal 31 |
| natural+seeded r1-policy (merged-r1) | r2: **15**, wall 3/3, mal 685 | Arm-C: **15**, wall 3/3, mal 76 |
| r2-eval + full-ladder seeded (merged-r2) | r3-norec: **18**, wall 3/3, ~70% spam | M6: **17**, wall 3/3, mal 2,695 |
| base seeded rollouts (base init) | — | M2: **12**, wall 0/3, mal 0 |

Across four one-run pairs, the reported totals are 57 for graded and 59 for uniform. This
does not show a reproducible grade advantage, but summing heterogeneous, non-independent
runs is not an effect estimate. The family motivates separate hypotheses about execution
discipline, defect transmission, rollout quality, and frontier coverage; it does not establish
that grades helped or failed to help competence.

### M2, the teacher-free end-to-end arm (2026-07-16): 12/30, wall 0/3

`2b-teacherfree` (eval run_20260715_151045): base init + BASE-generated seeded rollouts
(collection run_20260715_002620, 480 sessions, wall passages 1/3) + uniform advantages —
zero teacher signal anywhere in the lineage. Result: **12/30 (4/4/4), wall 0/3, malformed 0**
— exactly base's profile; the seeded self-imitation recipe did NOT transfer the wall when the
imitated rollouts came from base itself.

**Working multi-stage hypothesis (not a controlled attribution):**
1. r1 teacher grading may alter execution discipline; M1 is an intended but unreplicated
   contrast and also differs in observed outcomes.
2. The r1 policy's seeded rollouts report 3/3 wall passages in the June collection;
   BASE's seeded rollouts are poor (1/3 passages, July collection). Self-imitation of the
   rich corpus transfers the wall (Arm-C 15/30, 3/3); of the poor corpus, nothing (M2 12/30,
   0/3).
3. The one-run Arm-C and M3 comparisons do not show a numerical benefit from per-token
   grades, but they do not establish a null or negative effect.

Candidate explanation: teacher grading may first alter the behavior policy, whose later
frontier experiences become useful self-imitation data. A fully crossed, replicated lineage
study is required to distinguish that curriculum-builder story from direct grade effects,
data-volume effects, and selection of better rollout trajectories.

Confounds, stated: M2's corpus is smaller (3,872 vs Arm-C's 7,024 records / ~121 vs 220
steps) and — the semantic variable — contains 1 wall passage vs the June seeded corpus's 3;
generating-policy quality and passage count are entangled with data volume here. A
passage-matched variant would disambiguate. The collection-time passage rates show the two
corpora differ, but do not explain why.

### M1, the r1-uniform control (2026-07-15): an execution-discipline contrast

`2b-opd-r1-uniform` (eval run_20260715_030342): uniform advantages (pre-registered c=0.4873)
on the exact round-1 corpus (5,564 base-rollout records), base init (== generator, as r1),
174 steps, 6h protocol. Result: **a near-no-op, as self-imitation of one's own on-policy
rollouts should be** — turns/session 8.7 (base 9.1, r1's teacher-exact 6.1), eat_food errors
77.8% (base 74.0, r1 48.3), MOB_NOT_FOUND 128 (base 113, r1 39), dangling-dialect 1% (base 3,
r1 100), navigate errors 3.6% (partial: base 5.3, r1 1.2), stages 13/30 (≈base),
**malformed emissions 0** (r1: 233 — every uniform arm is defect-free; every graded arm isn't).

This one-run contrast contradicts the strongest blanket claim that teacher grading changes
nothing measurable. It motivates, but does not complete, three attributions:
- execution style/discipline may respond to teacher-directed gradients;
- task competence may depend more strongly on frontier-state coverage;
- malformed-call frequency may depend on gradient and corpus conditions.

Each attribution still requires independent training seeds and a fully crossed design. The
current record cannot establish what the teacher “taught,” whether execution discipline
mediates competence, or whether any channel is necessary.

### Unverified maintainer report: five-arm decision probes (baseline_20260701)

The referenced immutable probe bundle is not tracked in this checkout. The counts and
interpretations in this subsection are retained only as a maintainer report and are not paper
evidence. `dataset/probes/baseline_20260701/` (branch `feat/social-dashboard-0_8b`; framework
`scripts/probes/{specs,runner,checks,report}.py`): 701 trials — five arms × seven seeded
single-decision probes × 20 trials, identical seed-digest-verified stimuli, standard 17-tool
surface, compact observes + programmatic note, ≤4-turn budget, intent-level (`pass_if` tool
rule) and world-effect (`pass_db`) scoring. The maintainer reports 699/701 scored
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

Maintainer-reported readings of the table: (1) per-decision capability map — 4B ≫ 0.8B on 6/7
probes (recovery 19 vs 5, turn-in 19 vs 5); (2) P-EQUIP reactive-recovery split — the 4B
equips the pole *reactively after a failed gather* 8/20 on top of 3/20 proactive, students
0–1; (3) P-D — the 4B chooses cook 7/20 at the seeded cook state as a generator, so the
full-run "never cooks" is reported as substantially reachability, not pure incompetence;
(4) P-C forensics (2026-07-15, all 60 trials read) — the 7/20→1/20 "collapse" is reported as
mostly a scoring artifact (the pass rule credits only in-place `gather` at a location with no
levelable node in `nearby`, rewarding base's walkthrough-recitation accidents and punishing
grounded travel pivots; the un-OPD'd 4B fails the same way, 2/20) **plus a real, subtler
seeding cost**: gate-passed-state training taught r3 a "gate already satisfied" prior at the
lily decision point (0/20 blueberry-first openings vs base 5/20), and r3's malformed
positional-arg syntax cost it 5/20 trials (17 malformed calls; base and 4B: 0); under
travel-aware credit the reported estimate is r3 ≈ 5–6/20, 4B ≈ 8–9/20 — a modest decline,
not a cliff; (5) 0.8B OPD damage at decision level — P-A 11→6, P-B 5→4 worse; P-C2 6→11,
P-REC 5→8 better, with failing trials showing the malformed dialect in action (verified
specimen: `<function=gather><parameter=resource_name>\nOak\n</` — corrupted close tag,
argument lost, verb salvaged with empty input).

Those interpretations cannot be audited here: the stimulus receipts, raw responses,
scorer outputs, exclusions, and analysis code revision are absent. They must not be cited as
capability, reachability, learned-prior, seeding-cost, or defect-transfer results until the
bundle is restored and the current validators pass.

### The 0.8B lane (runs July 1–5, audited July 14): lower scores after natural-rollout OPD

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
1. The single natural-rollout OPD run is lower than the single base-0.8B run (3 vs 13), while
   the recovery-enabled system run scores 10. This is compatible with defect sensitivity at
   lower capacity, but checkpoint, execution, and infrastructure confounds prevent attributing
   the difference to OPD or the defect.
2. The reported base-0.8B and base-2B scores are 13 and 12. One run per size does not
   establish size-ladder saturation.
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

### Unverified maintainer report: cook-state grading probe (2026-07-11)

The raw contexts, endpoint/checkpoint receipt, target-token scores, exclusions, and output
bundle are not tracked. The following table is a maintainer report, not paper evidence, and
must not be cited until regenerated into an immutable validated bundle. The utility
`scripts/opd/cook_grade_probe.py` was reportedly run on 30 cook-decision and 30 lily-control
states with four candidate continuations.

| candidate | @cook states | @lily states |
|---|---|---|
| observe | **−0.126** | **−0.158** |
| craft_item cook | −0.417 | −0.451 |
| gather (oak / lily) | −0.861 | −0.949 |
| attack rat | −0.909 | −1.008 |

Reported median margins are cook −0.285 (0/30 positive) and control −0.879 (1/30 positive).
The maintainer proposed two interpretations:

1. The maintainer interprets the reported rankings as inconsistent with a cook-specific
   grading failure because `observe` ranks above the intended action in both sampled state
   families. This interpretation is unverified and must not replace the paper's bounded
   cook-null language.
2. **Each candidate's grade is similar across these two sampled state sets** (cook −0.417 vs −0.451, observe −0.126
   vs −0.158 across conditions; per-state spread ≤~0.15 nats): at single-action granularity the
   dense per-token grade may be dominated by candidate-intrinsic sequence probability rather
   than state appropriateness. This is an unverified hypothesis, not a result.

Caveats reported by the maintainer: candidates were bare tool calls; comparisons across
heterogeneous candidates carry length/surface effects; and the probe covers single actions.

The maintainer also reports an advantage-level follow-up (2026-07-13, A2′; same probe, r1
student scored alongside the 4B so the metric is the actual OPD signal, teacher − student):
the state-invariance reportedly largely DISSOLVES at the advantage level — candidate-intrinsic
surface biases cancel between models; observe's likelihood dominance cancels (advantage only
+0.13–0.19 everywhere). At teacher-competent lily states the correct gather-lily carries
+0.411 (5/30 positive margins); at cook states the correct cook call gets +0.190 while WRONG
task verbs get +0.82 (gather-oak) and +0.70 (attack-rat), 0/30 positive margin —
control-minus-cook margin gap **+0.576 nats**. Reported consequences: (a) the raw-likelihood
"grades are ~state-invariant" reading measured the wrong quantity (retain it only as a fact
about likelihoods); (b) "the teacher can't grade what it can't do" is partially rehabilitated
in its advantage-level formulation — the training signal is reportedly better-aligned at
teacher-competent states; (c) it is still not clean state-conditional tutoring anywhere
(attack-rat tops lily states too). Because the target-token responses and tokenization
receipts are absent, this does not establish that the gradient favors wrong verbs, that
grading is better aligned at one state family, or that any teacher-competence mechanism holds.

### Unverified maintainer report: live seeded Rick's-Roll probes (2026-07-17)

The raw live-run bundles, exact server/database snapshot, endpoint/checkpoint receipts, and
seed manifests are not tracked. The following counts are maintainer-reported and not paper
evidence. The maintainer describes single base-2B agents
(`kaetram-qwen-2b`, non-SFT) and one 4B agent (`kaetram-qwen-4b`) seeded at the fixed
milestones (`seed_milestones.py`, branch `feat/ricks-roll-seeds`) and driven live through
`play_qwen`; MongoDB quest/inventory read directly for ground truth.

In one reported seeded run, base-2B emitted the intended cook-call form. Seeded
on a cooking station with 5 rawshrimp (`r4_cook`), it emitted
`craft_item(cooking, cookedshrimp, 5)` and **attempted it 21 times in one run** (16 "Could not
reach cooking station"). If validated, this would weaken only the narrow claim that the model
never samples the action form; it would not establish robust cooking competence or cause.

The live probes identify four plausible seeding/execution blockers:
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
4. **Observed model execution:** base-2B warps to Mudwich from unrecognized seed tiles, and
   **neither base-2B nor the 4B ever steps on a door** — seeded 1 tile from the stage-2 quest
   door (`r6_door`, (260,230), position verified to STICK), across 4 runs (2B grinder /
   2B completionist / 4B) **zero door-targeted navigates** occurred; agents instead grind mobs,
   eat the quest `seaweedroll`, or wander to the fishing spot (stage-1 behavior at stage 2).

The maintainer reports no live stage completion. If validated, the traces would identify
gated-region seeding and door/navigation as plausible blockers, without ranking them against
cooking competence or establishing the cause of the historical null.
The seed fixes therefore improve experimental validity without upgrading a mechanism claim.
The pre-door Rick/Lena seeds are not yet validated end to end.

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
3. **Retain tool recovery as a separately measured runtime arm** pending the matched factorial,
   tracking the pure-weights defect rate separately.

*Counter provenance: the historical notes report 405 recovery markers (`[format]` results);
`n_malformed_emit` (335) is a lower bound — the harness rewrites assistant content to canonical
before logging, so it counts only malformed text that survived recovery. The analyzer
(`scripts/log_analysis/`) decodes the `[format]` result prefix, detects plain-string validation
errors (`is_error`), and tracks the malformed-spam class via `n_malformed_emit`.*
