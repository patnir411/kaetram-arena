from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd import make_uniform_advantages as uniform_module
from scripts.opd import resample_records as resample_module
from scripts.opd.make_uniform_advantages import (
    ArtifactBuildError as UniformBuildError,
)
from scripts.opd.make_uniform_advantages import build_uniform_advantages
from scripts.opd.resample_records import (
    ArtifactBuildError as ResampleBuildError,
)
from scripts.opd.resample_records import resample_records
from scripts.opd.training_record_bundle import (
    TrainingRecordBundleError,
    load_verified_training_records,
)
from scripts.opd.opd_data_manifest import (
    BUILDER_RELATIVE_PATH,
    MANIFEST_SCHEMA_VERSION as ROOT_MANIFEST_SCHEMA_VERSION,
)
from scripts.opd.receipt_chain import (
    BUILD_SOURCE_PATHS,
    canonical_sha256,
    sha256_path,
)
from scripts.opd.record_schema import (
    OPD_TRAIN_RECORD_SCHEMA_SHA256,
    OPD_TRAIN_RECORD_SCHEMA_VERSION,
    OPD_TRAIN_RECORD_VALIDATOR_SHA256,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    _write_root_receipt(path)


def _write_root_receipt(path: Path) -> Path:
    repo = Path(__file__).parents[2]
    inventory = [{
        "run_id": "run_1",
        "path": "dataset/raw/agent_test/runs/run_1/session_1.log",
        "sha256": "c" * 64,
        "size_bytes": 1,
        "meta_path": "dataset/raw/agent_test/runs/run_1/session_1.meta.json",
        "meta_sha256": "9" * 64,
        "meta_size_bytes": 1,
        "personality_prompt_path": "prompts/personalities/completionist.md",
    }]
    tokenizer_sha = "d" * 64
    attestation = lambda label: {
        "deployment_id": f"{label}-deployment",
        "api_model": f"{label}-model",
        "checkpoint_sha256": ("a" if label == "student" else "b") * 64,
        "tokenizer_sha256": tokenizer_sha,
        "render_contract_sha256": "e" * 64,
    }
    build_sources = {
        relative: sha256_path(repo / relative)
        for relative in BUILD_SOURCE_PATHS
    }
    receipt = {
        "schema_version": ROOT_MANIFEST_SCHEMA_VERSION,
        "builder": BUILDER_RELATIVE_PATH,
        "script_sha256": build_sources[BUILDER_RELATIVE_PATH],
        "source_runs": ["run_1"],
        "source_logs": inventory,
        "source_sha256": canonical_sha256(inventory),
        "output": str(path),
        "output_sha256": _sha256(path),
        "heldout": "heldout.jsonl",
        "heldout_sha256": "f" * 64,
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "n_records": len(path.read_bytes().splitlines()),
        "n_heldout": 0,
        "candidate_states": len(path.read_bytes().splitlines()),
        "candidate_states_sha256": "2" * 64,
        "status_counts": {
            "ok": len(path.read_bytes().splitlines()),
        },
        "excluded_states": [],
        "excluded_states_sha256": canonical_sha256([]),
        "build_sources": build_sources,
        "parameters": {
            "student_endpoint_attestation": attestation("student"),
            "teacher_endpoint_attestation": attestation("teacher"),
            "tokenizer_sha256": tokenizer_sha,
            "tokenizer_snapshot_sha256": "1" * 64,
            "runtime_versions": {
                "python": "3.12.0",
                "httpx": "0.28.1",
                "transformers": "5.5.0",
                "tokenizers": "0.22.2",
            },
            "max_history_messages": 28,
            "max_sequence_tokens": 16384,
            "kl_coefficient": 1.0,
            "holdout_every": 10,
            "early_weight": 1.5,
            "malformed_parameter_pattern": r"<parameter=[^>\n]*=[^>\n]*>",
            "counterfactual_grading": True,
            "limit": 0,
        },
    }
    receipt_path = path.with_suffix(".manifest.json")
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt_path


def _record(record_id: int, advantages: list[float]) -> dict:
    target_ids = list(range(2, len(advantages) + 2))
    return {
        "record_id": record_id,
        "input_ids": [1] + target_ids,
        "labels": [-100] + target_ids,
        "behavior_logprobs": [0.0] + [-1.0] * len(advantages),
        "advantages": [0.0] + advantages,
        "step_weight": 1.0,
        "n_action": len(advantages),
    }


def test_uniform_advantages_preserves_mask_and_attests_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "uniform.jsonl"
    _write_jsonl(
        source,
        [
            {
                **_record(0, [-1.0, 3.0]),
                "labels": [-100, 2, 3],
                "behavior_logprobs": [0.0, -1.0, -2.0],
            },
            _record(1, [2.0, 0.0]),
        ],
    )

    manifest = build_uniform_advantages(source, output)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[0]["advantages"] == [0.0, 2.0, 2.0]
    assert records[1]["advantages"] == [0.0, 2.0, 0.0]
    assert manifest["c"] == 2.0
    assert manifest["source_sha256"] == _sha256(source)
    assert manifest["output_sha256"] == _sha256(output)
    assert manifest["record_schema_version"] == "kaetram-opd-train-record-v2"
    assert manifest["schema_version"] == "uniform-advantages-manifest-v3"
    assert manifest["parent_manifest"]["schema_version"] == ROOT_MANIFEST_SCHEMA_VERSION
    assert manifest["parent_manifest_sha256"] == canonical_sha256(
        manifest["parent_manifest"]
    )
    assert len(manifest["record_schema_sha256"]) == 64
    assert len(manifest["record_schema_validator_sha256"]) == 64
    assert json.loads(output.with_suffix(".manifest.json").read_text()) == manifest


@pytest.mark.parametrize(
    "record,match",
    [
        (_record(0, [0.0, 0.0]), "no nonzero advantages"),
        (
            _record(0, [1.0, float("inf")]),
            "finite numeric list",
        ),
        (
            {**_record(0, [1.0, 2.0]), "input_ids": [1, 2]},
            "aligned",
        ),
    ],
)
def test_uniform_advantages_rejects_invalid_corpora(
    tmp_path: Path,
    record: dict,
    match: str,
) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [record])
    with pytest.raises(UniformBuildError, match=match):
        build_uniform_advantages(source, tmp_path / "output.jsonl")


