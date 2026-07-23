# Paper readiness: novelty assessment, centering, and cleanup plan (July 10, 2026)

**Status:** synthesis of a six-lane research pass (OPD lineage, game environments,
privileged-info + state-seeding, harness co-evolution, venue/rigor standards, codebase audit)
run July 10, 2026 against the literature as of that date. Supersedes the novelty section of
[contribution.md](contribution.md) (May-era, r10/KTO framing — historical only).
Companion to the paper draft `reference/overview.tex` and the experiment record
[opd-2b.md](../experiments/opd-2b.md).

---

## 1. Per-claim novelty verdicts (critical)

Cross-checked against the 89-page OPD survey (arXiv:2604.00626, full-text parsed — zero
mention of environment resets/seeding), the awesome-on-policy-distillation tracker (~140
entries), and targeted searches. The multi-turn agent-OPD space went from empty (TML's
Oct 2025 "unexplored" caveat) to ~10 papers between April and June 2026 — **the open gaps
below are closing fast; re-run these searches immediately before any submission.**

### Claimable (with required positioning)

1. **Environment-state seeding for on-policy distillation** — no published work joins
   persistent-world state seeding (a DB write: quest stage, skill level, inventory, position)
   with distillation; verified absent from the OPD survey. All OPD-side analogues operate in
   trajectory/token space (SCoRe verified prefixes 2509.14257, MOTAB backtracking 2605.19433,
   TCOD depth curriculum 2604.24005, Guided-OPD teacher turn-injection 2606.15912). The RL
   ancestry is deep and MUST be cited as ancestry: Kakade & Langford 2002 (restart-distribution
   mismatch — the formal statement of our problem), Florensa 1707.05300, Backplay 1807.06919,
   Salimans & Chen 1812.03381, Go-Explore (Nature 2021), R3 (ICML 2024, 2402.05808 — closest
   LLM-era precedent, but text-prefix state + RL reward), RFCL (ICLR 2024, 2405.03379).
   **Nearest neighbor overall: ReTRy (2505.09546)** — reset-distribution repair for privileged
   teacher→student transfer in robotics; differences: their reset states come from teacher
   rollouts (ours from diagnosed failure walls the teacher also can't reach), RL reward vs our
   dense reverse-KL, robotics vs LLM tool agent. Not citing ReTRy is a novelty-review liability.
   Frame as "we connect and instantiate" (Kakade+TCOD+ReTRy in one paragraph), never "we discovered."

2. **The teacher-forcing copy-prior, causally isolated** — the *phenomenon* (teacher endorses
   student degeneracies under teacher forcing) is published as the "flawed prefix trap"
   (survey), "teacher may fail to penalize, and sometimes even reinforce" (Revisiting OPD
   2603.25562 — the closest paper), SAGE-OPD's motivation (2606.19659), KAT's agreement trap
   (2606.09471). But ALL attribute it to OOD-prefix *noise/unreliability*. Our two additions
   are unpublished: (a) the causal isolation — ~85% probability on a continuation the teacher
   NEVER emits generatively, pinned to a structured in-context copy reflex (mechanistic
   substrate: induction heads, Olsson et al. 2022; self-preference bias 2410.21819), i.e. the
   signal is *wrong-signed*, not noisy; (b) the negative result that grading-side suppression
   (flip-probe-verified −1.21 nats) does NOT transfer to generation, while a harness recovery
   affordance does. This pair is mildly adversarial to the entire published fix family
   (TIP/SAGE/top-K support matching — all grading-side), which is what makes it interesting.

3. **Style-without-competence at environment-milestone level** — phenomenon published
   off-policy (Gudibande "False Promise" ICLR 2024) and token-level for OPD (Rethinking OPD
   2604.13016: "progressive alignment on high-probability tokens at student-visited states";
   Kaur 2607.05184: "gradient transfers register rather than capability"; measurement
   methodology: Action Graph Similarity, ACL 2026, 2604.21255 — consider adopting AGS to
   formalize our tool-mix/tempo metrics). Ours is the first *environment-milestone-level*
   demonstration (KL/style converged, zero quest stages) found. Present r1+r2 jointly: r1 the
   symptom, r2 the intervention that flips the outcome — the causal demonstration of the
   visitation bottleneck. The *diagnosis alone* is no longer novel (TCOD, Guided-OPD, survey,
   "Post-Training is About States" 2605.22731 all articulate it).

4. **Teacher behavioral-support hole ("can't grade what it can't do")** — generic teacher
   ceilings are published (MAD-OPD 2605.01347, survey, "Your Teacher Can't Help" 2605.30833 —
   all token-fidelity or depth-decay versions). The *skill-coverage* version — the teacher
   policy never performs an environment skill (cook), so those states get degenerate grades
   regardless of confidence, violating DAgger's queryable-expert assumption (Ross 2011) — is
   unstated in the literature. Worth a named subsection, not a paper.

5. **Defect-level causal attribution, harness vs weights** — the same named defect attacked
   three times weights-side (masking → contained-not-cured; counterfactual grading → regressed
   the emission; plus the r1 origin) and once harness-side (recovery + history rewrite → 98.5%
   self-correction, zero relapse). GEPA (ICLR 2026 Oral, 2507.19457) and EvoTest (ICLR 2026,
   2510.13220) compare scaffold-vs-weights only at aggregate-task level; nobody runs the
   head-to-head at single-defect granularity. Our strongest methodological card.

6. **Environment novelties (supporting, not headline):** first MMORPG whose entire agent
   surface is typed MCP tools (no pixels, no code); first quest-chain stage-scored MMORPG
   benchmark (nearest: PokéAgent's 15-milestone speedrun ladder, 2603.15563); first OPD-in-a-game
   (no OPD paper targets games); archetype-conditioned progression scoring (personas
   exist for fidelity eval and playtesting-diversity — MIMIC 2510.01635 — not progression
   aggregation); a same-release size inversion (27B < 9B) on a quest benchmark (no published
   instance; needs variance control before claiming — cf. Vending-Bench 2502.15840).

### NOT claimable (do not lead with these)

- **"Harness/scaffold beats weights"** — now field consensus: the 2026 wave (Adapting the
  Interface/Life-Harness 2605.22166 — NOTE: Life-Harness is the *system name* in that paper,
  not a fourth paper; our notes double-count it — Continual Harness 2605.09998, Meta-Harness
  2603.28052, Self-Harness 2606.09498, HASE 2607.03935), plus SWE-agent (NeurIPS 2024),
  Starace GAIA 28pp scaffold spread (2606.08529), Claw-SWE-Bench 27.4pp (2606.12344), DGM
  20→50% (2505.22954). Cite the wave, position within it. Honest counterexamples to keep:
  METR elicitation gap (post-training +25pp vs scaffolding +8pp on an agency-tuned model),
  mini-SWE-agent — scaffold lift shrinks as post-training absorbs the interface; our claims
  hold in the small-model/weak-elicitation regime, say so.
- **Visitation-coupling as a diagnosis** — published (TCOD, Guided-OPD, SAGE-OPD, survey).
- **Privileged-information distillation as a mechanism** — π-Distill/OPSD (2602.04942),
  Self-Distilled Reasoner (2601.18734), HDPO (2603.23871), + a dense 2026 hint-conditioned
  cluster. Our round-4 plan is an *instance*; the delta is PI raising a teacher above its own
  action competence for grading, in an embodied env.
- **First typed-tool game agent / first MCP game benchmark** — false (NetPlay 2403.00690,
  TextStarCraft2, TITAN 2509.22170; Orak 2506.03610 uses MCP plumbing across 12 games).
- **"First open-source MMORPG agent platform on Kaetram"** — **AgentWorld (openagents-org,
  Nov 2025) is built directly on Kaetram** (REST API + Python CLI, no paper/benchmark/training).
  MUST cite and differentiate or a reviewer will find it.

### Obligations the paper takes on

- Cite + differentiate: ReTRy, TCOD, Guided-OPD, SAGE-OPD, Revisiting OPD (2603.25562),
  Rethinking OPD (2604.13016), MAD-OPD, SCoRe, π-Distill/OPSD, Self-Distilled Reasoner, HDPO,
  the harness wave (2605.22166, 2605.09998, 2603.28052, 2607.03935), PIPE/interface
  shortcutting (2602.01611), PA-Tool (ACL 2026, 2510.07248), AgentWorld, Orak, PokéAgent,
  BALROG, HeroBench (2508.12782), Qwen3 tech report (strong-to-weak distillation — the
  recipe anchor for 4B→2B).
- **Engage constrained decoding** as the obvious alternative to the recovery affordance.
  Best defense: "The Constraint Tax" (2605.26128) — on small models constrained decoding gets
  100% validity while task accuracy craters (91.5%→48.0% on a 1.5B tool task). Ideally ablate
  (ToolDec/XGrammar-style FSM vs recovery); at minimum argue it.
- **Address the Turnstile hazard** (Amazon Science, July 2026): our rewritten-clean histories
  feed our own OPD corpus; retokenization drift from history rewriting is a documented
  training risk. One paragraph on how token capture handles this.
- Fix citation bugs: Life-Harness ≠ separate paper; all three wave papers are unreviewed
  preprints (cite as arXiv, not venues); Backplay's ICLR acceptance unconfirmed.
- CodeAct (ICML 2024, 2402.01030) argues code-actions > tool calls — rebut: never tested on
  games; typed tools are what enables clean trajectory extraction + small-model format
  compliance (and cite the small-model multi-turn cliff: BFCL v4, Qwen3-1.7B 16.9% multi-turn).

---

## 2. What to center the paper around

**Recommendation: center on the OPD case study (the overview.tex framing is right), sharpened
to three headline contributions, with the environment as supporting infrastructure.**

1. **Seeding the world, not the loss** (r2): environment-state seeding corrects OPD's
   visitation coupling; capability instilled, verified unseeded (3/3 vs 0/3), pure-weights.
2. **The copy-prior** (r1/r3): dense reverse-KL faithfully distills a wrong-signed,
   in-context grader defect; grading-side fixes provably don't reach generation; a harness
   recovery affordance does — defect-level harness/weights attribution.
3. **The support holes** (r1 + Rick's): style-without-competence at milestone level +
   the teacher behavioral-support hole. (r10's marginal-collapse regression stays as the
   motivating prologue — it's our only powered result, p=0.029.)

Why not center the environment/benchmark: benchmark papers now need 13–20 evaluated models
(BALROG 13, Orak 20; VideoGameBench rejected at 7), human+random baselines, and held-out
content; we have 3 archetypes × a handful of Qwen sizes. The environment earns its place as
the *instrument* (first typed-MCP MMORPG, contamination-free by obscurity, ground-truthed
stage metric) inside a findings paper — BALROG's shape (environment + memorable diagnostics),
not MineDojo's (resource trifecta).

Why not center "harness beats weights": the thesis is now consensus (see §1). Our harness
material survives as contribution #2 (defect-level attribution) and as the honest lever
decomposition (+3 weights / +2 harness / +1 within-noise).

**Suggested title direction:** keep "visitation-corrected distillation" front and center,
e.g. *"Seeding the World, Not the Loss: On-Policy Distillation of a Small MMORPG Tool-Use
Agent"* — with the copy-prior and lever-attribution as co-headliners in the abstract.

**One strategic gift for the intro:** DeepMind × Fenris Creations (EVE Online) announced
May 6, 2026 — pixels, offline shards, nothing published. The biggest lab endorsed persistent
MMOs as the long-horizon testbed while leaving the publication space open; our typed-tool
design is the counter-position that exists *now*.

---

## 3. Venue strategy

- **TMLR (rolling, ~2–4 mo)** — recommended archival home. Acceptance = claims supported by
  evidence + audience interest; reviewers may not reject for modest significance; the review
  dynamic is claim-narrowing, which fits n=1–4 with exact tests. Voyager precedent (TMLR 2024).
  J2C certification is free conference upside.
- **SEA workshop @ NeurIPS 2026 (deadline Aug 29, 2026)** — near-term feedback; environment
  design + agent training in scope; non-archival so it doesn't burn the TMLR/ICLR shot.
- **ICLR 2027 (abstract Sept 19 / full Sept 24, 2026)** — only if we add: n≥3 per key arm,
  the harness×weights 2×2 factorial, and one generalization probe (held-out quest). Otherwise
  the single-run + harness-confound reviews are near-mechanical.
- COLM 2026 passed (decisions July 8); COLM 2027 ~Mar 2027 is the fallback. ICBINB (negative
  results, LLM-focused) recurs ~Jan–Feb 2027 — natural home for the r10 story if split out.
- NeurIPS 2026 main passed (May 6).

---

## 4. Experiments to add (priority-ordered)

Statistical framing to adopt everywhere: run = unit of inference; exact tests at run level;
hierarchical bootstrap (archetype → run → stage) as secondary (METR-style); raw-results table
of every run (~15 rows, half a page, disarming); cost + constraint disclosure (cite
2602.07150 + SWE-rebench 5-run norm); "all 4 base runs exactly 7/30" stated explicitly as an
empirical variance bound. Never present 3 runs × 10 stages as n=30.

| # | Experiment | Why | Cost / status |
|---|---|---|---|
| E1 | **Base-2B + recovery control** | Does the +2-stage harness term require distilled weights? Cheapest missing cell; completes the factorial with existing cells (base, r2, r2+rec=17, r3+rec=18) | **One command today** (`--qwen-base 3` + `KAETRAM_TOOL_RECOVERY=1`), ~$6–23 serving, 6h |
| E2 | **Replicate the wall arms** (K=3: base, r2; ideally r3) | Converts 3/3-vs-0/3 (Fisher p=0.05, borderline) into pooled 6/6-vs-0/3+ (p≈0.012) or better; the single change that most upgrades the core claim | Needs a replication driver script; ~6 runs × 6h serving ≈ $50–150 |
| E3 | **Seeding ablation**: rebuild r2 corpus without the seeded slice, retrain, eval | Isolates the seeding lever from "just another round of OPD"; HF r2 records carry `session` fields so the split is recoverable without re-collection | Build+train ~$5–10 (fast kernels) + one 6h eval; needs parameterized serve |
| E4 | **Recovery on/off completion** (r3 weights, recovery OFF) | Completes the 2×2 at the r2→r3 step | One command today |
| E5 | **Constrained-decoding comparison** (grammar-forced tool calls vs recovery, on r2 or r3 weights) | Pre-empts the obvious reviewer alternative; Constraint Tax predicts validity-up/competence-down — either result is a finding | Serving-stack work (SGLang grammar support); medium effort |
| E6 | **Seeding-reliance / leakage check** | HiLL "hint reliance" + PTD-PO zero-spoiler templates: show the unseeded-eval passage isn't a seeding-era artifact (we largely have this — foreground it) plus a mixed-start note (R3/Florensa annealing) | Analysis-only, mostly written |
| E7 | Second wall (different quest/skill gate) seeded + evaluated | Generalization of the seeding claim beyond Herbalist | One collection + round; ~$30–50 |
| E8 | (ICLR-only) held-out quest / harness×weights 2×2 across eras / 27B re-runs with CIs | Main-conference bar | Larger |

Round-4 science (privileged-context grading for Rick's, auto-re-equip, route-carrying session
note) remains the *next-result* track — separable from paper hardening; the paper stands on
rounds 1–3 + E1–E6.

---

## 5. Codebase cleanup plan (from the July 10 audit)

**P0 — correctness/repro (before any public "paper repo" claim):**
1. `REPRODUCING.md` at top level: pinned env (Kaetram-Open commit + KAETRAM_PATCHES applied,
   Node 20, Mongo, Modal); reproduce-tables-from-HF-rollouts path (no GPU); retrain-round-N
   path; full-loop path; **claim → run-ID → HF config → command mapping table**.
2. Pin harness-side deps (`requirements.txt`/`pyproject.toml` frozen from `.venv`; Modal
   images already pinned). Vendor the missing test fixture (`test_opd_probe_replay.py`
   depends on a gitignored session log — fails on fresh clone).
3. Extract the probe-named load-bearing libraries into an `opd/` package:
   `session_replay.py` (ex-opd_probe), `rendering.py` (ex-opd_round1.turn_to_chat),
   canonicalize, seeding — killing the `sys.path.insert` chains. `opd_2b_data.py` currently
   imports from `opd_probe.py`/`opd_round1.py`/`opd_wall_probe.py`; a reviewer deleting
   "probe scripts" breaks the build.
4. Scrub real Modal workspace URLs (opd_2b_data.py docstring, ROUND1_RUNBOOK.md).
5. Unit tests for the two untested cores: `_opd_collator`+`compute_loss` (synthetic 3-record
   batch: clip/advantage/step-weight math) and the data build's holdout/masking on a canned
   session.

**P1 — structure:**
6. `experiments/` with per-arm YAML configs (round1–3, ablation_r2_recovery,
   control_base_recovery, seeding_ablation) + one `scripts/run_arm.py`; records env flags
   (`KAETRAM_TOOL_RECOVERY`, `KAETRAM_OBSERVE_COMPACT`, seeding lane) that `run.meta.json`
   currently doesn't capture. This is also the E1–E4 enabler.
7. One parameterized serve (`finetune/serve_student.py`, MODEL_PATH/app-name via env or
   flags) replacing the four 388-line `serve_modal_2b*` clones; add an HF-weights load path.
8. Replication driver (`scripts/replicate_eval.sh`): K sequential 6h evals per arm, Mongo
   reset between, arms×runs manifest, generalized `r10_stats.py` for the exact tests.

**P2 — pruning (release tree ~27 → ~14 top-level entries):**
- Archive (`legacy/` or tagged branch): r1–r10 SFT lane (extract_turns, convert_to_qwen,
  train_modal, serve_modal{,_base}, eval_harness + eval_compare + eval_offline + run-eval.sh,
  r10_credit_diag), parked 9B lane (opd_modal_data, train_opd_modal), KTO/GRPO stubs +
  dataset/qwen_kto, opd_onpolicy_probe, seed_herbalist_wall (verify restart-agent.sh refs),
  serve_modal_27b, dataset/eval (fix/remove the broken `latest` symlink).
- Delete: gdn_kernel_probe.py, opd_tinker_probe.py (zero refs), world/ + dataset/world_model
  (~27 MB + 2k LOC, deprecated), nanobanana-output/ (133 MB), .codeviz/ (38 MB), empty dirs,
  overview.{aux,log,out}. Local-only: dedupe extracted vs extracted_v2 (byte-identical sizes).
- Keep as provenance: gate transcripts (gitignore exceptions), ROUND1_RUNBOOK.md → move to
  `docs/provenance/` (it's evidence, not instructions).
- `reference/MODAL.md` is stale (documents the 9B lane); refresh or mark historical.
  `research/paper/contribution.md` superseded by this doc — add a banner.
- CITATION.cff + CI (unit suite on push).

**Strengths to preserve** (the audit's words): gate transcripts as provenance,
r10_stats.py's no-hardcoded-numbers philosophy, built-in calibration probes/tripwires,
analyze.py as ground-truth extractor, HF release with matching record counts, the candid
limitations section.

---

## 6. Addendum: Codex (GPT-5.6) adversarial cross-review (same day)

An independent adversarial pass by Codex CLI (gpt-5.6-sol, given the three core docs + its own
web research) converged with §1–§4 on most verdicts (copy-prior probe = most original completed
result; TMLR + SEA viable, ICLR 2027 not yet; seeding first-method claim unsafe) and added
material corrections, all verified:

**Two nearest neighbors our pass missed (both confirmed real):**
- **DoorMan** — "Opening the Sim-to-Real Door for Humanoid Pixel-to-Action Policy Transfer"
  (arXiv:2512.01061): caches mid-stage simulator snapshots and resets into them inside a
  teacher–student pipeline. Resets principally train the privileged *teacher* (RL) and the
  student transfer is DAgger-style — not our method — but it is closer to our *snapshot/DB
  mechanism* than TCOD. Kills any "first environment-state seeding for distillation" wording;
  the safe claim is "an LLM-tool OPD case study using persistent-world milestone resets."
- **ReOPD** — "Multi-Turn On-Policy Distillation with Prefix Replay" (arXiv:2607.04763, July
  2026): names the *prefix trap* as a two-sided student-occupancy vs teacher-reliability
  tradeoff and designs the prefix distribution to control where the teacher is queried — the
  trajectory-space dual of our environment-state seeding. Must-cite; brand new.

**The central causal-identification critique (accepted — changes experiment priority):**
r1→r2 simultaneously changed the visitation distribution (seeded slice), the OPD iteration and
source policy, corpus size, data construction (verbatim emission), masking, and gate logic —
and r1's own policy visited the wall far more than base (58 vs 20 lily attempts), so the wall
passage could partly come from natural visitation growth. **"Seeding caused the wall passage"
is not causally identified by the existing arms.** The missing falsifier is the matched
±seeding ablation, now the top-priority experiment (supersedes the §4 ordering):

- **E3′ (top priority): matched, replicated seeding ablation.** From the identical r1
  checkpoint: Arm A = natural r1-eval records only; Arm B = same + seeded wall slice;
  equalize records/tokens/steps/masking/rendering (resample natural records into Arm A if
  needed); ≥3 training seeds per arm (train is ~$5 each post-kernel-fix); independently reset
  unseeded evals; pre-register wall passage as primary endpoint. If only one extra run is
  affordable: train the natural-only arm first — it is the falsifier.
- E1 (base+recovery) stays: still one command and trivially cheap.
- E2 replication remains valuable but addresses variance, not identification.

**Claim-tightening list (fold into overview.tex; several are analysis-only fixes):**
1. "SFT learned the corpus marginal instead of a state-conditional policy" — marginal
   *imprinting* is shown; marginal-*only* is not (many conditional policies share a marginal).
   Reword, or add state-stratified action probes.
2. "Moved exactly onto the teacher's distribution" — abstract phrasing contradicted by the
   overshoots the paper itself documents; say "tempo exact-matched; several axes moved toward
   or past the teacher."
3. "+3 weights / +2 harness / +1 r3-weights" — don't present single-run contrasts as an
   additive causal decomposition; report the observed cells (r2=15, r2+rec=17, r3+rec=18) and
   note the missing r3-without-recovery cell + possible interactions.
4. "r3 weights buy speed" — currently confounded (r3+recovery vs r2-without-recovery); the
   r2+recovery ablation run (run_20260613_214956) exists, so compute ITS time-to-wall —
   an analysis-only fix with existing data.
5. "A teacher cannot grade what it cannot do" — **RESOLVED July 11 (probe run, claim must be
   reworded)**: `scripts/opd/cook_grade_probe.py` shows the teacher ranks the correct cook call
   above other task verbs at cook states, and the teacher-competent lily CONTROL fails the same
   margin test — action-choice discrimination in per-token grades is weak *globally* (grades
   are ~state-invariant, dominated by the action-frequency/surface prior). Reword to the
   over-determined version (pole gate; no successful cook rollouts to amplify) and use the
   state-invariance finding as new evidence for the structure-not-reasoning grading mechanism.
   Full result: opd-2b.md "Cook-state grading probe".
6. "Only harness recovery cured the defect" — it is the only *attempted* cure; constrained
   decoding / canonical positive SFT / schema token masking are untested (E5 covers the first).
7. Call-level binomial significance — calls within a handful of trajectories are
   autocorrelated; cluster at run/session level, keep call-level intervals descriptive.
8. "Counterfactual grading regressed the emission" — the stopped run indicts the whole r3
   *package* (grading + new seeding + new corpus), not counterfactual grading in isolation;
   scope the sentence.

**Centering adjustment (synthesis of both passes):** keep the OPD case study, but frame the
headline as the *three-boundary mechanistic audit* — (i) occupancy boundary (teacher edge at
unvisited states; seeding as the intervention, causal *after* E3′), (ii) grader boundary
(wrong-signed prefix-conditioned grading = the copy-prior, our most original result),
(iii) capability boundary (teacher skill-coverage null, after the §5 probe) — with
"we invented state seeding" explicitly NOT the claim. Candidate title shape: *"Where On-Policy
Distillation Supervision Breaks: Visitation, Prefix-Conditioned Grading, and Runtime Recovery
in a 2B Tool-Use Agent."*

## 7. Codex round-2 review (2026-07-13, after E1/E4/A1–A3/E3′-gate)

Codex re-reviewed with the hardening results (it ran `arm_stats.py --verify` itself). Verdict:
"the new results improve the paper substantially; TMLR viability improved in every E3′ branch."
Its corrections, all accepted:

**Reframes:**
- E4 claim wording: *weights carry long-horizon competence through an extreme execution-format
  pathology; runtime recovery chiefly converts latent competence into efficient environment
  interaction.* And sharper: the 405 one-per-session recoveries show **session-local correction,
  not a cured policy** — the defect reappears at every fresh context; recovery cures the
  trajectory-level attractor while weights retain the session-opening defect.
- A2 → major result section, **not** headline yet: the probe measures teacher *likelihood*, not
  the OPD advantage (teacher − student); candidate-intrinsic biases may cancel if the student
  shares them. Required before promotion: rerun the analysis on teacher-minus-student
  advantages (needs student /score on the same candidates — cheap). Also: heterogeneous
  candidates, different state sets per condition, "action-frequency prior" is an inference.
  Safe phrasing: "grades were far more sensitive to action identity than to whether the state
  called for cooking or gathering." Do NOT yet say "amplifying seeded successes" (no outcome
  label in OPD; undemonstrated).
- Proposed center (v3): *"OPD transfers three different things on three different timescales:
  behavioral priors, frontier competence, and execution defects — and token-level KL does not
  distinguish them"* (r1 → r2/r3 → A2 → copy-prior → E4 → recovery as the narrative spine).
- E3′ abstract wording pre-drafted for all three outcome branches (3/3 falsifies → recenter on
  iterative coverage; 0/3 secures subject to replication; 1/3 suggestive-only). See transcript.

**Statistics it killed (fix in arm_stats.py + never cite):**
- The monotone-trend permutation p=0.0005 is INVALID (personas are not independently
  randomized; joint shuffle gives ~0.083 at best, and arm order wasn't randomized). Keep the
  4→4→5→6 pattern descriptively; drop the p-value.
- The hierarchical bootstrap CIs on 1-run arms manufacture variation from non-exchangeable
  quests; present raw tables, bootstrap only after real run replication.
- Agent-level Fisher tests are pseudoreplication (personas share checkpoint+harness); the
  base-family pooling also mixes recovery conditions post-hoc and includes E1's suspect
  grinder. Keep counts descriptive: "1 of 6 observed persona-runs," never "rarely"/significant.
- "Six-cell factorial" → "six observed weight×recovery configurations" (one run per cell,
  mixed infrastructure eras).
- Speed contrast: report wall-clock as observed; add hardware-insensitive measures
  (turns-to-wall, executed-calls-to-wall, tokens-to-wall).
- opd-2b.md carried mutually exclusive causal stories (old +2/+1 decomposition at ~L560/L625
  vs the hardening retraction) — superseded markers added 2026-07-13.

**Its single most important remaining experiment (unchanged by E3′ outcome):** the clean
replicated ±seeding study on the post-collision harness — 3 independent training seeds per
arm, contemporaneous same-VM evals, fresh-port assertions, pre-registered wall endpoint. The
publication obstacle is no longer a missing cell; it is replicated, port-clean evidence at the
run/checkpoint level.

## 8. Codex round-3 (2026-07-13, post-E3′ + external 4-reviewer panel): mechanism question + go/no-go

E3′ landed 12/30 wall 1/3 (vs seeded 15/30, 3/3; KL gates indistinguishable). An external
4-reviewer panel raised the **self-imitation threat**: with ~state-invariant teacher grades
(A2), seeded-arm gains may be reset-conditioned self-imitation, not distillation. Codex pass 3
(it read the trainer loss before answering):

**Hypothesis space (use these terms):** teacher-selective distillation / reset-conditioned
self-imitation / marginal teacher regularization / combination. A2 raises #2–3 but does not
distinguish them; the panel OVERSTATED A2 ("state-blind grader" not established — bare-action
teacher likelihood ≠ the teacher-minus-student advantage).

**Discriminators:**
- **Arm-C is decisive** and our uniform-advantages-in-same-trainer design is *preferable* to
  plain SFT for causal isolation — call it **"uniform clipped self-imitation"** (changes only
  the advantage pattern; preserves init/records/behavior-logprobs/clipping/masks/steps).
  Implementation musts: keep the r2 abstention mask; explicit loss mask (masked ≠ zero-adv);
  pre-register c (match OPD's initial full-batch grad norm); report movement from r1-init;
  CE-SFT as secondary baseline only. Outcome→claim table in transcript (≈15/30+3/3 → teacher
  grading unnecessary, recenter on self-imitation; ≈12/30+1/3 while OPD replicates → teacher
  weighting contributes beyond coverage; intermediate → combination, "most realistic").
- **Analysis-C** (success-vs-fail advantage comparison): worth doing, NOT decisive — null ≠
  no selection. Better: initial-gradient contribution per record, stratified by
  verb×frontier×depth, plus advantage-shuffle-within-strata; outcome labels = stage
  transitions/item completion, not stochastic harvest ticks.

**E3′ wording fixed** (in opd-2b.md): "determined" → observed-arm contrast that survives every
Arm-C outcome and never implies teacher generalization.

**HarnessFix (2606.06324, verified real):** must-cite; kills any "first defect-level" absence
claim. Positioning sentence adopted: *we complement HarnessFix's trace-grounded harness
diagnosis/repair with a within-defect longitudinal comparison of weights-side and runtime
interventions; unlike a harness-origin flaw, our defect emerges during OPD, and runtime
canonicalization contains its trajectory-level consequences without removing it from the
policy.* Drop "head-to-head" (unreplicated cells don't earn the term).

**Early-Sept ICLR go/no-go (8 criteria, checkable):** (1) port-clean replicated ±seeding —
NOTE 3 paired seeds give sign-test floor p=0.125; **5 unanimous paired seeds give p≈0.031** —
plan 5; (2) Arm-C resolved (either direction; intermediate single run = no-go until resolved);
(3) actual-advantage A2 (teacher−student, paired contexts, variance decomposition) or demote;
(4) held-out-quest probe with replicated improvement over base AND natural-only (null = TMLR
fine, ICLR no); (5) copy-prior breadth (multiple malformed families/contexts, ideally second
model size); (6) protocol integrity (resolve June port-collision question; auto-capture
VM/ports/PIDs/flags/checkpoint-hash/cutoff in run metadata); (7) run-level stats only;
(8) narrative-consistency scrub (no remnants of: recovery-carried-stages, grading-paralysis,
cured-policy, cook-specific-hole, seeding-proves-teacher-generalization, first-defect-level).
**Odds: submitted today 15–20%; 3-seed package 25–35%; full 5-seed package 40–50%** — the
panel's 30–40% is a conditional projection, not present-tense. TMLR remains the credible
archival target without the full package.

## 9. Post-audit state (2026-07-15) — confirmed set + experiment menu v2

**Confirmed (each n=1 unless noted):** seeding necessary at r2 (E3′); teacher-direction
unnecessary at r2 on stages/wall/efficiency/style (Arm-C + 4-layer deep audit: strategy-level
same-policy, advantage anatomy state-null, style needs no pull); teacher grading's unique
causal product is the malformed-call defect (11% vs 0.2% arm-level; wrong-signed copy-prior
token-level; 0.8B 13→3 collapse; probe specimens); weights carry stages through the defect
(E4), recovery = efficiency + session-local containment; KL-gates uncorrelated with capability
(5 points incl. −1.1%→+3); r10 marginal-imprinting regression (powered); scaffold lift 7→19;
base-0.8B ≈ base-2B (ladder flat below 2B); five-arm decision-probe study adopted
(4B≫0.8B map, teacher reactive pole recovery 11/20, cook 7/20 = reachability, P-C means-ends
negative transfer 2b 7/20 → r3 1/20).

**Experiment menu v2 (priority order):**
- M0 (free): stratified Analysis-C (advantage-shuffle within strata) + P-C collapse forensics
  (why did r3 lose the means-ends pivot — read the 19 failing trials + r3 full-run pivots).
- M1 (~$15–30): **r1-uniform control** — uniform advantages on the round-1 base-rollout
  corpus; the falsifier of the strong "teacher contributed nothing measurable anywhere" form
  (execution-wins attribution: navigate −77% etc.).
- M2 (~$20–35): **teacher-free end-to-end arm** — seed base-2B, collect base seeded rollouts,
  uniform train from base init; closes the upstream causal chain (merged-r1 init + r1-generated
  seeded rollouts currently carry teacher signal into every capable arm). First checkpoint:
  do base seeded rollouts pass the wall at all?
- M3 (~$10–30): natural+uniform cell (completes the 2×2; predicted ≤12).
- M4 (~$150–400, e8): **5-seed × 3-arm replication** (seeded+teacher / seeded+uniform /
  natural+teacher), contemporaneous, port-clean — the publication package (5 unanimous paired
  seeds → sign-test p≈0.031). Subsumes the old ±seeding-only plan.
- M5 (~$50): 0.8B seeded pair (uniform vs 4B-graded, recovery ON) — capacity-conditional
  mechanism question, scored per-run AND on the probe panel.
- M6: r3-uniform (was the 15→18 step also self-imitation? uniform advantages on the r3 corpus).
- M7 (ICLR-only): held-out quest probe; copy-prior breadth (families × model pairs).
Probe-panel data should be committed to main (dataset/probes/baseline_20260701/results.jsonl —
currently branch-only) when the user next curates the branch.

## 10. Sequencing proposal (original — superseded in part by §9)

1. **Week 1:** P0 cleanup (1–5) + E1 + E4 (both one-command) + **kick off E3′ (matched
   ±seeding ablation — the falsifier; train the natural-only arm first)** + start E2
   replication runs (wall-clock-bound, start early).
2. **Week 2:** E3 seeding ablation (needs P1.7 parameterized serve) + E6 analysis + rewrite
   overview.tex related-work/claims against §1 (repositioning pass: add ReTRy, TCOD,
   Guided-OPD, SAGE-OPD, Revisiting/Rethinking OPD, harness wave, AgentWorld, Constraint Tax,
   Turnstile; fix Life-Harness citation; add exact-test + raw-runs-table sections).
3. **Week 3:** SEA workshop version (4-pg short) from the rewritten draft → submit Aug 29;
   continue E2/E5/E7 for the TMLR full version.
4. **Then:** TMLR submission; ICLR 2027 go/no-go decided by whether E2 + the 2×2 + a
   held-out probe are done by mid-September.
