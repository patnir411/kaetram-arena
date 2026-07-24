# Reproducible local unit tests

This repository has a clean-clone bootstrap for the CPU-only `tests/unit`
collection. It recreates the exact Python package versions validated on
2026-07-18, verifies the installed inventory, and runs the complete collection.
It does not reproduce training, GPU inference, live gameplay, or paper results.

## Bootstrap from a clean clone

Requirements:

- a clean Git checkout at the commit being tested;
- CPython 3.12;
- network access to the official Python Package Index; and
- enough space for a small Python environment. No browser binary is downloaded.

Run:

```bash
python3 scripts/bootstrap_unit_tests.py bootstrap --python python3.12
```

The script creates `.venv-unit-tests` and runs `pytest -q tests/unit`. It uses
the direct dependency declaration in `requirements/unit-tests.in` and installs
the complete exact-version closure in `requirements/unit-tests.lock` with
dependency resolution disabled. Pip runs in isolated mode against the official
index and accepts wheels only. The environment is then checked for missing,
changed, or unexpected packages.

The bootstrap deliberately refuses:

- dirty checkouts, preserving uncommitted user work;
- Python versions outside the 3.12 series;
- existing or symlinked environment paths;
- paths outside the repository or names other than `.venv-unit-tests*`; and
- dependency entries that are not exact `name==version` pins.

It never deletes or overwrites an environment. If installation is interrupted,
inspect and manually remove that exact `.venv-unit-tests*` directory or choose a
new allowed name.

To rerun verification and tests without reinstalling:

```bash
python3 scripts/bootstrap_unit_tests.py check
```

`check` requires the bootstrap marker to match the current Git commit, lock-file
digest, Python series, pip version, and installed package inventory.

Pull requests run the same bootstrap on Ubuntu 24.04 with immutable commit pins
for the official checkout and Python-setup actions. The workflow does not cache
or reuse a virtual environment, so every CI run exercises installation from the
reviewed lock before collecting tests.

## Intentional skips and optional assets

The reproducible baseline is the collected CPU-only unit suite, including its
reported skips. Optional tests need assets or services that are intentionally
not installed or contacted by this bootstrap:

- **Qwen tokenizer/model checks:** require a cached
  `unsloth/Qwen3.5-9B` tokenizer and compatible Transformers installation;
- **Torch/collator checks:** require the large Torch/Transformers training
  stack and, for meaningful parity testing, the corresponding model assets;
- **dataset checks:** require locally generated SFT/OPD datasets excluded from
  Git because of size and provenance;
- **OPD replay checks:** require recorded session-log fixtures not published in
  the clean repository; and
- **score endpoint checks:** require explicit `STUDENT_ENDPOINT` and
  `TEACHER_ENDPOINT` live services.

Playwright is installed because MCP modules import its Python API during unit
collection. Chromium and the Kaetram game server are not installed or launched.
Those belong to the separate end-to-end environment, not this local unit-test
contract.

Do not interpret a green local bootstrap as evidence that checkpoints, remote
endpoints, generated datasets, browser gameplay, or GPU kernels are reproducible.
