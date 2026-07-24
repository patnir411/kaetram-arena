from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.opd.factorial_analyze import (
    _paired_t_inference,
    _practical_relevance_decision,
    _sign_flip_p,
    build_analysis,
    publish_analysis_artifacts,
)
from scripts.opd.factorial_eval import (
    ManifestError,
    build_plan,
    seal_cell_bundle,
    seal_completed_inventory,
    seal_prelaunch_record,
)


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research" / "experiments" / "opd-2b-factorial.example.json"
SOURCE_SHA = "a" * 40


@pytest.fixture(autouse=True)
def _registered_clean_analysis_source(monkeypatch):
    git = {
        "repository": "git@example.test:owner/repo.git",
        "commit": SOURCE_SHA,
        "branch": "private-review-branch",
        "dirty": False,
        "dirty_paths": [],
    }
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.capture_git_state",
        lambda _repo: git,
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_analyze.capture_git_state",
        lambda _repo: git,
    )


def _plan(tmp_path: Path, replicates: int = 20, *, phase: str = "confirmatory"):
    raw = json.loads(MANIFEST.read_text())
    raw["isolation"]["output_root"] = str(tmp_path / "runs")
    raw["isolation"]["sandbox_root"] = str(tmp_path / "sandboxes")
    raw["protocol"]["source_git_commit"] = SOURCE_SHA
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw))
    plan = build_plan(manifest)
    if replicates == 20 and phase == "confirmatory":
        return plan
    return replace(
        plan,
        sampling_phase=phase,
        planned_replicates=replicates,
        confirmatory_replicates=replicates,
        cells=tuple(cell for cell in plan.cells if cell.replicate <= replicates),
    )


def _write_results(plan, *, omit_cell: str = "", alternate_sha_cell: str = "") -> None:
    weight_offset = {"base": 0, "r2": 2, "r3": 4}
    for cell in plan.cells:
        if cell.cell_id == omit_cell:
            continue
        value = weight_offset[cell.weight] + int(cell.recovery)
        if cell.weight == "r3" and cell.recovery and cell.replicate % 2:
            value += 1
        path = Path(cell.run_dir) / cell.cell_id / "results.json"
        path.parent.mkdir(parents=True)
        (Path(cell.run_dir) / "launcher.log").write_text("launcher output\n")
        (path.parent / "system_prompt.md").write_text("resolved prompt\n")
        (path.parent / "episode_001.jsonl").write_text('{"type":"assistant"}\n')
        (path.parent / "episode_001_state.json").write_text(json.dumps({
            "schema_version": "kaetram.eval-state-boundary.v1",
            "episode": 1,
        }))
        raw_dir = path.parent / "episode_001_raw"
        raw_dir.mkdir()
        (raw_dir / "session_1_test.log").write_text(
            '{"type":"raw_model_emission","content":"exact"}\n'
        )
        path.write_text(json.dumps({
            "meta": {
                "model": cell.cell_id,
                "endpoint": f"env:{cell.endpoint_env}",
                "scenario": plan.scenario,
                "duration_seconds_budget": plan.duration_seconds,
                "protocol_id": plan.protocol_id,
                "experiment_manifest_sha256": plan.manifest_sha256,
                "endpoint_attestation_sha256": cell.endpoint_attestation_sha256,
                "checkpoint_sha256": cell.checkpoint_sha256,
                "tokenizer_sha256": cell.tokenizer_sha256,
                "render_contract_sha256": cell.render_contract_sha256,
                "total_episodes": 1,
                "ok_episodes": 1,
                "tool_schema_source": plan.tool_schema_source,
                "include_game_knowledge": not plan.omit_game_knowledge,
                "held_out_quest": plan.held_out_quest,
                "inference_seed": cell.inference_seed,
                "prompt_agent_name": cell.prompt_agent_name,
                "factorial_schedule_algorithm": plan.schedule_algorithm,
                "factorial_schedule_seed": plan.schedule_seed,
                "factorial_schedule_index": cell.schedule_index,
                "factorial_batch_index": cell.batch_index,
                "factorial_cluster_id": cell.cluster_id,
                "factorial_pair_id": cell.pair_id,
                "tool_recovery_enabled": cell.recovery,
                "environment_seed_mechanism": plan.environment_seed_mechanism,
                "environment_seed": cell.environment_seed,
                "environment_rng_algorithm": plan.environment_rng_algorithm,
                "environment_game_revision": plan.environment_game_revision,
                "environment_game_bundle_sha256": plan.environment_game_bundle_sha256,
                "environment_seed_reason": plan.environment_seed_reason,
                "environment_rng_attestation": {
                    "schema": plan.environment_seed_mechanism,
                    "algorithm": plan.environment_rng_algorithm,
                    "seedSha256": hashlib.sha256(
                        str(cell.environment_seed).encode()
                    ).hexdigest(),
                    "gameRevision": plan.environment_game_revision,
                    "serverBundleSha256": plan.environment_game_bundle_sha256,
                    "drawsAtAttestation": 0,
                },
                "git_sha": "b" * 40 if cell.cell_id == alternate_sha_cell else SOURCE_SHA,
            },
            "episodes": [{
                "episode": 1,
                "status": "ok",
                "returncode": 0,
                "duration_seconds": plan.duration_seconds,
                "turns_played": 100,
                "core3_stages_advanced": value,
            }],
        }))


