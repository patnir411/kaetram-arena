# OPD round-1 night-chain runbook (2026-06-09 → 06-10) — CHAIN COMPLETE

> Final state (2026-06-11): all stages done. Results written up in
> `research/experiments/opd-2b.md` (style transferred, competence didn't; Core-3 12/30 flat,
> game-state errors cut by a third, argument-dropout regression 8.3%). Post-train gate table:
> `dataset/opd_2b/round1/gate_r1_output.txt`. Round 2 planned — see opd-2b.md tail.

State machine for the autonomous chain. Authorizations (user, final as of 2026-06-10 02:05 UTC):
auto-proceed through build→train→gate→eval. The eval launches REGARDLESS of the gate
verdict (gate is diagnostic only), runs **6 HOURS** with **hourly regression checks**
(stop early ONLY on major regression — see Stage D), then assess vs baseline.
Pipeline FAILURES (train crash with traceback, missing checkpoint, eval infra breakage)
=> STOP, hold spend, discuss with user first. Two NON-failures that proceed autonomously:
gate FAIL (eval runs anyway) and Modal preemption (auto-restarts + resumes from checkpoint).

## Stage A — data build ✅ DONE (2026-06-09 22:33 UTC)
Final: **5,564 train records / 574 heldout**, zero score failures on the A100 config,
uploaded to kaetram-model-vol:/opd_2b/round1/records.jsonl. 37% of records early-turn
upweighted (step_weight 1.5). Lengths p50=8.4K, p95=11.2K, max=12.9K tokens.
Pre-train rKL diagnostic (mean logp_2B − logp_4B per action token; positive = 2B
over-confident where 4B disagrees → OPD suppresses):
  eat_food +0.298 · respawn +0.292 · craft_item +0.290 · equip_item +0.284 ·
  gather +0.279 · buy_item +0.265 · drop_item +0.250 · attack +0.245 ·
  interact_npc +0.240 · navigate +0.212 · observe +0.206
  (also captured hallucinated verbs: obsserve +0.213, quer_quest +0.162)
  frontier: Herbalist +0.261 > Foresting +0.226
Signal lands exactly on the 2B's documented weaknesses (eat-discipline worst).

## Stage B — train ✅ DONE (2026-06-10, attempt 3; 174/174 steps, adapter + merged committed to kaetram-model-vol:/checkpoints/kaetram-qwen3.5-2b-opd-r1/)
`modal run finetune/train_opd_2b.py` — 174 steps, H100, lr 5e-5, eff. batch 32.
**~168s/step (~8h total)** — the image lacks working fla/tilelang kernels so the
18/24 Gated-DeltaNet layers run the pure-torch fallback (see Round-2 prep below).
History: attempt 1 preempted by Modal at step 84/174 (02:34:52 UTC — documented Modal
behavior, "all Functions subject to preemption", restart-on-same-input); attempt 2
auto-restarted from step 0 (no resume logic then); killed and relaunched as attempt 3
with **SAVE_STEPS=30 + resume_from_checkpoint** — any future preemption costs <=30 steps.
Sentinel armed (marker OPDTRAINSENTINELC): done / client-exit / 25-min stall / preemption.
Watch for: "Adapter committed", "Merged committed". Preemption => expect auto-restart
+ "Resuming from existing checkpoints"; only escalate if resume fails.

## Stage C — deploy + gate ✅ DONE (gate ran in ops session 06-10; output not saved — re-captured 06-11 to round1/gate_r1_output.txt)
4. `modal deploy finetune/serve_modal_2b_opd.py`
5. NEW_STUDENT_EP=https://patnir411--kaetram-qwen-2b-opd-inference-serve.modal.run/v1 \
     python3 scripts/opd/opd_gate.py --heldout dataset/opd_2b/round1/heldout.jsonl
   PASS = rKL reduction >= 30% AND 0/3 degenerate completions.
   Record verdict + by-verb table. DIAGNOSTIC ONLY — eval proceeds either way
   (the verdict matters for INTERPRETING the eval: flat eval + big rKL move =
   "KL not behaviorally load-bearing"; flat eval + no rKL move = "training didn't take").

## Stage D — eval ✅ DONE (run_20260610_140358, full 6h, no early stop)
6. Confirm game lane free: `ss -ltn | grep -E ':(9001|9011|9021)'` must be empty.
7. KAETRAM_OBSERVE_COMPACT=1 \
   KAETRAM_QWEN_SFT_ENDPOINT=https://patnir411--kaetram-qwen-2b-opd-inference-serve.modal.run/v1 \
   KAETRAM_QWEN_SFT_MODEL=2b-opd-r1 \
   ./scripts/restart-agent.sh --qwen-sft 3 --grinder 1 --completionist 1 --explorer 1 --hours 6
