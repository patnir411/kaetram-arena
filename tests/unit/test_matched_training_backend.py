from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd import matched_training as mt
from scripts.opd import matched_training_backend as backend

SOURCE_REPO = Path(__file__).resolve().parents[2]


class _FakeTokenizer:
    def __init__(self, decoded: dict[tuple[int, ...], str] | None = None):
        self.decoded = decoded or {}

    def decode(self, token_ids, *, skip_special_tokens=False):
        return self.decoded.get(tuple(token_ids), "ordinary training text")


class _TinyRawTokenizer:
    def get_vocab_size(self, *, with_added_tokens=True):
        return 2

    def decode(self, token_ids, *, skip_special_tokens=False):
        return "".join({0: "A", 1: "B"}[token_id] for token_id in token_ids)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_json(value) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _write(path: Path, value: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return _sha_bytes(value)


def _natural_fixture(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    monkeypatch.setattr(backend, "REPO", repo)
    monkeypatch.setattr(
        backend,
        "_load_verified_tokenizer",
        lambda *_args, **_kwargs: _FakeTokenizer(),
    )
    interface_files = []
    for name, content in (("system.md", b"system"), ("knowledge.md", b"knowledge"), ("render.py", b"render")):
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
    state_content = {"player": "train-player", "quest_stage": 2}
    history_content = [{"role": "user", "content": "visible training state"}]
    record = {
        "schema_version": backend.SOURCE_SCHEMA,
        "record_id": "natural-0001",
        "identities": {
            "base_checkpoint_artifact_id": "base",
            "teacher_artifact_id": "teacher",
            "render_contract_sha256": render_sha,
            "held_out_registration_artifact_id": "heldout",
        },
        "state": {
            "kind": "canonical_natural_rollout",
            "constructor": "fresh_canonical_world_online",
            "content": state_content,
            "content_sha256": _sha_json(state_content),
        },
        "history": {
            "kind": "authentic_online_history",
            "source": "same_rollout",
            "content": history_content,
            "content_sha256": _sha_json(history_content),
        },
        "supervision": {
            "input_ids": [10, 11, 12],
            "labels": [-100, 11, 12],
            "advantages": [0.0, 1.0, 1.0],
            "behavior_logprobs": [0.0, -0.2, -0.1],
            "step_weight": 1.0,
        },
        "budget_usage": {
            "action_tokens": 2,
            "teacher_scoring_tokens": 3,
            "environment_interactions": 1,
        },
        "semantics": {
            "mode": "natural",
            "world_initialization": "canonical_unseeded",
            "rollout_id": "rollout-1",
        },
    }
    source_path = repo / "artifacts" / "natural.jsonl"
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
                "kind": "checkpoint", "status": "verified",
                "payload": {"uri": f"file:{base_path}", "sha256": _write(base_path, b"weights")},
            },
            "teacher": {
                "kind": "teacher_attestation", "status": "verified",
                "payload": {"uri": f"file:{teacher_path}", "sha256": _write(teacher_path, b"teacher")},
            },
            "heldout": {
                "kind": "heldout_registration", "status": "verified",
                "payload": {
                    "uri": "repo:research/experiments/heldout-quest-v2.json",
                    "sha256": heldout_sha,
                },
            },
            "natural": {
                "kind": "on_policy_rollouts", "status": "verified",
                "payload": {"uri": f"file:{source_path}", "sha256": source_sha},
                "held_out_exclusion": {
                    "registration_artifact_id": "heldout", "status": "pass", "scanned_records": 1,
                },
            },
        },
    }
    registry_path = repo / "artifacts" / "registry.json"
    registry_bytes = (json.dumps(registry, indent=2) + "\n").encode()
    registry_sha = _write(registry_path, registry_bytes)
    cell = {
        "schema_version": backend.CELL_SCHEMA,
        "experiment_id": "material-test",
        "cell_id": "natural-opd-seed-7",
        "arm": {
            "arm_id": "natural_opd", "role": "primary", "objective": "opd",
            "training_artifact_id": "natural", "recovery": "on",
            "state_source": {
                "kind": "canonical_natural_rollout", "constructor": "fresh_canonical_world_online",
            },
            "history_constructor": {"kind": "authentic_online_history", "source": "same_rollout"},
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
            "optimizer": {"name": "adamw_8bit"},
            "artifact_root": str(repo / "artifacts"),
            "budgets": {
                "action_tokens": 2, "teacher_scoring_tokens": 3, "environment_interactions": 1,
            },
            "artifact_registry": {"path": str(registry_path), "sha256": registry_sha},
        },
    }
    cell_path = repo / "outputs" / cell["cell_id"] / "cell-config.json"
    _write(cell_path, (json.dumps(cell, indent=2) + "\n").encode())
    return cell_path


