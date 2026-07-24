from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from run_manifest import sha256_json
from canonical_start import CANONICAL_INITIAL_STATE
from scripts.opd.analyze_local_weight_pilot import (
    AnalysisError,
    _api_error_count,
    _canonical_start_ok,
    _file_sha256,
    _ordered_session_logs,
    _validate_cell_attestation,
    _validate_prelaunch,
    _validate_raw_emissions,
    _validate_state_boundaries,
    _verify_artifacts,
    summarize_rows,
)


def _row(weight: str, value: int) -> dict:
    return {
        "weight": weight,
        "valid_tools": value,
        "valid_tools_per_minute": value / 5,
        "turns": value,
        "tool_parse_rate": 1.0,
        "api_errors": 0,
        "raw_generations": value + 1,
        "generations_with_structured_call": value,
        "generations_without_structured_call": 1,
        "emitted_structured_calls": value,
        "budget_overrun_seconds": 10.0,
        "core3_stages_advanced": 0,
        "quest_stages_advanced": 0,
        "xp_db_delta": value,
        "unique_positions": 2,
    }


def test_descriptive_summary_preserves_all_three_cells_per_weight() -> None:
    rows = [
        _row(weight, value)
        for weight in ("base", "r2", "r3")
        for value in (1, 2, 3)
    ]
    summary = summarize_rows(rows)
    assert summary["base"]["valid_tools"] == [1, 2, 3]
    assert summary["base"]["mean_valid_tools"] == 2
    assert summary["r2"]["mean_valid_tools_per_minute"] == 0.4
    assert summary["r3"]["zero_turn_cells"] == 0
    assert all(item["api_errors"] == 0 for item in summary.values())


def test_canonical_start_validator_is_exact() -> None:
    state = {"canonical_first_observation": deepcopy(CANONICAL_INITIAL_STATE)}
    assert _canonical_start_ok(state)
    state["canonical_first_observation"]["is_dead"] = True
    assert not _canonical_start_ok(state)


def _write_inventory(root: Path, records: list[dict]) -> str:
    inventory = {
        "schema_version": "kaetram.local-weight-pilot-artifacts.v1",
        "file_count": len(records),
        "files": records,
        "tree_sha256": sha256_json(records),
    }
    path = root / "artifact-inventory.json"
    path.write_text(json.dumps(inventory, sort_keys=True))
    return _file_sha256(path)


def test_artifact_verifier_rejects_files_outside_sealed_inventory(
    tmp_path: Path,
) -> None:
    retained = tmp_path / "result.json"
    retained.write_text("{}")
    digest = _write_inventory(
        tmp_path,
        [{
            "path": retained.name,
            "size_bytes": retained.stat().st_size,
            "sha256": _file_sha256(retained),
        }],
    )
    assert _verify_artifacts(tmp_path, digest) == 1
    (tmp_path / "unsealed.txt").write_text("late mutation")
    with pytest.raises(AnalysisError, match="file set differs"):
        _verify_artifacts(tmp_path, digest)


def test_artifact_verifier_rejects_path_traversal(tmp_path: Path) -> None:
    digest = _write_inventory(
        tmp_path,
        [{"path": "../outside", "size_bytes": 0, "sha256": "0" * 64}],
    )
    with pytest.raises(AnalysisError, match="unsafe or duplicate"):
        _verify_artifacts(tmp_path, digest)