def test_uniform_advantages_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, [_record(0, [1.0])])
    output.write_text("owned-by-user\n")
    with pytest.raises(UniformBuildError, match="refusing to overwrite"):
        build_uniform_advantages(source, output)
    assert output.read_text() == "owned-by-user\n"


@pytest.mark.parametrize(
    ("publisher", "error"),
    [
        (uniform_module._publish_create_only, UniformBuildError),
        (resample_module._publish_create_only, ResampleBuildError),
    ],
)
def test_transform_publish_never_replaces_late_destination(
    tmp_path: Path,
    publisher,
    error: type[Exception],
) -> None:
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new\n")
    destination.write_text("owned\n")
    with pytest.raises(error, match="concurrently created"):
        publisher(temporary, destination)
    assert destination.read_text() == "owned\n"
    assert temporary.read_text() == "new\n"


def test_resample_is_deterministic_exact_count_and_attested(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output_a = tmp_path / "resampled-a.jsonl"
    output_b = tmp_path / "resampled-b.jsonl"
    _write_jsonl(
        source,
        [_record(0, [1.0]), _record(1, [2.0]), _record(2, [3.0])],
    )

    manifest_a = resample_records(source, output_a, target=9, seed=42)
    manifest_b = resample_records(source, output_b, target=9, seed=42)
    lines_a = output_a.read_bytes().splitlines()
    lines_b = output_b.read_bytes().splitlines()
    assert len(lines_a) == 9
    assert lines_a == lines_b
    assert lines_a[:3] == source.read_bytes().splitlines()
    assert manifest_a["output_sha256"] == _sha256(output_a)
    assert manifest_a["record_schema_version"] == "kaetram-opd-train-record-v2"
    assert manifest_a["schema_version"] == "resampled-records-manifest-v3"
    assert manifest_a["parent_manifest"]["schema_version"] == ROOT_MANIFEST_SCHEMA_VERSION
    assert len(manifest_a["record_schema_sha256"]) == 64
    assert len(manifest_a["record_schema_validator_sha256"]) == 64
    assert manifest_a["sampled_indices_sha256"] == manifest_b["sampled_indices_sha256"]
    assert json.loads(output_a.with_suffix(".manifest.json").read_text()) == manifest_a


def test_resample_rejects_malformed_input_and_overwrite(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(json.dumps(_record(0, [1.0])) + "\nnot-json\n")
    _write_root_receipt(malformed)
    with pytest.raises(ResampleBuildError, match="invalid UTF-8 JSON"):
        resample_records(malformed, tmp_path / "out.jsonl", target=3, seed=1)

    source = tmp_path / "source.jsonl"
    output = tmp_path / "existing.jsonl"
    _write_jsonl(source, [_record(0, [1.0])])
    output.write_text("owned-by-user\n")
    with pytest.raises(ResampleBuildError, match="refusing to overwrite"):
        resample_records(source, output, target=2, seed=1)
    assert output.read_text() == "owned-by-user\n"


def test_transformers_reject_noncanonical_json_objects(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [{"advantages": [1.0]}])
    with pytest.raises(UniformBuildError, match="missing required OPD field"):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")
    with pytest.raises(ResampleBuildError, match="missing required OPD field"):
        resample_records(source, tmp_path / "resampled.jsonl", target=2, seed=1)


@pytest.mark.parametrize(
    "record,match",
    [
        (
            {
                **_record(0, [100.0, 1.0]),
                "labels": [-100, 2, 3],
                "advantages": [100.0, 1.0, 1.0],
            },
            "ignored position 0 must have zero",
        ),
        (
            {
                **_record(0, [0.0, 1.0]),
                "labels": [-100, 1, 3],
            },
            "must equal input_id",
        ),
        (
            {
                **_record(0, [0.0, 1.0, 0.0]),
                "labels": [-100, 2, -100, 4],
            },
            "contiguous prefix",
        ),
    ],
)
def test_transformers_reject_training_corrupting_mask_geometry(
    tmp_path: Path,
    record: dict,
    match: str,
) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [record])
    with pytest.raises(UniformBuildError, match=match):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")
    with pytest.raises(ResampleBuildError, match=match):
        resample_records(source, tmp_path / "resampled.jsonl", target=2, seed=1)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({"labels": [1, 2]}, "label position 0"),
        (
            {
                "input_ids": [1, 2],
                "labels": [-100, 2],
                "advantages": [0.0, 1.0],
                "behavior_logprobs": [0.0, 0.1],
                "step_weight": 1.0,
            },
            "non-positive",
        ),
        (
            {
                "input_ids": [1],
                "labels": [-100],
                "advantages": [0.0],
                "behavior_logprobs": [0.0],
                "step_weight": 1.0,
            },
            "at least two",
        ),
        ({"n_action": 99}, "post-shift"),
    ],
)
def test_causal_training_schema_rejects_unusable_records(
    tmp_path: Path,
    mutation: dict,
    match: str,
) -> None:
    record = _record(0, [1.0])
    record.update(mutation)
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [record])
    with pytest.raises(UniformBuildError, match=match):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")


