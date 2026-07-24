from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.analyze_local_recovery_factorial import (
    ARM_VALUE_METRICS,
    AnalysisError,
    _build_cell_row,
    _pair_differences,
    _paper_table_markdown,
    _paper_table_tex,
    _publish_analysis,
    _rename_directory_noreplace,
    _require_complete_estimands,
    _resume_unblind,
    _reserve_unblind,
    _summarize,
    _validate_intent_report_identity,
    _verify_completed_cell_artifacts,
    _validate_recovery_receipts,
    _validate_recovery_accounting,
    main,
    verify_sealed_bundle_integrity,
)
from run_manifest import sha256_json


def _audit(recovered: dict[str, int], malformed: int = 1) -> dict:
    count = sum(recovered.values())
    return {
        "schema_version": "kaetram-recovery-audit-v1",
        "totals": {
            "sessions": 1,
            "malformed_emissions": malformed,
            "recovered_calls": count,
            "recovered_execution_errors": 0,
            "repeat_recoveries_within_window": 0,
        },
        "recovered_by_tool": recovered,
    }


def test_recovery_on_accounts_raw_plus_recovered_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({"observe": 2}),
    )
    result = _validate_recovery_accounting(
        [Path("unused")],
        {
            "raw_action_counts": {"warp": 1},
            "raw_malformed_emissions": 1,
            "raw_recoverable_calls": 2,
            "raw_recoverable_action_counts": {"observe": 2},
        },
        {"warp": 1, "observe": 2},
        True,
    )
    assert result["recovered_calls"] == 2
    assert result["malformed_emissions"] == 1


def test_recovery_off_rejects_any_recovered_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({"observe": 1}),
    )
    with pytest.raises(AnalysisError, match="recovery-off"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            False,
        )


def test_recovery_accounting_rejects_unexplained_canonical_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({}, malformed=0),
    )
    with pytest.raises(AnalysisError, match="canonical executions differ"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {"warp": 1},
                "raw_malformed_emissions": 0,
                "raw_recoverable_calls": 0,
                "raw_recoverable_action_counts": {},
            },
            {"warp": 1, "observe": 1},
            True,
        )


def test_recovery_accounting_rejects_malformed_count_disagreement(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({}, malformed=2),
    )
    with pytest.raises(AnalysisError, match="malformed count differs"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 0,
                "raw_recoverable_action_counts": {},
            },
            {},
            False,
        )


def test_recovery_accounting_rejects_impossible_error_and_repeat_totals(
    monkeypatch,
) -> None:
    audit = _audit({"observe": 1})
    audit["totals"]["recovered_execution_errors"] = 2
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: audit,
    )
    with pytest.raises(AnalysisError, match="errors exceed"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            True,
        )

    audit["totals"]["recovered_execution_errors"] = 0
    audit["totals"]["repeat_recoveries_within_window"] = 2
    with pytest.raises(AnalysisError, match="repeat recoveries exceed"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            True,
        )


def test_recovery_accounting_reconciles_modern_raw_and_rewritten_log(
    tmp_path: Path,
) -> None:
    log = tmp_path / "session_1_test.log"
    records = [
        {
            "type": "raw_model_emission",
            "content": '<function=observe()>',
            "tool_calls": [],
        },
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "recovered_1_0",
                "name": "observe",
                "input": {},
            }]},
        },
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "recovered_1_0",
                "content": "[format] corrected\n\n{\"ok\": true}",
            }]},
        },
    ]
    log.write_text("".join(json.dumps(record) + "\n" for record in records))

    result = _validate_recovery_accounting(
        [log],
        {
            "raw_action_counts": {},
            "raw_malformed_emissions": 1,
            "raw_recoverable_calls": 1,
            "raw_recoverable_action_counts": {"observe": 1},
        },
        {"observe": 1},
        True,
    )
    assert result["malformed_emissions"] == 1
    assert result["recovered_calls"] == 1
    assert result["recovered_execution_successes"] == 1


