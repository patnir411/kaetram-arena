from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finetune import train_corrected_interface_sft as trainer
from scripts.opd import corrected_interface_sft as adapter
from scripts.opd import matched_training as mt
from scripts.opd import matched_training_backend as backend

SOURCE_REPO = Path(__file__).resolve().parents[2]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_json(value) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _write(path: Path, value: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return _sha_bytes(value)


def _update_normalized_reference(plan_path: Path, records_path: Path) -> None:
    plan = json.loads(plan_path.read_text())
    plan["normalized_records"]["sha256"] = _sha_bytes(records_path.read_bytes())
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    monkeypatch.setattr(backend, "REPO", repo)
    monkeypatch.setattr(
        backend,
        "_load_verified_tokenizer",
        lambda *_args, **_kwargs: type(
            "FakeTokenizer",
            (),
            {
                "decode": lambda self, token_ids, skip_special_tokens=False: (
                    "ordinary training text"
                )
            },
        )(),
    )
    monkeypatch.setattr(adapter, "REPO", repo)
    interface_files = []
    for name, content in (
        ("system.md", b"system"),
        ("knowledge.md", b"knowledge"),
        ("render.py", b"frozen-render"),
    ):
        path = repo / "interface" / name
        interface_files.append({"path": str(path), "sha256": _write(path, content)})
    render_sha = _sha_json({
        "contract_id": mt.INTERFACE_CONTRACT,
        "files": [
            {"path": Path(item["path"]).relative_to(repo).as_posix(), "sha256": item["sha256"]}
            for item in interface_files
        ],
    })
    parameterization = {
        "contract_id": mt.PARAMETERIZATION_CONTRACT,
        "method": "lora",
        "fresh_adapter_per_cell": True,
        "precision": "bf16",
        "rank": 64,
        "alpha": 64,
        "dropout": 0.0,
        "bias": "none",
        "target_modules": list(mt.LORA_TARGET_MODULES),
        "task_type": "CAUSAL_LM",
        "base_model_trainable": False,
        "init_lora_weights": True,
    }
    state_content = {"player": "training-player", "quest_stage": 2}
    history_content = [{"role": "user", "content": "DO-NOT-RERENDER-SENTINEL"}]
    record = {
        "schema_version": backend.SOURCE_SCHEMA,
        "record_id": "corrected-sft-0001",
        "identities": {
            "base_checkpoint_artifact_id": "base",
            "teacher_artifact_id": "teacher",
            "render_contract_sha256": render_sha,
            "held_out_registration_artifact_id": "heldout",
        },
        "state": {
            "kind": "corrected_interface_teacher_trajectory_state",
            "constructor": "corrected_interface_teacher_trajectory_replay",
            "content": state_content,
            "content_sha256": _sha_json(state_content),
        },
        "history": {
            "kind": "corrected_interface_teacher_history",
            "source": "same_corrected_teacher_trajectory",
            "content": history_content,
            "content_sha256": _sha_json(history_content),
        },
        "supervision": {
            "input_ids": [10, 11, 12],
            "labels": [-100, 11, 12],
            "advantages": None,
            "behavior_logprobs": None,
            "step_weight": 1.0,
        },
        "budget_usage": {
            "action_tokens": 2,
            "teacher_scoring_tokens": 3,
            "environment_interactions": 1,
        },
        "semantics": {
            "mode": "corrected_interface_teacher_trajectory",
            "teacher_trajectory_id": "teacher-trajectory-1",
            "teacher_action_evidence_sha256": "c" * 64,
            "corrected_interface_contract_sha256": render_sha,
        },
    }
    source_path = repo / "artifacts" / "corrected-sft.jsonl"
    source_sha = _write(source_path, (json.dumps(record) + "\n").encode())
    base_path = repo / "artifacts" / "base.bin"
    teacher_path = repo / "artifacts" / "teacher.json"
    heldout_path = repo / "research" / "experiments" / "heldout-quest-v2.json"
    _write(
        repo / "research" / "experiments" / "heldout-quest.json",
        (SOURCE_REPO / "research" / "experiments" / "heldout-quest.json").read_bytes(),
    )
    heldout_sha = _write(
        heldout_path,
        (SOURCE_REPO / "research" / "experiments" / "heldout-quest-v2.json").read_bytes(),
    )
    registry = {
        "schema_version": mt.REGISTRY_SCHEMA,
        "artifacts": {
            "base": {
                "kind": "checkpoint",
                "status": "verified",
                "payload": {"uri": f"file:{base_path}", "sha256": _write(base_path, b"weights")},
            },
            "teacher": {
                "kind": "teacher_attestation",
                "status": "verified",
                "payload": {
                    "uri": f"file:{teacher_path}",
                    "sha256": _write(teacher_path, b"teacher"),
                },
            },
            "heldout": {
                "kind": "heldout_registration",
                "status": "verified",
                "payload": {
                    "uri": "repo:research/experiments/heldout-quest-v2.json",
                    "sha256": heldout_sha,
                },
            },
            "corrected": {
                "kind": "corrected_interface_teacher_trajectories",
                "status": "verified",
                "payload": {"uri": f"file:{source_path}", "sha256": source_sha},
                "held_out_exclusion": {
                    "registration_artifact_id": "heldout",
                    "status": "pass",
                    "scanned_records": 1,
                },
            },
        },
    }
    registry_path = repo / "artifacts" / "registry.json"
    registry_sha = _write(
        registry_path, (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode()
    )
    cell = {
        "schema_version": backend.CELL_SCHEMA,
        "experiment_id": "corrected-sft-test",
        "cell_id": "corrected-interface-sft-seed-7",
        "arm": {
            "arm_id": "corrected_interface_sft",
            "role": "baseline",
            "objective": "sft",
            "training_artifact_id": "corrected",
            "recovery": "on",
            "state_source": {
                "kind": "corrected_interface_teacher_trajectory_state",
                "constructor": "corrected_interface_teacher_trajectory_replay",
            },
            "history_constructor": {
                "kind": "corrected_interface_teacher_history",
                "source": "same_corrected_teacher_trajectory",
            },
        },
        "training_seed": 7,
        "shared_contract": {
            "source_git_commit": "a" * 40,
            "experiment_manifest_sha256": "b" * 64,
            "base_checkpoint_artifact_id": "base",
            "teacher_artifact_id": "teacher",
            "teacher_endpoint_env": "TEACHER_ENDPOINT",
            "held_out_registration_artifact_id": "heldout",
            "held_out_registration": {
                "path": "research/experiments/heldout-quest-v2.json",
                "sha256": heldout_sha,
            },
            "interface_contract_id": mt.INTERFACE_CONTRACT,
            "frozen_interfaces": interface_files,
            "parameterization": parameterization,
            "parameterization_sha256": _sha_json(parameterization),
            "optimizer": {
                "name": "adamw_8bit",
                "learning_rate": 5e-5,
                "betas": [0.9, 0.999],
                "weight_decay": 0.0,
                "scheduler": "cosine",
                "warmup_ratio": 0.03,
                "gradient_clip_norm": 1.0,
                "effective_batch_size": 32,
                "epochs": 1,
            },
            "artifact_root": str(repo / "artifacts"),
            "budgets": {
                "action_tokens": 2,
                "teacher_scoring_tokens": 3,
                "environment_interactions": 1,
            },
            "artifact_registry": {"path": str(registry_path), "sha256": registry_sha},
        },
    }
    cell_path = repo / "outputs" / cell["cell_id"] / "cell-config.json"
    _write(cell_path, (json.dumps(cell, indent=2, sort_keys=True) + "\n").encode())
    backend.materialize(cell_path)
    return cell_path.parent / "backend-plan.json", repo


def test_materializes_only_pretokenized_records_without_claiming_training(
    tmp_path, monkeypatch
) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    result = adapter.materialize(plan_path)
    assert result["status"] == "prepared_not_trained"
    assert result["trainer_execution_status"] == "not_run"
    assert result["trainer_compatibility"] == "executable_pending_compute"
    records_path = Path(result["output_artifact"]["uri"].removeprefix("file:"))
    record = json.loads(records_path.read_text())
    assert set(record) == {
        "schema_version", "record_id", "input_ids", "labels", "step_weight",
        "budget_usage", "identities", "source_normalized_record_sha256",
    }
    assert record["input_ids"] == [10, 11, 12]
    assert record["labels"] == [-100, 11, 12]
    assert "DO-NOT-RERENDER-SENTINEL" not in records_path.read_text()
    adapter_plan = json.loads(Path(result["adapter_plan"]["path"]).read_text())
    assert adapter_plan["trainer_route"]["entrypoint"].endswith(
        "finetune/train_corrected_interface_sft.py"
    )
    assert adapter_plan["rendering_status"] == "not_performed_and_forbidden"
    with pytest.raises(FileExistsError):
        adapter.materialize(plan_path)


def test_rejects_normalized_token_hash_drift(tmp_path, monkeypatch) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    records_path = Path(plan["normalized_records"]["path"])
    records_path.write_text(records_path.read_text() + "\n")
    with pytest.raises(mt.ProtocolError, match="normalized token record SHA-256 mismatch"):
        adapter.build_adapter_plan(plan_path)


def test_rejects_frozen_interface_drift(tmp_path, monkeypatch) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    cell = json.loads(Path(plan["cell_config"]["path"]).read_text())
    Path(cell["shared_contract"]["frozen_interfaces"][0]["path"]).write_text("drift")
    with pytest.raises(mt.ProtocolError, match="frozen rendered interface drift"):
        adapter.build_adapter_plan(plan_path)


def test_rejects_record_identity_divergence_even_when_bundle_is_rehashed(
    tmp_path, monkeypatch
) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    records_path = Path(plan["normalized_records"]["path"])
    record = json.loads(records_path.read_text())
    record["identities"]["render_contract_sha256"] = "d" * 64
    records_path.write_text(json.dumps(record) + "\n")
    _update_normalized_reference(plan_path, records_path)
    with pytest.raises(mt.ProtocolError, match="frozen identity mismatch"):
        adapter.build_adapter_plan(plan_path)


def test_rejects_budget_divergence_even_when_bundle_is_rehashed(tmp_path, monkeypatch) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    records_path = Path(plan["normalized_records"]["path"])
    record = json.loads(records_path.read_text())
    record["budget_usage"]["teacher_scoring_tokens"] += 1
    records_path.write_text(json.dumps(record) + "\n")
    _update_normalized_reference(plan_path, records_path)
    with pytest.raises(mt.ProtocolError, match="do not exactly fill the matched budget"):
        adapter.build_adapter_plan(plan_path)


def test_rejects_non_causal_or_invalid_sft_labels(tmp_path, monkeypatch) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    records_path = Path(plan["normalized_records"]["path"])
    record = json.loads(records_path.read_text())
    record["labels"][1] = 99
    records_path.write_text(json.dumps(record) + "\n")
    _update_normalized_reference(plan_path, records_path)
    with pytest.raises(mt.ProtocolError, match="does not equal its input token"):
        adapter.build_adapter_plan(plan_path)


def test_rejects_non_sft_or_already_executed_backend_plan(tmp_path, monkeypatch) -> None:
    plan_path, _ = _fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    plan["arm_id"] = "natural_opd"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with pytest.raises(mt.ProtocolError, match="only the corrected_interface_sft arm"):
        adapter.build_adapter_plan(plan_path)

    plan["arm_id"] = "corrected_interface_sft"
    plan["execution_status"] = "trained"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with pytest.raises(mt.ProtocolError, match="execution_status not_run"):
        adapter.build_adapter_plan(plan_path)


def _training_fixture(tmp_path, monkeypatch) -> tuple[Path, list[dict]]:
    backend_plan, _ = _fixture(tmp_path, monkeypatch)
    result = adapter.materialize(backend_plan)
    plan_path = Path(result["adapter_plan"]["path"])
    _, records = trainer.load_training_contract(plan_path)
    return plan_path, records


def test_direct_token_training_contract_is_executable_but_not_run(tmp_path, monkeypatch) -> None:
    plan_path, records = _training_fixture(tmp_path, monkeypatch)
    plan, loaded = trainer.load_training_contract(plan_path)
    assert loaded == records
    assert plan["execution_status"] == "not_run"
    assert plan["trainer_route"]["compatibility"] == "executable_pending_compute"
    args = trainer._training_arguments(plan, Path("/tmp/reviewed-output"))
    assert args["optim"] == "adamw_bnb_8bit"
    assert args["learning_rate"] == 5e-5
    assert args["gradient_accumulation_steps"] == 32
    assert args["num_train_epochs"] == 1
    assert args["bf16"] is True
    assert plan["parameterization"]["rank"] == 64
    assert plan["parameterization"]["alpha"] == 64
    assert plan["parameterization"]["target_modules"] == list(mt.LORA_TARGET_MODULES)
    assert trainer.lora_config_kwargs(plan["parameterization"]) == {
        "r": 64,
        "lora_alpha": 64,
        "lora_dropout": 0.0,
        "bias": "none",
        "target_modules": list(mt.LORA_TARGET_MODULES),
        "task_type": "CAUSAL_LM",
        "init_lora_weights": True,
    }
    trainer.verify_trainable_parameter_names([
        "base_model.model.layers.0.self_attn.q_proj.lora_A.default.weight",
        "base_model.model.layers.0.self_attn.q_proj.lora_B.default.weight",
    ])
    with pytest.raises(mt.ProtocolError, match="non-LoRA parameters"):
        trainer.verify_trainable_parameter_names(["model.layers.0.self_attn.q_proj.weight"])


def test_collator_pads_tokens_and_preserves_causal_loss_mask(tmp_path, monkeypatch) -> None:
    _, records = _training_fixture(tmp_path, monkeypatch)
    shorter = {**records[0], "record_id": "shorter", "input_ids": [20, 21], "labels": [-100, 21]}
    batch = trainer.collate_as_lists([records[0], shorter], pad_token_id=0)
    assert batch["input_ids"] == [[10, 11, 12], [20, 21, 0]]
    assert batch["labels"] == [[-100, 11, 12], [-100, 21, -100]]
    assert batch["attention_mask"] == [[1, 1, 1], [1, 1, 0]]
    assert trainer.causal_targets(batch["labels"]) == [[11, 12], [21, -100]]


def test_loss_contract_rejects_all_masked_causal_row() -> None:
    with pytest.raises(mt.ProtocolError, match="causal supervised target"):
        trainer.causal_targets([[-100, -100, -100]])


def test_training_contract_fails_closed_on_token_bundle_drift(tmp_path, monkeypatch) -> None:
    plan_path, _ = _training_fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    records_path = Path(plan["pretokenized_records"]["path"])
    records_path.write_text(records_path.read_text() + "\n")
    with pytest.raises(mt.ProtocolError, match="material hash or schema mismatch"):
        trainer.load_training_contract(plan_path)


def test_training_contract_fails_closed_on_trainer_or_optimizer_drift(tmp_path, monkeypatch) -> None:
    plan_path, _ = _training_fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    plan["trainer_route"]["entrypoint_sha256"] = "f" * 64
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with pytest.raises(mt.ProtocolError, match="reviewed trainer entrypoint"):
        trainer.load_training_contract(plan_path)

    plan["trainer_route"]["entrypoint_sha256"] = trainer._sha256(Path(trainer.__file__))
    plan["optimizer"]["effective_batch_size"] = 0
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with pytest.raises(mt.ProtocolError, match="effective_batch_size"):
        trainer.load_training_contract(plan_path)


def test_training_contract_fails_closed_on_lora_parameterization_drift(
    tmp_path, monkeypatch
) -> None:
    plan_path, _ = _training_fixture(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text())
    plan["parameterization"]["rank"] = 32
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    with pytest.raises(mt.ProtocolError, match="parameterization.rank"):
        trainer.load_training_contract(plan_path)


def test_execute_requires_exact_confirmation_and_enablement(tmp_path, monkeypatch) -> None:
    plan_path, _ = _training_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv(trainer.ENABLE_ENV, raising=False)
    with pytest.raises(mt.ProtocolError, match="confirm-execute"):
        trainer.execute_training(plan_path, "wrong")
    plan = json.loads(plan_path.read_text())
    expected = f"{plan['experiment_id']}:{plan['cell_id']}"
    with pytest.raises(mt.ProtocolError, match=trainer.ENABLE_ENV):
        trainer.execute_training(plan_path, expected)
