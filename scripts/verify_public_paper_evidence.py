#!/usr/bin/env python3
"""Verify every checked-in raw-evidence bundle used by the paper."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.audit_trigger_incidence_artifact import audit_artifact  # noqa: E402
from scripts.opd.audit_trigger_seed_diversity import audit_seed_diversity  # noqa: E402
from scripts.opd.verify_trigger_incidence_artifact import verify_bundle  # noqa: E402
from scripts.score_july_public_artifact import verify_artifact  # noqa: E402


def verify_public_evidence(repo: Path = REPO) -> dict:
    july = verify_artifact(repo / "research" / "artifacts" / "july-score-replay-v1")
    trigger_root = (
        repo / "research" / "artifacts" / "local-trigger-incidence-v1"
    )
    trigger = verify_bundle(trigger_root)
    independent = audit_artifact(trigger_root)
    seed_diversity = audit_seed_diversity(trigger_root)
    if trigger["artifact_index_sha256"] != independent["artifact_index_sha256"]:
        raise RuntimeError("trigger verifier and independent auditor disagree")
    if (
        seed_diversity["artifact_index_sha256"]
        != trigger["artifact_index_sha256"]
    ):
        raise RuntimeError("seed audit examined a different trigger artifact")
    return {
        "schema_version": "kaetram.public-paper-evidence-verification.v1",
        "july_score_replay": {
            "artifact_manifest_sha256": july["artifact_index"]["manifest_sha256"],
            "observation_count": july["observation_count"],
            "scores_manifest_sha256": july["scores"]["manifest_sha256"],
        },
        "trigger_incidence": {
            **trigger,
            "independent_cell_count": independent["cell_count"],
            "independent_contrast_count": independent["contrast_count"],
            "state_condition_groups": seed_diversity["state_condition_groups"],
            "groups_with_identical_semantic_responses": seed_diversity[
                "groups_with_identical_semantic_responses"
            ],
            "semantic_response_count_after_deduplication": seed_diversity[
                "semantic_response_count_after_within_group_deduplication"
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    print(json.dumps(verify_public_evidence(args.repo_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
