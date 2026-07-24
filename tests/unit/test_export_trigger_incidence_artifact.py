import json
import shutil
import sys
from pathlib import Path

import pytest

from scripts.opd import trigger_incidence_probe as probe
from scripts.opd.export_trigger_incidence_artifact import (
    ANALYSIS_FILES,
    EXPORT_SCHEMA,
    PUBLIC_ATTESTATION_EXTRAS,
    RUN_FILES,
    ExportError,
    export_bundle,
    sha256_file,
    sha256_json,
)


RUN_DATA_FILES = (
    "prelaunch.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
)
COMMIT = "a" * 40


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _seal_run(root: Path, study_id: str, snapshot: str) -> None:
    records = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": sha256_file(root / name),
        }
        for name in RUN_DATA_FILES
    ]
    _write_json(
        root / "artifact-index.json",
        {
            "schema_version": f"{probe.RUN_SCHEMA}.artifacts",
            "study_id": study_id,
            "snapshot": snapshot,
            "files": records,
            "tree_sha256": sha256_json(records),
        },
    )


def _seal_analysis(root: Path) -> None:
    names = ("analysis-summary.json", "cells.csv", "contrasts.csv")
    records = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": sha256_file(root / name),
        }
        for name in names
    ]
    _write_json(
        root / "artifact-index.json",
        {
            "schema_version": f"{probe.ANALYSIS_SCHEMA}.artifacts",
            "files": records,
            "tree_sha256": sha256_json(records),
        },
    )


def _health(registration: dict, snapshot: str) -> dict:
    expected = registration["snapshots"][snapshot]
    attestation = {
        "api_model": expected["api_model"],
        "checkpoint_sha256": expected["checkpoint_sha256"],
        **registration["endpoint_contract"],
        "deployment_id": f"local-{snapshot}",
        "runtime_environment_receipt_sha256": "1" * 64,
        "snapshot_lock_sha256": "2" * 64,
        "snapshot_tree_sha256": "3" * 64,
        "tokenizer_source_revision": "4" * 40,
    }
    assert set(attestation) == {
        "api_model",
        "checkpoint_sha256",
        *registration["endpoint_contract"].keys(),
        *PUBLIC_ATTESTATION_EXTRAS,
    }
    return {"status": "ok", "attestation": attestation}


def _completed(rows: list[dict], snapshot: str) -> dict:
    return {
        "schema_version": f"{probe.RUN_SCHEMA}.completed",
        "study_id": "study",
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": sum(row["status"] == "ok" for row in rows),
        "failed_requests": sum(row["status"] != "ok" for row in rows),
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity")) for row in rows
        ),
        "malformed_emissions": sum(
            bool(row.get("malformed_emission")) for row in rows
        ),
        "structured_tool_responses": sum(
            bool(row.get("has_structured_tool_call")) for row in rows
        ),
        "no_structured_tool_call_responses": sum(
            bool(row.get("no_structured_tool_call")) for row in rows
        ),
        "endpoint_identity_stable": True,
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )


def _load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _generate_analysis(fixture: dict) -> None:
    analysis_dir = fixture["analysis_dir"]
    if analysis_dir.exists():
        shutil.rmtree(analysis_dir)
    original = probe._git_identity
    probe._git_identity = lambda: {
        "source_git_commit": COMMIT,
        "dirty_paths": [],
    }
    try:
        probe.analyze(
            fixture["registration_path"],
            fixture["design_dir"] / "design.json",
            fixture["run_dirs"],
            analysis_dir,
        )
    finally:
        probe._git_identity = original


