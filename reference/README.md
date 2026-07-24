# Submission artifact boundary

The only current submission-format manuscript is:

- source: `reference/naacl_submission.tex`
- rendered draft: `output/pdf/kaetram-opd-naacl-working-draft.pdf`

`reference/overview.pdf` is a historical technical report. It is not the ACL/ARR
submission artifact and must not be uploaded to a venue. Its historical claims
have not all survived the evidence audit.

## Rebuild and validate

From any directory, run:

```bash
./scripts/build_submission.sh
```

The script builds from the vendored official ACL style files, runs BibTeX and
the required LaTeX passes, rejects undefined citations or references and
overfull boxes, verifies A4 output, and writes the stable submission artifact
to `output/pdf/kaetram-opd-naacl-working-draft.pdf`. Intermediate files remain
under `tmp/pdfs/submission-build/` and are not submission artifacts.
