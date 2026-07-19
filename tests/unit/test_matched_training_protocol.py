from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.opd import matched_training as mt


SOURCE_REPO = Path(__file__).resolve().parents[2]
MANIFEST = SOURCE_REPO / "research" / "experiments" / "opd-matched-training.example.json"
REGISTRY = (
    SOURCE_REPO
    / "research"
    / "experiments"
    / "opd-matched-training-artifacts.example.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sandbox_manifest(tmp_path: Path, monkeypatch, mutate_manifest=None, mutate_registry=None) -> Path:
    repo = tmp_path / "repo"
    for source in (
        MANIFEST,
        REGISTRY,
        SOURCE_REPO / "prompts" / "system.md",
        SOURCE_REPO / "prompts" / "game_knowledge.md",
        SOURCE_REPO / "finetune" / "render.py",
        SOURCE_REPO / "scripts" / "opd" / "matched_training_backend.py",
    ):
        relative = source.relative_to(SOURCE_REPO)
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest_path = repo / MANIFEST.relative_to(SOURCE_REPO)
    registry_path = repo / REGISTRY.relative_to(SOURCE_REPO)
    raw = json.loads(manifest_path.read_text())
    registry = json.loads(registry_path.read_text())
    if mutate_registry:
        mutate_registry(registry)
        registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
        raw["protocol"]["artifact_registry"]["sha256"] = _sha256(registry_path)
    if mutate_manifest:
        mutate_manifest(raw)
    manifest_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
    monkeypatch.setattr(mt, "REPO", repo)
    return manifest_path


def test_example_expands_core_and_separate_history_cells_with_matched_contracts(
    tmp_path: Path, monkeypatch
) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    assert tuple(arm["arm_id"] for arm in plan.arms) == mt.ARM_IDS
    assert sum(arm["role"] == "primary" for arm in plan.arms) == 6
    assert sum(arm["role"] == "mechanism_or_baseline" for arm in plan.arms) == 4
    assert tuple(item["ablation_id"] for item in plan.history_ablations) == mt.HISTORY_ABLATION_IDS
    assert len(plan.cells) == 70
    assert sum(cell.role == "history_ablation" for cell in plan.cells) == 20
    assert {cell.seed for cell in plan.cells} == set(plan.training_seed_schedule)
    assert {json.dumps(cell.config["shared_contract"], sort_keys=True) for cell in plan.cells} == {
        json.dumps(plan.cells[0].config["shared_contract"], sort_keys=True)
    }
    assert all("budgets" not in cell.config["arm"] for cell in plan.cells)
    assert all("optimizer" not in cell.config["arm"] for cell in plan.cells)
    assert all("parameterization" not in cell.config["arm"] for cell in plan.cells)
    assert plan.parameterization["rank"] == 64
    assert plan.parameterization["alpha"] == 64
    assert plan.parameterization["target_modules"] == list(mt.LORA_TARGET_MODULES)
    assert all(
        cell.config["shared_contract"]["parameterization_sha256"]
        == plan.parameterization_sha256
        for cell in plan.cells
    )
    assert plan.launch_blockers


def test_cli_dry_run_has_no_training_or_output_side_effects(tmp_path: Path) -> None:
    output_root = SOURCE_REPO / "artifacts" / "opd-matched-training" / "opd-matched-ten-arm-v1"
    assert not output_root.exists()
    result = subprocess.run(
        [sys.executable, str(SOURCE_REPO / "scripts" / "opd" / "matched_training.py"),
         str(MANIFEST), "--dry-run"],
        cwd=SOURCE_REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Nothing was launched" in result.stdout
    assert "KAETRAM_MATCHED_TRAINING_TEACHER_ENDPOINT" in result.stdout
    assert not output_root.exists()


def test_missing_or_reordered_arm_fails_closed(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        raw["arms"] = raw["arms"][:-1]

    with pytest.raises(mt.ProtocolError, match="exactly six primary"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_missing_or_reordered_history_ablation_fails_closed(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        raw["history_ablations"] = list(reversed(raw["history_ablations"]))

    with pytest.raises(mt.ProtocolError, match="registered order"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_arm_cannot_override_shared_budget(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        raw["arms"][0]["budgets"] = {"action_tokens": 1}

    with pytest.raises(mt.ProtocolError, match="only arm-specific fields"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_lora_parameterization_is_shared_and_fails_closed_on_drift(
    tmp_path: Path, monkeypatch
) -> None:
    def mutate(raw):
        raw["shared_inputs"]["parameterization"]["rank"] = 32

    with pytest.raises(mt.ProtocolError, match="parameterization.rank"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_state_and_history_constructor_is_frozen_per_arm(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        raw["arms"][1]["history_constructor"]["kind"] = "authentic_teacher_history"

    with pytest.raises(mt.ProtocolError, match="differs from the frozen protocol"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_guided_opd_freezes_published_training_progress_schedule(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        guided = next(arm for arm in raw["arms"] if arm["arm_id"] == "guided_opd")
        guided["guided_annealing"]["total_training_steps"] = 251

    with pytest.raises(mt.ProtocolError, match="250 total training steps"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_guided_opd_remains_an_unconditional_launch_blocker(tmp_path: Path, monkeypatch) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    assert any("guided_opd requires" in blocker for blocker in plan.launch_blockers)

    enabled = replace(
        plan,
        allow_launch=True,
        launch_blockers=tuple(
            blocker for blocker in plan.launch_blockers if "guided_opd requires" in blocker
        ),
    )
    monkeypatch.setattr(
        mt.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Guided-OPD blocker must precede Popen"),
    )
    with pytest.raises(mt.ProtocolError, match="live mixed-rollout collector"):
        mt.launch(
            enabled,
            confirmation=enabled.experiment_id,
            environ={enabled.teacher_endpoint_env: "https://teacher.invalid/v1"},
        )


def test_tcod_prefixes_require_db_authoritative_success_evidence(tmp_path: Path, monkeypatch) -> None:
    def mutate(registry):
        registry["artifacts"]["tcod_success_prefixes"]["teacher_success_evidence"][
            "metric"
        ] = "self_reported_success"

    with pytest.raises(mt.ProtocolError, match="DB-authoritative teacher success"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_registry=mutate))


def test_score_requires_first_model_visible_error_evidence(tmp_path: Path, monkeypatch) -> None:
    def mutate(registry):
        registry["artifacts"]["score_first_error_prefixes"]["first_error_evidence"][
            "metric"
        ] = "first_environment_error"

    with pytest.raises(mt.ProtocolError, match="first model-visible student error"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_registry=mutate))


def test_backplay_must_span_full_action_budget(tmp_path: Path, monkeypatch) -> None:
    def mutate(raw):
        backplay = next(
            item for item in raw["history_ablations"]
            if item["ablation_id"] == "backplay_witness_annealing"
        )
        backplay["backplay_annealing"]["anneal_action_tokens"] -= 1

    with pytest.raises(mt.ProtocolError, match="full action budget"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_manifest=mutate))


def test_every_arm_artifact_must_bind_same_heldout_registration(tmp_path: Path, monkeypatch) -> None:
    def mutate(registry):
        registry["artifacts"]["random_valid_snapshots"]["held_out_exclusion"][
            "registration_artifact_id"
        ] = "different_registration"

    with pytest.raises(mt.ProtocolError, match="not bound to held-out registration"):
        mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch, mutate_registry=mutate))


def test_registry_drift_is_rejected(tmp_path: Path, monkeypatch) -> None:
    manifest = _sandbox_manifest(tmp_path, monkeypatch)
    registry = mt.REPO / "research" / "experiments" / REGISTRY.name
    registry.write_text(registry.read_text() + " ")
    with pytest.raises(mt.ProtocolError, match="registry SHA-256 mismatch"):
        mt.build_plan(manifest)


def test_launch_interlocks_and_unresolved_artifacts_precede_popen(
    tmp_path: Path, monkeypatch
) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    monkeypatch.setattr(
        mt.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Popen must not be reached"),
    )
    with pytest.raises(mt.ProtocolError, match="allow_launch"):
        mt.launch(plan, confirmation=plan.experiment_id, environ={})
    enabled = replace(plan, allow_launch=True)
    with pytest.raises(mt.ProtocolError, match="confirm-launch"):
        mt.launch(enabled, confirmation="wrong", environ={})
    with pytest.raises(mt.ProtocolError, match="artifact"):
        mt.launch(enabled, confirmation=plan.experiment_id, environ={})


def test_plan_never_resolves_teacher_endpoint_secret(tmp_path: Path, monkeypatch) -> None:
    secret = "https://teacher.invalid/v1?token=TOP_SECRET"
    monkeypatch.setenv("KAETRAM_MATCHED_TRAINING_TEACHER_ENDPOINT", secret)
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    rendered = json.dumps(mt.plan_dict(plan))
    assert secret not in rendered
    assert "TOP_SECRET" not in rendered
    assert plan.teacher_endpoint_env in rendered


def test_backend_result_must_match_identity_seed_and_exact_budgets(
    tmp_path: Path, monkeypatch
) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    cell = plan.cells[0]
    result_path = Path(cell.output_dir) / "result.json"
    result_path.parent.mkdir(parents=True)
    (result_path.parent / "cell-config.json").write_text(json.dumps(cell.config))
    result = {
        "schema_version": "kaetram.matched-training-result.v1",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "status": "completed",
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
        "teacher_artifact_id": plan.teacher_artifact_id,
        "training_seed": cell.seed,
        "consumed_budgets": plan.budgets,
        "output_artifact": {"uri": "s3://example/checkpoint", "sha256": "a" * 64},
    }
    result_path.write_text(json.dumps(result))
    mt.validate_cell_result(plan, cell)

    result["consumed_budgets"] = {**plan.budgets, "action_tokens": 1}
    result_path.write_text(json.dumps(result))
    with pytest.raises(mt.ProtocolError, match="result contract mismatch"):
        mt.validate_cell_result(plan, cell)


def test_backend_result_rejects_unresolved_output_artifact(tmp_path: Path, monkeypatch) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    cell = plan.cells[0]
    path = Path(cell.output_dir) / "result.json"
    path.parent.mkdir(parents=True)
    (path.parent / "cell-config.json").write_text(json.dumps(cell.config))
    path.write_text(json.dumps({
        "schema_version": "kaetram.matched-training-result.v1",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "status": "completed",
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
        "teacher_artifact_id": plan.teacher_artifact_id,
        "training_seed": cell.seed,
        "consumed_budgets": plan.budgets,
        "output_artifact": {"uri": "UNRESOLVED://checkpoint", "sha256": "a" * 64},
    }))
    with pytest.raises(mt.ProtocolError, match="URI is unresolved"):
        mt.validate_cell_result(plan, cell)


def test_prepared_backend_result_verifies_material_and_never_claims_training(
    tmp_path: Path, monkeypatch
) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    cell = plan.cells[0]
    output_dir = Path(cell.output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "cell-config.json").write_text(json.dumps(cell.config))
    records = output_dir / "normalized-records.jsonl"
    records.write_text('{"record_id":"one"}\n')
    backend_plan = output_dir / "backend-plan.json"
    backend_plan.write_text(json.dumps({
        "schema_version": "kaetram.matched-training-backend-plan.v1",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "arm_id": cell.arm_id,
        "training_seed": cell.seed,
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "budgets": plan.budgets,
        "execution_status": "not_run",
        "normalized_records": {"path": str(records), "sha256": _sha256(records)},
    }))
    result = {
        "schema_version": "kaetram.matched-training-result.v2",
        "experiment_id": plan.experiment_id,
        "cell_id": cell.cell_id,
        "status": "prepared_not_trained",
        "source_git_commit": plan.source_git_commit,
        "experiment_manifest_sha256": plan.manifest_sha256,
        "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
        "teacher_artifact_id": plan.teacher_artifact_id,
        "training_seed": cell.seed,
        "allocated_budgets": plan.budgets,
        "backend_plan": {"path": str(backend_plan), "sha256": _sha256(backend_plan)},
        "output_artifact": {
            "kind": "normalized_training_records",
            "uri": f"file:{records}",
            "sha256": _sha256(records),
        },
        "trainer_execution_status": "not_run",
        "trainer_compatibility": "record_schema_compatible_not_executed",
    }
    (output_dir / "result.json").write_text(json.dumps(result))
    mt.validate_cell_result(plan, cell)
    records.write_text("tampered\n")
    with pytest.raises(mt.ProtocolError, match="material SHA-256 mismatch"):
        mt.validate_cell_result(plan, cell)


def test_resolved_launch_writes_seal_configs_and_validates_every_result(
    tmp_path: Path, monkeypatch
) -> None:
    plan = mt.build_plan(_sandbox_manifest(tmp_path, monkeypatch))
    backend = mt.REPO / "backend.py"
    backend.write_text("# hash-locked test adapter\n")
    plan = replace(
        plan,
        allow_launch=True,
        launch_blockers=(),
        source_git_commit="a" * 40,
        backend_adapter_path=str(backend),
        backend_adapter_sha256=_sha256(backend),
        max_parallel=2,
    )
    monkeypatch.setattr(mt, "_git_state", lambda: (plan.source_git_commit, []))
    launched = []

    class FakeProcess:
        def __init__(self, args, **kwargs):
            config_path = Path(args[args.index("--cell-config") + 1])
            config = json.loads(config_path.read_text())
            cell = next(item for item in plan.cells if item.cell_id == config["cell_id"])
            launched.append(cell.cell_id)
            (config_path.parent / "result.json").write_text(json.dumps({
                "schema_version": "kaetram.matched-training-result.v1",
                "experiment_id": plan.experiment_id,
                "cell_id": cell.cell_id,
                "status": "completed",
                "source_git_commit": plan.source_git_commit,
                "experiment_manifest_sha256": plan.manifest_sha256,
                "base_checkpoint_artifact_id": plan.base_checkpoint_artifact_id,
                "teacher_artifact_id": plan.teacher_artifact_id,
                "training_seed": cell.seed,
                "consumed_budgets": plan.budgets,
                "output_artifact": {
                    "uri": f"s3://example/{cell.cell_id}",
                    "sha256": "a" * 64,
                },
            }))

        def wait(self):
            return 0

        def poll(self):
            return 0

        def terminate(self):
            raise AssertionError("completed fake process must not be terminated")

    monkeypatch.setattr(mt.subprocess, "Popen", FakeProcess)
    assert mt.launch(
        plan,
        confirmation=plan.experiment_id,
        environ={plan.teacher_endpoint_env: "https://teacher.invalid/v1"},
    ) == 0
    assert launched == [cell.cell_id for cell in plan.cells]
    seal = Path(plan.output_root) / plan.experiment_id / "prelaunch.json"
    payload = json.loads(seal.read_text())
    assert len(payload["cells"]) == 70
    assert all("cell_contract_sha256" in cell for cell in payload["cells"])
