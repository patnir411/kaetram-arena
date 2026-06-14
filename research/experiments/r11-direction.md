# r11 Direction: scaffold ceiling, harness-enforcement, and the harness+weights co-evolution thesis

**Status:** strategic synthesis, June 5 2026. Companion to [r11-probing.md](r11-probing.md). Answers
one question objectively: **are we at diminishing returns on scaffold-tuning, and should we keep
doing it?** Triangulated from three sources — our own run curve, our harness architecture, and the
June-2026 agent-harness literature.

> **Update (June 10 2026):** the thesis held, but the OPD instantiation below moved off 9B. The 27B
> check confirmed capacity-isn't-the-lever going up ([r11-27b-sanity-check.md](r11-27b-sanity-check.md)),
> the size ladder showed the scaffold holds going down (4B 17/30, 2B 12–13/30), and the r10-repair
> lane was parked — recovering r10 to ≈base+scaffold level was judged feasible but expensive and
> uninteresting ("why not just use base"). OPD now runs **4B-teacher → base-2B student** as a clean
> capability-instillation test. See [opd-2b.md](opd-2b.md).

## Where r11 sits

r11 = **on-policy distillation (reverse-KL, under the scaffold), straight from the instruct
student — no SFT init.** The **teacher is a scaffolded larger same-family Qwen itself**;
scaffold-tuning's job is therefore to raise the teacher ceiling. The Claude reference runs
demonstrate full Core 3 *including Rick's Roll* (only in 6h runs, see
[claude-core3-completion-reference.md](claude-core3-completion-reference.md)) and serve as the
behavioral target documentation — the walls past the teacher's ceiling are harness levers.

## The empirical returns curve (Core-3 stages /30, base-Qwen + scaffold)

Best-run envelope over the iteration sequence (variance ±2–3/30 run-to-run; metric = new stages
reached, `analyze.py metrics` summed across the 3 archetype agents):

```
4 → 9–10 (05-28/29) → 12 (06-01) → [8 regress, state-shape bug] → 15 (06-02) → 19 (06-03) → 16 (06-04)
```

| Fact | Evidence |
|---|---|
| Gains came from **harness/state-contract**, not prompt wording | +7 jump = `normalize_quest_lists`/`current_step` refactor; +4 = continued state-contract work |
| **Prompt-text is spent** | ~4 survival-rule rewrites moved nothing; survival non-adoption persists across 3 runs |
| The remaining **12/30 is all Rick's Roll** | 0/21 agent-runs ever passed Rick's stage 1; Foresting is a solved floor (21/21) |
| Rick's wall is **environmental**, not wording | Door (379,388) → L76–L118 seaside; e2e-verified June 4 (`tests/e2e/mcp/test_ricksroll_mechanics.py`) |

**Verdict on the curve:** still climbing on the *causal* steps (+7, +4 — not decaying), but the
headroom is now gated on a non-prompt blocker — the harness/state-contract changes drove every gain,
not the wording.

## Scaffold provenance + design rules

The scaffold (May 28–Jun 4) rested on one diagnosis: base-Qwen's Herbalist's stall was **not** a
no-memory problem — it reads its persisted progress every session but fails to *trust/continue* from
it (re-planned from stage 0; nothing computed `remaining = needed − held`). The fix: push
re-derivation into deterministic tool computation surfaced as **advisory facts the agent verifies,
never an oracle** (`current_step`) — and the validated leverage ordering became
**harness/state-contract > prompt-text**. Two standing constraints: stay at **16K** (SFT was
trained/gated there; raising it is a labeled distribution-shift experiment) and preserve train/eval
parity on any observe/tool change. **Deferred r11 eval protocol:** held-out quest, no-knowledge
ablation, `current_step`-disabled ablation, same-state teacher/student next-action comparison,
per-axis metrics.

## The localized knowing-doing gap (the key harness finding)