def test_analysis_requires_one_recovery_receipt_per_session(tmp_path: Path) -> None:
    raw = tmp_path / "episode_001_raw"
    raw.mkdir()
    (raw / "harness_meta_template.json").write_text(
        '{"tool_recovery_enabled":true}'
    )
    (raw / "session_1_test.log").write_text("{}\n")
    with pytest.raises(AnalysisError, match="recovery receipts"):
        _validate_recovery_receipts(
            tmp_path,
            {"meta": {"tool_recovery_enabled": True}},
            True,
            "cell",
            {"model": "2b-base", "tool_recovery_enabled": True},
        )


def test_analysis_rejects_session_identity_drift(tmp_path: Path) -> None:
    raw = tmp_path / "episode_001_raw"
    raw.mkdir()
    expected = {
        "model": "2b-base",
        "inference_seed": 1729,
        "tool_recovery_enabled": True,
    }
    (raw / "harness_meta_template.json").write_text(json.dumps(expected))
    (raw / "session_1_test.log").write_text("{}\n")
    (raw / "session_1_test.meta.json").write_text(json.dumps({
        **expected,
        "model": "2b-r2",
    }))

    with pytest.raises(AnalysisError, match="session identity mismatch"):
        _validate_recovery_receipts(
            tmp_path,
            {"meta": {"tool_recovery_enabled": True}},
            True,
            "cell",
            expected,
        )


def test_pair_differences_require_and_preserve_all_nine_pairs() -> None:
    rows = []
    metrics = (
        "canonical_executed_calls",
        "canonical_executed_calls_per_minute",
        "raw_structured_calls",
        "malformed_emissions",
        "recovered_calls",
        "core3_stages_advanced",
        "quest_stages_advanced",
        "xp_db_delta",
        "unique_positions",
    )
    for replicate in (1, 2, 3):
        for weight in ("base", "r2", "r3"):
            for recovery in (False, True):
                schedule_index = len(rows)
                row = {
                    "replicate": replicate,
                    "weight": weight,
                    "recovery": recovery,
                    "schedule_index": schedule_index,
                }
                row.update({metric: int(recovery) for metric in metrics})
                rows.append(row)
    pairs = _pair_differences(rows)
    assert len(pairs["complete_pairs"]) == 9
    assert all(
        set(pair["on_minus_off"].values()) == {1}
        for pair in pairs["complete_pairs"]
    )


def test_arm_summary_retains_replicate_values_and_descriptive_means() -> None:
    rows = []
    for replicate in (3, 1, 2):
        rows.append(_build_cell_row(
            cell={
                "cell_id": f"rep{replicate:02d}-base-rec-off",
                "replicate": replicate,
                "snapshot": "base_2b",
                "recovery": False,
                "schedule_index": replicate + 10,
            },
            duration=60.0,
            duration_budget=59,
            episode={
                "turns_played": replicate,
                "tool_calls_valid": replicate,
                "tool_parse_rate": replicate,
                "core3_stages_advanced": replicate,
                "quest_stages_advanced": replicate,
                "xp_db_delta": replicate,
                "unique_positions": replicate,
            },
            recomputed={"action_counts": {"observe": replicate}},
            raw_metrics={
                "raw_generations": replicate,
                "generations_with_structured_call": replicate,
                "generations_without_structured_call": 0,
                "emitted_structured_calls": replicate,
                "raw_action_counts": {"observe": replicate},
            },
            recovery_metrics={
                "malformed_emissions": replicate,
                "recoverable_raw_calls": replicate,
                "recovered_calls": replicate,
                "recovered_execution_errors": 0,
                "recovered_execution_successes": replicate,
                "repeat_recoveries_within_window": replicate,
                "recovered_by_tool": {"observe": replicate},
            },
            api_errors=replicate,
            sub_sessions=replicate,
        ))

    arm = _summarize(rows)["base-recovery-off"]
    assert arm["cell_ids"] == [
        "rep01-base-rec-off",
        "rep02-base-rec-off",
        "rep03-base-rec-off",
    ]
    assert arm["replicates"] == [1, 2, 3]
    assert arm["missing_replicates"] == []
    assert arm["values"]["malformed_emissions"] == [1, 2, 3]
    assert arm["means"]["malformed_emissions"] == 2
    assert arm["values"]["raw_structured_calls_per_minute"] == [1, 2, 3]
    assert arm["means"]["raw_structured_calls_per_minute"] == 2
    assert arm["pooled_structured_call_emission_rate"] == 1