8. Verify after launch: `python3 scripts/log_analysis/analyze.py status` (3 agents up),
   run.meta.json model == "2b-opd-r1", session logs contain NO "ASCII_MAP"
   (compact-observe parity with baseline run_20260608_185339).
9. HOURLY check (user-authorized early stop — explicit exception to never-stop-runs):
   `python3 scripts/log_analysis/analyze.py status` + `errors`. STOP the run
   (scripts/nuke-agents.sh) ONLY on MAJOR regression = GATE-1-style collapse:
   Core-3 progress far behind baseline pace (baseline: Foresting 1/3 by ~40min,
   4/10 by ~3h) AND error rate exploding (>200/agent/hour, eat-inedible loops,
   phantom attacks). Mild slowness or mixed signals => keep running all 6h.
10. Arm an eval watcher (orchestrate runs in tmux — NOT harness-tracked):
    background loop until ports 9001/9011/9021 all close, then notify.

## Stage E — analysis ✅ DONE (2026-06-11; full results in research/experiments/opd-2b.md)
11. python3 scripts/log_analysis/analyze.py --run <new_run_id> metrics / errors / quest
    vs baseline run_20260608_185339 (same 6h duration, same compact observes,
    same 3 archetypes, same Mongo-reset start). Error-rate reference: baseline
    ~85-95 errors/1000 turns; MOB_NOT_FOUND ~100/agent, eat-inedible ~50/agent.
    Then write up in research/experiments/ + session_log.md.

Baseline numbers (full 6h, for reference): 4/10 core3 each agent; ~1,950-2,130
turns/agent; format/argument 100%; never cooks; never reaches Rick's.