def test_materializes_hash_verified_records_without_claiming_training(tmp_path, monkeypatch) -> None:
    cell_path = _natural_fixture(tmp_path, monkeypatch)
    result = backend.materialize(cell_path)
    assert result["status"] == "prepared_not_trained"
    assert result["trainer_execution_status"] == "not_run"
    records_path = Path(result["output_artifact"]["uri"].removeprefix("file:"))
    assert _sha_bytes(records_path.read_bytes()) == result["output_artifact"]["sha256"]
    normalized = json.loads(records_path.read_text())
    assert normalized["arm_id"] == "natural_opd"
    assert normalized["budget_usage"]["action_tokens"] == 2
    with pytest.raises(FileExistsError):
        backend.materialize(cell_path)


def test_relative_cell_contract_resolves_from_repository_not_caller_cwd(
    tmp_path, monkeypatch
) -> None:
    cell_path = _natural_fixture(tmp_path, monkeypatch)
    relative = cell_path.relative_to(backend.REPO)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    plan, records = backend.build_backend_plan(relative)
    assert plan["cell_id"] == "natural-opd-seed-7"
    assert len(records) == 1


def test_rejects_source_material_hash_drift(tmp_path, monkeypatch) -> None:
    cell_path = _natural_fixture(tmp_path, monkeypatch)
    cell = json.loads(cell_path.read_text())
    registry_path = Path(cell["shared_contract"]["artifact_registry"]["path"])
    registry = json.loads(registry_path.read_text())
    source_path = Path(registry["artifacts"]["natural"]["payload"]["uri"].removeprefix("file:"))
    source_path.write_text(source_path.read_text() + "\n")
    with pytest.raises(mt.ProtocolError, match="material SHA-256 mismatch"):
        backend.build_backend_plan(cell_path)


def test_score_semantics_require_verified_first_model_visible_error() -> None:
    arm = {"arm_id": "score_first_error_prefixes"}
    record = {
        "state": {"content_sha256": "c" * 64},
        "semantics": {
            "mode": "verified_first_model_visible_error_prefix",
            "student_trajectory_id": "student-1",
            "first_error_index": 4,
            "verified_prefix_token_count": 2,
            "verified_prefix_sha256": "a" * 64,
            "correction_target_sha256": "b" * 64,
            "first_error_evidence_sha256": "d" * 64,
            "prefix_verifier_sha256": "e" * 64,
        },
    }
    assert backend._validate_semantics(arm, record, record_id="score-1")["first_error_index"] == 4
    record["semantics"]["mode"] = "unverified_error_prefix"
    with pytest.raises(mt.ProtocolError, match="verified first-error prefix"):
        backend._validate_semantics(arm, record, record_id="score-1")


