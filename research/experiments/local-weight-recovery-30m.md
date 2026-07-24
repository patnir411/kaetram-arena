# Local 30-minute weights × recovery exploratory factorial

Status: completed exploratory run; preregistered before launch and unblinded
once after sealed-bundle verification.

This no-paid-endpoint study is the next feasibility tier after the completed
five-minute renderer pilot. It asks whether a longer local horizon exposes any
quest progress or malformed-call recovery events worth carrying into the
six-hour confirmatory design. It is not powered to establish weight or recovery
effects and cannot enter the confirmatory estimate.

## Frozen design

- 3 public 2B weight blobs: Base, OPD R2, and OPD R3.
- Recovery off and on for every weight.
- 3 paired inference/environment seeds.
- 1 completionist episode per cell, 30 minutes each.
- 18 cells and 9 nominal local runtime hours, plus endpoint startup and final
  in-flight-generation overruns.
- Canonical start, canonical 17-tool schema, one locked Base tokenizer, one
  patched render contract, one exact game build, and recovery as the only
  within-weight interface change.
- Schedule order is frozen by `balanced-paired-order-v1`; no outcome-dependent stopping,
  relaunching, or exclusion is permitted.

Within each replicate, the two recovery conditions for a weight are adjacent.
The frozen order is: replicate 1, Base off/on, R2 on/off, R3 off/on; replicate
2, R3 off/on, Base on/off, R2 off/on; replicate 3, R2 on/off, R3 on/off, Base
off/on. Each weight occupies each pair position once. Pair adjacency and this
Latin balance limit long-run time drift but do not remove it. Recovery-on is
first in four of nine pairs (and either first once or twice for each weight);
the paired report retains the order of every contrast.

The completionist identity is used because this tier diagnoses quest-facing
behavior and the historical recovery report. It does not reproduce the
three-personality Core-3 score and must not be presented as that benchmark.

## Primary diagnostics

For every cell, retain and audit:

1. raw endpoint generations;
2. generations with and without a structured call;
3. raw structured calls per observed minute after a shallow registered
   name/property/type validator, plus canonical executed calls per minute;
4. literal logged `API error:` lines;
5. raw malformed emissions, recovery opportunities, attempts, and execution
   outcomes;
6. terminal-chain validity and budget overrun; and
7. exact canonical-start, DB-boundary, endpoint, renderer, game-build, and
   seed attestations.

The episode is the cell-level observation, but the three replicate/seed blocks
are the independent sampling units. The analysis reports all 18 cells and all
raw denominators. For each of the six arms it reports all three values and
descriptive means. For every replicate and weight it reports recovery-on minus
recovery-off for valid calls, call rate, raw structured calls, malformed
emissions, recovered calls, Core-3 and all-quest stages, XP, and unique
positions. It reports all nine contrasts without a superiority test, confidence
interval, pooled confirmatory estimate, or default “pilot passed” threshold.

Cells marked invalid by the launcher remain in the sealed inventory and the
report lists them without imputing outcomes. If a launcher-valid cell fails the
independent provenance, recovery, terminal, canonical-start, raw-emission,
database-boundary, or conservation audit, analysis fails closed rather than
quietly excluding it; no descriptive result is issued until the discrepancy is
resolved.

A malformed emission is raw endpoint content matched by
`canonicalize.is_malformed`. A recovery opportunity is a no-structured-call
raw emission from which `canonicalize.recover_tool_calls` returns at least one
canonical-name call. In recovery-on cells, each candidate is executed once,
the malformed history is replaced by a canonical assistant tool-call turn, and
the result carries the frozen `[format]` correction. A recovered call succeeds
only when its retained tool result is paired and does not decode as an
execution error. Canonical executions must reconcile by tool to raw structured
calls plus recovered calls (on), or raw structured calls alone (off).

Pairing fixes the initial inference and environment seeds, not the realized
trajectory. Once recovery changes an executed action, later observations and
the number and order of random draws may diverge. Recovery also adds correction
text and may mechanically change throughput. Finally, a sequential nine-hour
run remains exposed to thermal, load, and endpoint-startup drift. These are
design limitations, not effects to be attributed to recovery or weights.

The registration pins all three checkpoint digests, tokenizer, chat template,
renderer, resolved system prompt, game revision and server bundle, decoding
parameters, recovery trigger/action/correction contract, and seed derivation.
The launcher additionally requires a clean arena commit containing this
registration and records that exact source commit in the sealed prelaunch
ledger.