def _seal(plan) -> None:
    endpoint_attestations = [
        {
            "weight": model["weight"],
            "endpoint_env": model["endpoint_env"],
            "endpoint_attestation_sha256": model["endpoint_attestation_sha256"],
            "expected_health": model["expected_health"],
        }
        for model in plan.model_provenance
    ]
    seal_prelaunch_record(
        plan,
        endpoint_attestations,
        {
            "schema": "kaetram-server-build-attestation/v1",
            "game_revision": plan.environment_game_revision,
            "source_tree_git_oid": "e" * 40,
            "entrypoint": "packages/server/dist/main.js",
            "entrypoint_sha256": plan.environment_game_bundle_sha256,
            "build_attestation_path": "/Users/private/game/attestation.json",
            "build_attestation_sha256": "f" * 64,
        },
    )
    for cell in plan.cells:
        seal_cell_bundle(plan, cell)
    seal_completed_inventory(plan)


def test_analysis_uses_replicates_not_personality_cells_as_n(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan)
    _seal(plan)
    analysis = build_analysis(plan, "core3_stages_advanced")
    assert analysis["n_cells"] == 360
    assert analysis["n_cluster_arms"] == 120
    assert analysis["n_replicates"] == 20
    assert analysis["inference_scope"] == {
        "population": "evaluation seeds and fresh-world trajectories",
        "checkpoint_treatment": "three fixed registered checkpoint artifacts",
        "conditional_on_fixed_checkpoints": True,
        "excluded_uncertainty": [
            "training-procedure variance",
            "training-seed variance",
        ],
    }
    recovery_r3 = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "recovery_on_minus_off_r3"
    )
    assert recovery_r3["paired_deltas"] == [6.0, 3.0] * 10
    assert recovery_r3["mean_delta"] == 4.5
    assert recovery_r3["paired_t_degrees_of_freedom"] == 19
    assert recovery_r3["paired_t_statistic"] == pytest.approx(
        13.076696830622,
        abs=1e-12,
    )
    assert recovery_r3["paired_t_raw_two_sided_p"] == pytest.approx(
        5.99044430439842e-11,
        rel=1e-12,
    )
    assert recovery_r3["student_t_critical_value"] == pytest.approx(
        3.013624610311,
        abs=1e-12,
    )
    assert recovery_r3["bonferroni_adjusted_p"] == pytest.approx(
        7 * 5.99044430439842e-11,
        rel=1e-12,
    )
    assert recovery_r3["bonferroni_simultaneous_ci_mean"] == pytest.approx(
        [3.462940647623, 5.537059352377],
        abs=1e-12,
    )
    assert recovery_r3["bonferroni_simultaneous_ci_mean"][0] > 3
    assert recovery_r3["practical_relevance_decision"] == "relevant_positive"
    assert recovery_r3["zero_null_decision"] == "reject"
    assert recovery_r3["hedges_g_z"] > 2
    assert recovery_r3["sensitivity_exact_two_sided_sign_flip_p"] == 2 / (2 ** 20)
    assert recovery_r3["inference_status"] == "confirmatory_preregistered"
    r3_base = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "r3_minus_base_recovery_off"
    )
    assert r3_base["paired_deltas"] == [12.0] * 20
    assert (
        r3_base["inference_status"]
        == "confirmatory_inference_not_estimable_zero_variance"
    )
    assert r3_base["bonferroni_adjusted_p"] is None
    assert r3_base["descriptive_bootstrap_95pct_ci_mean"] is None
    assert r3_base["sensitivity_exact_two_sided_sign_flip_p"] is None
    assert r3_base["practical_relevance_decision"] == "inconclusive"
    assert r3_base["zero_null_decision"] == "not_estimable"
    recovery_main = next(
        effect for effect in analysis["factorial_main_effects"]
        if effect["name"] == "recovery_main_effect"
    )
    assert recovery_main["paired_deltas"] == [4.0, 3.0] * 10
    interaction = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "r3_minus_base_recovery_interaction"
    )
    assert interaction["paired_deltas"] == [3.0, 0.0] * 10
    assert interaction["sensitivity_exact_two_sided_sign_flip_p"] == 2 / (2 ** 10)
    assert analysis["sample_size_contract"]["status"] == "power_preregistered_confirmatory"
    serialized = json.dumps(analysis)
    assert str(tmp_path) not in serialized
    assert "/Users/" not in serialized
    assert analysis["execution_source_git_sha"] == SOURCE_SHA
    assert analysis["analysis_source_git_sha"] == SOURCE_SHA
    assert analysis["analysis_source_files"]


