# Paper 1 submission readiness — July 23, 2026

## Decision

**Do not submit the current manuscript to AAAI-27 or EACL 2027. Target NAACL 2027 through the October 12, 2026 ARR cycle. Keep ICLR 2027 only as a stretch option if its official call appears and the full experiment package is ready by early September.**

AAAI-27 is not the remembered September deadline. Its abstract is due **July 21, 2026** and the full paper is due **July 28, 2026**, both AoE. The main track allows seven content pages. The historical technical report is fourteen pages, the central OPD result has one run per arm, and several headline inputs remain private. The July mechanism scores now have a reviewer-replayable anonymous projection, but that does not repair the causal design. A ten-day conversion would be a rushed lottery ticket, not a serious top-paper attempt.

NAACL 2027 confirms an October 12, 2026 ARR deadline and a June 1–5, 2027
conference in San Francisco. Current ARR rules allow eight content pages for
long papers, with unlimited references; require the ACL template and a dedicated
Limitations section after the conclusion and before references; use two-way
anonymized review; and require the Responsible NLP Research Checklist. An
incorrect or misleading checklist can trigger desk rejection. Ethics and
Limitations are excluded from the content-page limit when placed correctly.
Appendices are optional, must not carry the main argument, and remain
double-column. The venue-specific NAACL call is not yet published, so recheck it
and re-vendor the official template immediately before submission.
As of July 23, the local ACL style and bibliography files are byte-identical to
official `acl-org/acl-style-files` master commit
`d5adc823ff0f80f98c80405ca0ab66c68e684409`; all 27 manuscript citation keys
resolve. The Responsible NLP Checklist may be published with accepted papers,
so the final answers must cite exact sections and disclose material AI
assistance truthfully.

ICLR 2027's call has not been published as of July 18. ICLR 2026 used September 19 for abstracts and September 24 for papers, so a similar late-September 2026 deadline is a planning estimate only. Do not represent it as confirmed.

Official sources:

