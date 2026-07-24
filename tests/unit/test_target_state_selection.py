from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.seed_selected_states import SeedPlanError, build_seed_plan, execute_seed_plan
from scripts.opd.select_target_states import (
    SelectionError,
    build_selection,
    progress_bin,
    state_equivalence,
)


REPO = Path(__file__).resolve().parents[2]

CHECKER_SOURCE = r'''#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--artifact", required=True)
parser.add_argument("--method", required=True)
parser.add_argument("--canonical-start-sha256", required=True)
parser.add_argument("--target-snapshot-sha256", required=True)
args = parser.parse_args()
path = Path(args.artifact)
artifact = json.loads(path.read_text())
states = artifact["path_state_sha256s"]
assert states[0] == args.canonical_start_sha256
assert states[-1] == args.target_snapshot_sha256
result = {
    "schema_version": "kaetram-player-state-reachability-check-v1",
    "status": "passed",
    "method": args.method,
    "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "canonical_start_sha256": args.canonical_start_sha256,
    "target_snapshot_sha256": args.target_snapshot_sha256,
    "execution_environment": "live-isolated-service",
}
if args.method == "witness_trajectory":
    transitions = artifact["transitions"]
    live_replay = artifact.get("checker_protocol") == "kaetram-live-player-state-replay-v1"
    if live_replay:
        for transition in transitions:
            assert set(transition["action"]) == {"tool", "arguments"}
        result.update({
            "runtime": artifact["runtime"],
            "executed_trace": [dict(index=index, **transition) for index, transition in enumerate(transitions)],
            "final_persistent_player_state": {
                "matches_target": True,
                "actual_sha256": "f" * 64,
                "expected_sha256": "f" * 64,
            },
        })
    else:
        assert len(transitions) == len(states) - 1
        for index, transition in enumerate(transitions):
            assert transition["before_state_sha256"] == states[index]
            assert transition["after_state_sha256"] == states[index + 1]
            assert transition["action"] == "fixture_replay_transition"
    result.update({
        "verification_kind": "transition_replay",
        "replayed_transition_count": len(transitions),
    })
else:
    assert artifact["invariants"]
    result.update({
        "verification_kind": "executed_invariant_checker",
        "checked_invariants": artifact["invariants"],
    })
print(json.dumps(result))
'''


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _config(tmp_path: Path, max_states: int = 2) -> Path:
    checker_path = tmp_path / "fixture_reachability_checker.py"
    checker_path.write_text(CHECKER_SOURCE)
    checker_sha256 = _digest(checker_path.read_bytes())
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "experiment_id": "target-player-test-v2",
        "held_out_registration": str(
            REPO / "research" / "experiments" / "heldout-quest-v2.json"
        ),
        "random_seed": 7,
        "max_states": max_states,
        "confidence_level": 0.95,
        "minimum_trials": {
            "natural_student_rollouts": 200,
            "teacher_trials": 50,
            "student_trials": 50,
            "recovery_trials": 50,
        },
        "reachability_checkers": {
            method: {
                "path": str(checker_path),
                "sha256": checker_sha256,
                "timeout_seconds": 5,
            }
            for method in ("witness_trajectory", "invariant_certificate")
        },
        "thresholds": {
            "max_student_visit_rate": 0.05,
            "min_teacher_success_rate": 0.6,
            "min_teacher_student_success_gap": 0.3,
            "min_recovery_rate": 0.8,
        },
    }))
    return path


def _candidate(
    state_id: str, *, quest_stage: int, position: int,
    student_visits: int = 0, natural_student_rollouts: int = 1000,
    teacher_successes: int = 90, teacher_trials: int = 100,
    student_successes: int = 10, student_trials: int = 100,
    recoveries: int = 98, recovery_trials: int = 100,
) -> dict:
    snapshot = {
        "position": [position, 20],
        "hit_points": 100,
        "mana": 20,
        "inventory": [],
        "bank": [],
        "equipment": [],
        "quests": [{"key": "foresting", "stage": quest_stage}],
        "achievements": [],
        "skills": [],
        "statistics": {},
        "player_info_overrides": {},
    }
    return {
        "schema_version": 2,
        "state_id": state_id,
        "snapshot": snapshot,
        "state_equivalence": state_equivalence(snapshot),
        "progress_bin": progress_bin(snapshot),
        "source_kind": "direct_snapshot",
        "source_run_ids": ["run_source"],
        "validity": {
            "legal_reachable": True,
            "internally_consistent": True,
            "e2e_seed_verified": True,
        },
        "validity_evidence": {
            key: {
                "artifact_path": f"artifacts/validity/{state_id}-{key}.json",
                "artifact_sha256": "pending",
                **({
                    "method": "witness_trajectory",
                    "canonical_start_sha256": "b" * 64,
                } if key == "legal_reachable" else {}),
            }
            for key in ("legal_reachable", "internally_consistent", "e2e_seed_verified")
        },
        "trial_evidence": {
            name: {
                "artifact_path": f"artifacts/trials/{state_id}-{name}.json",
                "artifact_sha256": "pending",
            }
            for name in ("visitation", "teacher_success", "student_success", "recovery")
        },
        "counts": {
            "student_visits": student_visits,
            "natural_student_rollouts": natural_student_rollouts,
            "teacher_successes": teacher_successes,
            "teacher_trials": teacher_trials,
            "student_successes": student_successes,
            "student_trials": student_trials,
            "recoveries": recoveries,
            "recovery_trials": recovery_trials,
        },
        "task_relevant": True,
        "endpoint_already_completed": False,
    }