def test_every_completed_cell_requires_a_verified_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    with pytest.raises(AnalysisError, match="lacks a sealed artifact inventory"):
        _verify_completed_cell_artifacts(tmp_path / "invalid-cell", {
            "status": "invalid",
            "artifact_inventory_sha256": "",
        })

    verified = []
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._verify_artifacts",
        lambda root, digest: verified.append((root, digest)) or 7,
    )
    cell_root = tmp_path / "invalid-cell"
    cell_root.mkdir()
    sealed_status = {
        "status": "invalid",
        "error": "launcher failure",
    }
    (cell_root / "cell-status.json").write_text(json.dumps(sealed_status))
    retained = {
        **sealed_status,
        "artifact_inventory_sha256": "a" * 64,
    }
    digest, count = _verify_completed_cell_artifacts(cell_root, retained)
    assert digest == "a" * 64
    assert count == 7
    assert verified == [(cell_root, "a" * 64)]

    retained["error"] = "outcome-dependent relabel"
    with pytest.raises(AnalysisError, match="differs from sealed cell status"):
        _verify_completed_cell_artifacts(cell_root, retained)


def test_integrity_check_rehashes_without_opening_outcomes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "prelaunch.json").write_text("{}")
    (tmp_path / "completed-inventory.json").write_text("{}")
    cells = [
        {
            "cell_id": f"cell-{index}",
            "snapshot": "base_2b",
            "schedule_index": index,
            "recovery": bool(index % 2),
        }
        for index in range(18)
    ]
    manifest = {
        "schema_version": "kaetram.local-weight-recovery-factorial.v1",
        "pilot_id": "local-weights-recovery-30m-v1",
        "claim_boundary": {"confirmatory": False},
        "cells": cells,
    }
    completed_by_id = {}
    for cell in cells:
        cell_root = tmp_path / cell["cell_id"]
        cell_root.mkdir()
        sealed_status = {
            "cell_id": cell["cell_id"],
            "snapshot": cell["snapshot"],
            "schedule_index": cell["schedule_index"],
            "recovery_assignment": cell["recovery"],
            "status": "valid",
            "returncode": 0,
            "tool_recovery_enabled": cell["recovery"],
        }
        (cell_root / "cell-status.json").write_text(
            json.dumps(sealed_status, sort_keys=True)
        )
        # Invalid JSON proves the integrity path hashes but never parses outcomes.
        (cell_root / "outcome-bearing-results.json").write_text("{not-json")
        records = []
        for relative in ("cell-status.json", "outcome-bearing-results.json"):
            path = cell_root / relative
            content = path.read_bytes()
            records.append({
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            })
        inventory = {
            "schema_version": "kaetram.local-weight-pilot-artifacts.v1",
            "file_count": len(records),
            "tree_sha256": sha256_json(records),
            "files": records,
        }
        inventory_path = cell_root / "artifact-inventory.json"
        inventory_path.write_text(json.dumps(inventory, sort_keys=True))
        completed_by_id[cell["cell_id"]] = {
            **sealed_status,
            "artifact_inventory_sha256": hashlib.sha256(
                inventory_path.read_bytes()
            ).hexdigest(),
        }
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.load_manifest",
        lambda _: (manifest, "a" * 64),
    )
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._load_validated_envelope",
        lambda *args, **kwargs: (
            tmp_path / "prelaunch.json",
            tmp_path / "completed-inventory.json",
            {},
            {},
            {"provenance_tier": "legacy_v1_unattested"},
            list(completed_by_id.values()),
            completed_by_id,
            18,
            0,
        ),
    )
    provenance = {
        "source_git_commit": "d" * 40,
        "inventory_sha256": "e" * 64,
    }

    report = verify_sealed_bundle_integrity(
        tmp_path,
        tmp_path / "manifest.json",
        allow_legacy_v1=True,
        analysis_code_provenance=provenance,
    )

    assert report["integrity_status"] == "verified"
    assert report["outcome_values_parsed"] is False
    assert report["all_registered_cells_launcher_valid"] is True
    assert report["files_rehashed"] == 36
    assert "rows" not in report
    assert "by_arm" not in report

    completed_by_id[cells[0]["cell_id"]]["returncode"] = 9
    with pytest.raises(AnalysisError, match="technically inconsistent"):
        verify_sealed_bundle_integrity(
            tmp_path,
            tmp_path / "manifest.json",
            allow_legacy_v1=True,
            analysis_code_provenance=provenance,
        )