## Round-2 prep: Qwen3.5 GDN training kernels — SOLVED
Round-1 trained on the pure-torch Gated-DeltaNet fallback (~168s/step). Fix proven in
the retrain/smoke: **pin `triton==3.3.1`** alongside fla 0.5.0 + causal-conv1d (already
in train_opd_2b.py's image) → ~13s/step (~13x). fla 0.5.0 refuses Triton>=3.4 on Hopper
(fla #640); tilelang 0.1.11 SIGABRTs (tvm::ffi double-registration) — avoid both.
Applies to train_opd_modal.py / train_modal.py too.

## Round-3 chain status — COMPLETE (June 13)

> Counterfactual-canonicalized grading (Plan A) + full-ladder milestone seeding + harness
> tool-recovery. Build 8,856 train / 1,040 heldout (4 score_fail; 365 counterfactual records,
> 0 masked) from r2-eval + 2 seeded runs (run_20260612_171400 / _194443). Flip probe set the
> canon scope: history-only = null (0% flip), history+doc-literals = −1.21 nats on 86% →
> adopted. Train from merged r2, 277 steps; gate PASS (+6.4% rKL, every verb).
> **First eval (run_20260613_105318) STOPPED at 35min**: counterfactual grading regressed the
> malformed *emission* (2/3 agents 57–76% spam — grader-suppression didn't transfer to
> generation). **Fix = harness tool-recovery** (KAETRAM_TOOL_RECOVERY: recover+execute+rewrite
> history clean + loud [format] note). **Rerun (run_20260613_112422): 18/30 — best of program,
> all 3 finished Herbalist 3/3 unseeded** (broke the stage-2 wall); Rick's 0/4 (3-link
> execution failure: pole never re-equipped → 0 shrimp → 0 cook; cook=0 transfer). Format
> historical notes report errors 10.7%→5.1%, 405 rewritten sessions with one recovery marker,
> and no later marker. Source logs/raw pre-rewrite emissions are not packaged; do not infer model
> self-correction or zero relapse.
> Full writeup: research/experiments/opd-2b.md. Round 4 (evidenced): privileged-context grading
> for Rick's + harness fixes (pole re-equip, route-progress session note).
>
> **Analyzer hardened (June 13):** scripts/log_analysis/ now decodes [format]-prefixed results,
> catches plain-string validation errors, and counts malformed-emission spam (was invisible).

## Round-2 chain status — COMPLETE (June 12)

> Final: build 7,024/825 zero-fail → train 220 steps/50min → gate PASS (+14.5% rKL, all
> verbs) → 6h unseeded eval run_20260612_044933 = **Core-3 15/30, all 3 agents passed the
> Herbalist stage-1 wall (4.1/4.5/5.0h)** — base 0/3, r1 0/3, r2 3/3. Full writeup:
> research/experiments/opd-2b.md; paper: reference/overview.pdf. Round-3 agenda:
> env-side format correction (attractor mutated to `<function=name(...)>` syntax, 599×),
> paprika-cluster seeding, survival/inventory harness levers.

## Round-2 chain status (June 11)

- **Stage A (build) 🔄 RUNNING** — launched June 11 after all preconditions cleared
  (below). 7,849 states / 825 heldout from run_20260610_140358 + run_20260610_222755;
  log /tmp/opd_r2_build.log; crash-resumable. Decisions (user, June 11): precondition 2
  → **raw advantages + trainer clamp** (round-1 recipe); precondition 3 → **mask
  advantages on malformed spans, NO dispatch shim for round 2** (eval parity; shim is a
  round-3 decision); gate → **full behavioral redesign** (malformed-rate hard vs r1 ref
  90%, degeneration, catastrophic blow-up; rKL directional-only, masked spans excluded);
  corpus → all-of-both interleaved. Pre-spend validation: 0 prefix violations / 0
  doubling over 524 sampled renders; 197 malformed spans ≈ 154+42 expected; mask decodes
  to `<parameter=accept_quest_offer=True>` exactly.
- Stage B (train): init merged-r1, fast GDN kernels (fp32-parity-verified, 8.9×), ~55min.
- Stage C (gate): redesigned opd_gate.py → dataset/opd_2b/round2/gate_r2_output.txt,
  then **STOP for user review** — eval only on explicit go. NO git commits/pushes.

## Round-2 build preconditions (June-11 audit — ALL CLEARED June 11, see status above)

Full analysis: research/experiments/opd-2b.md ("Argument-dropout root cause" + round-2
tail). Source runs for the build: run_20260610_140358 + run_20260610_222755 (+ seeded
bucket-B run if collected). Nothing on disk is corrupted — these gate the BUILD only.

1. **fix `opd_2b_data._emission_text` (REGRESSION, blocks build):** current code doubles
   every tool call — turn.text retains the full raw `<tool_call>` XML (verified: 100% of
   assistant text blocks in BOTH source runs) and the function appends a re-synthesized,
   param-stripped copy on top. Round-1 records on disk are clean (the strip was lost in
   the post-build rewrite). Fix: emission := turn.text verbatim + "<|im_end|>\n"; delete
   the re-synthesis loop. This is also what puts the malformed accept spans in front of
   the teacher (the parsed tool_calls have the key already stripped).
2. **ADV_OUTLIER zeroing → clamp:** |rkl|>=3 zeroing silently replaced round-1's recipe
   (pass raw advantages; trainer clamps via ADV_CLAMP=3 — round-1 records carry raw ±4.3).
   With byte-faithful emissions, large disagreements are signal. Clamp or pass raw.
3. **mask advantages on malformed param-key spans** (key contains '='): the teacher's
   grading there is unreliable — live probe measured adv **+0.09 TOWARD the malformation**
   on r1 rollouts (and +0.39 toward the correct form elsewhere; context-dependent flip).
   Abstain, don't reinforce. PENDING user approval of the handling choice (mask vs
   negative shaping vs leave); also pending: play_qwen dispatch shim (normalize
   `key=value` param names; deploy only BETWEEN runs), prompt doc-literal rewrite
   (deferred to Phase D).
4. **opd_gate.py: add format-validity check** on sampled completions (flag any
   `<parameter=...=...>` key) alongside the repetition check — the rKL gate is
   structurally blind to format defects.
5. **endpoint GPUs:** build scores against the r1 student endpoint — flip
   serve_modal_2b_opd.py L4→A100 for the batch build (L4 OOM-crash-loops at 16K-ctx
   concurrency, round-1 lesson), back to L4 for eval serving. serve_modal_2b_opd_r2.py
   is staged for the r2 eval. serve_modal_2b.py is still on A100 (revert to L4 when the
   round-2 build no longer needs it).
6. **verified correct, no action:** train_opd_2b.py round-2 defaults (INIT_MODEL =
   merged r1 == rollout policy; RECORDS_PATH /opd_2b/round2); serving-context fidelity
   (serve wrapper's _adapt_messages_for_qwen_template strips inline XML and renders
   tool_calls once ≈ turn_to_chat — serving history was never doubled).
