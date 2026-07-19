from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference_seed import MAX_INFERENCE_SEED, derive_request_seed, validate_inference_seed
from eval_harness import run_episode, verify_environment_rng_attestation


REPO = Path(__file__).resolve().parents[2]
ENDPOINTS = (
    "finetune/serve_modal_2b.py",
    "finetune/serve_modal_2b_opd_r2.py",
    "finetune/serve_modal_2b_opd_r3.py",
)


def test_request_seed_derivation_is_stable_bounded_and_turn_specific():
    first = derive_request_seed(11001, 1, 1)
    assert first == derive_request_seed(11001, 1, 1)
    assert 0 <= first <= MAX_INFERENCE_SEED
    assert len({
        derive_request_seed(11001, 1, 1),
        derive_request_seed(11001, 1, 2),
        derive_request_seed(11001, 2, 1),
        derive_request_seed(11002, 1, 1),
    }) == 4


@pytest.mark.parametrize("value", [True, -1, MAX_INFERENCE_SEED + 1, "11001"])
def test_inference_seed_validation_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_inference_seed(value)


def test_play_qwen_passes_derived_seed_as_openai_request_field():
    source = (REPO / "play_qwen.py").read_text()
    ast.parse(source)
    assert 'completion_kwargs["seed"] = derive_request_seed(' in source
    assert "client.chat.completions.create(**completion_kwargs)" in source


def test_eval_harness_propagates_seed_and_provenance_to_play_qwen(
    tmp_path: Path, monkeypatch
):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("eval_harness.subprocess.run", fake_run)
    provenance = {
        "factorial_schedule_algorithm": "sha256-rank-v1",
        "factorial_schedule_seed": 20260718,
        "factorial_schedule_index": 0,
        "factorial_batch_index": 0,
        "factorial_cluster_id": "rep01-base",
        "factorial_pair_id": "rep01-base-grinder",
        "environment_seed_mechanism": "kaetram-environment-rng-attestation/v2",
        "environment_seed": 21001,
        "environment_rng_algorithm": "mulberry32-sha256-v1",
        "environment_game_revision": "6a75dea54983e5a3a10da71c03bf9ece218f56bb",
        "environment_seed_reason": "server gameplay RNG only",
    }
    run_dir = tmp_path / "run"
    run_episode(
        project_dir=str(REPO),
        endpoint="https://example.invalid/v1",
        model_api_name="2b-base",
        sandbox=str(tmp_path / "sandbox"),
        duration_seconds=1,
        system_prompt_file=str(tmp_path / "prompt.md"),
        username="seedbot",
        run_dir=run_dir,
        inference_seed=11001,
        run_provenance=provenance,
    )
    assert captured["cmd"][captured["cmd"].index("--inference-seed") + 1] == "11001"
    meta = json.loads((run_dir / "harness_meta_template.json").read_text())
    assert meta["inference_seed"] == 11001
    assert all(meta[key] == value for key, value in provenance.items())


def test_environment_rng_attestation_verification_is_fail_closed(tmp_path: Path):
    provenance = {
        "environment_seed_mechanism": "kaetram-environment-rng-attestation/v2",
        "environment_seed": 21001,
        "environment_rng_algorithm": "mulberry32-sha256-v1",
        "environment_game_revision": "6a75dea54983e5a3a10da71c03bf9ece218f56bb",
        "environment_game_bundle_sha256": "c" * 64,
    }
    attestation = {
        "schema": provenance["environment_seed_mechanism"],
        "algorithm": provenance["environment_rng_algorithm"],
        "seedSha256": hashlib.sha256(b"21001").hexdigest(),
        "gameRevision": provenance["environment_game_revision"],
        "serverBundleSha256": provenance["environment_game_bundle_sha256"],
        "drawsAtAttestation": 0,
        "coverage": ["audited gameplay helpers"],
    }
    path = tmp_path / "environment-rng.json"
    path.write_text(json.dumps(attestation))
    verified = verify_environment_rng_attestation(path, provenance)
    assert verified == {key: attestation[key] for key in (
        "schema", "algorithm", "seedSha256", "gameRevision", "serverBundleSha256",
        "drawsAtAttestation",
    )}

    attestation["seedSha256"] = "0" * 64
    path.write_text(json.dumps(attestation))
    with pytest.raises(RuntimeError, match="attestation mismatch"):
        verify_environment_rng_attestation(path, provenance)


@pytest.mark.parametrize("relative_path", ENDPOINTS)
def test_confirmatory_2b_endpoint_passes_validated_seed_to_sglang(relative_path: str):
    source = (REPO / relative_path).read_text()
    ast.parse(source)
    assert 'seed = body.get("seed")' in source
    assert 'seed = validate_inference_seed(seed, label="seed")' in source
    assert 'sampling_params["sampling_seed"] = seed' in source
    assert '"supports_seed": True' in source
