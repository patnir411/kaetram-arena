from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_manifest import sha256_json
from scripts.opd.local_weight_pilot import (
    ENDPOINT_ENV,
    PILOT_INVENTORY_SCHEMA_VERSION,
    PILOT_PRELAUNCH_SCHEMA_VERSION,
    RECOVERY_INVENTORY_SCHEMA_VERSION,
    RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    PilotError,
    _ledger_schema_versions,
    _preflight_endpoints,
    _validate_schedule,
    attest_mongodb_runtime,
    attest_playwright_runtime,
    build_eval_command,
    build_eval_environment,
    build_artifact_inventory,
    clean_python_environment,
    load_manifest,
    preserve_invoked_path,
    validate_effective_recovery,
)


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research/experiments/local-weight-pilot.json"
RECOVERY_MANIFEST = (
    REPO / "research/experiments/local-weight-recovery-30m.json"
)


def test_registered_pilot_is_small_paired_and_non_confirmatory() -> None:
    raw, digest = load_manifest(MANIFEST)
    assert len(digest) == 64
    assert raw["claim_boundary"]["confirmatory"] is False
    assert raw["protocol"]["duration_seconds"] == 300
    assert len(raw["cells"]) == 9
    for replicate in (1, 2, 3):
        block = [cell for cell in raw["cells"] if cell["replicate"] == replicate]
        assert len({cell["inference_seed"] for cell in block}) == 1
        assert len({cell["environment_seed"] for cell in block}) == 1
        assert {cell["snapshot"] for cell in block} == {
            "base_2b",
            "opd_r2_2b",
            "opd_r3_2b",
        }


def test_dry_run_launches_nothing_and_reports_nominal_runtime(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/opd/local_weight_pilot.py")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "cell_count": 9,
        "confirmatory": False,
        "duration_seconds_per_cell": 300,
        "manifest_sha256": payload["manifest_sha256"],
        "mode": "dry_run",
        "nominal_runtime_seconds": 2700,
        "nothing_launched": True,
        "pilot_id": "local-render-parity-pilot-v1",
    }
    assert not list(tmp_path.iterdir())


def test_schedule_or_claim_drift_is_rejected() -> None:
    raw = json.loads(MANIFEST.read_text())
    raw["cells"][0]["inference_seed"] = 999
    with pytest.raises(PilotError, match="inference seed is not paired"):
        _validate_schedule(raw)


def test_registered_recovery_factorial_is_complete_paired_and_exploratory() -> None:
    raw, digest = load_manifest(RECOVERY_MANIFEST)
    assert len(digest) == 64
    assert raw["protocol"]["duration_seconds"] == 1800
    assert len(raw["cells"]) == 18
    for replicate in (1, 2, 3):
        block = [cell for cell in raw["cells"] if cell["replicate"] == replicate]
        assert len({cell["inference_seed"] for cell in block}) == 1
        assert len({cell["environment_seed"] for cell in block}) == 1
        assert {
            (cell["snapshot"], cell["recovery"]) for cell in block
        } == {
            (weight, recovery)
            for weight in ("base_2b", "opd_r2_2b", "opd_r3_2b")
            for recovery in (False, True)
        }
        ordered = sorted(block, key=lambda cell: cell["schedule_index"])
        assert all(
            ordered[index]["snapshot"] == ordered[index + 1]["snapshot"]
            for index in (0, 2, 4)
        )


def test_recovery_factorial_uses_distinct_sealed_ledger_schemas() -> None:
    pilot, _ = load_manifest(MANIFEST)
    factorial, _ = load_manifest(RECOVERY_MANIFEST)
    assert _ledger_schema_versions(pilot) == (
        PILOT_PRELAUNCH_SCHEMA_VERSION,
        PILOT_INVENTORY_SCHEMA_VERSION,
    )
    assert _ledger_schema_versions(factorial) == (
        RECOVERY_PRELAUNCH_SCHEMA_VERSION,
        RECOVERY_INVENTORY_SCHEMA_VERSION,
    )
    assert PILOT_PRELAUNCH_SCHEMA_VERSION.endswith(".v3")
    assert RECOVERY_PRELAUNCH_SCHEMA_VERSION.endswith(".v3")


