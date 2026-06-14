# r10 — post-training analysis: why SFT regressed below base, and the r11 plan

r10 is the supervised fine-tune of Qwen3.5-9B on a Claude-Sonnet teacher corpus,
trained to play Kaetram through a 17-tool MCP interface. On the Core 3
progression benchmark the fine-tuned model scores **3.5× lower than the
unfinetuned base model** it was built from. This document records the result,
the mechanism behind it, the supporting literature, and the r11 plan.

> **Baseline-comparability caveat (added June 10 2026):** every number in this document was
> measured under the **pre-R11 harness** (May 2026). The R11 scaffold (May 28–Jun 4, see
> [r11-direction.md](r11-direction.md)) changes the state contract enough that these figures are
> not comparable to any post-June run: under R11, base plays 12–19/30 (not 7/30) and r10-sft
> plays 10–12/30 (not 2/30 — [r11-probing.md](r11-probing.md)). The 3.5× regression claim
> stands *within* this document's matched-harness comparison. §7's r11 plan is superseded in
> part: the OPD lane moved to a 4B-teacher → base-2B student instillation test ([opd-2b.md](opd-2b.md)).

The short version: the SFT policy reproduces the *tool-frequency marginal* of
its training corpus rather than a task-appropriate, state-conditional policy. It
suppresses exactly the verbs Core 3 requires — the dialogue tools used to accept
quests and read objectives — because those verbs are rare in the corpus, even
though they carry most of the progress credit. The base model, retaining its
pretrained chat-model prior, invokes them far more often and therefore
out-performs the student.

---

## 1. Setup

**Model / training.** Qwen3.5-9B base, LoRA `r=64, α=64`, `use_rslora=False`,
bf16, 1 epoch, LR `1e-4`, H100 80GB (final run ~43h wall-clock at ~$4.7/hr =
$197, billing-verified; the "~22h" in older notes was an optimistic pre-run ETA
the job overshot — `MODAL.md`'s own ~5.5 min/step predicts ~43h for 8,510
records). Loss is masked to assistant turns (`train_on_responses_only`).

**Corpus.** 5 Claude-Sonnet runs × 3 capability archetypes
(grinder / completionist / explorer) ≈ 135 sessions / ~19k raw turns. Converted
to records with a 3-turn sliding window (`stride=1`) plus single observe→action
pairs. A 16,384-token truncation gate drops ~33% of multi-turn records (4,799).
Final dataset: 8,510 train / 853 val (9,363). Thinking-mode fusion: ~75% of
assistant turns carry chain-of-thought, ~25% are reflexive (no-think).

**Eval protocol.** Time-based episodes (≥3h). Both arms produce the same on-wire
tool-call format: the base model is served with the tool spec injected into the
chat template (`serve_modal_base.py`, `tools=`), the fine-tuned model from
training (`serve_modal.py`). Each episode resets the database and sandbox.
Metric: **Core 3 stages** = Foresting (3) + Herbalist's Desperation (3) +
Rick's Roll (4) = max 10 per agent, 30 per 3-agent run.

All numbers below are re-derived from the session logs by `scripts/r10_stats.py`
and `scripts/r10_credit_diag.py`.

**Cost (billing-verified, `modal billing report`, May 27).** Full r10 cycle
**$360 Modal** = training $264 (final run $197 + ~$67 in earlier/aborted
attempts) + eval serving $96 (base $67 + finetuned $29). Project-to-date across
all iterations since March: **$919**.

## 2. Result

| Run | Harness | Duration | Grinder | Completionist | Explorer | Stages/30 |
|-----|---------|----------|---------|---------------|----------|-----------|
| `run_20260510_173852` | base | 3h | 1 | 3✅ | 3✅ | **7** |
| `run_20260510_211339` | base | 6h | 1 | 3✅ | 3✅ | **7** |
| `run_20260519_223921` | base | 3h | 1 | 3✅ | 3✅ | **7** |
| `run_20260520_143530` | base | 3h | 1 | 3✅ | 3✅ | **7** |
| `run_20260520_014319` | r10-sft | 3h | 0 | 3✅ | 0 | **3** |
| `run_20260520_044433` | r10-sft | 3h | 0 | 1 | 0 | **1** |
| `run_20260520_173902` | r10-sft | 3h | 1 | 0 | 1 | **2** |

`3✅` = Foresting completed (3/3 stages).

