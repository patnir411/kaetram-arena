#!/usr/bin/env python3
"""Apply a frozen persistent player-state curriculum to three Qwen training lanes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, assert_text_not_reserved  # noqa: E402


USERNAMES = ("qwengrinder", "qwencompletionist", "qwenexplorer")


class SeedPlanError(ValueError):
    pass


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def load_selection(path: Path) -> dict[str, Any]:
    try:
        selection = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedPlanError(f"cannot load selection {path}: {exc}") from exc
    if (
        not isinstance(selection, dict)
        or selection.get("schema_version") != "kaetram-target-player-state-selection-v2"
    ):
        raise SeedPlanError("selection schema_version is invalid")
    if not isinstance(selection.get("arms"), dict):
        raise SeedPlanError("selection arms must be an object")
    return selection


def build_seed_plan(selection: dict[str, Any], *, arm: str, batch: int) -> dict[str, Any]:
    if isinstance(batch, bool) or not isinstance(batch, int) or batch < 0:
        raise SeedPlanError("batch must be a nonnegative integer")
    states = selection["arms"].get(arm)
    if not isinstance(states, list):
        raise SeedPlanError(f"unknown selection arm: {arm}")
    start = batch * len(USERNAMES)
    chosen = states[start:start + len(USERNAMES)]
    if len(chosen) != len(USERNAMES):
        raise SeedPlanError(
            f"arm {arm!r} batch {batch} has {len(chosen)} states; three are required"
        )
    registration = selection.get("config", {}).get("held_out_registration")
    if not isinstance(registration, str) or not registration:
        raise SeedPlanError("selection does not record held_out_registration")
    registration_path = Path(registration)
    if not registration_path.is_absolute():
        registration_path = (REPO / registration_path).resolve()
    assignments = []
    for username, state in zip(USERNAMES, chosen, strict=True):
        snapshot = state.get("snapshot")
        if not isinstance(snapshot, dict) or _snapshot_hash(snapshot) != state.get("snapshot_sha256"):
            raise SeedPlanError(f"state {state.get('state_id')} snapshot hash mismatch")
        try:
            assert_text_not_reserved(
                json.dumps(snapshot, sort_keys=True),
                use="training_seed",
                source=f"selection state {state.get('state_id')}",
                path=registration_path,
            )
        except HeldOutGuardError as exc:
            raise SeedPlanError(str(exc)) from exc
        assignments.append({
            "username": username,
            "state_id": state["state_id"],
            "snapshot_sha256": state["snapshot_sha256"],
            "snapshot": snapshot,
        })
    return {
        "schema_version": "kaetram-target-player-state-seed-plan-v2",
        "experiment_id": selection["experiment_id"],
        "arm": arm,
        "batch": batch,
        "assignments": assignments,
    }


def execute_seed_plan(
    plan: dict[str, Any], seed_fn: Callable[..., Any], cleanup_fn: Callable[[str], Any],
) -> list[Any]:
    # Delete all player_* rows first. Upsert-only seeding would otherwise retain
    # stale abilities or any collection omitted by a prior schema version.
    for assignment in plan["assignments"]:
        cleanup_fn(assignment["username"])
    return [
        seed_fn(assignment["username"], **assignment["snapshot"])
        for assignment in plan["assignments"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--batch", type=int, default=0)
    parser.add_argument("--write-plan", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="", help="must equal EXPERIMENT_ID:ARM")
    args = parser.parse_args()
    try:
        plan = build_seed_plan(load_selection(args.selection), arm=args.arm, batch=args.batch)
        rendered = json.dumps(plan, indent=2, sort_keys=True) + "\n"
        print(rendered, end="")
        if args.write_plan:
            if args.write_plan.exists():
                raise SeedPlanError(f"refusing to overwrite seed plan: {args.write_plan}")
            args.write_plan.parent.mkdir(parents=True, exist_ok=True)
            args.write_plan.write_text(rendered)
        if not args.execute:
            print("Preflight passed. Database was not changed.")
            return 0
        expected = f"{plan['experiment_id']}:{plan['arm']}"
        if args.confirm != expected:
            raise SeedPlanError(f"--confirm must exactly equal {expected!r}")
        from tests.e2e.helpers.seed import cleanup_player, seed_player
        execute_seed_plan(plan, seed_player, cleanup_player)
    except SeedPlanError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