def test_one_replicate_is_explicitly_preliminary_only(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1, phase="pilot")
    _write_results(plan)
    _seal(plan)
    analysis = build_analysis(plan, "core3_stages_advanced")
    assert all(effect["inference_status"] == "preliminary_only" for effect in analysis["effects"])
    assert all(
        effect["sensitivity_exact_two_sided_sign_flip_p"] is None
        for effect in analysis["effects"]
    )
    assert all(effect["paired_t_raw_two_sided_p"] is None for effect in analysis["effects"])


def test_analysis_rejects_unregistered_metric_override(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    _seal(plan)
    with pytest.raises(ManifestError, match="does not match preregistered"):
        build_analysis(plan, "held_out_quest_completed_delta")


def test_analysis_rejects_unregistered_or_dirty_analysis_source(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        "scripts.opd.factorial_analyze.capture_git_state",
        lambda _repo: {
            "commit": "b" * 40,
            "dirty": False,
            "dirty_paths": [],
        },
    )
    with pytest.raises(ManifestError, match="does not match"):
        build_analysis(plan)

    monkeypatch.setattr(
        "scripts.opd.factorial_analyze.capture_git_state",
        lambda _repo: {
            "commit": SOURCE_SHA,
            "dirty": True,
            "dirty_paths": ["scripts/opd/factorial_analyze.py"],
        },
    )
    with pytest.raises(ManifestError, match="clean source tree"):
        build_analysis(plan)


def test_completion_cannot_be_sealed_without_prelaunch_ledger(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1, phase="pilot")
    _write_results(plan)
    for cell in plan.cells:
        seal_cell_bundle(plan, cell)
    with pytest.raises(ManifestError, match="prelaunch ledger"):
        seal_completed_inventory(plan)


def test_analysis_fails_closed_on_missing_cell(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan, omit_cell=plan.cells[-1].cell_id)
    with pytest.raises(ManifestError, match="completed factorial inventory"):
        build_analysis(plan, "core3_stages_advanced")


def test_confirmatory_analysis_rejects_nineteen_complete_clusters(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=19)
    _write_results(plan)
    _seal(plan)
    with pytest.raises(ManifestError, match="all 20 registered replicate clusters"):
        build_analysis(plan, "core3_stages_advanced")


def test_analysis_rejects_result_commit_that_differs_from_registration(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan, alternate_sha_cell=plan.cells[-1].cell_id)
    _seal(plan)
    with pytest.raises(ManifestError, match="registered source commit"):
        build_analysis(plan, "core3_stages_advanced")


def test_pair_publication_rolls_back_if_second_artifact_fails(tmp_path: Path, monkeypatch) -> None:
    json_path = tmp_path / "analysis.json"
    csv_path = tmp_path / "clusters.csv"
    real_link = __import__("os").link
    calls = 0

    def fail_second_link(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated CSV publication failure")
        return real_link(source, target)

    monkeypatch.setattr("scripts.opd.factorial_analyze.os.link", fail_second_link)

    with pytest.raises(ManifestError, match="complete analysis artifact pair"):
        publish_analysis_artifacts(json_path, csv_path, {"clusters": []})

    assert not json_path.exists()
    assert not csv_path.exists()


def test_analysis_rejects_missing_metric(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    first = plan.cells[0]
    path = Path(first.run_dir) / first.cell_id / "results.json"
    result = json.loads(path.read_text())
    del result["episodes"][0]["core3_stages_advanced"]
    path.write_text(json.dumps(result))
    _seal(plan)
    with pytest.raises(ManifestError, match="must be a finite numeric value"):
        build_analysis(plan)


def test_analysis_rejects_out_of_range_primary_metric(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    first = plan.cells[0]
    path = Path(first.run_dir) / first.cell_id / "results.json"
    result = json.loads(path.read_text())
    result["episodes"][0]["core3_stages_advanced"] = 11
    path.write_text(json.dumps(result))
    _seal(plan)
    with pytest.raises(ManifestError, match=r"integer in \[0, 10\]"):
        build_analysis(plan)


def test_exact_sign_flip_scales_past_twenty_replicates() -> None:
    assert _sign_flip_p([1.0] * 25) == 2 / (2 ** 25)


def test_sesoi_boundaries_are_strictly_inconclusive() -> None:
    assert _practical_relevance_decision([3.0, 5.0], sesoi=3) == "inconclusive"
    assert _practical_relevance_decision([-5.0, -3.0], sesoi=3) == "inconclusive"
    assert _practical_relevance_decision([-3.0, 3.0], sesoi=3) == "inconclusive"
    assert _practical_relevance_decision([3.01, 5.0], sesoi=3) == "relevant_positive"
    assert _practical_relevance_decision([-2.99, 2.99], sesoi=3) == "practically_equivalent"


def test_paired_t_zero_variance_policy_is_conservative() -> None:
    result = _paired_t_inference(
        [4.0] * 20,
        familywise_alpha=0.05,
        comparisons=7,
        sesoi=3,
    )
    assert result == {
        "status": "confirmatory_inference_not_estimable_zero_variance",
        "paired_t_statistic": None,
        "paired_t_degrees_of_freedom": 19,
        "paired_t_raw_two_sided_p": None,
        "student_t_critical_value": None,
        "bonferroni_adjusted_p": None,
        "bonferroni_simultaneous_ci_mean": None,
        "simultaneous_confidence_level": 1 - 0.05 / 7,
        "cohen_d_z": None,
        "hedges_g_z": None,
        "zero_null_decision": "not_estimable",
        "practical_relevance_decision": "inconclusive",
    }