def _trial_payload(row: dict, name: str) -> bytes:
    specs = {
        "visitation": ("natural_student_visitation", "student", "student_visits", "natural_student_rollouts"),
        "teacher_success": ("conditional_task_success", "teacher", "teacher_successes", "teacher_trials"),
        "student_success": ("conditional_task_success", "student", "student_successes", "student_trials"),
        "recovery": ("player_state_recovery", "student", "recoveries", "recovery_trials"),
    }
    kind, role, numerator_key, denominator_key = specs[name]
    successes = row["counts"][numerator_key]
    trials = row["counts"][denominator_key]
    snapshot_sha256 = _digest(json.dumps(
        row["snapshot"], ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode())
    success_id = "conditional-task-success-v1" if name in {"teacher_success", "student_success"} else f"{kind}-v1"
    return json.dumps({
        "schema_version": "kaetram-player-state-trials-v1",
        "kind": kind,
        "state_id": row["state_id"],
        "snapshot_sha256": snapshot_sha256,
        "state_equivalence": row["state_equivalence"],
        "policy": {
            "role": role,
            "policy_id": f"fixture-{role}-policy",
            "checkpoint_sha256": ("1" if role == "teacher" else "2") * 64,
        },
        "history_constructor": {"id": "fixture-history-v1", "revision": "3" * 64},
        "horizon": {"value": 128, "unit": "turns"},
        "success_definition": {"id": success_id, "revision": "4" * 64},
        "seeds": list(range(trials)),
        "outcomes": [True] * successes + [False] * (trials - successes),
    }, sort_keys=True).encode()


def _candidates(tmp_path: Path, rows: list[dict]) -> Path:
    for row in rows:
        for key, evidence in row["validity_evidence"].items():
            if evidence["artifact_sha256"] != "pending":
                continue
            evidence_path = tmp_path / evidence["artifact_path"]
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            if key == "legal_reachable":
                target_sha256 = _digest(json.dumps(
                    row["snapshot"], ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                ).encode())
                start_sha256 = evidence["canonical_start_sha256"]
                method = evidence.get("method")
                artifact = {
                    "schema_version": 2,
                    "method": method,
                    "canonical_start_sha256": start_sha256,
                    "target_snapshot_sha256": target_sha256,
                    "path_state_sha256s": [start_sha256, target_sha256],
                }
                if method != "invariant_certificate":
                    artifact["transitions"] = [{
                        "action": "fixture_replay_transition",
                        "before_state_sha256": start_sha256,
                        "after_state_sha256": target_sha256,
                    }]
                else:
                    artifact["invariants"] = ["fixture_path_legality", "fixture_quest_legality"]
                payload = json.dumps(artifact, sort_keys=True).encode()
            else:
                payload = json.dumps({"state_id": row["state_id"], "check": key}, sort_keys=True).encode()
            evidence_path.write_bytes(payload)
            evidence["artifact_sha256"] = _digest(payload)
        for name, evidence in row["trial_evidence"].items():
            if evidence["artifact_sha256"] != "pending":
                continue
            evidence_path = tmp_path / evidence["artifact_path"]
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = _trial_payload(row, name)
            evidence_path.write_bytes(payload)
            evidence["artifact_sha256"] = _digest(payload)
    path = tmp_path / "candidates.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _pool() -> list[dict]:
    return [
        _candidate("target_a", quest_stage=1, position=1),
        _candidate("target_b", quest_stage=2, position=34),
        _candidate("progress_control_a", quest_stage=1, position=65, student_visits=100, natural_student_rollouts=200, teacher_successes=40),
        _candidate("progress_control_b", quest_stage=2, position=97, student_visits=100, natural_student_rollouts=200, teacher_successes=40),
        _candidate("visitation_only_a", quest_stage=3, position=129, teacher_successes=40),
        _candidate("visitation_only_b", quest_stage=4, position=161, teacher_successes=40),
        _candidate("teacher_only_a", quest_stage=5, position=193, student_visits=100, natural_student_rollouts=200),
        _candidate("teacher_only_b", quest_stage=6, position=225, student_visits=100, natural_student_rollouts=200),
    ]


def _rewrite_artifact(candidates: Path, row_index: int, group: str, name: str, mutate) -> None:
    rows = [json.loads(line) for line in candidates.read_text().splitlines()]
    evidence = rows[row_index][group][name]
    artifact_path = candidates.parent / evidence["artifact_path"]
    artifact = json.loads(artifact_path.read_text())
    mutate(artifact)
    payload = json.dumps(artifact, sort_keys=True).encode()
    artifact_path.write_bytes(payload)
    evidence["artifact_sha256"] = _digest(payload)
    candidates.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_selection_freezes_uncertainty_aware_non_degenerate_arms(tmp_path: Path) -> None:
    candidates = _candidates(tmp_path, _pool())
    config = _config(tmp_path)
    first = build_selection(candidates, config)
    second = build_selection(candidates, config)
    assert first == second
    assert first["schema_version"] == "kaetram-target-player-state-selection-v2"
    assert [row["state_id"] for row in first["arms"]["targeted"]] == ["target_a", "target_b"]
    target_ids = {row["state_id"] for row in first["arms"]["targeted"]}
    for name, arm in first["arms"].items():
        assert len(arm) == 2
        if name != "targeted":
            assert {row["state_id"] for row in arm} != target_ids
    assert {
        row["progress_bin"]["key"] for row in first["arms"]["progress_matched"]
    } == {progress_bin(_pool()[0]["snapshot"])["key"], progress_bin(_pool()[1]["snapshot"])["key"]}
    target = first["arms"]["targeted"][0]
    assert target["derived"]["teacher_success_interval"][0] > 0.6
    assert target["reachability_checker_result"]["verification_kind"] == "transition_replay"


def test_duplicate_snapshot_and_heldout_leak_fail_closed(tmp_path: Path) -> None:
    duplicate = _pool()
    duplicate[1]["snapshot"] = duplicate[0]["snapshot"]
    with pytest.raises(SelectionError, match="duplicate persistent player snapshot"):
        build_selection(_candidates(tmp_path, duplicate), _config(tmp_path))

    leaking = _pool()
    leaking[0]["snapshot"]["quests"] = [{"key": "desertquest", "stage": 1}]
    with pytest.raises(SelectionError, match="held-out quest leakage"):
        build_selection(_candidates(tmp_path, leaking), _config(tmp_path))

    provenance_leak = _pool()
    provenance_leak[0]["source_run_ids"] = ["desertquest-candidate-discovery"]
    with pytest.raises(SelectionError, match="held-out quest leakage"):
        build_selection(_candidates(tmp_path, provenance_leak), _config(tmp_path))


def test_progress_and_equivalence_are_calculated_not_free_text(tmp_path: Path) -> None:
    bad_progress = _pool()
    bad_progress[0]["progress_bin"]["key"] = "0" * 64
    with pytest.raises(SelectionError, match="progress_bin does not match"):
        build_selection(_candidates(tmp_path, bad_progress), _config(tmp_path))

    bad_equivalence = _pool()
    bad_equivalence[0]["state_equivalence"]["predicate_id"] = "self-reported-equivalence"
    with pytest.raises(SelectionError, match="state_equivalence does not match"):
        build_selection(_candidates(tmp_path, bad_equivalence), _config(tmp_path))


def test_incomplete_snapshot_and_unverifiable_validity_fail_closed(tmp_path: Path) -> None:
    incomplete = _pool()
    del incomplete[0]["snapshot"]["inventory"]
    with pytest.raises(SelectionError, match="complete seed_player record"):
        build_selection(_candidates(tmp_path, incomplete), _config(tmp_path))

    contradictory = _pool()
    contradictory[0]["snapshot"]["player_info_overrides"] = {"x": 999}
    with pytest.raises(SelectionError, match="cannot replace authoritative fields"):
        build_selection(_candidates(tmp_path, contradictory), _config(tmp_path))

    malformed = _pool()
    malformed[0]["snapshot"]["skills"] = ["foraging"]
    with pytest.raises(SelectionError, match="skills must be a list of objects"):
        build_selection(_candidates(tmp_path, malformed), _config(tmp_path))

    mismatched = _pool()
    mismatched[0]["validity_evidence"]["legal_reachable"]["artifact_sha256"] = "a" * 64
    with pytest.raises(SelectionError, match="digest mismatch"):
        build_selection(_candidates(tmp_path, mismatched), _config(tmp_path))


def test_reachability_requires_executed_pinned_checker(tmp_path: Path) -> None:
    missing_method = _pool()
    del missing_method[0]["validity_evidence"]["legal_reachable"]["method"]
    with pytest.raises(SelectionError, match="legal reachability method"):
        build_selection(_candidates(tmp_path, missing_method), _config(tmp_path))

    candidates = _candidates(tmp_path, _pool())
    config = _config(tmp_path)
    config_raw = json.loads(config.read_text())
    config_raw["reachability_checkers"]["witness_trajectory"]["sha256"] = "a" * 64
    config.write_text(json.dumps(config_raw))
    with pytest.raises(SelectionError, match="checker.*digest mismatch"):
        build_selection(candidates, config)

    candidates = _candidates(tmp_path, _pool())
    config = _config(tmp_path)
    config_raw = json.loads(config.read_text())
    checker_path = Path(config_raw["reachability_checkers"]["witness_trajectory"]["path"])
    checker_path.write_text(CHECKER_SOURCE.replace('"transition_replay"', '"declared_witness"'))
    changed_checker_sha256 = _digest(checker_path.read_bytes())
    config_raw["reachability_checkers"]["witness_trajectory"]["sha256"] = changed_checker_sha256
    config_raw["reachability_checkers"]["invariant_certificate"]["sha256"] = changed_checker_sha256
    config.write_text(json.dumps(config_raw))
    with pytest.raises(SelectionError, match="did not replay witness transitions"):
        build_selection(candidates, config)

    candidates = _candidates(tmp_path, _pool())
    _rewrite_artifact(
        candidates, 0, "validity_evidence", "legal_reachable",
        lambda artifact: artifact["transitions"][0].update(after_state_sha256="c" * 64),
    )
    with pytest.raises(SelectionError, match="breaks path continuity"):
        build_selection(candidates, _config(tmp_path))


def test_executed_invariant_checker_is_accepted(tmp_path: Path) -> None:
    rows = _pool()
    rows[0]["validity_evidence"]["legal_reachable"]["method"] = "invariant_certificate"
    selection = build_selection(_candidates(tmp_path, rows), _config(tmp_path))
    selected = next(row for row in selection["arms"]["targeted"] if row["state_id"] == "target_a")
    assert selected["reachability_checker_result"]["verification_kind"] == "executed_invariant_checker"
    assert selected["reachability_checker_result"]["checked_invariants"]


def test_selector_preserves_live_replay_trace_and_runtime_attestation(tmp_path: Path) -> None:
    candidates = _candidates(tmp_path, _pool())

    def make_live_replay(artifact: dict) -> None:
        artifact["checker_protocol"] = "kaetram-live-player-state-replay-v1"
        artifact["runtime"] = {
            "adapter_id": "kaetram-mcp-mongo-isolated-v1",
            "harness_git_revision": "a" * 40,
            "game_git_revision": "b" * 40,
            "state_digest_schema": "kaetram-mcp-observation-canonical-json-v1",
            "persistent_digest_schema": "kaetram-seeded-player-collections-v1",
        }
        artifact["transitions"] = [{
            "action": {"tool": "navigate", "arguments": {"x": 2, "y": 20}},
            "before_observation_sha256": "1" * 64,
            "tool_result_sha256": "2" * 64,
            "after_observation_sha256": "3" * 64,
        }]

    _rewrite_artifact(
        candidates, 0, "validity_evidence", "legal_reachable", make_live_replay,
    )
    selection = build_selection(candidates, _config(tmp_path))
    selected = next(row for row in selection["arms"]["targeted"] if row["state_id"] == "target_a")
    result = selected["reachability_checker_result"]
    assert result["runtime"]["adapter_id"] == "kaetram-mcp-mongo-isolated-v1"
    assert result["executed_trace"][0]["action"]["tool"] == "navigate"


def test_hashed_trial_evidence_binds_counts_and_provenance(tmp_path: Path) -> None:
    candidates = _candidates(tmp_path, _pool())
    rows = [json.loads(line) for line in candidates.read_text().splitlines()]
    rows[0]["counts"]["teacher_successes"] = 89
    candidates.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(SelectionError, match="counts do not match hashed trial outcomes"):
        build_selection(candidates, _config(tmp_path))

    candidates = _candidates(tmp_path, _pool())
    artifact_path = tmp_path / json.loads(candidates.read_text().splitlines()[0])["trial_evidence"]["teacher_success"]["artifact_path"]
    artifact_path.write_text(artifact_path.read_text() + " ")
    with pytest.raises(SelectionError, match="digest mismatch"):
        build_selection(candidates, _config(tmp_path))

    candidates = _candidates(tmp_path, _pool())
    _rewrite_artifact(
        candidates, 0, "trial_evidence", "student_success",
        lambda artifact: artifact["history_constructor"].update(revision="9" * 64),
    )
    with pytest.raises(SelectionError, match="teacher/student history_constructor mismatch"):
        build_selection(candidates, _config(tmp_path))


def test_minimum_trials_and_confidence_bounds_fail_closed(tmp_path: Path) -> None:
    one_shot = _pool()
    one_shot[0] = _candidate(
        "target_a", quest_stage=1, position=1,
        teacher_successes=1, teacher_trials=1,
    )
    with pytest.raises(SelectionError, match="teacher_trials has 1 trials; minimum is 50"):
        build_selection(_candidates(tmp_path, one_shot), _config(tmp_path))

    uncertain = _pool()
    uncertain[0] = _candidate(
        "target_a", quest_stage=1, position=1,
        teacher_successes=33, teacher_trials=50,
        student_successes=5, student_trials=50,
    )
    assert uncertain[0]["counts"]["teacher_successes"] / 50 >= 0.6
    with pytest.raises(SelectionError, match="targeted rule selected 1 states"):
        build_selection(_candidates(tmp_path, uncertain), _config(tmp_path))


def test_missing_progress_control_is_a_hard_error(tmp_path: Path) -> None:
    rows = [
        row for row in _pool()
        if row["state_id"] != "progress_control_b"
    ]
    with pytest.raises(SelectionError, match="progress-matched control"):
        build_selection(_candidates(tmp_path, rows), _config(tmp_path))


def test_seed_plan_requires_three_frozen_states_and_preserves_hashes(tmp_path: Path) -> None:
    selection = build_selection(_candidates(tmp_path, _pool()), _config(tmp_path, max_states=2))
    with pytest.raises(SeedPlanError, match="three are required"):
        build_seed_plan(selection, arm="targeted", batch=0)

    larger = _pool() + [
        _candidate("target_c", quest_stage=3, position=257),
        _candidate("progress_control_c", quest_stage=3, position=289, student_visits=100, natural_student_rollouts=200, teacher_successes=40),
        _candidate("visitation_only_c", quest_stage=7, position=321, teacher_successes=40),
        _candidate("teacher_only_c", quest_stage=8, position=353, student_visits=100, natural_student_rollouts=200),
    ]
    selection = build_selection(_candidates(tmp_path, larger), _config(tmp_path, max_states=3))
    plan = build_seed_plan(selection, arm="targeted", batch=0)
    assert len(plan["assignments"]) == 3
    calls = []
    cleanup_calls = []
    execute_seed_plan(
        plan,
        lambda username, **snapshot: calls.append((username, snapshot)),
        cleanup_calls.append,
    )
    assert cleanup_calls == ["qwengrinder", "qwencompletionist", "qwenexplorer"]
    assert [call[0] for call in calls] == ["qwengrinder", "qwencompletionist", "qwenexplorer"]


def test_seed_plan_detects_snapshot_tampering(tmp_path: Path) -> None:
    rows = _pool() + [
        _candidate("target_c", quest_stage=3, position=257),
        _candidate("progress_control_c", quest_stage=3, position=289, student_visits=100, natural_student_rollouts=200, teacher_successes=40),
        _candidate("visitation_only_c", quest_stage=7, position=321, teacher_successes=40),
        _candidate("teacher_only_c", quest_stage=8, position=353, student_visits=100, natural_student_rollouts=200),
    ]
    selection = build_selection(_candidates(tmp_path, rows), _config(tmp_path, max_states=3))
    selection["arms"]["targeted"][0]["snapshot"]["hit_points"] = 1
    with pytest.raises(SeedPlanError, match="hash mismatch"):
        build_seed_plan(selection, arm="targeted", batch=0)