def test_score_bundle_binds_verified_token_boundary_and_routes_adapter(
    tmp_path, monkeypatch
) -> None:
    cell_path = _natural_fixture(tmp_path, monkeypatch)
    cell = json.loads(cell_path.read_text())
    registry_path = Path(cell["shared_contract"]["artifact_registry"]["path"])
    registry = json.loads(registry_path.read_text())
    artifact = registry["artifacts"]["natural"]
    source_path = Path(artifact["payload"]["uri"].removeprefix("file:"))
    record = json.loads(source_path.read_text())
    record["state"]["kind"] = "verified_first_error_prefix_state"
    record["state"]["constructor"] = "restore_verified_pre_error_state"
    record["history"]["kind"] = "verified_pre_error_prefix"
    record["history"]["source"] = "same_verified_student_trajectory"
    record["supervision"]["advantages"] = None
    record["supervision"]["behavior_logprobs"] = None
    record["semantics"] = {
        "mode": "verified_first_model_visible_error_prefix",
        "student_trajectory_id": "student-1",
        "first_error_index": 1,
        "verified_prefix_token_count": 1,
        "verified_prefix_sha256": _sha_json([10]),
        "correction_target_sha256": _sha_json([11, 12]),
        "first_error_evidence_sha256": "d" * 64,
        "prefix_verifier_sha256": "e" * 64,
    }
    source_bytes = (json.dumps(record) + "\n").encode()
    source_path.write_bytes(source_bytes)
    artifact["kind"] = "verified_first_error_prefixes"
    artifact["payload"]["sha256"] = _sha_bytes(source_bytes)
    artifact["first_error_evidence"] = {
        "status": "pass",
        "metric": "first_model_visible_student_error",
        "evidence_sha256": "d" * 64,
        "prefix_verifier_sha256": "e" * 64,
    }
    registry_bytes = (json.dumps(registry, indent=2) + "\n").encode()
    registry_path.write_bytes(registry_bytes)
    cell["arm"] = {
        "arm_id": "score_first_error_prefixes",
        "role": "mechanism_or_baseline",
        "objective": "score",
        "training_artifact_id": "natural",
        "recovery": "on",
        "state_source": {
            "kind": "verified_first_error_prefix_state",
            "constructor": "restore_verified_pre_error_state",
        },
        "history_constructor": {
            "kind": "verified_pre_error_prefix",
            "source": "same_verified_student_trajectory",
        },
    }
    cell["shared_contract"]["artifact_registry"]["sha256"] = _sha_bytes(registry_bytes)
    cell_path.write_text(json.dumps(cell, indent=2) + "\n")

    plan, normalized = backend.build_backend_plan(cell_path)
    assert plan["trainer_route"] == {
        "entrypoint": backend.SCORE_STYLE_ADAPTER,
        "compatibility": "score_style_two_stage_adapter_stage2_inputs_required_not_executed",
        "reason": (
            "the adapter prepares correction SFT and validates the short-horizon target-reward "
            "loss contract, but fails closed until stage-1 checkpoint, stage-2 rollout/reward "
            "evidence, and per-stage budgets are registered"
        ),
    }
    assert normalized[0]["semantics"]["verified_prefix_token_count"] == 1
    assert normalized[0]["labels"] == [-100, 11, 12]


def test_guided_semantics_require_exact_rollout_decision_field() -> None:
    arm = {"arm_id": "guided_opd"}
    record = {
        "state": {"content_sha256": "c" * 64},
        "semantics": {
            "mode": "guided_opd_actor_turn",
            "trajectory_id": "trajectory-1",
            "turn_index": 1,
            "actor_role": "teacher",
            "turn_loss": "forward_kl",
            "role_decision": {"schema_version": "kaetram.guided-opd-role-decision.v1"},
        },
    }
    assert backend._validate_semantics(arm, record, record_id="guided-1")["role_decision"]
    record["semantics"]["unexpected"] = True
    with pytest.raises(mt.ProtocolError, match="fields must be exactly"):
        backend._validate_semantics(arm, record, record_id="guided-1")


def test_artifact_root_rejects_traversal_and_symlinks(tmp_path) -> None:
    root = tmp_path / "immutable-artifacts"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    with pytest.raises(mt.ProtocolError, match="inside the declared artifact root"):
        backend._material_path(f"file:{outside}", artifact_root=root, label="payload")
    link = root / "linked.jsonl"
    link.symlink_to(outside)
    with pytest.raises(mt.ProtocolError, match="symlink"):
        backend._material_path(f"file:{link}", artifact_root=root, label="payload")


def test_heldout_alias_scan_checks_values_not_json_keys() -> None:
    record = {
        "state": {"content": {"secret-quest-alias": "ordinary value"}},
        "history": {"content": ["visible history"]},
    }
    backend._history_alias_scan(record, ["secret-quest-alias"], record_id="record-1")
    record["history"]["content"].append("mentions SECRET-QUEST-ALIAS here")
    with pytest.raises(mt.ProtocolError, match="leaks held-out alias"):
        backend._history_alias_scan(record, ["secret-quest-alias"], record_id="record-1")


def test_heldout_alias_scan_rejects_separator_variants() -> None:
    record = {
        "state": {"content": {"location": "ordinary"}},
        "history": {"content": ["seek secret quest alias"]},
    }
    with pytest.raises(mt.ProtocolError, match="leaks held-out alias"):
        backend._history_alias_scan(
            record, ["secret-quest-alias"], record_id="record-1"
        )
    record["history"]["content"] = ["seek Ｄｙｉｎｇ Ｓｏｌｄｉｅｒ"]
    with pytest.raises(mt.ProtocolError, match="leaks held-out alias"):
        backend._history_alias_scan(
            record, ["Dying Soldier"], record_id="record-1"
        )


