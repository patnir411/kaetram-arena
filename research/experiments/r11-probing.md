# r11 OPD feasibility

**Updated:** 2026-06-06 · **Branch:** feat/r11-opd-tinker · **Status:** diagnostic — the 9B lane it scoped was parked, never trained (see postscript; OPD ran on the 4B→2B lane, `opd-2b.md`).

> **Postscript (June 10 2026):** the lane this doc scoped — OPD of r10 toward base+scaffold at
> 9B — was **parked**, with round-1 data built but never trained
> (`dataset/opd_r11/round1/records.jsonl`, 1,074 records; `finetune/train_opd_modal.py`).
> Rationale: finding §1 below ("the scaffold recovers r10 to ~base band on its own") makes the
> success case ≈ "why not just use base"; the diagnostics here remain valid and motivated the
> pivot to a capability-instillation test at 4B-teacher → base-2B student. See [opd-2b.md](opd-2b.md).

## 1. The problem

| policy | Core-3 (of 30) | note |
|---|---|---|
| base Qwen3.5-9B | 7 | n=4, zero variance |
| r10-sft (base + LoRA) | 2 | off-policy SFT on Sonnet trajectories regressed below base |
| base + R11 scaffold | 12–19 | strong same-size policy → the OPD teacher |
| **r10-sft + R11 scaffold** | **10–12** | n=2 runs (3h→12, 5h→10); the scaffold recovers r10 from 2/30 (§3) |

r11 asks whether on-policy distillation (OPD) can recover r10 toward the scaffolded-base
teacher, and ideally exceed it.

## 2. Method — measure play, not log-prob

Feasibility is read on-policy and behaviourally. Teacher-forced log-probability under a forced
prefix does not predict free-running behaviour (exposure bias), so the only valid signal is the
agent actually playing:

- **On-policy eval.** Run r10 under the R11 scaffold in real play (`restart-agent.sh --qwen-sft 3`)
  and read its actual tool distribution + Core-3 delta — did the scaffold move r10's policy?
- **DAgger probe** (`scripts/opd/opd_onpolicy_probe.py`). On r10's *own* play states, query the
  teacher (base+scaffold) for its action; r10's action is already in the log (its real on-policy
  sample), so only the teacher is generated. This is DAgger labelling — roll out the learner, ask
  the expert on the learner's states — on the student's own distribution, by sampled behaviour.
  Reports teacher↔student agreement + the verb-shift on disagreements.

## 3. Results (two runs: 3h + 5h, 3 personalities each)

**The scaffold recovers r10 from 2/30 to a 10–12/30 plateau** (`run_20260605_173451` 3h → 12;
`run_20260605_223917` 5h → 10) — into base's lower band, and the extra 2h did not lift it. Its
behaviour matches the productive policy: attack drops to ~2–3% (from dominant), gather/observe run
the Foresting loop, query_quest is used 13–21×/agent. All three finish Foresting (via `gather(Oak)`
+ `interact_npc`/`query_quest`); the plateau is at **Herbalist's 1/3→2/3** — the grinder reaches 1/3
then churns 100+ observes / 180+ navigates without advancing. base+scaffold gets a little further at
this frontier (Herbalist 2/3, grinder grazes Rick's).

**r10 and the teacher are behaviourally close** (DAgger, n=120):
- Teacher agrees with r10 on **65%** of r10's own states.
- Near-identical tool mix (observe 41/52%, navigate 23/26%, gather 11/8%, query_quest 3/2%,
  interact_npc 3/2%).
- The 35% disagreement is mostly "observe a bit more" (navigate→observe ×7, gather→observe ×4).

Under R11, r10 already samples the credit verbs and plays at roughly the teacher's level. The
residual Core-3 gap (12 vs 12–19) sits at Herbalist+/Rick's, where both policies stall; what
drives it is still being assessed.

## 4. r11 recommendation

1. **The scaffold (a $0 context change) recovers r10 from 2/30 into base's lower band (~10–12/30),
   a few stages behind base+scaffold at the Herbalist frontier.** OPD *toward base+scaffold* is at
   most a modest lever (65% agreement on r10's own states, same tool mix) ceiled at base (12–19/30).
2. **The residual gap (Herbalist+/Rick's) is still being assessed.** Both r10+R11 and base+scaffold
   stall there; what drives it is not yet attributed. Candidate levers to test: harness-enforcement
   on those steps, and/or a stronger teacher (next point).
3. **To exceed base+scaffold**, distil toward a teacher that clears Rick's — the Claude corpus does
   full Core-3. A small reverse-KL OPD run, measured in play, is the only go/no-go for the weights
   lever (at this doc's vintage no OPD trainer existed; `finetune/train_opd_2b.py` was written later for the 4B→2B lane).

## 5. Artifacts

- `scripts/opd/opd_probe.py` — log→messages reconstruction (validated by `tests/unit/test_opd_probe_replay.py`).
- `scripts/opd/opd_onpolicy_probe.py` — the DAgger probe; data `dataset/opd_probe/onpolicy_scores.jsonl`.
- `scripts/run-eval.sh` / `eval_harness.py` — isolated play-eval harness; `/v1/score` endpoints
  (`finetune/serve_modal*.py`) + `tests/unit/test_score_endpoint.py`.

Caveats: DAgger n=120, single 3h run, completionist-weighted.

## 6. References

- Thinking Machines Lab, *On-Policy Distillation* — https://thinkingmachines.ai/blog/on-policy-distillation/
- Agarwal et al., *GKD* — https://arxiv.org/abs/2306.13649
- Gu et al., *MiniLLM* (reverse-KL / mode-seeking) — https://arxiv.org/abs/2306.08543
- Ross et al., *DAgger* — https://arxiv.org/abs/1011.0686
- Ranzato et al., *Sequence Level Training* (exposure bias) — https://arxiv.org/abs/1511.06732
- *Decoupling KL and Trajectories* — https://arxiv.org/abs/2605.16826
- DeepSeek-R1 (cold-start SFT → RL) — https://arxiv.org/abs/2501.12948
