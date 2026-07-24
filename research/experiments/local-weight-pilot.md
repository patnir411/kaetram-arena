# Zero-cost matched-weights pilot

`local-weight-pilot.json` preregisters a nine-cell feasibility pilot:
three public weight snapshots crossed with three paired inference/environment
seeds, one five-minute episode per cell, one completionist prompt identity, and
no recovery intervention. It is not the six-hour confirmatory factorial.

The pilot answers an operational question before longer runs consume local
time: after enforcing one tokenizer and one render contract, do the 2B arms
produce enough valid structured actions to justify a larger experiment? The
registered primary diagnostics are action throughput, zero-turn episodes,
tool-parse rate, API errors, and budget overrun. Quest, XP, and movement values
are exploratory. No pilot outcome can establish model superiority or be pooled
into the confirmatory estimate.

## Clean-clone, zero-cost bootstrap

The launcher and the MLX server intentionally use different pinned Python
environments. From a clean Arena checkout at the reviewed commit, create both
new environments (the bootstrap scripts refuse dirty checkouts and refuse to
reuse an existing environment):

```bash
python3.12 scripts/bootstrap_unit_tests.py bootstrap \
  --venv .venv-unit-tests-pilot
python3.12 scripts/bootstrap_local_mlx.py bootstrap \
  --venv .venv-local-mlx-pilot
EVAL_ENV="$PWD/.venv-unit-tests-pilot"
EVAL_ISO=(
  "$EVAL_ENV/bin/python" -I -S -B -X
  "pycache_prefix=$EVAL_ENV/.kaetram-disabled-pycache"
  "$PWD/scripts/isolated_python_entry.py"
  --repo-root "$PWD" --environment-root "$EVAL_ENV"
)
MLX_ENV="$PWD/.venv-local-mlx-pilot"
MLX_ISO=(
  "$MLX_ENV/bin/python" -I -S -B -X
  "pycache_prefix=$MLX_ENV/.kaetram-disabled-pycache"
  "$PWD/scripts/isolated_python_entry.py"
  --repo-root "$PWD" --environment-root "$MLX_ENV"
)
"${EVAL_ISO[@]}" --module playwright -- install chromium
```

The first environment is the evaluation/launcher runtime and is pinned by
`requirements/unit-tests.lock`; it contains the OpenAI-compatible client, MCP,
and test dependencies. The second is the Apple-silicon model-serving runtime
and is pinned by `requirements/local-mlx.lock`. The MLX bootstrap fails closed
on anything other than macOS arm64 and Python 3.12. Both install only exact
versions listed in the checked-in complete locks from public PyPI, with
dependency resolution disabled. Their markers also hash every
distribution-declared installed file, reject undeclared import-active files,
and bind the active standard-library trees. The reviewed isolated entrypoint
disables `site`, ambient `PYTHON*` controls, user packages, and default bytecode
caches for the launcher, endpoint, MLX-LM backend, evaluation harness, agent,
and MCP game server. It also refuses ignored or untracked repository files
that Python could import ahead of the attested packages. The Playwright
command downloads the Chromium revision selected by the pinned Python package
from Playwright's public CDN. Neither flow uses Modal or a paid endpoint.

Download the three public, revision-locked Hugging Face snapshots. Every file
is size- and content-verified against the checked-in lock, and the receipt
binds the complete snapshot trees:

```bash
"${EVAL_ISO[@]}" --script "$PWD/scripts/fetch_hf_snapshot.py" -- \
  --dest /path/to/kaetram-model-snapshots \
  --snapshot base_2b opd_r2_2b opd_r3_2b \
  --receipt /path/to/kaetram-model-snapshots/fetch-receipt.json
```

The game is a separate MPL-2.0 checkout. The registered environment is the
public Kaetram-Open PR head also mirrored by the project fork:

```bash
git clone https://github.com/barathvelmu/Kaetram-Open.git /path/to/Kaetram-Open
git -C /path/to/Kaetram-Open checkout \
  7a3d722e8e200ca44fd959099386b42a5fbe0cb5
(
  cd /path/to/Kaetram-Open
  corepack yarn install --immutable
  corepack yarn build
)
"${EVAL_ISO[@]}" --script "$PWD/scripts/configure_local_game_env.py" -- \
  --game-dir /path/to/Kaetram-Open
git -C /path/to/Kaetram-Open status --porcelain=v1
```

Use Node 20 for the game; the launcher checks the selected binary and refuses
every other major version. The final command above must print nothing. The
older nine-cell registration does not bind the game commit or built-bundle
digest: its launcher requires a clean game checkout and a self-consistent
build attestation, then records both identities. The later 30-minute recovery
factorial additionally refuses any game commit or built-bundle digest that
differs from its registration. Reproducing the nine-cell pilot therefore
requires the operator to check out the recorded commit above; this limitation
must remain explicit when interpreting that pilot.

Provision the local MongoDB lane from the pinned public image. The host binding
is loopback-only, and the container has no authentication or public listener:

```bash
docker pull \
  mongo@sha256:9bdaeb6dac6e7e762e84e2f84103d1f9bb078fa1ba6bde8bb9d2274f655ad173
docker run --detach --name kaetram-mongo \
  --publish 127.0.0.1:27017:27017 \
  mongo@sha256:9bdaeb6dac6e7e762e84e2f84103d1f9bb078fa1ba6bde8bb9d2274f655ad173
docker exec kaetram-mongo mongosh kaetram_devlopment \
  --quiet --eval 'db.runCommand({ping:1}).ok'
```

The last command must print `1`. If the named container was provisioned
earlier, start that exact container instead of running a second one. Before
creating the output directory or loading a model, the launcher now rechecks
both Python markers and installed-file tree identities, launches and hashes
the pinned Chromium executable, verifies the Mongo image/digest/ping and
loopback port mapping, and seals all receipts into `prelaunch.json`. Each MLX
endpoint independently rechecks its active environment and carries the same
receipt digest in `/health`.

Dry-run validation uses the pinned launcher environment and has no model,
game, database, or output side effects:

```bash
"${EVAL_ISO[@]}" --script "$PWD/scripts/opd/local_weight_pilot.py"
```

Launch requires the exact pilot ID plus explicit local runtimes and artifact
roots. The MLX interpreter must come from the separately verified environment:

```bash
"${EVAL_ISO[@]}" --script "$PWD/scripts/opd/local_weight_pilot.py" -- \
  --launch \
  --confirm local-render-parity-pilot-v1 \
  --output-root /path/to/new/pilot-output \
  --snapshots-root /path/to/kaetram-model-snapshots \
  --game-dir /path/to/Kaetram-Open \
  --mlx-python .venv-local-mlx-pilot/bin/python \
  --node-binary /path/to/node20
```

The launcher refuses dirty Arena or game checkouts, an already-existing output
root, a game build that does not attest the clean game commit, occupied
loopback endpoint ports, a non-Node-20 runtime, endpoint identity drift, or
cross-arm tokenizer/render mismatches. It preflights all endpoints and seals
`prelaunch.json` before the first outcome. Each cell retains its endpoint
receipt, endpoint/evaluation logs, canonical-start and environment-RNG
receipts, raw session evidence, result file, and validity status. A per-cell
artifact inventory hashes every retained file and is itself bound into the
completed inventory. Symlinked evidence is rejected. Failed cells remain in
`completed-inventory.json`; there are no outcome-based exclusions.

The nominal model budget is 45 minutes. Local model loading, game startup, and
in-flight generation can make wall time longer. The launcher uses no Modal,
cloud GPU, or paid endpoint.