def test_trainer_bundle_verifies_receipt_transform_and_parameters(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "uniform.jsonl"
    _write_jsonl(source, [_record(0, [-1.0, 3.0]), _record(1, [2.0])])
    build_uniform_advantages(source, output)
    loaded = load_verified_training_records(
        output, output.with_suffix(".manifest.json")
    )
    assert len(loaded) == 2

    manifest_path = output.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["n_records"] = 999
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(TrainingRecordBundleError, match="n_records mismatch"):
        load_verified_training_records(output, manifest_path)


def test_trainer_bundle_rejects_missing_receipt_and_byte_tamper(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "resampled.jsonl"
    _write_jsonl(source, [_record(0, [1.0]), _record(1, [2.0])])
    resample_records(source, output, target=3, seed=42)
    manifest_path = output.with_suffix(".manifest.json")

    with pytest.raises(TrainingRecordBundleError, match="required"):
        load_verified_training_records(output, "")
    output.write_bytes(output.read_bytes() + b"\n")
    with pytest.raises(TrainingRecordBundleError, match="SHA-256"):
        load_verified_training_records(output, manifest_path)


def test_builder_receipt_supports_canonical_untransformed_records(
    tmp_path: Path,
) -> None:
    records = tmp_path / "records.jsonl"
    receipt = tmp_path / "records.manifest.json"
    _write_jsonl(records, [_record(0, [1.0]), _record(1, [-2.0])])
    assert len(load_verified_training_records(records, receipt)) == 2


def test_builder_receipt_rejects_unaccounted_or_transient_statuses(
    tmp_path: Path,
) -> None:
    records = tmp_path / "records.jsonl"
    receipt_path = tmp_path / "records.manifest.json"
    _write_jsonl(records, [_record(0, [1.0])])
    receipt = json.loads(receipt_path.read_text())
    receipt["candidate_states"] = 2
    receipt["status_counts"] = {"ok": 1, "score_fail": 1}
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(
        TrainingRecordBundleError,
        match="candidate/status accounting",
    ):
        load_verified_training_records(records, receipt_path)


def test_generic_posthoc_identity_receipts_are_not_supported(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    receipt = tmp_path / "records.manifest.json"
    _write_jsonl(records, [_record(0, [1.0])])
    receipt.write_text(json.dumps({
        "schema_version": "opd-training-records-manifest-v1",
        "source_sha256": "a" * 64,
        "output_sha256": _sha256(records),
    }))
    with pytest.raises(TrainingRecordBundleError, match="unsupported"):
        load_verified_training_records(records, receipt)


def test_transformers_require_and_preserve_a_recursive_parent_chain(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    uniform = tmp_path / "uniform.jsonl"
    resampled = tmp_path / "resampled.jsonl"
    _write_jsonl(source, [_record(0, [-1.0]), _record(1, [2.0])])
    build_uniform_advantages(source, uniform)
    resample_records(uniform, resampled, target=3, seed=7)
    receipt = json.loads(resampled.with_suffix(".manifest.json").read_text())
    assert receipt["parent_manifest"]["schema_version"] == (
        "uniform-advantages-manifest-v3"
    )
    assert receipt["parent_manifest"]["parent_manifest"]["schema_version"] == (
        ROOT_MANIFEST_SCHEMA_VERSION
    )
    assert len(load_verified_training_records(
        resampled, resampled.with_suffix(".manifest.json")
    )) == 3

    receipt["parent_manifest"]["parent_manifest"]["source_runs"] = ["fake-run"]
    receipt["parent_manifest"]["parent_manifest_sha256"] = canonical_sha256(
        receipt["parent_manifest"]["parent_manifest"]
    )
    receipt["parent_manifest_sha256"] = canonical_sha256(receipt["parent_manifest"])
    resampled.with_suffix(".manifest.json").write_text(json.dumps(receipt))
    with pytest.raises(
        TrainingRecordBundleError, match="source run coverage"
    ):
        load_verified_training_records(
            resampled, resampled.with_suffix(".manifest.json")
        )


def test_trainer_rejects_plausible_tamper_in_intermediate_uniform_receipt(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    uniform = tmp_path / "uniform.jsonl"
    resampled = tmp_path / "resampled.jsonl"
    _write_jsonl(source, [_record(0, [-1.0]), _record(1, [3.0])])
    build_uniform_advantages(source, uniform)
    resample_records(uniform, resampled, target=4, seed=7)

    receipt_path = resampled.with_suffix(".manifest.json")
    receipt = json.loads(receipt_path.read_text())
    receipt["parent_manifest"]["c"] = 1.25
    receipt["parent_manifest_sha256"] = canonical_sha256(receipt["parent_manifest"])
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(
        TrainingRecordBundleError,
        match="nonzero advantage different from c",
    ):
        load_verified_training_records(resampled, receipt_path)


def test_trainer_rejects_tampered_resample_recipe(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "resampled.jsonl"
    _write_jsonl(source, [_record(0, [1.0]), _record(1, [2.0])])
    resample_records(source, output, target=4, seed=7)

    receipt_path = output.with_suffix(".manifest.json")
    receipt = json.loads(receipt_path.read_text())
    receipt["seed"] = 8
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(
        TrainingRecordBundleError,
        match="sampled-index digest mismatch",
    ):
        load_verified_training_records(output, receipt_path)


def test_transformer_rejects_second_uniform_anywhere_in_chain(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    uniform = tmp_path / "uniform.jsonl"
    resampled = tmp_path / "resampled.jsonl"
    _write_jsonl(source, [_record(0, [1.0]), _record(1, [3.0])])
    build_uniform_advantages(source, uniform)
    resample_records(uniform, resampled, target=3, seed=7)
    with pytest.raises(UniformBuildError, match="multiple uniform"):
        build_uniform_advantages(resampled, tmp_path / "uniform-again.jsonl")


def test_transformer_rejects_source_without_builder_receipt(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(_record(0, [1.0])) + "\n")
    with pytest.raises(UniformBuildError, match="source provenance manifest"):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")