@pytest.mark.parametrize("bad_label", [-2, 1000])
def test_array_validation_rejects_invalid_label_token_ids(bad_label) -> None:
    supervision = {
        "input_ids": [10, 11],
        "labels": [-100, bad_label],
        "advantages": [0.0, 1.0],
        "behavior_logprobs": [0.0, -0.2],
        "step_weight": 1.0,
    }
    with pytest.raises(mt.ProtocolError, match="in-vocabulary token IDs"):
        backend._validate_arrays(
            supervision,
            objective="opd",
            record_id="record-1",
            tokenizer_vocab_size=1000,
            forbidden_token_sequences=[[777, 778]],
            aliases=["secret-quest-alias"],
            tokenizer=_FakeTokenizer(),
        )


def test_array_validation_rejects_heldout_token_sequence() -> None:
    supervision = {
        "input_ids": [10, 777, 778, 11],
        "labels": [-100, 777, 778, 11],
        "advantages": [0.0, 1.0, 1.0, 1.0],
        "behavior_logprobs": [0.0, -0.2, -0.2, -0.1],
        "step_weight": 1.0,
    }
    with pytest.raises(mt.ProtocolError, match="held-out token sequence"):
        backend._validate_arrays(
            supervision,
            objective="opd",
            record_id="record-1",
            tokenizer_vocab_size=1000,
            forbidden_token_sequences=[[777, 778]],
            aliases=["secret-quest-alias"],
            tokenizer=_FakeTokenizer(),
        )


def test_array_validation_scans_labels_and_rejects_mismatched_targets() -> None:
    label_leak = {
        "input_ids": [10, 11, 12, 13],
        "labels": [-100, 777, 778, 13],
        "advantages": [0.0, 1.0, 1.0, 1.0],
        "behavior_logprobs": [0.0, -0.2, -0.2, -0.1],
        "step_weight": 1.0,
    }
    with pytest.raises(mt.ProtocolError, match="labels leak"):
        backend._validate_arrays(
            label_leak,
            objective="opd",
            record_id="record-1",
            tokenizer_vocab_size=1000,
            forbidden_token_sequences=[[777, 778]],
            aliases=["secret-quest-alias"],
            tokenizer=_FakeTokenizer(),
        )
    label_leak["labels"] = [-100, 12, 12, 13]
    with pytest.raises(mt.ProtocolError, match="must equal"):
        backend._validate_arrays(
            label_leak,
            objective="opd",
            record_id="record-1",
            tokenizer_vocab_size=1000,
            forbidden_token_sequences=[[777, 778]],
            aliases=["secret-quest-alias"],
            tokenizer=_FakeTokenizer(),
        )


def test_array_validation_decodes_normalization_equivalent_token_leak() -> None:
    supervision = {
        "input_ids": [4737, 517, 12, 19179],
        "labels": [4737, 517, 12, 19179],
        "advantages": [0.0, 1.0, 1.0, 1.0],
        "behavior_logprobs": [0.0, -0.2, -0.2, -0.1],
        "step_weight": 1.0,
    }
    tokenizer = _FakeTokenizer({
        (4737, 517, 12, 19179): "Desert-Quest",
    })
    with pytest.raises(mt.ProtocolError, match="decodes to held-out"):
        backend._validate_arrays(
            supervision,
            objective="opd",
            record_id="record-1",
            aliases=["Desert Quest"],
            tokenizer_vocab_size=248077,
            forbidden_token_sequences=[[4737, 517, 14615]],
            tokenizer=tokenizer,
        )
    unicode_tokenizer = _FakeTokenizer({
        (42,): "Ｄｅｓｅｒｔ Ｑｕｅｓｔ",
    })
    unicode_supervision = {
        "input_ids": [42],
        "labels": [42],
        "advantages": [1.0],
        "behavior_logprobs": [-0.1],
        "step_weight": 1.0,
    }
    with pytest.raises(mt.ProtocolError, match="decodes to held-out"):
        backend._validate_arrays(
            unicode_supervision,
            objective="opd",
            record_id="record-unicode",
            aliases=["Desert Quest"],
            tokenizer_vocab_size=248077,
            forbidden_token_sequences=[[4737, 517, 14615]],
            tokenizer=unicode_tokenizer,
        )


def test_effective_tokenizer_decodes_registered_added_token_suffix() -> None:
    tokenizer = backend._EffectiveTokenizer(
        _TinyRawTokenizer(),
        {0: "A", 1: "B", 2: "<extra-one>", 3: "<extra-two>"},
    )
    assert tokenizer.get_vocab_size(with_added_tokens=True) == 4
    assert tokenizer.decode([0, 2, 1, 3]) == "A<extra-one>B<extra-two>"
    with pytest.raises(mt.ProtocolError, match="absent"):
        tokenizer.decode([4])
