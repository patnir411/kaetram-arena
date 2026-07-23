# Program state — July 17, 2026 (the consolidated picture)

**What this is:** the single current source of truth for where the OPD research program
stands after the July review + hardening + mechanism + origin campaigns. Supersedes the
narrative framing of [paper-readiness-2026-07-10.md](paper-readiness-2026-07-10.md) §§1–9
(kept for provenance); the experiment ground truth remains
[opd-2b.md](../experiments/opd-2b.md).

---

## 1. The arc, in five acts

1. **June (rounds 1–3):** "OPD took a 2B from 12/30 to 18/30." Claims: env-state seeding
   fixes visitation coupling; a teacher-forcing copy-prior defect; harness+weights
   co-evolution. Paper drafted (`reference/overview.tex`), X threads, HF release.
2. **July 10–13 (reviews):** three independent adversarial passes (six-agent literature
   synthesis; Codex ×3; a 4-reviewer panel) — novelty verdicts, causal-identification gaps,
   invalid statistics, venue strategy (TMLR + SEA; ICLR conditional).
3. **July 11–13 (hardening):** E1/E4/A1–A3/E3′. Killed the "+2 harness stages"
   decomposition (E4: weights carry 18/30 through ~70% spam); recovery = efficiency;
   seeding survived its falsifier (E3′ 12 vs r2 15, KL gates indistinguishable).
4. **July 14–16 (mechanism):** the uniform-advantage matched-pair family (Arm-C, M1, M2,
   M3, M6) + probe-study adoption + 0.8B lane audit. Result: the teacher's grades never
   bought a stage anywhere; competence rides on frontier-state coverage + rollout quality;
   the teacher's real service was indirect (built the executor whose rollouts became the
   curriculum).
5. **July 16–17 (origin + audits):** the defect's complete causal chain (vulnerability =
   method's; exposure/surface/amplification = ours); two full audits — zero claims
   invalidated, pipeline math proven numerically, flagship claim config-scoped; the
   defect-tax asymmetry reopened "do clean grades have positive value?" → the clean-r1
   discriminating arm (eval in flight, run_20260716_215512).

## 2. The findings stack (each line controlled, verified, reproduced)

**F1. Dense teacher grading transfers three separable things** — and a controlled
attribution exists for each:
- *Execution discipline* ← the gradient (M1: uniform on same corpus = near-no-op;
  navigate errors, re-grounding, tempo need the teacher signal).
- *Task competence* ← NOT the grades: state coverage + self-imitation of quality rollouts
  (Arm-C 15=r2 15 with gate −1.1%; E3′; M6 17≈r3 18; four matched pairs: graded 57 vs
  uniform 59 stages).
- *The format defect* ← the gradient under our exposure config (0–76 malformed in uniform
  arms vs 233–685 in every graded arm; two propagation channels — creation from clean
  corpora via the gradient, preservation from contaminated corpora via imitation, M6).