def test_raw_emission_audit_counts_no_call_generations(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text(
        "\n".join([
            json.dumps({
                "type": "raw_model_emission",
                "tool_calls": [],
            }),
            json.dumps({
                "type": "raw_model_emission",
                "tool_calls": [{
                    "name": "warp",
                    "arguments": '{"location":"mudwich"}',
                }],
            }),
        ])
    )
    metrics = _validate_raw_emissions([log])
    assert metrics["raw_generations"] == 2
    assert metrics["emitted_structured_calls"] == 1
    assert metrics["generations_without_structured_call"] == 1
    assert metrics["raw_malformed_emissions"] == 0
    assert metrics["raw_recoverable_calls"] == 0


def test_raw_emission_audit_counts_recoverable_malformed_calls(
    tmp_path: Path,
) -> None:
    log = tmp_path / "session_1.log"
    log.write_text(json.dumps({
        "type": "raw_model_emission",
        "content": '<function=warp("mudwich")>',
        "tool_calls": [],
    }))
    metrics = _validate_raw_emissions([log])
    assert metrics["raw_malformed_emissions"] == 1
    assert metrics["raw_recoverable_calls"] == 1
    assert metrics["raw_recoverable_action_counts"] == {"warp": 1}


def test_raw_emission_audit_rejects_malformed_arguments(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text(json.dumps({
        "type": "raw_model_emission",
        "tool_calls": [{"name": "warp", "arguments": "{bad"}],
    }))
    with pytest.raises(AnalysisError, match="not valid JSON"):
        _validate_raw_emissions([log])


def test_raw_emission_audit_rejects_malformed_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text("{bad\n")
    with pytest.raises(AnalysisError, match="malformed retained JSONL"):
        _validate_raw_emissions([log])


def test_api_error_audit_reads_retained_stderr(tmp_path: Path) -> None:
    stderr = tmp_path / "sandbox" / "debug" / "stderr.log"
    stderr.parent.mkdir(parents=True)
    stderr.write_text("  [2] API error: transient\ncontinued\n")
    assert _api_error_count(tmp_path) == 1


def test_session_order_uses_preserved_execution_time_beyond_nine(
    tmp_path: Path,
) -> None:
    paths = []
    for index in range(1, 12):
        path = tmp_path / f"session_{index}_test.log"
        path.write_text("{}\n")
        paths.append(path)
    assert _ordered_session_logs(tmp_path) == paths


def test_state_boundary_audit_rejects_missing_db_snapshot() -> None:
    with pytest.raises(AnalysisError, match="missing DB boundary"):
        _validate_state_boundaries({}, "cell")


def _extended_prelaunch_fixture() -> tuple[dict, dict]:
    manifest = {
        "pilot_id": "pilot-v1",
        "claim_boundary": "exploratory",
        "protocol": {"mongo_database": "kaetram_eval"},
        "models": {
            "base": {"api_model": "2b-base"},
            "r2": {"api_model": "2b-r2"},
        },
        "cells": [{"cell_id": "base"}, {"cell_id": "r2"}],
    }
    database = {
        "schema": "kaetram-game-database-attestation/v2",
        "expected_database": "kaetram_eval",
        "effective_database": "kaetram_eval",
        "effective_backend": "mongodb",
        "skip_database": False,
        "effective_host": "127.0.0.1",
        "effective_port": 27017,
        "tls": False,
        "srv": False,
        "authentication_enabled": False,
        "node_env": "",
        "config_files": [
            {"path": ".env.defaults", "sha256": "1" * 64},
            {"path": ".env", "sha256": "2" * 64},
        ],
    }
    database["attestation_sha256"] = sha256_json(database)
    common = {
        "tokenizer_sha256": "3" * 64,
        "render_contract_sha256": "4" * 64,
        "chat_template_sha256": "5" * 64,
        "snapshot_lock_sha256": "6" * 64,
        "fix_mistral_regex": False,
    }
    def python_receipt(kind: str, marker_schema: str, tree: str) -> dict:
        marker = {
            "schema_version": marker_schema,
            "git_commit": "a" * 40,
            "lock_sha256": "a" * 64,
            "python_version": "3.12.12",
            "python_executable_sha256": "b" * 64,
            "pip_version": "26.1.2",
            "installed_distribution_count": 10,
            "installed_file_count": 100,
            "installed_tree_sha256": tree * 64,
            "runtime_search_path_count": 3,
            "runtime_tree_sha256": ("e" if kind == "local_eval" else "f") * 64,
        }
        if kind == "local_mlx":
            marker.update({"sys_platform": "darwin", "machine": "arm64"})
        record = {
            "schema_version": "kaetram.pinned-python-environment-receipt.v1",
            "environment_kind": kind,
            "marker_sha256": sha256_json(marker),
            "marker": marker,
        }
        return {**record, "receipt_sha256": sha256_json(record)}

    eval_environment = python_receipt(
        "local_eval", "kaetram.local-unit-tests.v3", "c"
    )
    mlx_environment = python_receipt(
        "local_mlx", "kaetram.local-mlx-environment.v3", "d"
    )
    playwright = {
        "schema_version": "kaetram.playwright-runtime-receipt.v1",
        "browser_name": "chromium",
        "browser_version": "149.0.7827.55",
        "executable_sha256": "e" * 64,
    }
    playwright["receipt_sha256"] = sha256_json(playwright)
    mongodb = {
        "schema_version": "kaetram.mongodb-runtime-receipt.v1",
        "container_name": "kaetram-mongo",
        "database": "kaetram_eval",
        "host": "127.0.0.1",
        "port": 27017,
        "image_id": (
            "sha256:b3b6a0771f6a4c269cc1fe1fd59e84e9c7f1601f0e273571004158e0ba8c5705"
        ),
        "image_repo_digest": (
            "mongo@sha256:9bdaeb6dac6e7e762e84e2f84103d1f9bb078fa1ba6bde8bb9d2274f655ad173"
        ),
        "docker_client_version": "29.2.1",
    }
    mongodb["receipt_sha256"] = sha256_json(mongodb)
    prelaunch = {
        "schema_version": "kaetram.local-weight-pilot-prelaunch.v3",
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "source_git_commit": "a" * 40,
        "game_git_commit": "b" * 40,
        "game_build_attestation": {
            "schema": "kaetram-server-build-attestation/v1",
            "gameRevision": "b" * 40,
            "entrypointSha256": "7" * 64,
        },
        "game_database_attestation": database,
        "endpoint_receipts": {
            name: {
                "status": "ok",
                "attestation": {
                    **common,
                    "api_model": model["api_model"],
                    "checkpoint_sha256": ("8" if name == "base" else "9") * 64,
                    "snapshot_tree_sha256": ("c" if name == "base" else "d") * 64,
                    "runtime_environment_receipt_sha256": mlx_environment[
                        "receipt_sha256"
                    ],
                },
            }
            for name, model in manifest["models"].items()
        },
        "cells": manifest["cells"],
        "runtime": {
            "eval_python": "/repo/.venv-unit-tests/bin/python",
            "mlx_python": "/repo/.venv-local-mlx/bin/python",
            "node_binary": "/path/to/node20",
            "node_version": "v20.20.2",
            "eval_environment": eval_environment,
            "mlx_environment": mlx_environment,
            "playwright": playwright,
            "mongodb": mongodb,
        },
    }
    return manifest, prelaunch


def test_prelaunch_validator_binds_full_snapshot_and_database_identity() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    validated = _validate_prelaunch(manifest, prelaunch)
    assert validated["snapshot_lock_sha256"] == "6" * 64
    assert validated["snapshot_tree_sha256"] == {
        "base": "c" * 64,
        "r2": "d" * 64,
    }
    assert validated["game_database_attestation_sha256"] == (
        prelaunch["game_database_attestation"]["attestation_sha256"]
    )

    prelaunch["game_database_attestation"]["config_files"][1]["sha256"] = "e" * 64
    with pytest.raises(AnalysisError, match="game-database attestation"):
        _validate_prelaunch(manifest, prelaunch)


def test_prelaunch_validator_rejects_partial_snapshot_identity() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    del prelaunch["endpoint_receipts"]["r2"]["attestation"][
        "snapshot_tree_sha256"
    ]
    with pytest.raises(AnalysisError, match="invalid snapshot_tree_sha256"):
        _validate_prelaunch(manifest, prelaunch)


def test_v3_prelaunch_cannot_downgrade_by_deleting_new_attestations() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    del prelaunch["game_database_attestation"]
    for receipt in prelaunch["endpoint_receipts"].values():
        del receipt["attestation"]["snapshot_tree_sha256"]
        del receipt["attestation"]["snapshot_lock_sha256"]
    with pytest.raises(AnalysisError, match="invalid snapshot_tree_sha256"):
        _validate_prelaunch(manifest, prelaunch)


def test_v3_prelaunch_cannot_delete_or_swap_runtime_receipts() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    del prelaunch["runtime"]["mlx_environment"]
    with pytest.raises(AnalysisError, match="runtime receipt"):
        _validate_prelaunch(manifest, prelaunch)

    manifest, prelaunch = _extended_prelaunch_fixture()
    prelaunch["runtime"]["mlx_environment"]["marker"]["installed_tree_sha256"] = (
        "f" * 64
    )
    with pytest.raises(AnalysisError, match="Python environment"):
        _validate_prelaunch(manifest, prelaunch)


def test_intermediate_v2_requires_explicit_legacy_admission() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    prelaunch["schema_version"] = "kaetram.local-weight-pilot-prelaunch.v2"
    del prelaunch["runtime"]
    for receipt in prelaunch["endpoint_receipts"].values():
        del receipt["attestation"]["runtime_environment_receipt_sha256"]
    with pytest.raises(AnalysisError, match="prelaunch contract differs"):
        _validate_prelaunch(manifest, prelaunch)
    validated = _validate_prelaunch(
        manifest, prelaunch, allow_legacy_v1=True
    )
    assert validated["provenance_tier"] == "prospective_v2_attested"
    assert validated["runtime_receipts"] is None


def test_legacy_v1_prelaunch_remains_explicitly_analyzable() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    prelaunch["schema_version"] = "kaetram.local-weight-pilot-prelaunch.v1"
    del prelaunch["game_database_attestation"]
    for receipt in prelaunch["endpoint_receipts"].values():
        del receipt["attestation"]["snapshot_tree_sha256"]
        del receipt["attestation"]["snapshot_lock_sha256"]
    with pytest.raises(AnalysisError, match="prelaunch contract differs"):
        _validate_prelaunch(manifest, prelaunch)
    validated = _validate_prelaunch(
        manifest,
        prelaunch,
        allow_legacy_v1=True,
    )
    assert validated["snapshot_tree_sha256"] is None
    assert validated["game_database_attestation"] is None


def test_v2_prelaunch_rejects_self_hashed_malformed_database_shape() -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    database = prelaunch["game_database_attestation"]
    database["config_files"] = "not-a-list"
    unsigned = dict(database)
    unsigned.pop("attestation_sha256")
    database["attestation_sha256"] = sha256_json(unsigned)
    with pytest.raises(AnalysisError, match="game-database attestation"):
        _validate_prelaunch(manifest, prelaunch)


def test_cell_validator_rejects_snapshot_tree_drift(tmp_path: Path) -> None:
    manifest, prelaunch = _extended_prelaunch_fixture()
    validated = _validate_prelaunch(manifest, prelaunch)
    receipt = deepcopy(prelaunch["endpoint_receipts"]["base"])
    receipt["attestation"]["snapshot_tree_sha256"] = "e" * 64
    (tmp_path / "endpoint-attestation.json").write_text(json.dumps(receipt))
    with pytest.raises(AnalysisError, match="endpoint attestation mismatch"):
        _validate_cell_attestation(
            tmp_path,
            "base",
            manifest["models"]["base"],
            validated,
        )