def test_recovery_factorial_dry_run_reports_nine_nominal_hours() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/opd/local_weight_pilot.py"),
            str(RECOVERY_MANIFEST),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["cell_count"] == 18
    assert payload["duration_seconds_per_cell"] == 1800
    assert payload["nominal_runtime_seconds"] == 32400
    assert payload["confirmatory"] is False


def test_recovery_factorial_rejects_integer_recovery_assignment() -> None:
    raw = json.loads(RECOVERY_MANIFEST.read_text())
    raw["cells"][0]["recovery"] = 0
    with pytest.raises(PilotError, match="must be Booleans"):
        _validate_schedule(raw)

    raw = json.loads(MANIFEST.read_text())
    raw["claim_boundary"]["confirmatory"] = True
    with pytest.raises(PilotError, match="non-confirmatory"):
        _validate_schedule(raw)


def test_recovery_factorial_rejects_seed_and_cell_label_drift() -> None:
    raw = json.loads(RECOVERY_MANIFEST.read_text())
    for cell in raw["cells"]:
        if cell["replicate"] == 2:
            cell["environment_seed"] = 42001
    with pytest.raises(PilotError, match="seed identities have drifted"):
        _validate_schedule(raw)

    raw = json.loads(RECOVERY_MANIFEST.read_text())
    first, second = raw["cells"][:2]
    first["cell_id"], second["cell_id"] = second["cell_id"], first["cell_id"]
    with pytest.raises(PilotError, match="ID does not match"):
        _validate_schedule(raw)


def test_recovery_factorial_rejects_artifact_contract_drift() -> None:
    raw = json.loads(RECOVERY_MANIFEST.read_text())
    raw["artifact_contract"]["tokenizer_sha256"] = "0" * 64
    with pytest.raises(PilotError, match="artifact contract has drifted"):
        _validate_schedule(raw)


def test_eval_command_uses_endpoint_environment_and_complete_provenance(
    tmp_path: Path,
) -> None:
    manifest, manifest_sha = load_manifest(MANIFEST)
    cell = manifest["cells"][0]
    endpoint = {
        "attestation": {
            "checkpoint_sha256": "a" * 64,
            "tokenizer_sha256": "b" * 64,
            "render_contract_sha256": "c" * 64,
        },
    }
    game = {
        "gameRevision": "d" * 40,
        "entrypointSha256": "e" * 64,
    }
    command = build_eval_command(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        cell=cell,
        cell_root=tmp_path / "cell",
        endpoint_attestation_sha256="f" * 64,
        endpoint_attestation=endpoint,
        game_attestation=game,
        game_database_attestation={"attestation_sha256": "9" * 64},
    )
    rendered = " ".join(command)
    assert command[0] == sys.executable
    assert command[1:4] == ["-I", "-S", "-B"]
    assert command[command.index("--script") + 1] == str(REPO / "eval_harness.py")
    assert f"{cell['cell_id']}={ENDPOINT_ENV}" in rendered
    assert "http://" not in rendered
    assert "--prompt-agent-name EvalCompletionist" in rendered
    assert "--duration-seconds 300" in rendered
    assert "--environment-seed 41001" in rendered
    assert "--checkpoint-sha256 " + "a" * 64 in rendered
    assert "--game-database-attestation-sha256 " + "9" * 64 in rendered
    assert "--tokenizer-sha256 " + "b" * 64 in rendered
    assert "--render-contract-sha256 " + "c" * 64 in rendered
    assert "--tool-recovery-enabled off" in rendered