**F2. Teacher-as-curriculum-builder** (the program's deepest single result): M2's fully
teacher-free lineage (base init + base's own seeded rollouts + uniform) = 12/30, wall 0/3 —
because base's seeded rollouts contain few successes (1/3) vs the teacher-disciplined r1
policy's (3/3). The teacher's grades built the executor; the executor's experiences carried
the competence; the grades themselves never did.
**F3. Seeding = a reliability lever, magnitude TBD**: seeded arms 6/6 wall vs
natural-retrained 3/6 observed (defect-adjusted plausibly 6/6 vs ~4/6); its categorical
June framing is dead, its causal role survives (Arm-C/E3′ diagonal), M4 replication decides
the effect size. Honest cost discovered: gate-passed-state seeding taught a "gate already
satisfied" prior (probe forensics).
**F4. Token-level KL is uninformative about capability** — five dissociations in both
directions, including gate −1.1% → +3 stages; the gate also never detected the program's
largest pathology.
**F5. The defect, fully dissected**: copy-prior (~85% endorsement of never-generated
forms; cross-grader general; doc-literal priming quantified at −1.2/−1.5 nats — unreported
in the literature); origin = method-vulnerability × our-exposure (Python doc literals,
tools-block-free gradient contexts where even base leaks 7–9%, silent parser laundering);
five validated fixes; E4/recovery results reframed as session-local containment +
efficiency.
**F6. Environment/infra findings**: scaffold lift 7→19 (defect-independent); size ladder
flat below 2B (base-0.8B 13 ≈ base-2B 12); capacity shows only 2B→4B; five-arm decision
probes (701 matched trials) as the per-decision instrument; r10's marginal-collapse
regression (still the only fully powered stat).

**Open question (decided within hours):** clean-r1 vs M1's 13/30 — whether the defect tax
masked a real positive competence contribution from clean grading (r2+recovery 17 >
Arm-C 15 points that way).

## 3. What the paper now is (center v4)

*Working title direction:* **"What Does Dense Teacher Grading Actually Transfer? A
Controlled Decomposition of On-Policy Distillation in a Long-Horizon Tool Agent."**
Core table: the matched-pair family (5 corpora × {teacher-graded, uniform-twin}). The
narrative: F1's three transfers → F2's curriculum mechanism → F4's gate blindness → F5's
defect anatomy with fixes → honest bands + replication plan. r10 stays the powered
prologue; the environment is the instrument, not the headline.
**Novelty position (survives all three reviews + web verification):** (i) the
uniform-advantage matched-pair methodology for OPD attribution (no precedent found);
(ii) copy-prior/doc-literal grader priming quantified; (iii) teacher-as-curriculum-builder;
(iv) defect two-channel propagation + complete origin chain + validated fixes; (v) the
decision-probe panel. Seeding-for-OPD survives as scoped contribution (cite ReTRy, TCOD,
DoorMan, R3, ReOPD).
**Venues:** SEA @ NeurIPS 2026 short (Aug 29) from current evidence; TMLR full after M4;
ICLR 2027 only with the full 5-seed package (Codex go/no-go §8 list stands, updated: M4
must run the CLEAN config with one legacy-config control seed).

## 4. Asset inventory

**Run registry (arm_stats.py, all reproduced by two independent scorers):** 14 arms —
June: base/r1/r2/r2+rec/r3+rec/4B/9B/27B ladder + r10 era; July: E1, E4, E3′, Arm-C, M1,
M2, M3, M6, clean-r1 (in flight). **Corpora + manifests:** round1/2/3, round1_uniform,
round2_uniform/_noseed/_natuni, round3_uniform, m2_teacherfree, round1_clean — all with
pre-registered constants on the volume. **Checkpoints:** r1/r2/r3 (+HF), all uniform twins,
teacherfree, r1-clean. **Probes:** flip_probe (+cross-grader), cook_grade (+A2′
advantage-level), defect_origin 2×2, decision-probe study (701 trials, branch), teacher_diag.
**Analysis kit:** arm_stats (verified vs r10 published stats; invalid tests demoted),
log_analysis parsers (qwen wire + [format] aware), scoring scripts (dual-validated).
**Docs:** opd-2b.md (experiment record, ~20 dated sections, superseded-markers discipline),
paper-readiness (reviews + go/no-go), this file.

## 5. The gap to close: record vs public materials

Everything public still tells the June story: `reference/overview.tex` (pre-campaign),
README headline ("12→18"), both X threads, HF model cards. The July findings supersede the
June framing in important ways (18/30's decomposition, seeding's scope, the defect's
origin, the teacher's real role). **The rewrite is now the critical-path task** — blocked
only on the clean-r1 readout (hours), which selects between two wordings of the flagship
claim.

## 6. Remaining work, priority-ordered

1. **Clean-r1 readout** (in flight) → final flagship wording; possibly clean-r2 (~$15).
2. **overview.tex full rewrite** to center v4 (+ the 7 reword items from the claim audit,
   HarnessFix + DoorMan/ReOPD citations, corrected stats). Then README + HF cards + a
   follow-up X thread when the user chooses.
3. **SEA 4-page short** (deadline Aug 29).
4. **M4 on the restored e8**: 5 seeds × 3 arms (seeded+graded / seeded+uniform /
   natural+graded), CLEAN config default + one legacy-config graded seed as the
   defect-exposure control; port-clean; pre-registered endpoints. The publication gate.
5. **Codebase release pass** (from the July 10 audit, still open): REPRODUCING.md with the
   claim→run→command table; requirements.txt; extract the probe-named load-bearing modules
   into an `opd/` package; vendor test fixtures; scrub workspace URLs; commit the
   decision-probe data from the branch; prune legacy lanes; CITATION.cff. (Done since the
   audit: parameterized serve, uniform-transform + build flags, arm registry, monitors.)
6. Optional cheap closures: Arm-C+recovery inertness check; 9B-grader retry; spontaneous
   seed-rate probe; M5 (0.8B seeded pair — redesign per M2 toward cross-policy imitation).

## 7. Costs (billing-verified July; historical per opd-2b.md)

June r1–r3: $160 (+$138 scoping). July 11–13 hardening: ~$65. July 15–17 mechanism +
origin + clean-r1: ~$62 projected ($50.21 at last checkpoint, cap $100). Project-to-date
since March: ~$1.35k. The entire July mechanism campaign — nine trained arms, five probe
studies, two audits — cost less than half of one June SFT cycle.