def _fixture(tmp_path: Path) -> dict:
    registration_path = tmp_path / "registration.json"
    conditions = [
        {
            "condition_id": "python-no-tools",
            "documentation": "python_docs",
            "native_tool_schema": "absent",
        },
        {
            "condition_id": "python-tools",
            "documentation": "python_docs",
            "native_tool_schema": "present",
        },
        {
            "condition_id": "canonical-no-tools",
            "documentation": "canonical_docs",
            "native_tool_schema": "absent",
        },
        {
            "condition_id": "canonical-tools",
            "documentation": "canonical_docs",
            "native_tool_schema": "present",
        },
    ]
    registration = {
        "schema_version": probe.REGISTRATION_SCHEMA,
        "study_id": "study",
        "snapshots": {
            snapshot: {
                "api_model": f"model-{snapshot}",
                "checkpoint_sha256": str(index) * 64,
            }
            for index, snapshot in enumerate(("base", "r2", "r3"), start=5)
        },
        "endpoint_contract": {
            "chat_template_sha256": "8" * 64,
            "fix_mistral_regex": False,
            "render_contract_sha256": "9" * 64,
            "tokenizer_sha256": "b" * 64,
        },
        "conditions": conditions,
        "state_pool": {
            "state_count": 1,
            "personality": "completionist",
        },
        "sampling": {
            "samples_per_state_condition": 1,
            "base_seed": 100,
        },
        "claim_boundary": "Finite-grid interface incidence only.",
    }
    _write_json(registration_path, registration)
    registration_sha = sha256_file(registration_path)

    design_dir = tmp_path / "design"
    messages = [
        {"role": "system", "content": "Use tools when useful."},
        {"role": "user", "content": "Continue the game."},
    ]
    states = [
        {
            "state_id": "state-01",
            "personality": "completionist",
            "source_log": "logs/source.jsonl",
            "source_log_sha256": "c" * 64,
            "messages_sha256": probe.sha256_json(messages),
            "messages": messages,
        }
    ]
    design = {
        "schema_version": probe.DESIGN_SCHEMA,
        "study_id": "study",
        "registration_sha256": registration_sha,
        "source_log_count": 1,
        "eligible_source_log_count": 1,
        "personality": "completionist",
        "selection_stride": 1,
        "states": states,
        "source_git_commit": COMMIT,
        "dirty_paths": [],
    }
    design_path = design_dir / "design.json"
    _write_json(design_path, design)
    _write_json(
        design_dir / "design.receipt.json",
        {
            "schema_version": f"{probe.DESIGN_SCHEMA}.receipt",
            "study_id": "study",
            "registration_sha256": registration_sha,
            "design_sha256": sha256_file(design_path),
            "state_count": 1,
            "selected_source_tree_sha256": probe._source_tree_sha256(states),
            "source_git_commit": COMMIT,
            "dirty_paths": [],
        },
    )

    run_dirs = []
    for snapshot in registration["snapshots"]:
        root = tmp_path / "runs" / snapshot
        root.mkdir(parents=True)
        health = _health(registration, snapshot)
        _write_json(
            root / "prelaunch.json",
            {
                "schema_version": f"{probe.RUN_SCHEMA}.prelaunch",
                "study_id": "study",
                "snapshot": snapshot,
                "registration_sha256": registration_sha,
                "design_sha256": sha256_file(design_path),
                "endpoint_health": health,
                "sampling": registration["sampling"],
                "source_git_commit": COMMIT,
                "dirty_paths": [],
            },
        )
        rows = []
        for schedule_index, condition in enumerate(conditions):
            message = {
                "role": "assistant",
                "content": f"plain response {schedule_index}",
            }
            rows.append(
                {
                    "schema_version": probe.RUN_SCHEMA,
                    "snapshot": snapshot,
                    "schedule_index": schedule_index,
                    "state_id": "state-01",
                    "state_index": 0,
                    "sample_index": 0,
                    "seed": 100,
                    "condition_id": condition["condition_id"],
                    "documentation": condition["documentation"],
                    "native_tool_schema": condition["native_tool_schema"],
                    "latency_seconds": 0.1,
                    "attempt_errors": [],
                    "status": "ok",
                    "response_message": message,
                    **probe.classify_response_message(message),
                }
            )
        _write_rows(root / "results.jsonl", rows)
        _write_json(
            root / "postflight.json",
            {
                "schema_version": f"{probe.RUN_SCHEMA}.postflight",
                "study_id": "study",
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
                "error": None,
            },
        )
        _write_json(root / "completed.json", _completed(rows, snapshot))
        _seal_run(root, "study", snapshot)
        run_dirs.append(root)

    fixture = {
        "registration_path": registration_path,
        "design_dir": design_dir,
        "run_dirs": run_dirs,
        "analysis_dir": tmp_path / "analysis",
    }
    _generate_analysis(fixture)
    return fixture


def _export(fixture: dict, output: Path) -> dict:
    return export_bundle(
        **fixture,
        output_dir=output,
        forbidden_fragments=(),
    )