## Exploratory game metrics

Report Core-3 and all-quest stage deltas, XP deltas, unique positions, and
action counts for every cell. Any apparent weight or recovery difference is
hypothesis-generating. A zero-progress outcome is informative about horizon and
launcher design, not evidence that the policies are equivalent.

## Claim boundary

This run may establish that the longer local factorial executed with sealed
recovery identity and may expose concrete trajectories or recovery events. It
cannot establish that one checkpoint is better, that recovery improves quest
completion, that OPD caused an effect, or that any result generalizes.

The complete machine-readable registration is
[`local-weight-recovery-30m.json`](local-weight-recovery-30m.json).

## Registered result

All 18 registered cells were launcher-valid. Before outcome parsing, the
integrity-only analyzer rehashed 1,266 sealed files without mismatch and fixed
bundle-index SHA-256
`c6c69bbf3416cf8405c211032b5c1e3fdc250c264325faed69bf618e941aab0e`.
The one-time create-only unblinding then completed successfully.

Across 958 raw generations, 294 contained canonical structured calls and 664
contained no structured call. The run logged no API errors, malformed
emissions, recovery opportunities, recovered calls, or recovery execution
errors. Four of 18 cells advanced one stage in the registered completionist
lane: two Base/recovery-off
cells, one Base/recovery-on cell, and one R3/recovery-off cell. No R2 cell and no
R3/recovery-on cell advanced a stage.

The primary diagnostic is therefore a manipulation-check failure. Because no
generation created a recovery opportunity, the recovery-on arm never applied
its treatment. Paired on-minus-off differences in throughput, position count,
XP, or quest progress are ordinary trajectory variation under an inert switch,
not recovery effects. The three checkpoints are fixed artifacts rather than
independently trained method replicates, and three seed blocks per arm are too
small for a policy-quality claim.

This result argues against spending the full confirmatory budget on the same
unmodified recovery design. A cheaper registered trigger-validation stage
should first demonstrate that the frozen evaluation distribution produces
nonzero eligible defects, or a separate controlled defect-injection study
should test recovery conditional on a known malformed emission. Neither may be
substituted post hoc for natural defect incidence.

Anonymous exact CSVs, a public summary, and clarified paper tables are
preserved under
[`research/results/local-weight-recovery-30m-v1`](../results/local-weight-recovery-30m-v1/).
The complete create-only report, artifact index, receipt, and raw 33 MB bundle
remain local and are not yet a reviewer-accessible release. The evidence tier
remains `legacy_v1_unattested`; the successful post-launch database audit does
not replace a preregistered server-side attestation.

## Post-launch analyzer hardening record

On July 23, 2026, before a `completed-inventory.json` or any cross-arm result
existed, the offline analyzer was tightened to implement the reporting contract
above more literally. The change makes every arm retain its three
replicate-ordered values and descriptive means, independently reconciles the
raw malformed-emission count with the recovery-log audit, and rejects
impossible recovery-error or repeat-recovery totals. Pooled-denominator call
emission rates are labeled separately from arithmetic means of the three cell
rates. Every completed cell receipt, including an invalid receipt, must bind a
successfully rehashed artifact inventory or the complete analysis fails closed.
The completed-ledger receipt must also exactly match the hashed
`cell-status.json`, preventing a later ledger edit from relabeling a
launcher-valid cell as an excluded invalid cell.

At that point only the first cell had sealed. Its artifact inventory and raw
malformed/recovery counts had been checked, but no quest outcome, recovery-on
cell, within-weight pair, arm contrast, or complete factorial report had been
inspected. The schedule, diagnostics, estimands, exclusion rule, and claim
boundary were not changed.

The recovery-on mate of the first cell sealed at 05:02:20 EDT. The first
auditor-requested provenance patch was committed 29 seconds later, before that
cell's results or pair difference were inspected and while no complete
factorial ledger existed. A later code audit—after the two cells' integrity and
raw-call counts were checked, but before a recovery-on quest outcome, pair
difference, arm contrast, or complete ledger was inspected—required exact
agreement between the completed receipt and hashed `cell-status.json`. These
patches only made exclusions and identities auditable and repaired a
production-row field-name mismatch; they did not change a diagnostic or
analysis rule.

## Post-launch database-lane audit

