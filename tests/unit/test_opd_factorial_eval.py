from __future__ import annotations

import hashlib
import json
import io
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.opd.factorial_eval import (
    _cleanup_processes,
    ManifestError,
    build_plan,
    cell_command,
    launch,
    plan_dict,
    seal_cell_bundle,
    seal_completed_inventory,
    seal_prelaunch_record,
    validate_cell_bundle,
    validate_completed_inventory,
    validate_live_endpoint_attestations,
    require_environment_seed_capability,
    validate_cell_result,
)


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research" / "experiments" / "opd-2b-factorial.example.json"


def _manifest_copy(tmp_path: Path, mutate=None) -> Path:
    raw = json.loads(MANIFEST.read_text())
    raw["isolation"]["output_root"] = str(tmp_path / "runs")
    raw["isolation"]["sandbox_root"] = str(tmp_path / "sandboxes")
    if mutate:
        mutate(raw)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw))
    return path


def test_manifest_generates_complete_paired_factorial_with_isolation(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    assert len(plan.cells) == 360
    assert {(c.weight, c.recovery) for c in plan.cells} == {
        (weight, recovery)
        for weight in ("base", "r2", "r3")
        for recovery in (False, True)
    }
    assert {c.personality for c in plan.cells} == {
        "grinder", "completionist", "explorer_tinkerer"
    }
    assert len({c.username for c in plan.cells}) == 360
    assert len({c.server_port for c in plan.cells}) == 360
    assert len({c.sandbox for c in plan.cells}) == 360
    assert len({c.run_dir for c in plan.cells}) == 360
    assert plan.episodes == 1
    assert plan.duration_seconds == 21600
    assert plan.protocol_id == "core3_canonical_unseeded_6h_v1"
    assert plan.primary_metric == "core3_stages_advanced"
    assert plan.planned_replicates == 20
    assert plan.max_parallel == 6
    assert plan.schedule_algorithm == "sha256-rank-v1"
    assert plan.environment_seed_mechanism == "kaetram-environment-rng-attestation/v2"
    assert plan.environment_rng_algorithm == "mulberry32-sha256-v1"
    assert len(plan.environment_seeds) == 20
    for pair_id in {c.pair_id for c in plan.cells}:
        pair = [c for c in plan.cells if c.pair_id == pair_id]
        assert {c.recovery for c in pair} == {False, True}
        assert abs(pair[0].schedule_index - pair[1].schedule_index) == 1
    for start in range(0, len(plan.cells), 6):
        batch = plan.cells[start:start + 6]
        assert len({cell.cluster_id for cell in batch}) == 1
        assert len({cell.batch_index for cell in batch}) == 1
    assert all(
        cell.inference_seed == plan.inference_seeds[cell.replicate - 1]
        for cell in plan.cells
    )
    assert all(
        cell.environment_seed == plan.environment_seeds[cell.replicate - 1]
        for cell in plan.cells
    )


def test_preflight_plan_uses_endpoint_placeholders_and_never_resolves_or_launches(tmp_path: Path):
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    plan = build_plan(_manifest_copy(tmp_path), environ={
        "KAETRAM_QWEN_2B_BASE_ENDPOINT": secret,
    })
    payload = plan_dict(plan)
    assert payload["mode"] == "preflight_only"
    assert payload["tool_schema_source"] == "canonical"
    commands = payload["commands"]
    rendered = json.dumps(payload)
    assert secret not in rendered
    assert "TOP_SECRET" not in rendered
    assert all("--models-env" in command for command in commands)
    assert all("--omit-game-knowledge" not in command for command in commands)
    assert all("--sandbox" in command for command in commands)
    assert all("--inference-seed" in command for command in commands)
    assert all("--duration-seconds" in command for command in commands)
    assert all("21600" in command for command in commands)
    assert payload["launchability"] == "attested_environment_rng_configured"
    assert all("--environment-seed" in command for command in commands)


def test_cli_dry_run_has_no_endpoint_game_db_or_directory_side_effects(tmp_path: Path):
    manifest = _manifest_copy(tmp_path)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KAETRAM_QWEN_2B_")
    }
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "opd" / "factorial_eval.py"),
         str(manifest), "--dry-run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Nothing was launched" in result.stdout
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "sandboxes").exists()


