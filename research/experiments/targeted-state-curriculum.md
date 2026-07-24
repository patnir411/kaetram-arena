# Reachability-targeted persistent player-state initialization

This protocol tests a narrower claim than generic intermediate-state OPD.
TCOD-B2F already reaches intermediate states by replaying successful teacher
prefixes, while Guided-OPD mixes teacher and student turns. The proposed arm
instead writes a verified persistent **player-state** snapshot directly and
selects it because the natural student rarely reaches its versioned equivalence
class while the teacher has a measured conditional success advantage there.

The database seeder restores player position, health, mana, inventory, bank,
equipment, quest/achievement/skill records, statistics, and explicitly allowed
player-info fields. It does not restore NPC state, resource nodes, shared maps,
other players, or arbitrary server state. Accordingly, this protocol makes no
full-world-state claim.

## Frozen candidate record

Each schema-v2 JSONL candidate records:

- a unique `state_id`, complete player `snapshot`, calculated `progress_bin`,
  calculated `state_equivalence`, and `source_kind`;
- source run IDs used to discover or validate it;
- legal-reachability, internal-consistency, and e2e-seed verification flags,
  each backed by an artifact path and SHA-256 digest;
- hashed trial artifacts and matching counts for natural student visitation,
  teacher success, student success from the state, and recoverability; and
- whether the state is task-relevant and whether it already completes the
  endpoint.

### Executed reachability verification

A declared witness or certificate is insufficient. For each accepted candidate,
the selector executes `scripts/opd/check_player_state_reachability.py`, pinned by
path and SHA-256 in the reviewed config. The checker runs from an isolated
canonical-start player through the existing stdio MCP/game interface. It accepts
only frozen model-visible state-changing tools, records and compares the exact
canonical JSON digest before and after every action, and compares the exact tool
result digest. Any action error or pre-state, result, or post-state divergence is
a hard failure.

The live-replay artifact uses
`checker_protocol: kaetram-live-player-state-replay-v1` and contains the exact
canonical-start and target snapshots, their digests, an initial observation
digest, executable `{tool, arguments}` transitions with expected pre/result/post
digests, and a `runtime` object. The runtime object pins the full harness and
Kaetram game Git object IDs, adapter ID, observation-digest schema, and
persistent-state-digest schema. Both Git worktrees must have no tracked changes.
The selector independently requires the checker result to reproduce that
runtime object and complete trace.

After replay, the adapter closes the player session so Kaetram saves it. It then
compares the replay player's seeded-field database projection against a separate
target player that was seeded and canonicalized through its own cold login/save
cycle. This exact comparison covers the persistent player fields represented by
the candidate; it does not compare or claim full world state. Both temporary
players are removed during cleanup.

Invariant-certificate mode does not execute candidate-supplied code or accept
free-form checks. It performs the same live replay and requires exactly this
allowlist: `canonical_start_loaded`, `runtime_revisions_exact`,
`every_transition_exact`, and `target_persistent_player_state_exact`.

The production adapter deliberately fails offline. A live verification requires
all of the following before selection can succeed:

- `KAETRAM_REACHABILITY_LIVE=1`;
- `KAETRAM_REACHABILITY_USERNAME` beginning with `reachability_`;
- `KAETRAM_MONGO_DB` naming an isolated e2e/test/reachability database;
- explicit `KAETRAM_MONGO_URI`, `KAETRAM_PORT`, and `KAETRAM_CLIENT_URL`
  endpoints (`KAETRAM_PORT=9001`, the data-collection lane, is forbidden);
- `KAETRAM_GAME_REPO` pointing at the exact clean Kaetram checkout; and
- reachable isolated Mongo, game, web-client, browser, and MCP dependencies.

Warm-session mode is forbidden because each verification requires cold,
isolated save boundaries. Missing services, dependencies, revision mismatches,
dirty tracked files, malformed MCP results, or any cleanup/replay exception
produce a nonzero checker exit. Unit and integration-contract tests use an
injected fake adapter to exercise divergence handling; those tests are not live
Kaetram certification. A real candidate still requires a successful live run.

### Provenance-bound repeated trials

Rates are recomputed from boolean outcomes in hashed trial artifacts; free rates
and self-reported counts cannot select a state. Every artifact binds the state
ID, exact snapshot digest, versioned state-equivalence key, policy role and ID,
checkpoint digest, history-constructor ID and revision, horizon, seed for every
trial, and success-definition ID and revision. Teacher and student conditional
success trials must use identical history constructors, horizons, and success
definitions. Counts in the candidate must exactly match the hashed outcomes.

The config sets meaningful minimum denominators (and cannot set any below 20).
Selection uses Wilson confidence intervals at the frozen confidence level:

- visitation uses its upper bound;
- teacher success and recovery use their lower bounds; and
- the conservative teacher advantage is teacher lower bound minus student upper
  bound.

Thus a one-shot success or a point estimate with inadequate uncertainty cannot
pass the combined rule.

### Versioned state equivalence and progress

Natural visitation means entering
`kaetram-persistent-player-state-equivalence-v1`, not reproducing an opaque or
self-declared label. The predicate hashes a canonical projection containing the
32-by-32 position cell plus inventory, bank, equipment, quests, achievements,
and skills. It excludes transient health, mana, statistics, and player-info
overrides. The selector recomputes both the key and the digest of the predicate
specification.

Progress matching uses the separately versioned
`kaetram-persistent-player-progress-v1` projection of quests, achievements, and
skills. Candidate-supplied free-text progress bins are no longer accepted; the
selector recomputes the bin and rejects mismatches.

A snapshot must explicitly cover every field accepted by the database seeder,
so omitted values cannot silently inherit defaults. Field types are validated,
and overrides cannot replace authoritative identity, position, health, or mana
fields. Duplicate player snapshots under different IDs, unsupported database
arguments, missing or digest-mismatched evidence, and held-out-quest leakage are
hard errors. Relative artifact paths are resolved beside the candidate JSONL;
relative checker paths are resolved beside the config.

## Selection and controls

```bash
python3 scripts/opd/select_target_states.py artifacts/state-candidates.jsonl \
  --config research/experiments/targeted-state-selection.example.json \
  --out artifacts/target-player-state-selection.json
```

The output freezes five equal-size arms:

1. combined targeted rule;
2. random valid states;
3. calculated-progress-matched valid states;
4. visitation-deficit only; and
5. teacher-advantage only.

Every control and single-factor ablation is selected outside the frozen targeted
IDs. The selector fails if an arm lacks enough non-target candidates or if a
nominal control arm is identical to the targeted arm. Other partial overlap is
not prohibited and should be reported.

Preflight one three-agent collection batch without touching MongoDB:

```bash
python3 scripts/opd/seed_selected_states.py \
  artifacts/target-player-state-selection.json --arm targeted --batch 0
```

Live seeding additionally requires `--execute` and an exact
`--confirm EXPERIMENT_ID:ARM` interlock. Every live assignment first deletes
that username from every player collection before writing the complete player
snapshot, preventing stale ability or schema-version rows from surviving an
upsert. Seeded states are training-only. Every headline evaluation begins
end-to-end from the original unseeded server state.

## Falsification boundary

The method claim fails if random or progress-matched states tie the targeted
arm, if a successful-prefix curriculum ties it, if the teacher is unreliable
at selected states, or if gains appear only in seeded evaluation. Report the
probability of reaching the versioned player-state equivalence class, crossing
the bottleneck conditional on arrival, and completing downstream after crossing.