def test_eval_environment_pins_db_schema_and_recovery_off(tmp_path: Path) -> None:
    manifest, _ = load_manifest(MANIFEST)
    env = build_eval_environment(
        {
            "KAETRAM_TOOL_RECOVERY": "1",
            "KAETRAM_MONGO_DB": "ambient_test_lane",
            "KAETRAM_TOOL_SCHEMA_SOURCE": "live",
            "PYTHONPATH": "/tmp/injected",
            "PythonHome": "/tmp/also-injected",
            "KAETRAM_MCP_PYTHON": "/tmp/alternate-python",
        },
        manifest=manifest,
        cell=manifest["cells"][0],
        game_dir=tmp_path / "game",
        node_binary=tmp_path / "node",
    )
    assert "KAETRAM_TOOL_RECOVERY" not in env
    assert env["KAETRAM_MONGO_DB"] == "kaetram_devlopment"
    assert env["KAETRAM_TOOL_SCHEMA_SOURCE"] == "canonical"
    assert env[ENDPOINT_ENV] == "http://127.0.0.1:9801/v1"
    assert "PYTHONPATH" not in env
    assert "PythonHome" not in env
    assert "KAETRAM_MCP_PYTHON" not in env


def test_python_environment_cleanup_is_case_insensitive() -> None:
    assert clean_python_environment({
        "PATH": "/bin",
        "PYTHONPATH": "/tmp/a",
        "pythonstartup": "/tmp/b",
    }) == {"PATH": "/bin"}


def test_recovery_factorial_environment_and_command_bind_effective_lane(
    tmp_path: Path,
) -> None:
    manifest, manifest_sha = load_manifest(RECOVERY_MANIFEST)
    on_cell = next(cell for cell in manifest["cells"] if cell["recovery"])
    off_cell = next(cell for cell in manifest["cells"] if not cell["recovery"])
    for cell, expected in ((on_cell, "1"), (off_cell, None)):
        env = build_eval_environment(
            {"KAETRAM_TOOL_RECOVERY": "junk"},
            manifest=manifest,
            cell=cell,
            game_dir=tmp_path / "game",
            node_binary=tmp_path / "node",
        )
        assert env.get("KAETRAM_TOOL_RECOVERY") == expected
    endpoint = {
        "attestation": {
            "checkpoint_sha256": "a" * 64,
            "tokenizer_sha256": "b" * 64,
            "render_contract_sha256": "c" * 64,
        },
    }
    command = build_eval_command(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        cell=on_cell,
        cell_root=tmp_path / "cell",
        endpoint_attestation_sha256="f" * 64,
        endpoint_attestation=endpoint,
        game_attestation={
            "gameRevision": "d" * 40,
            "entrypointSha256": "e" * 64,
        },
        game_database_attestation={"attestation_sha256": "9" * 64},
    )
    assert "--tool-recovery-enabled on" in " ".join(command)
    pair_index = command.index("--factorial-pair-id") + 1
    assert command[pair_index].endswith("-base")


@pytest.mark.parametrize("raw_value", ["0", "false", "junk"])
def test_eval_harness_rejects_ambiguous_recovery_environment(
    raw_value: str,
) -> None:
    env = {**os.environ, "KAETRAM_TOOL_RECOVERY": raw_value}
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "eval_harness.py"),
            "--tool-recovery-enabled",
            "on",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "must be absent/empty for off or exactly 1 for on" in result.stderr


def test_recovery_receipt_requires_results_template_and_every_session(
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "eval" / "cell" / "results.json"
    raw_dir = results_path.parent / "episode_001_raw"
    raw_dir.mkdir(parents=True)
    results = {"meta": {"tool_recovery_enabled": True}}
    results_path.write_text(json.dumps(results))
    (raw_dir / "harness_meta_template.json").write_text(
        json.dumps({"tool_recovery_enabled": True})
    )
    (raw_dir / "session_1.meta.json").write_text(
        json.dumps({"tool_recovery_enabled": True})
    )
    (raw_dir / "session_1.log").write_text("{}\n")
    assert validate_effective_recovery(
        results, results_path, expected=True, cell_id="cell"
    )
    (raw_dir / "session_2.log").write_text("{}\n")
    with pytest.raises(PilotError, match="recovery receipts"):
        validate_effective_recovery(
            results, results_path, expected=True, cell_id="cell"
        )
    (raw_dir / "session_2.log").unlink()
    (raw_dir / "session_1.meta.json").write_text(
        json.dumps({"tool_recovery_enabled": False})
    )
    with pytest.raises(PilotError, match="session recovery identity mismatch"):
        validate_effective_recovery(
            results, results_path, expected=True, cell_id="cell"
        )


def test_cell_artifact_inventory_hashes_content_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "result.json").write_text('{"ok":true}\n')
    first = build_artifact_inventory(tmp_path)
    assert first["file_count"] == 1
    assert first["files"][0]["path"] == "nested/result.json"

    (tmp_path / "nested" / "result.json").write_text('{"ok":false}\n')
    second = build_artifact_inventory(tmp_path)
    assert second["tree_sha256"] != first["tree_sha256"]

    (tmp_path / "link").symlink_to(tmp_path / "nested" / "result.json")
    with pytest.raises(PilotError, match="symlink"):
        build_artifact_inventory(tmp_path)