def test_cell_commands_keep_recovery_out_of_argv(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    pair = [c for c in plan.cells if c.pair_id == "rep01-base-grinder"]
    off = next(cell for cell in pair if not cell.recovery)
    on = next(cell for cell in pair if cell.recovery)
    assert off.recovery is False and on.recovery is True
    assert cell_command(plan, off) == cell_command(
        plan, replace(on, cell_id=off.cell_id, username=off.username,
                      server_port=off.server_port, sandbox=off.sandbox, run_dir=off.run_dir,
                      schedule_index=off.schedule_index),
    )


def test_schedule_is_deterministic_and_seed_sensitive_without_breaking_blocks(tmp_path: Path):
    first = build_plan(_manifest_copy(tmp_path))
    second = build_plan(_manifest_copy(tmp_path))
    assert [cell.cell_id for cell in first.cells] == [cell.cell_id for cell in second.cells]

    changed = build_plan(_manifest_copy(
        tmp_path,
        lambda raw: raw["randomization"].update({"schedule_seed": 20260719}),
    ))
    assert [cell.cell_id for cell in first.cells] != [cell.cell_id for cell in changed.cells]
    for start in range(0, len(changed.cells), 6):
        assert len({cell.cluster_id for cell in changed.cells[start:start + 6]}) == 1


def test_randomization_contract_rejects_missing_environment_seed_attestation(tmp_path: Path):
    def mutate(raw):
        raw["randomization"].pop("environment_seed")

    with pytest.raises(ManifestError, match="environment_seed"):
        build_plan(_manifest_copy(tmp_path, mutate))


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda raw: raw.update({"schema_version": 1}), "schema_version"),
        (
            lambda raw: raw["randomization"].update({"inference_seeds": [11001]}),
            "one seed per replicate",
        ),
        (
            lambda raw: raw["randomization"].update(
                {"inference_seeds": [11001, 11001, *range(11003, 11021)]}
            ),
            "must be unique",
        ),
        (
            lambda raw: raw["randomization"]["environment_seed"].update(
                {"seeds": [21001]}
            ),
            "one seed per replicate",
        ),
        (
            lambda raw: raw["execution"].update({"max_parallel": 5}),
            "one analysis cluster",
        ),
    ],
)
def test_randomization_contract_rejects_unreviewed_shapes(
    tmp_path: Path, mutate, match: str
):
    with pytest.raises(ManifestError, match=match):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_manifest_is_frozen_core3_protocol_without_heldout(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    assert not plan.omit_game_knowledge
    assert plan.held_out_quest == ""
    assert all("--held-out-quest" not in cell_command(plan, cell) for cell in plan.cells)


def test_manifest_rejects_non_six_hour_or_seeded_core3_protocol(tmp_path: Path):
    def mutate(raw):
        raw["protocol"]["duration_seconds"] = 1800

    with pytest.raises(ManifestError, match="21600"):
        build_plan(_manifest_copy(tmp_path, mutate))

    def seed(raw):
        raw["protocol"]["world_initialization"] = "targeted_seed"

    with pytest.raises(ManifestError, match="canonical_unseeded"):
        build_plan(_manifest_copy(tmp_path, seed))


def test_invalid_or_incomplete_factorial_is_rejected(tmp_path: Path):
    def mutate(raw):
        raw["design"]["weights"] = ["base", "r2"]

    with pytest.raises(ManifestError, match="weights"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_requires_canonical_tool_schema(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["tool_schema_source"] = "live"

    with pytest.raises(ManifestError, match="tool_schema_source='canonical'"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_rejects_episode_pseudoreplication(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["episodes"] = 10

    with pytest.raises(ManifestError, match="episodes=1"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_requires_three_historical_personality_lanes(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["personalities"] = ["completionist"]

    with pytest.raises(ManifestError, match="personalities"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_launch_requires_manifest_switch_and_exact_confirmation_without_popen(tmp_path: Path, monkeypatch):
    plan = build_plan(_manifest_copy(tmp_path))
    called = False

    def forbidden_popen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Popen must not be reached")

    monkeypatch.setattr("scripts.opd.factorial_eval.subprocess.Popen", forbidden_popen)
    with pytest.raises(ManifestError, match="allow_launch"):
        launch(plan, confirmation=plan.experiment_id, environ={})
    assert not called

    enabled = replace(plan, allow_launch=True)
    with pytest.raises(ManifestError, match="confirm-launch"):
        launch(enabled, confirmation="wrong", environ={})
    assert not called


def test_launch_requires_all_endpoint_environment_variables(tmp_path: Path, monkeypatch):
    plan = replace(build_plan(_manifest_copy(tmp_path)), allow_launch=True)
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.require_environment_seed_capability",
        lambda *_args: {"entrypoint_sha256": "c" * 64},
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Popen must not be reached"),
    )
    with pytest.raises(ManifestError, match="missing endpoint"):
        launch(plan, confirmation=plan.experiment_id, environ={})


def test_launch_sets_canonical_schema_recovery_and_respects_parallel_cap(tmp_path: Path, monkeypatch):
    plan = replace(build_plan(_manifest_copy(tmp_path)), allow_launch=True)
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    endpoint_env = {
        "KAETRAM_QWEN_2B_BASE_ENDPOINT": secret,
        "KAETRAM_QWEN_2B_R2_ENDPOINT": secret,
        "KAETRAM_QWEN_2B_R3_ENDPOINT": secret,
    }
    captured = []
    active = 0
    maximum_active = 0

    class FakeProcess:
        def __init__(self, args, kwargs):
            nonlocal active, maximum_active
            self.args = args
            self.kwargs = kwargs
            self.returncode = None
            active += 1
            maximum_active = max(maximum_active, active)
            captured.append(self)

        def wait(self):
            nonlocal active
            if self.returncode is None:
                self.returncode = 0
                active -= 1
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.wait()

    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(args, kwargs),
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.require_environment_seed_capability",
        lambda *_args: {"entrypoint_sha256": "c" * 64},
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.validate_live_endpoint_attestations",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.seal_prelaunch_record",
        lambda *args, **kwargs: tmp_path / "prelaunch.json",
    )
    monkeypatch.setattr("scripts.opd.factorial_eval.validate_cell_result", lambda *args: None)
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.seal_cell_bundle",
        lambda *args: tmp_path / "cell-bundle.json",
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.seal_completed_inventory",
        lambda *args: tmp_path / "completed.json",
    )
    assert launch(plan, confirmation=plan.experiment_id, environ=endpoint_env) == 0
    assert len(captured) == 360
    assert maximum_active == plan.max_parallel == 6
    assert all(p.kwargs["start_new_session"] is True for p in captured)
    assert all(p.kwargs["env"]["KAETRAM_TOOL_SCHEMA_SOURCE"] == "canonical" for p in captured)
    assert sum("KAETRAM_TOOL_RECOVERY" in p.kwargs["env"] for p in captured) == 180
    assert all(secret not in json.dumps(p.args[0]) for p in captured)


def test_confirmatory_launch_fails_closed_when_environment_rng_is_unavailable(
    tmp_path: Path, monkeypatch
):
    plan = replace(
        build_plan(_manifest_copy(tmp_path)),
        allow_launch=True,
        environment_seed_mechanism="unavailable",
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Popen must not be reached"),
    )
    endpoints = {
        cell.endpoint_env: "https://signed.example.invalid/v1"
        for cell in plan.cells
    }
    with pytest.raises(ManifestError, match="unsupported Kaetram environment RNG"):
        launch(plan, confirmation=plan.experiment_id, environ=endpoints)


def test_environment_rng_capability_requires_exact_built_checkout(tmp_path: Path, monkeypatch):
    plan = build_plan(_manifest_copy(tmp_path))
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(game_dir)], check=True)
    subprocess.run(["git", "-C", str(game_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(game_dir), "config", "user.name", "Test"], check=True)
    (game_dir / "tracked.txt").write_text("source\n")
    subprocess.run(["git", "-C", str(game_dir), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(game_dir), "commit", "-qm", "source"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(game_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    source_tree = subprocess.check_output(
        ["git", "-C", str(game_dir), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    plan = replace(plan, environment_game_revision=revision)
    server_build = game_dir / "packages" / "server" / "dist" / "main.js"
    server_build.parent.mkdir(parents=True)
    server_build.write_text("// test build")
    bundle_sha = hashlib.sha256(server_build.read_bytes()).hexdigest()
    (server_build.parent / "kaetram-build-attestation.json").write_text(json.dumps({
        "schema": "kaetram-server-build-attestation/v1",
        "gameRevision": revision,
        "sourceTreeGitOid": source_tree,
        "entrypoint": "packages/server/dist/main.js",
        "entrypointSha256": bundle_sha,
    }))

    capability = require_environment_seed_capability(
        plan, {"KAETRAM_GAME_DIR": str(game_dir)}
    )
    assert capability["entrypoint_sha256"] == bundle_sha

    with pytest.raises(ManifestError, match="revision mismatch"):
        require_environment_seed_capability(
            replace(plan, environment_game_revision="0" * 40),
            {"KAETRAM_GAME_DIR": str(game_dir)},
        )

    server_build.write_text("// stale build")
    with pytest.raises(ManifestError, match="bundle digest mismatch"):
        require_environment_seed_capability(plan, {"KAETRAM_GAME_DIR": str(game_dir)})


def test_cleanup_terminates_the_owned_process_group(monkeypatch) -> None:
    sent = []

    class HungProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("factorial-cell", timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

    log_handle = io.StringIO()
    monkeypatch.setattr(os, "killpg", lambda pid, sig: sent.append((pid, sig)))

    _cleanup_processes([(None, HungProcess(), log_handle)])

    assert sent == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert log_handle.closed


def test_cell_result_validation_rejects_failed_or_misattributed_artifacts(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    cell = plan.cells[0]
    result_path = Path(cell.run_dir) / cell.cell_id / "results.json"
    result_path.parent.mkdir(parents=True)

    def write_result(*, status="ok", model=None, returncode=0, duration_seconds=None):
        result_path.write_text(json.dumps({
            "meta": {
                "model": model or cell.cell_id,
                "scenario": plan.scenario,
                "duration_seconds_budget": plan.duration_seconds,
                "protocol_id": plan.protocol_id,
                "experiment_manifest_sha256": plan.manifest_sha256,
                "endpoint_attestation_sha256": cell.endpoint_attestation_sha256,
                "checkpoint_sha256": cell.checkpoint_sha256,
                "tokenizer_sha256": cell.tokenizer_sha256,
                "render_contract_sha256": cell.render_contract_sha256,
                "total_episodes": 1,
                "ok_episodes": int(status == "ok"),
                "tool_schema_source": plan.tool_schema_source,
                "include_game_knowledge": not plan.omit_game_knowledge,
                "held_out_quest": plan.held_out_quest,
                "inference_seed": cell.inference_seed,
                "factorial_schedule_algorithm": plan.schedule_algorithm,
                "factorial_schedule_seed": plan.schedule_seed,
                "factorial_schedule_index": cell.schedule_index,
                "factorial_batch_index": cell.batch_index,
                "factorial_cluster_id": cell.cluster_id,
                "factorial_pair_id": cell.pair_id,
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
            },
            "episodes": [{
                "episode": 1,
                "status": status,
                "returncode": returncode,
                "duration_seconds": (
                    plan.duration_seconds if duration_seconds is None else duration_seconds
                ),
            }],
        }))

    write_result()
    validate_cell_result(plan, cell)

    write_result(status="no_log")
    with pytest.raises(ManifestError, match="failed episode"):
        validate_cell_result(plan, cell)

    write_result(returncode=1)
    with pytest.raises(ManifestError, match="nonzero episode return code"):
        validate_cell_result(plan, cell)

    write_result(duration_seconds=plan.duration_seconds - 1)
    with pytest.raises(ManifestError, match="shorter than the registered duration"):
        validate_cell_result(plan, cell)

    write_result(model="different-cell")
    with pytest.raises(ManifestError, match="metadata mismatch"):
        validate_cell_result(plan, cell)


def test_cell_result_validation_accepts_frozen_core3_empty_heldout(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    cell = plan.cells[0]
    result_path = Path(cell.run_dir) / cell.cell_id / "results.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({
        "meta": {
            "model": cell.cell_id,
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
            "include_game_knowledge": True,
            "held_out_quest": "",
            "inference_seed": cell.inference_seed,
            "factorial_schedule_algorithm": plan.schedule_algorithm,
            "factorial_schedule_seed": plan.schedule_seed,
            "factorial_schedule_index": cell.schedule_index,
            "factorial_batch_index": cell.batch_index,
            "factorial_cluster_id": cell.cluster_id,
            "factorial_pair_id": cell.pair_id,
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
        },
        "episodes": [{
            "episode": 1,
            "status": "ok",
            "returncode": 0,
            "duration_seconds": plan.duration_seconds,
        }],
    }))

    validate_cell_result(plan, cell)


def _write_complete_cell_artifacts(plan, cell, *, include_raw_emission=True):
    cell_root = Path(cell.run_dir) / cell.cell_id
    cell_root.mkdir(parents=True)
    (Path(cell.run_dir) / "launcher.log").write_text("launcher output\n")
    (cell_root / "system_prompt.md").write_text("resolved prompt\n")
    (cell_root / "episode_001.jsonl").write_text('{"type":"assistant"}\n')
    (cell_root / "episode_001_state.json").write_text(json.dumps({
        "schema_version": "kaetram.eval-state-boundary.v1",
        "episode": 1,
    }))
    raw_dir = cell_root / "episode_001_raw"
    raw_dir.mkdir()
    record_type = "raw_model_emission" if include_raw_emission else "assistant"
    (raw_dir / "session_1_test.log").write_text(json.dumps({
        "type": record_type,
        "content": "exact endpoint response",
    }) + "\n")
    (raw_dir / "session_1_test.meta.json").write_text("{}\n")
    (cell_root / "results.json").write_text(json.dumps({
        "meta": {
            "model": cell.cell_id,
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
            "factorial_schedule_algorithm": plan.schedule_algorithm,
            "factorial_schedule_seed": plan.schedule_seed,
            "factorial_schedule_index": cell.schedule_index,
            "factorial_batch_index": cell.batch_index,
            "factorial_cluster_id": cell.cluster_id,
            "factorial_pair_id": cell.pair_id,
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
        },
        "episodes": [{
            "episode": 1,
            "status": "ok",
            "returncode": 0,
            "duration_seconds": plan.duration_seconds,
        }],
    }))


def test_completed_cell_bundle_seals_raw_prompt_state_and_inventory(tmp_path: Path):
    full_plan = build_plan(_manifest_copy(tmp_path))
    cell = full_plan.cells[0]
    plan = replace(full_plan, cells=(cell,))
    _write_complete_cell_artifacts(plan, cell)

    validate_cell_result(plan, cell)
    bundle_path = seal_cell_bundle(plan, cell)
    bundle = json.loads(bundle_path.read_text())
    assert bundle["schema_version"] == "kaetram.factorial-cell-bundle.v1"
    assert {artifact["name"] for artifact in bundle["artifacts"]} == {
        "results",
        "resolved_prompt",
        "launcher_log",
        "episode_001_parsed_transcript",
        "episode_001_raw_sessions",
        "episode_001_state_boundary",
    }
    with pytest.raises(ManifestError, match="refusing to overwrite"):
        seal_cell_bundle(plan, cell)
    assert validate_cell_bundle(plan, cell)["bundle_sha256"] == bundle["bundle_sha256"]

    prompt_path = Path(cell.run_dir) / cell.cell_id / "system_prompt.md"
    prompt_path.write_text("tampered prompt\n")
    with pytest.raises(ManifestError, match="sha256 mismatch"):
        validate_cell_bundle(plan, cell)
    prompt_path.write_text("resolved prompt\n")

    inventory_path = seal_completed_inventory(plan)
    inventory = json.loads(inventory_path.read_text())
    assert inventory["requested_cell_ids"] == [cell.cell_id]
    assert inventory["completed_cells"][0]["bundle_sha256"] == bundle["bundle_sha256"]
    assert validate_completed_inventory(plan)["inventory_sha256"] == inventory["inventory_sha256"]

    raw_log = Path(cell.run_dir) / cell.cell_id / "episode_001_raw" / "session_1_test.log"
    raw_log.write_text('{"type":"raw_model_emission","content":"changed"}\n')
    with pytest.raises(ManifestError, match="sha256 mismatch"):
        validate_completed_inventory(plan)


def test_cell_bundle_rejects_rewritten_only_logs(tmp_path: Path):
    full_plan = build_plan(_manifest_copy(tmp_path))
    cell = full_plan.cells[0]
    plan = replace(full_plan, cells=(cell,))
    _write_complete_cell_artifacts(plan, cell, include_raw_emission=False)
    with pytest.raises(ManifestError, match="pre-rewrite model emissions"):
        seal_cell_bundle(plan, cell)


def test_live_launch_rejects_unresolved_example_attestations_before_network(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    endpoint_env = {cell.endpoint_env: "https://example.invalid/v1" for cell in plan.cells}
    with pytest.raises(ManifestError, match="unresolved_example"):
        validate_live_endpoint_attestations(plan, endpoint_env)


def test_live_endpoint_identity_is_verified_against_health_payload(tmp_path: Path, monkeypatch):
    plan = build_plan(_manifest_copy(tmp_path))
    model_provenance = tuple({
        **model,
        "attestation_status": "attested",
        "checkpoint_sha256": "b" * 64,
        "expected_health": {**model["expected_health"], "checkpoint_sha256": "b" * 64},
    } for model in plan.model_provenance)
    plan = replace(plan, model_provenance=model_provenance)
    endpoint_env = {
        model["endpoint_env"]: f"https://{model['weight']}.example.invalid/v1"
        for model in model_provenance
    }
    expected_by_host = {
        f"{model['weight']}.example.invalid": model["expected_health"]
        for model in model_provenance
    }

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"attestation": self.payload}).encode()

    def fake_urlopen(request, timeout):
        host = request.full_url.split("/", 3)[2]
        assert request.full_url.endswith("/health")
        assert timeout == 60.0
        return Response(expected_by_host[host])

    monkeypatch.setattr("scripts.opd.factorial_eval.urlopen", fake_urlopen)
    verified = validate_live_endpoint_attestations(plan, endpoint_env)
    assert [item["weight"] for item in verified] == ["base", "r2", "r3"]

    expected_by_host["r2.example.invalid"] = {
        **expected_by_host["r2.example.invalid"],
        "tokenizer_sha256": "f" * 64,
    }
    with pytest.raises(ManifestError, match="identity attestation mismatch"):
        validate_live_endpoint_attestations(plan, endpoint_env)


def test_prelaunch_ledger_is_self_hashed_create_only_and_preserves_heldout_metadata(
    tmp_path: Path, monkeypatch,
):
    plan = build_plan(_manifest_copy(tmp_path))
    commit = "a" * 40
    plan = replace(plan, source_git_commit=commit)
    monkeypatch.setattr("scripts.opd.factorial_eval.capture_git_state", lambda repo: {
        "repository": "git@example.test:owner/repo.git",
        "commit": commit,
        "branch": "main",
        "dirty": False,
        "dirty_paths": [],
    })
    server_build = {"entrypoint_sha256": "c" * 64}
    path = seal_prelaunch_record(plan, [], server_build)
    record = json.loads(path.read_text())
    assert record["held_out"] == {
        "quest": "",
        "registration": "",
        "registration_sha256": "",
    }
    assert record["environment_rng"] == {
        "mechanism": plan.environment_seed_mechanism,
        "algorithm": plan.environment_rng_algorithm,
        "game_revision": plan.environment_game_revision,
        "replicate_seeds": list(plan.environment_seeds),
        "residual_nondeterminism": plan.environment_seed_reason,
        "server_build": server_build,
    }
    assert record["prelaunch_sha256"]
    with pytest.raises(ManifestError, match="refusing to overwrite"):
        seal_prelaunch_record(plan, [], server_build)


def test_manifest_rejects_prompt_or_power_artifact_digest_drift(tmp_path: Path):
    def prompt_drift(raw):
        raw["protocol"]["prompt_inputs"][0]["sha256"] = "f" * 64

    with pytest.raises(ManifestError, match="digest mismatch"):
        build_plan(_manifest_copy(tmp_path, prompt_drift))

    def power_drift(raw):
        raw["analysis"]["sample_size"]["power_analysis_sha256"] = "f" * 64

    with pytest.raises(ManifestError, match="digest mismatch"):
        build_plan(_manifest_copy(tmp_path, power_drift))