def test_integrity_only_cli_does_not_require_unblind_arguments(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    report = {
        "integrity_status": "verified",
        "bundle_index_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.load_manifest",
        lambda _: ({
            "schema_version": "kaetram.local-weight-recovery-factorial.v1",
        }, "a" * 64),
    )
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.verify_sealed_bundle_integrity",
        lambda *args, **kwargs: report,
    )

    assert main([
        str(tmp_path),
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--integrity-only",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == report


def _complete_rows() -> list[dict]:
    rows = []
    schedule_index = 0
    for replicate in (1, 2, 3):
        for weight in ("base", "r2", "r3"):
            for recovery in (False, True):
                row = {
                    metric: 0 for metric in ARM_VALUE_METRICS
                }
                row.update({
                    "cell_id": (
                        f"rep{replicate:02d}-{weight}-"
                        f"rec-{'on' if recovery else 'off'}"
                    ),
                    "replicate": replicate,
                    "weight": weight,
                    "recovery": recovery,
                    "schedule_index": schedule_index,
                    "canonical_action_counts": {},
                    "raw_action_counts": {},
                    "recovered_by_tool": {},
                })
                row["raw_generations"] = 1
                rows.append(row)
                schedule_index += 1
    return rows


def _complete_report() -> dict:
    rows = _complete_rows()
    by_arm = _summarize(rows)
    pairs = _pair_differences(rows)
    return {
        "pilot_id": "local-weights-recovery-30m-v1",
        "analysis_status": "complete_descriptive",
        "descriptive_results_released": True,
        "invalid_cell_receipts": [],
        "bundle_index_sha256": "b" * 64,
        "files_rehashed": 42,
        "rows": rows,
        "by_arm": by_arm,
        "paired_differences": pairs,
        "analysis_code_provenance": {
            "inventory_sha256": "c" * 64,
            "source_git_commit": "d" * 40,
        },
    }


def _bind_report_to_intent(report: dict, intent: dict) -> dict:
    return {
        **report,
        "manifest_sha256": intent["manifest_sha256"],
        "bundle_index": intent["bundle_index"],
        "bundle_index_sha256": intent["bundle_index_sha256"],
    }


def test_registered_estimands_require_every_cell_arm_and_pair() -> None:
    rows = _complete_rows()
    arms = _summarize(rows)
    pairs = _pair_differences(rows)
    _require_complete_estimands(rows, arms, pairs)

    partial = rows[:-1]
    with pytest.raises(AnalysisError, match="all 18"):
        _require_complete_estimands(
            partial,
            _summarize(partial),
            _pair_differences(partial),
        )


def test_paper_renderers_are_fixed_and_descriptive() -> None:
    report = _complete_report()
    markdown = _paper_table_markdown(report)
    latex = _paper_table_tex(report)
    assert markdown.count("| Base |") == 2
    assert markdown.count("| Round 2 |") == 2
    assert markdown.count("| Round 3 |") == 2
    assert "Descriptive only" in markdown
    assert "\\label{tab:local-recovery-factorial}" in latex
    assert "Descriptive only" in latex


def test_paper_renderers_withhold_partial_estimands() -> None:
    report = {
        **_complete_report(),
        "descriptive_results_released": False,
        "invalid_cell_receipts": [{
            "cell_id": "rep03-r3-rec-on",
            "error": "launcher failure",
        }],
        "rows": [],
        "by_arm": {},
        "paired_differences": {
            "complete_pairs": [],
            "incomplete_pairs": [],
        },
    }
    markdown = _paper_table_markdown(report)
    latex = _paper_table_tex(report)
    assert "results withheld" in markdown
    assert "rep03-r3-rec-on" in markdown
    assert "\\begin{tabular}" not in latex
    assert "withheld" in latex


def test_unblind_transaction_is_create_only_and_publishes_bound_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "prelaunch.json").write_text("{}")
    completed = {
        "schema_version": "completed",
        "cells": [
            {
                "cell_id": f"cell-{index:02d}",
                "artifact_inventory_sha256": f"{index + 1:064x}",
            }
            for index in range(18)
        ],
    }
    (root / "completed-inventory.json").write_text(json.dumps(completed))
    output_dir = tmp_path / "analysis"
    manifest = {"pilot_id": "local-weights-recovery-30m-v1"}
    manifest_sha256 = sha256_json(manifest)
    code = {
        "inventory_sha256": "c" * 64,
        "source_git_commit": "d" * 40,
    }
    registry = tmp_path / "registry"
    intent_path, registry_intent_path, intent = _reserve_unblind(
        root,
        output_dir,
        manifest,
        manifest_sha256,
        code,
        registry,
    )
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._validate_publication_inputs",
        lambda *_: None,
    )
    receipt = _publish_analysis(
        root,
        output_dir,
        _bind_report_to_intent(_complete_report(), intent),
        intent_path,
        registry_intent_path,
        intent,
    )

    assert (output_dir / "analysis-report.json").is_file()
    assert (output_dir / "cells.csv").is_file()
    assert (output_dir / "paired-differences.csv").is_file()
    assert (output_dir / "paper-table.md").is_file()
    assert (output_dir / "paper-table.tex").is_file()
    assert (output_dir / "analysis-unblind-receipt.json").is_file()
    assert receipt["artifact_index_sha256"]
    assert (root / "analysis-unblind-receipt.json").is_file()
    with pytest.raises(AnalysisError, match="completed unblind"):
        _reserve_unblind(
            root,
            tmp_path / "analysis-2",
            manifest,
            manifest_sha256,
            code,
            registry,
        )