- **Base is identically reproducible.** Four runs — three 3h, one 6h, across a
  10-day span with fresh database state each time — all reach
  `(1, 3✅, 3✅) = 7/30`, zero variance. The 6h run does not beat the 3h runs:
  additional wall-clock time yields no additional progress, because the agent
  has no memory across session rollovers.
- **The fine-tune regresses 3.5×.** Base 7/30 vs SFT mean 2.0/30 (`[3, 1, 2]`,
  std 1.0). Every base run exceeds every SFT run (perfect separation).
- **Foresting is the only quest either arm completes.** Herbalist's Desperation
  and Rick's Roll see zero progress in all seven runs, both arms — a **teacher
  ceiling**: the Sonnet teacher itself does not reliably accept these quests, so
  neither the base model nor a student trained on its traces can exceed it.

## 3. Statistical significance

The effect is descriptive — perfect separation between arms. With small n the
appropriate tests are exact, non-parametric.

- **Core 3 stages per run — Mann-Whitney U, exact, one-sided: p = 0.029.**
  Base scores are all 7 (ties), so an exact computation is required;
  the normal approximation (scipy's default under ties) is unreliable at this
  sample size. The exact value is 1/C(7,3): of the 35 ways to label which 3 of
  7 runs are the fine-tune, only the observed labeling gives perfect separation.
  At the n=5/5 cap (below) with perfect separation this floor falls to
  1/C(10,5) = 0.004.
- **Foresting completion — Fisher's exact, one-sided: p = 0.016, OR = 16.**
  Base completes 8/12 agent-attempts (67%), the fine-tune 1/9 (11%) — a **6.0×**
  drop in completion rate. Caveat: agents are clustered within runs, so this 2×2
  treats agent-attempts as independent and is a secondary measure.

Eval is capped at **n ≤ 5 base / n ≤ 5 SFT**. The separation is already perfect;
additional runs lower the p-value floor but add no mechanistic insight.

## 4. Mechanism: the tool-mix fingerprint

The fine-tuned policy's inference-time tool distribution is its *training-target
distribution*, not a task-appropriate one. For the completionist archetype
(% of tool calls; corpus = the completionist training-target actions, base = the
three 3h base runs):

| Tool | Training-target % | SFT inference % | Base inference % | \|SFT − corpus\| |
|---|---|---|---|---|
| `interact_npc` | 2.4 | **2.1** | 10.8 | **0.3** |
| `query_quest` | 2.2 | **2.5** | 11.2 | **0.3** |
| `navigate` | 27.7 | **26.4** | 5.5 | **1.3** |
| `observe` | 41.2 | 33.0 | 43.4 | 8.2 |

On the decision verbs — `interact_npc` and `query_quest` (used to talk to NPCs,
accept quests, and read objectives) and `navigate` — the fine-tune's inference
rate tracks the training marginal to within ~1pp. `observe` is the exception
(8.2pp lower at inference). The base model, by contrast, invokes the dialogue
verbs roughly **5× more often** (10.8% and 11.2% vs ~2%).

Expressed as per-run counts, the fine-tune **suppresses** `interact_npc` 5.6×
(63.3 → 11.3) and `query_quest` 4.8× (65.7 → 13.7), and **amplifies** `navigate`
4.49× (32 → 143.7). The student replaced goal-directed dialogue with kinetic
movement — it learned the teacher's verb frequencies, not the teacher's
state-conditional choices.

## 5. Credit structure of the training corpus

A hindsight-credit analysis explains why imitating the corpus marginal is
harmful. Each action turn in the source corpus is labeled by whether any quest
stage advances within the next N turns of the same session
(`scripts/r10_credit_diag.py`):

- **52% of the 135 source sessions are "dead"** — they contain no quest-stage
  advance at all — and **49% of all action turns occur in those dead sessions.**
  Only **4.0% / 6.9% / 11.6%** of action turns are followed by an advance within
  5 / 10 / 20 turns. Uniform-weight SFT (cross-entropy over every turn) treats
  all of this as equally worth imitating.
- The credit is concentrated in the rare verbs. **`interact_npc` is the
  highest-credit tool** — 36% of its calls precede an advance within 5 turns,
  71% occur in advancing sessions — yet it is only **3.8%** of the corpus.
  `query_quest` is similar. By contrast **`attack` is 25.5% of the corpus with
  ~0% near-term credit**, and `navigate` (30.3% of the corpus) carries only ~3%.
- Leveling, where it matters, runs through `gather` (Lumberjacking gates
  Foresting), not combat: `gather` appears in 70% of advancing sessions despite
  modest per-call credit.

So the corpus is dominated by low-credit kinetic action, and uniform imitation
amplifies precisely the wrong marginal — over-weighting movement and combat,
under-weighting the dialogue that actually advances quests.

## 6. Why SFT regressed: four contributing mechanisms

The regression is over-determined; four documented effects compound:

1. **Compounding off-policy error.** Behavioral cloning incurs error that grows
   super-linearly with horizon (O(εT²)): the student reaches states the teacher
   never demonstrated and has no signal to correct course. Long-horizon RPG play
   — hundreds of dependent steps — is a worst case. (Ross, Gordon & Bagnell,
   *DAgger*, AISTATS 2011, arXiv:1011.0686.)
2. **Capacity-gap distortion.** A student much smaller than its teacher fits the
   teacher's *marginal* rather than its *function*; distillation fidelity stays
   poor even with capacity to spare. (Stanton et al., *Does Knowledge
   Distillation Really Work?*, NeurIPS 2021, arXiv:2106.05945.) The optimal
   teacher scales roughly linearly with the student, so a frontier-model →
   9B gap sits far past that optimum. (*Towards the Law of Capacity Gap*,
   arXiv:2311.07052.) The §4 fingerprint — marginal-matching — is the predicted
   symptom.
3. **Diversity collapse from cross-entropy SFT.** CE over-concentrates on
   observed tokens and reduces output diversity, which an entropy-preserving
   objective can mitigate. (GEM, *Preserving Diversity in SFT*,
   arXiv:2408.16673.)
4. **Capability suppression via skewed implicit inference.** Fine-tuning skews a
   model's implicit inference of *which task it is doing* toward the fine-tuning
   distribution, suppressing capabilities the base model had. (Kotha, Springer &
   Raghunathan, *Understanding Catastrophic Forgetting via Implicit Inference*,
   ICLR 2024, arXiv:2309.10105.) A direct analog: domain-specialized SFT drove a
   prover's function-calling accuracy from 89.4% to ~0%, and 100 targeted
   agentic traces restored it to 83.8% on a tool-use benchmark. (*Awakening the
   Sleeping Agent*, arXiv:2604.08388.) The 5.6× suppression of `interact_npc` is
   the same phenomenon.

The broader picture is consistent with the finding that SFT tends to memorize
while reinforcement learning generalizes (Chu et al., ICML 2025,
arXiv:2501.17161), and that RL sharpens a base model's existing distribution
rather than expanding it (Yue et al., NeurIPS 2025, arXiv:2504.13837) — which is
why the base model, with its prior intact, beats a student that has been pushed
onto a narrow corpus marginal.

## 7. The r11 plan

*(Superseded: this section's Phase-B plan — 9B self-distillation toward base+scaffold — was not executed. The OPD lane moved to a 4B-teacher → base-2B student capability-instillation test (rounds 1–3, base 12 → 15 → 18/30). See [opd-2b.md](opd-2b.md). The Phase-A SFT-substrate ideas below remain as historical record.)*

The objective is first to *recover* base-level performance, then to exceed it.

**Phase A — fix the SFT substrate (same data, sub-day turnaround).**
- *Trajectory filtering (RAFT-style):* drop the 52% dead sessions and
  low-credit tails before training, removing the bad marginal at its source.
- *Credit reweighting:* weight surviving records by hindsight advance, raising
  the loss contribution of `interact_npc` / `query_quest` / `gather`-to-advance
  turns and lowering combat/idle movement. Reweighting is applied after the
  session-level train/val split, on the training set only.
- *Deferred:* an entropy-preserving loss (GEM) and function-masking are loss/
  interface changes rather than data changes, and are higher-risk; revisit only
  if Phase A is insufficient.

**Phase B — on-policy distillation from the base model.** Train on the
student's own rollouts with dense per-token feedback from a teacher, the field's
consensus remedy for SFT regression (Thinking Machines, *On-Policy
Distillation*, 2025; their case study recovered an SFT-induced instruction-
following drop from 45 back to 83). On-policy distillation needs teacher
logprobs on the student's states; using **our own served base Qwen3.5-9B as the
teacher** makes those logprobs available. This is the off-policy-then-on-policy
("strong-to-weak") recipe from the Qwen3 report (arXiv:2505.09388).

**Beyond r11.** Reinforcement learning with verifiable rewards / self-play is
gated on first having a non-regressing student. Two orthogonal candidates
address structural limits independent of the above: widening the 3-turn training
window (§8) and adding a cross-session memory channel.

## 8. Limitations and threats to validity

- **The 3-turn training window is a structural ceiling on long-horizon play.**
  The model never sees the 50–150-turn arcs the teacher demonstrates, so
  strategy that requires remembering an earlier attempt is absent from training.
  Longer runtime context cannot help a model not trained to use it.
- **~33% of multi-turn records are dropped at the 16K truncation gate.** Long
  observation payloads make long windows likelier to drop, so the surviving
  corpus may be biased toward shorter, more tactical content. Unaudited.
- **`Session #N` drifts out of distribution at runtime** (trained on 1–16, runs
  reach 300+). Likely benign — the bootstrap text differs by 1–2 tokens — but it
  is the one place runtime escapes the training distribution.
- **The base eval is partly a format eval.** Tool-call format is supplied to the
  base model by the chat template each turn, whereas the fine-tune learned it.
  Both reach ~100% format compliance, so the measured gap is mostly a policy
  gap — but this makes the base a fair, possibly conservative, floor.
- **No cross-episode memory in eval.** Each episode resets state, which is
  correct for clean per-episode metrics but measures single-character lifespans,
  not learning over time.
- **Teacher / model conflation.** The corpus is one teacher (Sonnet via Claude
  Code) and the comparator is the base of the student model. The §4 fingerprint
  is the strongest evidence the regression is distillation-induced rather than a
  generic "teacher ≠ base model" effect, but the variables are not fully
  isolated.
- **Small n** (≤5 per arm), by design (§3).

## References

- Ross, Gordon & Bagnell. *A Reduction of Imitation Learning and Structured Prediction (DAgger).* AISTATS 2011. arXiv:1011.0686
- Stanton et al. *Does Knowledge Distillation Really Work?* NeurIPS 2021. arXiv:2106.05945
- *Towards the Law of Capacity Gap in Distilling Language Models.* arXiv:2311.07052
- *Preserving Diversity in Supervised Fine-Tuning (GEM).* arXiv:2408.16673
- Kotha, Springer & Raghunathan. *Understanding Catastrophic Forgetting via Implicit Inference.* ICLR 2024. arXiv:2309.10105
- *Awakening the Sleeping Agent.* arXiv:2604.08388
- Chu et al. *SFT Memorizes, RL Generalizes.* ICML 2025. arXiv:2501.17161
- Yue et al. *Does RL Really Incentivize Reasoning Beyond the Base Model?* NeurIPS 2025. arXiv:2504.13837
- Thinking Machines Lab. *On-Policy Distillation.* 2025. thinkingmachines.ai/blog/on-policy-distillation/
- Qwen3 Technical Report (strong-to-weak distillation). arXiv:2505.09388

## Appendix A — reproduction

```
$ python3 scripts/r10_stats.py
Per-run Core-3 stages  base (n=4): [7, 7, 7, 7]   sft (n=3): [3, 1, 2]   ratio=3.50x
Mann-Whitney U (one-sided): exact p = 0.0286   (asymptotic p = 0.0160; ties trigger the approximation under method='auto')
Foresting completion base 8/12 (67%) sft 1/9 (11%)  Fisher p=0.0159 OR=16.0  drop 6.0x
Completionist tool-mix: interact_npc 2.4/2.1/10.8, query_quest 2.2/2.5/11.2, navigate 27.7/26.4/5.5, observe 41.2/33.0/43.4  (corpus/SFT/base)
```

- `scripts/r10_stats.py` — stage vectors, Mann-Whitney (exact), Fisher, tool-mix
  fingerprint; re-derived from logs. Add run IDs to `BASE`/`SFT` to extend to
  n=5/5 (exact floor → 0.004).
- `scripts/r10_credit_diag.py` — §5 hindsight-credit diagnostic.
- Pipeline: `convert_to_qwen.py`, `finetune/train_modal.py`,
  `finetune/serve_modal.py`, `finetune/serve_modal_base.py`, `play_qwen.py`.
  Dataset provenance: `dataset/qwen_sft/metadata.json`.