Our harness already has a strong **enforced-affordance** layer that works: `attack` auto-loots,
`respawn` auto-warps out of the spawn dungeon, `buy_item`/`craft_item`/`interact_npc` auto-walk,
`navigate` snaps-to-walkable + BFS + auto-reroutes + clears aggro. The agent reliably benefits from
all of these because it does not have to *choose* them.

**Failures concentrate exactly at the advisory/enforced boundary.** Everything still blocked is
*advisory* — text the model may ignore:

| Blocker | Layer today | Signature |
|---|---|---|
| Survival non-adoption (dominant; caps completionist/explorer at Herbalist 2/3) | advisory (no auto-eat; model must decide) | 29 deaths, 2–4 attacks/run; *quotes* "grind to survive", never grinds |
| Never-cook / frozen shrimp count (grinder Rick's) | advisory sequencing | fishes to 3 rawshrimp, **0 `craft_item` calls**, never reaches 5 |
| Re-derivation tax | advisory note | 40–56% of calls are observe+query_quest re-deriving known facts |
| Respawn-dungeon standing / navigate dead-ends | advisory (`_enrich_location` text) | dismisses the warp hint; `navigate` not blocked from failing |

This is the knowing-doing gap, localized to a boundary we control: **move the right behaviors from
advisory → enforced.** The literature confirms this gap is a *capability* limit of small instruct
models in long-horizon agentic settings, not an instruction-clarity problem
([The Instruction Gap](https://hf.co/papers/2601.03269);
[Many-Tier Instruction Hierarchy](https://hf.co/papers/2604.09443);
[When Models Can't Follow](https://hf.co/papers/2510.18892)).

## June-2026 agent-harness literature (the external validation)

The field converged — in the ~6 weeks before this writing — on exactly this move:

| Paper | Finding relevant to us |
|---|---|
| **From Model Scaling to System Scaling** ([2605.26112](https://hf.co/papers/2605.26112)) | Progress now comes from *scaling the harness* (execution/memory/context/skill-routing/verification), not model capacity |
| **Adapting the Interface, Not the Model / Life-Harness** ([2605.22166](https://arxiv.org/abs/2605.22166)) | Turn each recurring failure into a rule/skill/validator/monitor at the model-env interface. **+88.5% rel., 116/126 settings, 18 backbones, frozen models.** Our blueprint. |
| **Continual Harness** ([2605.09998](https://arxiv.org/abs/2605.09998)) | Automated harness self-improvement recovers a *majority of the gap to a hand-engineered expert harness without weights*; its optional teacher-relabel→weight-update co-learning loop **is literally r11** |
| **AutoHarness** ([2603.03329](https://arxiv.org/pdf/2603.03329)), Sentinel | Affordance *enforcement* (prevent illegal/lethal moves; verify-before-commit) works — our refuse-lethal-attack / refuse-navigate-into-dungeon pattern |
| **SIA** ([2605.27276](https://hf.co/papers/2605.27276)), **HarnessForge** ([2606.01779](https://hf.co/papers/2606.01779)), **Adaptive Auto-Harness** ([2606.01770](https://hf.co/papers/2606.01770)) | The frontier is *joint harness + weight co-evolution* = "bounded harness pass + r11 together" |
| **Harness Updating Is Not Harness Benefit** ([2605.30621](https://arxiv.org/html/2605.30621)) | **Caution:** not every harness change helps — *measure* each lever's contribution |
| **Don't Just Fine-tune the Agent, Tune the Environment** ([2510.10197](https://hf.co/papers/2510.10197)) | Tuning environment/affordances beats agent fine-tuning for OOD generalization — legitimizes the harness-enforcement category as distinct from prompting |

On the training side, OPD is the cost-effective, setting-matched recipe for a small
long-horizon tool agent: [SOD (small-LM agents OPD)](https://hf.co/papers/2605.07725),
[Privileged-Information Distillation (beats SFT-then-RL, multi-turn)](https://hf.co/papers/2602.04942),
[On-Policy Context Distillation (distill the scaffold into weights)](https://arxiv.org/pdf/2602.12275),
[OPD survey](https://arxiv.org/html/2604.00626v1),
[Thinking Machines OPD](https://thinkingmachines.ai/blog/on-policy-distillation/) (RL-parity at a
fraction of cost). "Privileged information" = our scaffold/walkthrough — distilling it into the
student is a named technique.

## Objective answer

**"Scaffold-tuning" is two activities; the evidence separates them:**

- **Prompt-text tuning (rule wording, personalities, walkthrough prose): diminishing — stop.** Our
  data, our prior research, and the instruction-following literature all agree the binding
  constraint is not wording. No 5th survival-rule rewrite.
- **Harness/affordance tuning (enforcement, auto-actions, surfacing): NOT diminishing — keep, but
  bounded.** Every real gain came from here; the literature endorses it as the highest-ROI lever for
  a frozen model right now.
- **Training (r11 OPD): a weights-level lever** (~$200, sub-day H100 — the then-planned 9B lane;
  the executed 4B→2B lane billed ~$16–33/round in the fast-kernel rounds, but r1 ran ~$111 on a
  broken-kernel 3× retrain — billing-verified, see opd-2b.md Costs). On-policy eval shows the scaffold alone recovers r10 to
  ~base level (12/30) and the student already tracks the teacher (65% agreement) — so OPD *toward
  base+scaffold* is a lever ceiled at the teacher's band; the walls past it (cook, Rick's door) are
  harness levers for the harness pass ([r11-probing.md](r11-probing.md)) — though in the executed
  rounds cook/Rick's-door stayed unsolved (Rick's 0/4 through round 3). The go/no-go is a small OPD
  run measured in play.

## Plan: bounded harness-enforcement pass (in parallel with starting r11)

Four levers, all **additive side-effects** of tools the agent already calls (the proven `auto_loot`
pattern), **env-gated**, and not touching the `observe` structured shape the SFT corpus depends on:

1. **Auto-eat floor in `attack`** (survival) — below ~40% HP with food in inventory, eat
   automatically; report `auto_ate`, mirroring `auto_loot`. `mcp_server/tools/combat.py`. **S.**
2. **Auto-cook in `gather`** scoped to Rick's + rawshrimp (never-cook) — make the missing
   `craft_item` a side-effect of fishing. `mcp_server/tools/gathering.py`. **M.** *Gated on the
   fishing-yield e2e verification first.*
3. **Canonical FACTS in the session-rollover bootstrap** (re-derivation) — reuse
   `_build_current_step`'s `{stage,needed,have,remaining}`. `play_qwen.py:_build_session_note`. **S–M;
   the one real parity caveat** (edits bootstrap text — keep strictly additive).
4. **`navigate` refuses + instructs from the spawn-dungeon box** — convert an ignored advisory into
   an enforced gate. `mcp_server/tools/navigation.py`. **S.**

**Honest exclusions (weights/training, not harness):**
- **Combat-drift** (grinder attacks instead of fishing) — it obeys its personality block *by design*;
  a harness veto would fight the data-factory's intended diversity. Fix in personality or training.
- **Strategic survival patience** ("grind N turns before approaching a lethal node") — auto-eat stops
  the bleeding but does not install the *plan*. This is the clean handoff line to r11.

**Discipline** (from "Harness Updating Is Not Harness Benefit" + our own parity history): every lever
**env-gated** (`KAETRAM_AUTO_EAT`, `KAETRAM_AUTO_COOK`), enabled on base-collection runs first and
**symmetrically in eval** (never serve a finetuned policy a response shape it did not train on),
**measured** (ablate to confirm each helps), and resolve the existing `KAETRAM_OBSERVE_COMPACT`
train/eval asymmetry before touching `observe`.

## The unifying thesis

**r11 is the Continual-Harness co-learning loop, manually orchestrated** (Qwen rollouts under the
scaffold → teacher relabel → OPD). So "bounded harness pass + r11" is not two competing tracks — it
is the single SOTA pattern (harness + weights co-evolution) done by hand. Longer-horizon directions
the field is taking (automated harness adaptation; sub-agents/skills/memory as first-class harness
components) are noted but out of scope for this iteration.