def test_exports_semantically_verified_hash_bound_bundle(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    manifest = _export(fixture, output)

    assert manifest["schema_version"] == EXPORT_SCHEMA
    assert len(manifest["files"]) == 3 + 3 * len(RUN_FILES) + len(ANALYSIS_FILES)
    assert (output / "runs" / "r3" / "results.jsonl").is_file()
    assert any(
        record["path"] == "runs/base/artifact-index.json"
        for record in manifest["files"]
    )
    assert json.loads((output / "artifact-index.json").read_text()) == manifest


def test_rejects_resealed_row_with_forged_outcome(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    run_dir = fixture["run_dirs"][0]
    rows = _load_rows(run_dir / "results.jsonl")
    rows[0]["response_message"]["content"] = (
        "<tool_call><function=move><parameter=x>1</parameter></function></tool_call>"
    )
    _write_rows(run_dir / "results.jsonl", rows)
    _seal_run(run_dir, "study", "base")

    with pytest.raises(ExportError, match="reanalysis"):
        _export(fixture, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_rejects_resealed_stale_analysis(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    cells = fixture["analysis_dir"] / "cells.csv"
    original = cells.read_text()
    altered = original.replace(",1,1,0,", ",1,1,0.25,", 1)
    assert altered != original
    cells.write_text(altered)
    _seal_analysis(fixture["analysis_dir"])

    with pytest.raises(ExportError, match="differs from raw-data reanalysis"):
        _export(fixture, tmp_path / "public")


def test_rejects_wrong_design_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt_path = fixture["design_dir"] / "design.receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["selected_source_tree_sha256"] = "d" * 64
    _write_json(receipt_path, receipt)

    with pytest.raises(ExportError, match="registration/design"):
        _export(fixture, tmp_path / "public")


def test_rejects_identity_path_after_semantic_verification(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    run_dir = fixture["run_dirs"][0]
    rows = _load_rows(run_dir / "results.jsonl")
    message = {
        "role": "assistant",
        "content": "saved at /Users/private/results.txt",
    }
    rows[0]["response_message"] = message
    rows[0].update(probe.classify_response_message(message))
    _write_rows(run_dir / "results.jsonl", rows)
    _write_json(run_dir / "completed.json", _completed(rows, "base"))
    _seal_run(run_dir, "study", "base")
    _generate_analysis(fixture)

    with pytest.raises(ExportError, match="identity-bearing pattern"):
        _export(fixture, tmp_path / "public")


def test_rejects_json_escaped_identity_path(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    run_dir = fixture["run_dirs"][0]
    rows = _load_rows(run_dir / "results.jsonl")
    message = {
        "role": "assistant",
        "content": "saved at /Users/private/secret.txt",
    }
    rows[0]["response_message"] = message
    rows[0].update(probe.classify_response_message(message))
    result_path = run_dir / "results.jsonl"
    _write_rows(result_path, rows)
    result_path.write_text(
        result_path.read_text().replace(
            "/Users/private/secret.txt",
            r"\u002fUsers\u002fprivate\u002fsecret.txt",
        )
    )
    assert "/Users/private" not in result_path.read_text()
    assert _load_rows(result_path)[0]["response_message"]["content"] == message["content"]
    _write_json(run_dir / "completed.json", _completed(rows, "base"))
    _seal_run(run_dir, "study", "base")
    _generate_analysis(fixture)

    with pytest.raises(ExportError, match="identity-bearing pattern"):
        _export(fixture, tmp_path / "public")


def test_rejects_symlinked_input_directory(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    design_dir = fixture["design_dir"]
    real_design = tmp_path / "design-real"
    design_dir.rename(real_design)
    design_dir.symlink_to(real_design, target_is_directory=True)

    with pytest.raises(ExportError, match="regular directory"):
        _export(fixture, tmp_path / "public")


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")

    with pytest.raises(ExportError, match="refusing to overwrite"):
        _export(fixture, output)
    assert sentinel.read_text() == "keep"


def test_rejects_output_inside_input(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = fixture["analysis_dir"] / "public"

    with pytest.raises(ExportError, match="output overlaps"):
        _export(fixture, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "bad_snapshot",
    ("../../escaped-run", "nested/run", "absolute"),
)
def test_rejects_snapshot_id_as_output_path(
    tmp_path: Path,
    bad_snapshot: str,
) -> None:
    fixture = _fixture(tmp_path)
    registration_path = fixture["registration_path"]
    registration = json.loads(registration_path.read_text())
    original = next(iter(registration["snapshots"]))
    snapshot_contract = registration["snapshots"].pop(original)
    escaped = tmp_path / "escaped-absolute"
    snapshot_id = str(escaped) if bad_snapshot == "absolute" else bad_snapshot
    registration["snapshots"] = {
        snapshot_id: snapshot_contract,
        **registration["snapshots"],
    }
    _write_json(registration_path, registration)

    with pytest.raises(ExportError, match="safe single path components"):
        _export(fixture, tmp_path / "public")
    assert not escaped.exists()
    assert not (tmp_path / "escaped-run").exists()


def test_analysis_provenance_binds_exact_python_and_script(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    summary_path = fixture["analysis_dir"] / "analysis-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["analysis_code_provenance"]["python_version"] = (
        f"{sys.version_info.major}.{sys.version_info.minor}.0"
    )
    _write_json(summary_path, summary)
    _seal_analysis(fixture["analysis_dir"])

    with pytest.raises(ExportError, match="implementation provenance"):
        _export(fixture, tmp_path / "public")