def test_invoked_virtualenv_interpreter_symlink_is_not_resolved(
    tmp_path: Path,
) -> None:
    real_python = tmp_path / "python-real"
    real_python.write_text("")
    venv_python = tmp_path / "venv-python"
    venv_python.symlink_to(real_python)
    assert preserve_invoked_path(venv_python) == venv_python
    assert preserve_invoked_path(venv_python) != venv_python.resolve()


def test_playwright_preflight_launches_and_hashes_browser(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "chromium"
    executable.write_bytes(b"browser")
    monkeypatch.setattr(
        "scripts.opd.local_weight_pilot._run_checked",
        lambda _command, _label: SimpleNamespace(
            stdout=json.dumps({
                "browser_name": "chromium",
                "browser_version": "149.0.7827.55",
                "executable_path": str(executable),
            })
        ),
    )
    receipt = attest_playwright_runtime()
    assert receipt["browser_name"] == "chromium"
    assert len(receipt["executable_sha256"]) == 64
    unsigned = dict(receipt)
    assert unsigned.pop("receipt_sha256") == sha256_json(unsigned)


def test_mongodb_preflight_pins_image_loopback_and_ping(monkeypatch) -> None:
    from scripts.opd import local_weight_pilot as pilot

    monkeypatch.setattr(pilot.shutil, "which", lambda _name: "/usr/bin/docker")

    def fake_run(command: list[str], _label: str) -> SimpleNamespace:
        if command[1:3] == ["inspect", "kaetram-mongo"]:
            return SimpleNamespace(stdout=json.dumps([{
                "Image": pilot.MONGO_IMAGE_ID,
                "State": {"Running": True},
                "NetworkSettings": {
                    "Ports": {
                        "27017/tcp": [{
                            "HostIp": "127.0.0.1",
                            "HostPort": "27017",
                        }],
                    },
                },
            }]))
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([{
                "RepoDigests": [pilot.MONGO_IMAGE_REPO_DIGEST],
            }]))
        if command[1] == "exec":
            return SimpleNamespace(stdout="1\n")
        if command[1] == "version":
            return SimpleNamespace(stdout="29.2.1\n")
        raise AssertionError(command)

    monkeypatch.setattr(pilot, "_run_checked", fake_run)
    receipt = attest_mongodb_runtime("kaetram_devlopment")
    assert receipt["image_id"] == pilot.MONGO_IMAGE_ID
    assert receipt["host"] == "127.0.0.1"
    assert receipt["database"] == "kaetram_devlopment"


def test_endpoint_preflight_binds_mlx_environment_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.opd import local_weight_pilot as pilot

    manifest, _ = load_manifest(MANIFEST)
    expected_runtime = "a" * 64

    def fake_start(**kwargs) -> tuple[object, dict]:
        snapshot = kwargs["snapshot"]
        return object(), {
            "status": "ok",
            "attestation": {
                "tokenizer_sha256": "b" * 64,
                "render_contract_sha256": "c" * 64,
                "runtime_environment_receipt_sha256": (
                    expected_runtime if snapshot != "opd_r3_2b" else "d" * 64
                ),
            },
        }

    monkeypatch.setattr(pilot, "_start_endpoint", fake_start)
    monkeypatch.setattr(pilot, "_stop_process", lambda _process: None)
    with pytest.raises(PilotError, match="MLX environment identity mismatch"):
        _preflight_endpoints(
            manifest,
            tmp_path / "python",
            tmp_path / "snapshots",
            tmp_path,
            expected_runtime,
        )
