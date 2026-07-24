# Venue and submission guide — verified July 23, 2026

## Decision

Target **NAACL 2027 through the October 12, 2026 ACL Rolling Review cycle**. Use the current official ACL review template and select **LLM Agents** as the primary area, with **Machine Learning for NLP** secondary.

This is the best realistic path because it gives the project time to run the missing causal and transfer experiments and because ARR explicitly welcomes NLP engineering experiments, analysis, reproduction studies, and negative findings. The official NAACL 2027 site is live, but its venue-specific call still says to stay tuned for details, so recheck it before submission.

ICLR 2027 remains a stretch option only if its official call appears and the entire confirmatory package is frozen by early September. Do not use ICLR 2026 dates or style as though they were 2027 rules.

AAAI-27 and EACL 2027 are no-go: their deadlines arrive before the required experiments and artifact repair can be completed responsibly.

## Confirmed working rules

- NAACL 2027: ARR deadline October 12, 2026 AoE; conference June 1–5, 2027 in San Francisco.
- ARR long paper: eight content pages; unlimited references; required uncounted Limitations section.
- Official ACL template, A4, two columns, two-way anonymized review.
- Anonymous supplementary code and data; no tracking links.
- No simultaneous archival review.
- All authors must complete registration and any assigned reviewing.
- Material LLM writing, coding, literature-search, or idea assistance must be disclosed in the Responsible NLP Checklist during review. Review PDFs must not contain acknowledgments; add the detailed acknowledgment only to the final version.
- The Responsible NLP Checklist may be published with an accepted paper; every answer needs a specific section reference or justification rather than blanket yes/no responses.

Template integrity check on July 23: local `acl.sty` and `acl_natbib.bst`
are byte-identical to official `acl-org/acl-style-files` master commit
`d5adc823ff0f80f98c80405ca0ab66c68e684409`.

Official sources:

- https://2027.naacl.org/
- https://aclrollingreview.org/cfp
- https://aclrollingreview.org/authors
- https://github.com/acl-org/acl-style-files
- https://acl-org.github.io/ACLPUB/formatting.html
- https://www.aclweb.org/adminwiki/index.php/ACL_Policy_on_Publication_Ethics

## Novelty boundary after the July literature audit

TCOD already uses teacher-success prefixes to initialize intermediate states for multi-turn OPD. Guided-OPD changes state occupancy through scheduled teacher turns, ReOPD makes prefix-distribution design explicit, and SCoRe starts training from verified pre-error prefixes. Broad novelty for “starting OPD from an intermediate or failure state” is therefore unavailable.

The candidate contribution is narrower:

> Directly restore witness-certified persistent player states that need not lie on successful teacher trajectories; select them using a frozen student-visitation, teacher-advantage, recoverability, and task-relevance rule; and compare canonical-start execution with registered gameplay-RNG seeds against natural OPD, generic matched resets, TCOD-B2F, and Guided-OPD. This is not a complete-world-state intervention.

Database persistence, typed state, MCP, and game play are implementation properties, not sufficient novelty claims.

## Submission gates

Do not submit unless every gate is green:

1. P0 fixes merged: evaluation correctness, full render/schema parity, immutable manifests, provenance validation, and clean-clone setup.
2. Fresh natural-visitation and targeted-state students trained from the same checkpoint under matched budgets and seeds.
3. Independent run-level replication with a locked power calculation and all runs reported.
4. Full weights × recovery factorial completed with raw pre-rewrite emissions.
5. Corrected-interface SFT, random/progress reset, TCOD-B2F, and Guided-OPD baselines completed.
6. Held-out quest, walkthrough/no-walkthrough, and retention evaluations completed.
7. Copy-prior probe replicated across states, defect families, and at least two teachers.
8. Anonymous clean clone regenerates every table and figure from immutable bundles.
9. Eight-page ACL-format main paper passes anonymity, citation, checklist, font, and PDF checks.

## Internal dates

- August 9: P0 artifact freeze and clean-clone smoke test.
- August 30: weights × recovery factorial complete.
- September 13: matched natural-versus-targeted training complete.
- September 20: SFT, TCOD, held-out, no-walkthrough, and retention baselines complete.
- September 27: statistics, figures, and artifacts frozen.
- October 5: complete anonymous ACL-format draft.
- October 12: submit only if every gate remains green.