- [AAAI-27 conference timetable](https://aaai.org/conference/aaai/aaai-27/)
- [AAAI-27 main-track call and seven-page limit](https://aaai.org/conference/aaai/aaai-27/main-technical-track-call/)
- [ICLR 2026 call, including the prior September cadence](https://iclr.cc/Conferences/2026/CallForPapers)
- [ICLR 2026 author guide](https://iclr.cc/Conferences/2026/AuthorGuide)
- [NAACL 2027 official site](https://2027.naacl.org/)
- [ACL Rolling Review call and rules](https://aclrollingreview.org/cfp)
- [ARR reviewer guidelines](https://aclrollingreview.org/reviewerguidelines)
- [Responsible NLP Research Checklist](https://aclrollingreview.org/responsibleNLPresearch/)
- [ACL review-version requirements](https://acl-org.github.io/ACLPUB/review-version.html)
- [ACL formatting requirements](https://acl-org.github.io/ACLPUB/formatting.html)
- [ARR submission form and artifact fields](https://aclrollingreview.org/submissionform)
- [Official ACL style files](https://github.com/acl-org/acl-style-files)

## Working paper

**Working title:** *Auditing State-Visitation Bottlenecks in On-Policy Distillation: A Persistent Game-Agent Case Study*

**Core question:** When a small agent never visits the states where its teacher is more capable, can changing the training-state distribution—without changing the reverse-KL objective—transfer long-horizon competence?

**Primary claim to validate:** A frozen, witness-certified persistent-player-state selector improves canonical-start run-level performance beyond matched generic resets, successful-prefix curricula, and natural OPD. Evaluation loads no intermediate player state but still uses recorded gameplay-RNG seeds. The current paper does not yet make this claim, and the implementation does not restore a complete shared world.

**Secondary diagnostic:** Historical notes report positive teacher-over-student distillation advantage for one malformed continuation. Because both candidates were not scored in the same matched contexts and the probe artifact is absent, preference reversal and the broader teacher-forcing copy-prior mechanism remain unvalidated.

**System conclusion:** Harness affordances and model weights target different
failure classes, but the historical runs do not identify their separate
effects. A recovered July run labelled round-3 with recovery disabled replays
to 18/30, which is counterevidence to an interface-only explanation; missing
checkpoint and launch attestations still prevent a pure-weights attribution.
The recovery-off 12/30-to-15/30 contrast likewise lacks exact historical parity
and is not a clean causal weights comparison.

**Recovered mechanism controls:** All 27 agent/run directories from nine July
arms are copy-verified and content-bound. A clean analysis receipt, immediate
input rehash, and run-start clock-alignment check precede record-level replay,
which reproduces
their six-hour totals `[12,18,12,15,13,14,12,17,14]`. Four intended
corpus/init-matched teacher-graded versus uniform pairs show no consistent
graded advantage, while a repaired-render graded sensitivity reverses the
nearest round-1 direction. This materially strengthens the corpus/visitation
hypothesis but rules out a blanket “grades add no value” claim. One training
run per cell and missing checkpoint/corpus/reset/render/seed receipts still
block an effect estimate. A checked-in anonymous projection of 21,524
score-relevant observations reproduces all nine totals from a clean checkout
and binds rows to source-record/log hashes; full transcripts remain private, so
the extraction itself is not independently repeatable.

**New registered diagnostic:** The 18-cell, 30-minute local factorial completed
with 18/18 launcher-valid cells and 1,266 rehashed files. Four cells advanced
one quest stage, but none of 958 generations was malformed or
recovery-eligible. The recovery intervention therefore never engaged. This is
useful negative evidence, not a recovery effect. The registered follow-up then
completed 1,200/1,200 local requests with zero failures. On the fixed
20-state grid, native schema exposure increased content-only recoverable-call
incidence by 30, 22.5, and 15 percentage points for Base, round 2, and round 3;
documentation effects were mixed. A second audit found that the five nominal
request seeds replayed the same semantic output in every state-condition group,
so the paper collapses duplicates to 20 outputs per cell and makes no
stochastic-uncertainty claim. The full identity-scrubbed raw bundle is checked
in and passes producer, independent-outcome, and seed-diversity verification.
This strengthens the interface audit but still does not satisfy the recovery or
training-effect identification gates.

Do not frame the paper as the first MCP game agent, embodied learning, continual learning, autonomous skill learning, or world-model learning. Orak already uses MCP across twelve games, and the present system operates on symbolic state and typed actions with procedural walkthroughs.

## Current go/no-go gates

The paper moves from **no-go** to **submission candidate** only when all of these are true:

1. The harness, game revision, prompts, tool schemas, model revisions, checkpoints, and datasets are frozen and hashed.
2. The dedicated DB lane and internal-key/display-name Core-3 scoring bugs are fixed, regression-tested, deployed, and attested in a clean live run.
3. A clean clone reproduces every main table and figure from preserved artifacts.
4. Round two's reported recovery-off wall passage is replicated under an exact frozen configuration with the run as the independent unit.
5. Training-state seeding is isolated from other round-to-round changes.
6. Tool-recovery is crossed with weights so the interface and training effects are identifiable.
7. The main result transfers to a held-out quest or second environment.
8. At least one serious OPD baseline is run under the same harness and budget.
9. The manuscript is anonymous, within the venue page limit, and includes reproducibility, ethics, and required LLM-use disclosure.
10. The Responsible NLP Research Checklist is answered from evidence, every
    author consents to review obligations, software/data artifacts fit the
    submission form's size and licensing constraints, and the PDF contains no
    prompt-injection text.

If gates 3–8 are not complete before the October 12 ARR deadline, use TMLR as
the rolling fallback or wait for a later conference rather than submit an
underpowered case study.

## Submission policy

The earlier plan to submit simultaneously to ICLR and NeurIPS is invalid. ICLR prohibits parallel submission of substantially similar work to another archival venue. An arXiv posting alongside one archival submission is allowed. Submit to one conference at a time.

The authors must also retain full responsibility for the paper and follow the chosen venue's AI-assistance policy. The ICLR 2026 guide required disclosure when an LLM made a significant contribution to research ideation or writing. A natural writing style is desirable; hiding material assistance is not. For ARR, preserve the evidence behind every checklist response, keep review PDFs anonymous and line-numbered, and exclude acknowledgments from the review version.

## Eight-week critical path

### Week 1: freeze and repair

- Freeze the exact environment and inference contract.
- Fix the evaluation save crash and full-schema parity tests.
- Package the remaining recovered r10 and OPD inputs into a licensed,
  reviewer-accessible, content-addressed artifact. The July score projection is
  already checked in; seek a full-transcript release or independent extraction
  audit if licensing and privacy permit.
- Choose one primary endpoint and write the analysis plan before new runs.

### Weeks 2–3: replicate the key causal result

- Re-run matched base and round-two weights under the identical frozen harness.
- Use independent environment/inference seeds and aggregate at the run level.
- Preserve every raw log, manifest, and result as an immutable run bundle.

### Weeks 3–4: identify the lever

- Train otherwise matched OPD arms with and without environment-state seeding.
- Evaluate all arms from the canonical initial player state with registered gameplay-RNG seeds.
- Cross round-two/round-three weights with tool recovery off/on.

### Weeks 4–5: test generalization

- Add one held-out quest that is not used for state seeding.
- Run the no-walkthrough ablation.
- If feasible, port the method to one standard textual/tool-use environment.

### Weeks 5–6: baselines and mechanism

- Compare against plain GKD/OPD and a temporal or state-curriculum baseline.
- Measure action probabilities on matched states rather than relying on aggregate tool frequencies.
- Run a controlled clean-history versus malformed-history teacher-scoring study.

### Weeks 6–7: write the conference paper

- Replace protocol-heavy space in the ACL-format draft with executed results,
  run-level uncertainty, mechanism tests, and held-out transfer.
- Move engineering history and exhaustive failure logs to the appendix.
- Lead with one causal result, one mechanism, and one generalization result.

### Week 8: adversarial review

- Re-run every table from the release artifact.
- Perform an internal reviewer pass for novelty, statistics, confounds, and anonymity.
- Submit only if the go/no-go gates above remain satisfied.

## Recommended eight-content-page ACL structure

1. Introduction and three contributions — 1 page
2. Related work and novelty boundary — 0.75 page
3. Persistent-agent setting and benchmark — 1 page
4. Visitation-corrected OPD method — 1.5 pages
5. Experimental protocol and preregistered endpoints — 1 page
6. Main replicated results and ablations — 2 pages
7. Copy-prior mechanism — 1 page
8. Limitations, reproducibility, conclusion — 0.75 page

The r1–r10 development history belongs in a compact ablation table and appendix, not as the spine of the main paper.