def test_unblind_resume_recovers_partial_staging_without_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "prelaunch.json").write_text("{}")
    cells = [
        {
            "cell_id": f"cell-{index:02d}",
            "artifact_inventory_sha256": f"{index + 1:064x}",
        }
        for index in range(18)
    ]
    (root / "completed-inventory.json").write_text(json.dumps({"cells": cells}))
    output_dir = tmp_path / "analysis"
    registry = tmp_path / "registry"
    manifest = {"pilot_id": "local-weights-recovery-30m-v1"}
    manifest_sha256 = sha256_json(manifest)
    code = {
        "inventory_sha256": "c" * 64,
        "source_git_commit": "d" * 40,
    }
    intent_path, registry_intent_path, intent = _reserve_unblind(
        root,
        output_dir,
        manifest,
        manifest_sha256,
        code,
        registry,
    )
    # Use the implementation's digest-derived staging name and simulate a
    # crash after the first exact artifact was staged.
    intent_sha = hashlib.sha256(intent_path.read_bytes()).hexdigest()
    intent_path.unlink()
    with pytest.raises(AnalysisError, match=intent_sha):
        _reserve_unblind(
            root,
            output_dir,
            manifest,
            manifest_sha256,
            code,
            registry,
        )
    with pytest.raises(AnalysisError, match="outside the sealed run root"):
        _resume_unblind(
            root,
            root / output_dir.name,
            manifest,
            manifest_sha256,
            code,
            registry,
            intent_sha,
        )
    staging = output_dir.parent / f".{output_dir.name}.staging-{intent_sha[:16]}"
    staging.mkdir()
    report = _bind_report_to_intent(_complete_report(), intent)
    analysis_bytes = (
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    (staging / "analysis-report.json").write_bytes(analysis_bytes)

    resumed_intent, resumed_registry, resumed = _resume_unblind(
        root,
        output_dir,
        manifest,
        manifest_sha256,
        code,
        registry,
        intent_sha,
    )
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._validate_publication_inputs",
        lambda *_: None,
    )
    receipt = _publish_analysis(
        root,
        output_dir,
        report,
        resumed_intent,
        resumed_registry,
        resumed,
    )
    assert output_dir.is_dir()
    assert not staging.exists()
    assert receipt["intent_sha256"] == intent_sha