A later hostile audit found that the launcher set `KAETRAM_MONGO_DB` for the
Python reset/snapshot path, while the pinned game server resolved its database
from ignored dotenv files. The running bundle did not exhibit the upstream
#37 mismatch: the registration and live harness both named
`kaetram_devlopment`; the game `.env` also named `kaetram_devlopment`, had
SHA-256 `58a086da1d6de1462f9903d2dab40878aa1e4831114410e07b4a9f7b9f93f614`,
selected `DATABASE=mongodb` through the tracked defaults, explicitly overrode
the tracked `SKIP_DATABASE=true` default with `SKIP_DATABASE=false`, and its
00:25:17 EDT modification time preceded the 04:01:01 EDT launcher start. No
quest outcome or cross-cell contrast was inspected for this check.

This is post-launch filesystem evidence, not a preregistered server-side
attestation. The current bundle therefore remains exploratory and cannot be
promoted to confirmatory evidence on that basis. A subsequent launcher revision
must fail before any cell unless the dotenv-resolved game database equals the
harness database on the registered loopback host and port, require the MongoDB
backend with database skipping disabled and local non-SRV/no-TLS/no-auth
settings, reject ambient dotenv controls, hash every contributing dotenv file
in the prelaunch ledger,
recheck those hashes after game startup, and carry the attestation into result
metadata. The same revision must reject unlocked files, directories, and
symlinks in model snapshot roots, construct runtime views only from locked
paths, reverify after model startup, and attest the complete locked snapshot
tree.
The revision containing this audit implements those gates for future launches;
it does not retroactively upgrade this legacy-v1 bundle.

## Frozen unblinding and export procedure

The complete factorial was unblinded only through the offline analyzer after
`completed-inventory.json` exists. The command requires the exact pilot ID as an
explicit confirmation and a new output directory outside the run root. Before
opening result-bearing artifacts it writes a create-only unblind intent into the
run root and a user-local registry keyed by the sealed bundle identity. A
byte-identical copy on the same account cannot start a second transaction. The
registry is a local procedural guard, not a cross-machine service. A normal
second attempt is refused; an interrupted attempt can resume only with the exact
intent digest, unchanged root path, bundle, analyzer revision, and resolved
output path.

The analyzer rehashes every sealed cell inventory and refuses to release partial
descriptive results: all 18 cells, all three replicates in each of the six arms,
and all nine paired contrasts must be present and valid. A launcher-invalid cell
therefore blocks the registered descriptive export rather than silently changing
an arm denominator. Publication is staged and byte-verified before one atomic
directory rename; an interruption leaves no partial final directory. For a
successful transaction it creates a JSON report,
cell-level CSV, paired-contrast CSV, fixed Markdown table, fixed LaTeX table,
artifact index, an embedded receipt that becomes visible in the same atomic
rename, and matching run-root/user-registry receipts binding the report, analyzer source
inventory captured before imports and rechecked after analysis, clean Git
revision, Python runtime, twice-rehashed sealed inputs, and output hashes.

Before creating an unblind intent, the bundle was checked with:

```bash
python3 scripts/opd/analyze_local_recovery_factorial.py RUN_ROOT \
  --allow-legacy-v1 \
  --integrity-only
```

This path parses only ledgers and artifact inventories, mechanically rehashes
the retained bytes, and does not decode result values.

For this legacy-v1 exploratory bundle, the following primary export was run
exactly once after the launcher exited successfully:

```bash
python3 scripts/opd/analyze_local_recovery_factorial.py RUN_ROOT \
  --allow-legacy-v1 \
  --confirm-unblind local-weights-recovery-30m-v1 \
  --output-dir ANALYSIS_OUTPUT
```

If that transaction is interrupted, use the intent SHA-256 printed or recovered
from `~/.local/state/kaetram-arena/unblind/*.intent.json` (or the equivalent
`XDG_STATE_HOME` path) and repeat the same command with
`--resume-unblind-intent INTENT_SHA256`. Do not delete the intent, staging
directory, or registry entry.

The completed bundle predates the versioned `results.json` schema added by
this revision, so it still requires the affirmative `--allow-legacy-v1` flag and
remains `legacy_v1_unattested`. This post-launch analyzer hardening was completed
without opening a result-bearing artifact and does not change an outcome,
diagnostic, contrast, exclusion, or claim boundary.
