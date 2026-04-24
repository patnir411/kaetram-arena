"""Thin re-export shim so tests ported from KaetramGPU that use
`from bench.seed import ...` resolve cleanly.

The canonical implementation lives at tests/e2e/helpers/seed.py — this
file just re-exports everything from there. Delete this shim after the
ported tests are migrated to import directly from the helpers package.
"""
from __future__ import annotations

# Re-export the public API from the canonical helper.
from tests.e2e.helpers.seed import (  # noqa: F401
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    FIXED_BCRYPT_HASH,
    DEFAULT_PASSWORD,
    ALL_COLLECTIONS,
    NON_STACKABLE_KEYS,
    TUTORIAL_FINISHED_QUEST,
    STARTER_KIT,
    cleanup_player,
    seed_player,
    snapshot_player,
    summarize_snapshot,
)