def test_publication_rejects_intent_report_identity_mismatch(tmp_path: Path) -> None:
    report = {
        **_complete_report(),
        "manifest_sha256": "e" * 64,
        "bundle_index": {"completed_inventory_sha256": "f" * 64},
    }
    report["bundle_index_sha256"] = sha256_json(report["bundle_index"])
    intent = {
        "pilot_id": report["pilot_id"],
        "manifest_sha256": report["manifest_sha256"],
        "bundle_index": report["bundle_index"],
        "bundle_index_sha256": "0" * 64,
        "analysis_code_inventory_sha256": "c" * 64,
        "analysis_source_git_commit": "d" * 40,
        "output_directory_name": "analysis",
    }
    with pytest.raises(AnalysisError, match="intent differs"):
        _validate_intent_report_identity(
            intent,
            report,
            tmp_path / "analysis",
        )


def test_publication_rejects_replaced_root_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "prelaunch.json").write_text("{}")
    (root / "completed-inventory.json").write_text(json.dumps({
        "cells": [
            {
                "cell_id": f"cell-{index:02d}",
                "artifact_inventory_sha256": f"{index + 1:064x}",
            }
            for index in range(18)
        ],
    }))
    manifest = {"pilot_id": "local-weights-recovery-30m-v1"}
    code = {
        "inventory_sha256": "c" * 64,
        "source_git_commit": "d" * 40,
    }
    output_dir = tmp_path / "analysis"
    intent_path, registry_intent_path, intent = _reserve_unblind(
        root,
        output_dir,
        manifest,
        sha256_json(manifest),
        code,
        tmp_path / "registry",
    )
    intent_path.write_text('{"tampered":true}\n')
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._validate_publication_inputs",
        lambda *_: None,
    )
    with pytest.raises(AnalysisError, match="differs from expected bytes"):
        _publish_analysis(
            root,
            output_dir,
            _bind_report_to_intent(_complete_report(), intent),
            intent_path,
            registry_intent_path,
            intent,
        )
    assert not output_dir.exists()


def test_atomic_publication_never_replaces_existing_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "new.txt").write_text("new")
    (destination / "existing.txt").write_text("existing")

    with pytest.raises(AnalysisError, match="appeared during publication"):
        _rename_directory_noreplace(source, destination)
    assert (source / "new.txt").read_text() == "new"
    assert (destination / "existing.txt").read_text() == "existing"

    (destination / "existing.txt").unlink()
    destination.rmdir()
    _rename_directory_noreplace(source, destination)
    assert not source.exists()
    assert (destination / "new.txt").read_text() == "new"


def test_publisher_never_populates_preexisting_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "prelaunch.json").write_text("{}")
    (root / "completed-inventory.json").write_text(json.dumps({
        "cells": [
            {
                "cell_id": f"cell-{index:02d}",
                "artifact_inventory_sha256": f"{index + 1:064x}",
            }
            for index in range(18)
        ],
    }))
    manifest = {"pilot_id": "local-weights-recovery-30m-v1"}
    code = {
        "inventory_sha256": "c" * 64,
        "source_git_commit": "d" * 40,
    }
    output_dir = tmp_path / "analysis"
    intent_path, registry_intent_path, intent = _reserve_unblind(
        root,
        output_dir,
        manifest,
        sha256_json(manifest),
        code,
        tmp_path / "registry",
    )
    output_dir.mkdir()
    marker = output_dir / "unrelated.txt"
    marker.write_text("leave me alone")
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial._validate_publication_inputs",
        lambda *_: None,
    )

    with pytest.raises(AnalysisError, match="differs from expected bytes"):
        _publish_analysis(
            root,
            output_dir,
            _bind_report_to_intent(_complete_report(), intent),
            intent_path,
            registry_intent_path,
            intent,
        )
    assert sorted(path.name for path in output_dir.iterdir()) == ["unrelated.txt"]
    assert marker.read_text() == "leave me alone"
