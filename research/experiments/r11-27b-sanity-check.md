# r11 × 27B sanity check: does a larger same-family model clear the walls for free?

**Status:** one-off sanity test, June 6–7 2026. Companion to [r11-direction.md](r11-direction.md)
and [r11-probing.md](r11-probing.md). Nothing trained — this is a zero-shot capacity probe.
Followed by the downward size ladder (4B 17/30, 2B 12–13/30) and the OPD pivot to
4B-teacher → base-2B student — see [opd-2b.md](opd-2b.md).

## The question

Before spending OPD compute on a *larger* model, check the cheap thing first: does the **r11
scaffold, unchanged, on a bigger same-family model** play distinctly better from the get-go? If a
3× parameter bump cleared the standing r11 blockers on its own, that would reframe the roadmap
(scale, don't scaffold/train). If it doesn't, the r11-direction thesis holds: the walls are
environmental/harness, not capacity.

## Setup

- **Model:** `Qwen/Qwen3.5-27B` — the non-SFT **instruct** model (the 27B analog of the 9B "base"
  lane; "base" = non-SFT, not Qwen's raw `-Base`). Served via `finetune/serve_modal_27b.py`
  (Modal SGLang, 1× H100, `context_length=16384`).
- **Everything else identical to the 9B base lane:** same r11 prompts, same MCP tool surface, same
  3 archetypes (grinder + completionist + explorer-tinkerer), same 16K session gate, same
  `KAETRAM_OBSERVE_COMPACT`, same warm-session rollover. Only the served weights changed.
- **Run:** `run_20260606_205254`, ~4h11m (stopped ~1h short of a full 6h), 3 agents.

## Verdict: no — not distinctly better. Same band, same walls.

**Core-3 stages: 15/30** (grinder 6 + completionist 4 + explorer 5). That sits in the *middle* of
the 9B+scaffold envelope, below its best:

```
9B + scaffold:   4 → 9–10 (05-28/29) → 12 (06-01) → 15 (06-02) → 19 (06-03) → 16 (06-04)
27B + scaffold:  15  ← this run
```

| What carried over from 9B | Evidence (27B run) |
|---|---|
| **Foresting is a solved floor** | 3/3 for all three agents (9/9 of Foresting's share) |
| **Rick's Roll wall is total** | 0/4 for all three; nobody even accepted it — the same environmental gate r11-direction flags |
| **Survivability ceiling unchanged** | completionist & explorer stayed **Level 1** (attack 0–1×), one-shot repeatedly in the L42–54 Herbalist's zone; only the grinder survived that leg, and only because it incidentally combat-leveled to L23 |
| **Advisory/enforced boundary still where it fails** | the agents follow the decision tree, skill-gate logic, warp-after-death and STUCK_CHECK rules well; they fail exactly on the advisory survival/strategy calls — same localization as 9B |

## What the larger model did NOT fix — and one thing it made worse

- **Did not fix:** the Herbalist's→Rick's leg. Best agent (explorer) reached Herbalist's 2/3; the
  Rick's-Roll fishing/seaside gate was never touched. The 12/30 headroom that r11-direction
  attributes to Rick's stays at 0 here too.
- **Made worse — tool-result confabulation.** The 27B intermittently emits tool-call markup the
  serve-side regex can't parse → the turn executes nothing → the model narrates the imagined result
  as fact. Worst observed: completionist `session_67`, **4 executed tools across ~54 reasoning
  turns**, while the narration claimed bluelily pickups and a stage-3 advance that never happened.
  Run-wide this shows as executed-tools/turn = **0.81 (completionist)** vs 0.99 (explorer). This is
  an instruction-tuned model severing itself from ground truth even while `current_step`/`query_quest`
  feed it correct facts — a serving/format-robustness defect, not raw-base sloppiness.
- **One self-inflicted footgun (orthogonal to size):** the grinder dropped its starter `fishingpole`
  via blind slot-index `drop_item` to free a slot for an XP grind, then spent ~30 sessions unable to
  fish for Rick's Roll — its state never surfaced "you no longer own a fishing pole."

## Implication for OPD-at-larger-size

**Don't prioritize a larger-size OPD on the strength of capacity alone.** A 3× same-family bump,
zero-shot under the current scaffold, lands mid-9B-band (15/30), leaves the Rick's-Roll wall at 0,
leaves the survivability ceiling intact, and adds a confabulation failure mode. This is consistent
with — and strengthens — the r11-direction verdict: the binding constraints are
**harness-affordance + weights (co-evolution)**, not model size. The cheap lever (bigger model,
same scaffold) does not move the gated metric. Size may still help *after* the harness-enforcement
and training levers are pulled, but it is not a substitute for them and not the next thing to spend
on.

## Caveats

- **n = 1 run, ~4h not 6h** (best 9B Core-3 numbers, and all Claude Rick's completions, come from
  6h runs). Run-to-run variance on the 9B curve is ±2–3/30, so 15 vs the 19 best is not a clean
  capacity regression — only clearly "not distinctly better."
- Non-SFT and un-probed: no DAgger/agreement probe was run (cf. r11-probing). This measures
  zero-shot scaffold behavior only.
- The 27B is a multimodal VLM driven text-only; tool-call parsing was the only serving wrinkle.

## Repro

```bash
modal deploy finetune/serve_modal_27b.py
./scripts/run-qwen27b.sh 6          # 3 agents, 6h, 16K, labeled kaetram-base-27b
python3 scripts/log_analysis/analyze.py metrics   # Core-3 stages /30
```
