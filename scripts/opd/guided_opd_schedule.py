#!/usr/bin/env python3
"""Emit one deterministic Guided-OPD complete-turn actor decision.

Live rollout collectors call this boundary before every complete actor turn and
preserve the returned object. The probability is derived from training progress
and must remain fixed for all turns in one trajectory. This command performs no
model, game, database, or accelerator action.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.opd.guided_opd_contract import GuidedContractError, make_role_decision  # noqa: E402


def decision_from_cell(
    cell_config_path: str | Path, *, decision_id: str, trajectory_id: str,
    turn_index: int, training_step: int,
) -> dict:
    path = Path(cell_config_path).resolve()
    try:
        cell = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GuidedContractError(f"cannot load cell config {path}: {exc}") from exc
    if not isinstance(cell, dict) or cell.get("schema_version") != "kaetram.matched-training-cell.v1":
        raise GuidedContractError("cell config schema mismatch")
    arm = cell.get("arm")
    if not isinstance(arm, dict) or arm.get("arm_id") != "guided_opd" \
            or arm.get("objective") != "opd":
        raise GuidedContractError("cell config is not a Guided-OPD arm")
    return make_role_decision(
        seed=cell.get("training_seed"),
        decision_id=decision_id,
        trajectory_id=trajectory_id,
        turn_index=turn_index,
        training_step=training_step,
        config=arm.get("guided_annealing"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-config", required=True, type=Path)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--trajectory-id", required=True)
    parser.add_argument("--turn-index", required=True, type=int)
    parser.add_argument("--training-step", required=True, type=int)
    args = parser.parse_args()
    try:
        decision = decision_from_cell(
            args.cell_config,
            decision_id=args.decision_id,
            trajectory_id=args.trajectory_id,
            turn_index=args.turn_index,
            training_step=args.training_step,
        )
    except GuidedContractError as exc:
        parser.error(str(exc))
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
