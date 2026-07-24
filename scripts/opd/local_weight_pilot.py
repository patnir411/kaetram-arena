#!/usr/bin/env python3
"""Run the preregistered zero-cost local weights pilot.

This launcher is intentionally separate from the confirmatory factorial
launcher. It executes a small feasibility pilot, preserves every cell, and
labels the resulting evidence as exploratory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from run_manifest import canonical_json_bytes, sha256_json  # noqa: E402
from eval_harness import (  # noqa: E402
    attest_game_database_configuration,
    resolve_system_prompt,
)
from play_qwen import (  # noqa: E402
    CONTEXT_BUDGET,
    MAX_SEQ_LEN,
    QWEN_THINK_PRESENCE_PENALTY,
    QWEN_THINK_TEMPERATURE,
    QWEN_THINK_TOP_K,
    QWEN_THINK_TOP_P,
    RESPONSE_BUDGET,
    _FORMAT_NOTE,
)
from scripts.local_mlx_endpoint import SUPPORTED_MODELS  # noqa: E402
from scripts import bootstrap_local_mlx, bootstrap_unit_tests  # noqa: E402
from scripts.isolated_python_entry import (  # noqa: E402
    isolated_contract_active,
    isolated_python_command,
)


SCHEMA_VERSION = "kaetram.local-weight-pilot.v1"
RECOVERY_FACTORIAL_SCHEMA_VERSION = "kaetram.local-weight-recovery-factorial.v1"
PILOT_PRELAUNCH_SCHEMA_VERSION = "kaetram.local-weight-pilot-prelaunch.v3"
INTERMEDIATE_PILOT_PRELAUNCH_SCHEMA_VERSION = (
    "kaetram.local-weight-pilot-prelaunch.v2"
)
LEGACY_PILOT_PRELAUNCH_SCHEMA_VERSION = "kaetram.local-weight-pilot-prelaunch.v1"
PILOT_INVENTORY_SCHEMA_VERSION = "kaetram.local-weight-pilot-inventory.v1"
RECOVERY_PRELAUNCH_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-prelaunch.v3"
)
INTERMEDIATE_RECOVERY_PRELAUNCH_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-prelaunch.v2"
)
LEGACY_RECOVERY_PRELAUNCH_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-prelaunch.v1"
)
RECOVERY_INVENTORY_SCHEMA_VERSION = (
    "kaetram.local-weight-recovery-factorial-inventory.v1"
)
PILOT_STATUS = "preregistered_exploratory"
WEIGHTS = ("base_2b", "opd_r2_2b", "opd_r3_2b")
WEIGHT_LABEL = {"base_2b": "base", "opd_r2_2b": "r2", "opd_r3_2b": "r3"}
ENDPOINT_ENV = "KAETRAM_LOCAL_PILOT_ENDPOINT"
ENDPOINT_HOST = "127.0.0.1"
ENDPOINT_PORT = 9801
BACKEND_PORT = 9802
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MONGO_CONTAINER = "kaetram-mongo"
MONGO_IMAGE_REPO_DIGEST = (
    "mongo@sha256:9bdaeb6dac6e7e762e84e2f84103d1f9bb078fa1ba6bde8bb9d2274f655ad173"
)
MONGO_IMAGE_ID = (
    "sha256:b3b6a0771f6a4c269cc1fe1fd59e84e9c7f1601f0e273571004158e0ba8c5705"
)


class PilotError(RuntimeError):
    """Raised when the exploratory pilot cannot preserve its contract."""


def clean_python_environment(base: dict[str, str]) -> dict[str, str]:
    """Remove ambient controls over Python startup and import resolution."""
    return {
        key: value
        for key, value in base.items()
        if not key.upper().startswith("PYTHON")
    }


def require_isolated_execution() -> None:
    """Require the reviewed wrapper before any result-bearing launch."""
    environment = Path(sys.executable).absolute().parent.parent
    if not isolated_contract_active(environment):
        raise PilotError(
            "launch must run through scripts/isolated_python_entry.py "
            "with the pinned evaluation environment"
        )
    unexpected = {
        key
        for key in os.environ
        if key.upper().startswith("PYTHON")
        and key not in {"PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"}
    }
    if unexpected:
        raise PilotError(
            "isolated launch retained ambient Python controls: "
            + ", ".join(sorted(unexpected))
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run_checked(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PilotError(f"{label} is unavailable") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PilotError(
            f"{label} failed with exit {result.returncode}"
            + (f": {detail}" if detail else "")
        )
    return result


def attest_playwright_runtime() -> dict:
    """Launch the pinned local browser once and bind its executable bytes."""
    result = _run_checked(
        isolated_python_command(
            sys.executable,
            repo_root=REPO,
            environment_root=Path(sys.executable).absolute().parent.parent,
            script=REPO / "scripts/attest_playwright_runtime.py",
        ),
        "pinned Playwright Chromium preflight",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PilotError("Playwright Chromium returned invalid identity") from exc
    executable = Path(str(payload.get("executable_path", "")))
    if (
        payload.get("browser_name") != "chromium"
        or not isinstance(payload.get("browser_version"), str)
        or not payload["browser_version"]
        or not executable.is_file()
        or executable.is_symlink()
    ):
        raise PilotError("Playwright Chromium identity is incomplete")
    record = {
        "schema_version": "kaetram.playwright-runtime-receipt.v1",
        "browser_name": "chromium",
        "browser_version": payload["browser_version"],
        "executable_sha256": _sha256_file(executable),
    }
    return {**record, "receipt_sha256": sha256_json(record)}


def attest_mongodb_runtime(expected_database: str) -> dict:
    """Require the pinned loopback-only Mongo container before model startup."""
    docker = shutil.which("docker")
    if not docker:
        raise PilotError("Docker is required for the local MongoDB lane")
    inspect = _run_checked(
        [docker, "inspect", MONGO_CONTAINER], "local MongoDB container inspection"
    )
    try:
        payload = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        raise PilotError("Docker returned invalid MongoDB inspection JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise PilotError("Docker returned an ambiguous MongoDB container identity")
    container = payload[0]
    image_id = container.get("Image")
    state = container.get("State")
    network = container.get("NetworkSettings")
    all_ports = network.get("Ports") if isinstance(network, dict) else None
    ports = all_ports.get("27017/tcp") if isinstance(all_ports, dict) else None
    if (
        image_id != MONGO_IMAGE_ID
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or ports != [{"HostIp": "127.0.0.1", "HostPort": "27017"}]
    ):
        raise PilotError(
            "local MongoDB must use the pinned image and expose only "
            "127.0.0.1:27017"
        )
    image = _run_checked(
        [docker, "image", "inspect", image_id], "local MongoDB image inspection"
    )
    try:
        image_payload = json.loads(image.stdout)
    except json.JSONDecodeError as exc:
        raise PilotError("Docker returned invalid MongoDB image JSON") from exc
    if (
        not isinstance(image_payload, list)
        or len(image_payload) != 1
        or not isinstance(image_payload[0], dict)
        or not isinstance(image_payload[0].get("RepoDigests"), list)
        or MONGO_IMAGE_REPO_DIGEST not in image_payload[0]["RepoDigests"]
    ):
        raise PilotError("local MongoDB image does not match the pinned repository digest")
    ping = _run_checked(
        [
            docker,
            "exec",
            MONGO_CONTAINER,
            "mongosh",
            expected_database,
            "--quiet",
            "--eval",
            "db.runCommand({ping:1}).ok",
        ],
        "local MongoDB ping",
    )
    if ping.stdout.strip() != "1":
        raise PilotError("local MongoDB ping did not return 1")
    docker_version = _run_checked(
        [docker, "version", "--format", "{{.Client.Version}}"],
        "Docker client version",
    ).stdout.strip()
    if not docker_version:
        raise PilotError("Docker client version is empty")
    record = {
        "schema_version": "kaetram.mongodb-runtime-receipt.v1",
        "container_name": MONGO_CONTAINER,
        "database": expected_database,
        "host": "127.0.0.1",
        "port": 27017,
        "image_id": MONGO_IMAGE_ID,
        "image_repo_digest": MONGO_IMAGE_REPO_DIGEST,
        "docker_client_version": docker_version,
    }
    return {**record, "receipt_sha256": sha256_json(record)}


def verify_python_environment_receipts(
    source_revision: str,
    mlx_python: Path,
) -> tuple[dict, dict]:
    """Verify both managed environments and return path-independent receipts."""
    try:
        eval_target = bootstrap_unit_tests.safe_venv_path(
            Path(sys.executable).absolute().parent.parent
        )
        expected_eval_python = bootstrap_unit_tests.venv_python(eval_target).absolute()
        invoked_mlx_python = preserve_invoked_path(mlx_python).absolute()
        mlx_target = bootstrap_local_mlx.safe_venv_path(
            invoked_mlx_python.parent.parent
        )
        expected_mlx_python = bootstrap_local_mlx.venv_python(mlx_target).absolute()
        if Path(sys.executable).absolute() != expected_eval_python:
            raise PilotError(
                "launcher interpreter is not exactly the managed evaluation Python"
            )
        if invoked_mlx_python != expected_mlx_python:
            raise PilotError(
                "MLX interpreter is not exactly the managed MLX Python"
            )
        eval_receipt = bootstrap_unit_tests.verified_environment_receipt(
            eval_target,
            source_revision,
        )
        mlx_receipt = bootstrap_local_mlx.verified_environment_receipt(
            mlx_target,
            source_revision,
        )
    except (
        bootstrap_unit_tests.BootstrapError,
        bootstrap_local_mlx.BootstrapError,
    ) as exc:
        raise PilotError(f"pinned Python environment verification failed: {exc}") from exc
    return eval_receipt, mlx_receipt


def preserve_invoked_path(path: Path) -> Path:
    """Make a CLI path absolute without resolving virtualenv symlinks."""
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _require_clean_git(repo: Path, label: str) -> str:
    try:
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PilotError(f"cannot inspect {label} git checkout") from exc
    if Path(top).resolve() != repo.resolve():
        raise PilotError(f"{label} path is not its git toplevel")
    if status:
        raise PilotError(f"{label} checkout must be clean")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PilotError(f"{label} revision is not an exact commit")
    return revision


def _validate_schedule(raw: dict) -> None:
    if raw.get("schema_version") == RECOVERY_FACTORIAL_SCHEMA_VERSION:
        _validate_recovery_factorial_schedule(raw)
        return
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise PilotError(f"manifest schema_version must be {SCHEMA_VERSION}")
    if raw.get("status") != PILOT_STATUS:
        raise PilotError(f"manifest status must be {PILOT_STATUS}")
    boundary = raw.get("claim_boundary")
    if not isinstance(boundary, dict) or boundary.get("confirmatory") is not False:
        raise PilotError("pilot must be explicitly non-confirmatory")
    protocol = raw.get("protocol")
    if not isinstance(protocol, dict):
        raise PilotError("manifest protocol must be an object")
    expected_protocol = {
        "scenario": "D",
        "duration_seconds": 300,
        "episodes_per_cell": 1,
        "personality": "completionist",
        "prompt_agent_name": "EvalCompletionist",
        "include_game_knowledge": True,
        "recovery": False,
        "tool_schema_source": "canonical",
        "mongo_database": "kaetram_devlopment",
        "schedule_algorithm": "sha256-rank-v1",
        "schedule_seed": 20260723,
        "environment_seed_mechanism": "kaetram-environment-rng-attestation/v2",
        "environment_rng_algorithm": "mulberry32-sha256-v1",
    }
    mismatches = {
        key: {"expected": value, "actual": protocol.get(key)}
        for key, value in expected_protocol.items()
        if protocol.get(key) != value
    }
    if mismatches:
        raise PilotError(f"unreviewed pilot protocol: {mismatches}")
    models = raw.get("models")
    if not isinstance(models, dict) or tuple(models) != WEIGHTS:
        raise PilotError(f"models must be ordered exactly as {list(WEIGHTS)}")
    for snapshot in WEIGHTS:
        if models[snapshot].get("api_model") != SUPPORTED_MODELS[snapshot]:
            raise PilotError(f"{snapshot} has an unreviewed API model")

    cells = raw.get("cells")
    if not isinstance(cells, list) or len(cells) != 9:
        raise PilotError("pilot must contain exactly nine cells")
    ids = [cell.get("cell_id") for cell in cells if isinstance(cell, dict)]
    if len(set(ids)) != 9 or any(
        not isinstance(cell_id, str)
        or re.fullmatch(r"rep0[1-3]-(?:base|r2|r3)", cell_id) is None
        for cell_id in ids
    ):
        raise PilotError("pilot cell IDs must be unique and reviewed")
    if [cell.get("schedule_index") for cell in cells] != list(range(9)):
        raise PilotError("pilot schedule indices must be contiguous and ordered")

    pilot_id = raw.get("pilot_id")
    for replicate in (1, 2, 3):
        block = [cell for cell in cells if cell.get("replicate") == replicate]
        if {cell.get("snapshot") for cell in block} != set(WEIGHTS):
            raise PilotError(f"replicate {replicate} must contain all weight arms")
        if len({cell.get("inference_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} inference seed is not paired")
        if len({cell.get("environment_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} environment seed is not paired")
        expected_order = sorted(
            WEIGHTS,
            key=lambda weight: hashlib.sha256(
                f"{pilot_id}|{replicate}|{weight}".encode()
            ).hexdigest(),
        )
        actual_order = [
            cell["snapshot"]
            for cell in sorted(block, key=lambda cell: cell["schedule_index"])
        ]
        if actual_order != expected_order:
            raise PilotError(f"replicate {replicate} schedule hash does not match")


def _validate_recovery_factorial_schedule(raw: dict) -> None:
    if raw.get("pilot_id") != "local-weights-recovery-30m-v1":
        raise PilotError("recovery-factorial pilot_id is not the reviewed identity")
    if raw.get("status") != PILOT_STATUS:
        raise PilotError(f"manifest status must be {PILOT_STATUS}")
    boundary = raw.get("claim_boundary")
    if not isinstance(boundary, dict) or boundary.get("confirmatory") is not False:
        raise PilotError("factorial pilot must be explicitly non-confirmatory")
    expected_prohibited = [
        "model superiority",
        "causal weight effect",
        "causal recovery effect or benefit",
        "quest-completion improvement",
        "generalization",
    ]
    if boundary.get("prohibited_claims") != expected_prohibited:
        raise PilotError("recovery-factorial claim boundary has drifted")
    protocol = raw.get("protocol")
    if not isinstance(protocol, dict):
        raise PilotError("manifest protocol must be an object")
    expected_protocol = {
        "scenario": "D",
        "duration_seconds": 1800,
        "episodes_per_cell": 1,
        "personality": "completionist",
        "prompt_agent_name": "EvalCompletionist",
        "include_game_knowledge": True,
        "recovery_factor": [False, True],
        "tool_schema_source": "canonical",
        "mongo_database": "kaetram_devlopment",
        "schedule_algorithm": "balanced-paired-order-v1",
        "schedule_seed": 20260725,
        "environment_seed_mechanism": "kaetram-environment-rng-attestation/v2",
        "environment_rng_algorithm": "mulberry32-sha256-v1",
        "environment_seed_reason": (
            "Paired seeds hold the initial deterministic gameplay RNG state fixed "
            "across all weight and recovery arms within each replicate; realized "
            "streams may diverge after treatment-dependent actions."
        ),
    }
    mismatches = {
        key: {"expected": value, "actual": protocol.get(key)}
        for key, value in expected_protocol.items()
        if protocol.get(key) != value
    }
    if mismatches:
        raise PilotError(f"unreviewed recovery-factorial protocol: {mismatches}")
    models = raw.get("models")
    if not isinstance(models, dict) or tuple(models) != WEIGHTS:
        raise PilotError(f"models must be ordered exactly as {list(WEIGHTS)}")
    for snapshot in WEIGHTS:
        if models[snapshot].get("api_model") != SUPPORTED_MODELS[snapshot]:
            raise PilotError(f"{snapshot} has an unreviewed API model")
    expected_checkpoints = {
        "base_2b": "aa33250c4fc64891ddfaba3a314fd9542ea371843c387178b425fbcc5ed680b1",
        "opd_r2_2b": "636aa92a16ee63965d5211625ba32c0524eb90da87f6b4727ee1f057e0486104",
        "opd_r3_2b": "c4df1ea2fda9253d595ff4a2068c72dd4de173241ab93cc1ee0d5dc42e302873",
    }
    if {
        snapshot: models[snapshot].get("checkpoint_sha256")
        for snapshot in WEIGHTS
    } != expected_checkpoints:
        raise PilotError("recovery-factorial checkpoint identities have drifted")
    expected_artifacts = {
        "tokenizer_sha256": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
        "render_contract_sha256": "11908184ad9ba71352740d4d429790ea427446b11146be202bf873bd7163979f",
        "chat_template_sha256": "562a0a6f72bf5722612866d9f5d7e947be4b08a429e407f8e522e70137db5e1e",
        "system_prompt_sha256": "b86e0fa3306778f03420ab59528df537723ba035d6d1efec723ac4624c49b734",
        "game_revision": "7a3d722e8e200ca44fd959099386b42a5fbe0cb5",
        "game_bundle_sha256": "b0f9e42b0da63dc7bb1f9172136cd8a1361f762e683b72011172db286c256916",
        "source_identity": (
            "The launch requires a clean arena commit containing this registration; "
            "prelaunch.json records that exact commit and every cell must match it."
        ),
    }
    if raw.get("artifact_contract") != expected_artifacts:
        raise PilotError("recovery-factorial artifact contract has drifted")
    expected_decoding = {
        "temperature": QWEN_THINK_TEMPERATURE,
        "top_p": QWEN_THINK_TOP_P,
        "top_k": QWEN_THINK_TOP_K,
        "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
        "max_response_tokens": RESPONSE_BUDGET,
        "max_context_tokens": MAX_SEQ_LEN,
        "request_seed_derivation": (
            "derive_request_seed(inference_seed, session_number, turn)"
        ),
    }
    if raw.get("decoding_contract") != expected_decoding:
        raise PilotError("recovery-factorial decoding contract has drifted")
    if CONTEXT_BUDGET != MAX_SEQ_LEN - RESPONSE_BUDGET:
        raise PilotError("runtime context budget is internally inconsistent")
    recovery_contract = raw.get("recovery_contract")
    if (
        not isinstance(recovery_contract, dict)
        or recovery_contract.get("correction_note_sha256")
        != hashlib.sha256(_FORMAT_NOTE.encode()).hexdigest()
        or recovery_contract.get("off_state") != "KAETRAM_TOOL_RECOVERY is absent"
        or recovery_contract.get("on_state")
        != "KAETRAM_TOOL_RECOVERY is exactly 1"
    ):
        raise PilotError("recovery-factorial treatment contract has drifted")

    cells = raw.get("cells")
    if not isinstance(cells, list) or len(cells) != 18:
        raise PilotError("recovery factorial must contain exactly 18 cells")
    ids = [cell.get("cell_id") for cell in cells if isinstance(cell, dict)]
    if len(set(ids)) != 18 or any(
        not isinstance(cell_id, str)
        or re.fullmatch(
            r"rep0[1-3]-(?:base|r2|r3)-rec-(?:off|on)", cell_id
        ) is None
        for cell_id in ids
    ):
        raise PilotError("recovery-factorial cell IDs must be unique and reviewed")
    if [cell.get("schedule_index") for cell in cells] != list(range(18)):
        raise PilotError(
            "recovery-factorial schedule indices must be contiguous and ordered"
        )

    pilot_id = raw.get("pilot_id")
    schedule_seed = protocol["schedule_seed"]
    arms = {(weight, recovery) for weight in WEIGHTS for recovery in (False, True)}
    expected_seeds = {
        1: (1729, 42001),
        2: (2718, 42002),
        3: (3141, 42003),
    }
    weight_ids = {
        "base_2b": "base",
        "opd_r2_2b": "r2",
        "opd_r3_2b": "r3",
    }
    for replicate in (1, 2, 3):
        block = [cell for cell in cells if cell.get("replicate") == replicate]
        if any(type(cell.get("recovery")) is not bool for cell in block):
            raise PilotError(
                f"replicate {replicate} recovery assignments must be Booleans"
            )
        if {(cell.get("snapshot"), cell.get("recovery")) for cell in block} != arms:
            raise PilotError(
                f"replicate {replicate} must contain all weights x recovery arms"
            )
        if len({cell.get("inference_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} inference seed is not paired")
        if len({cell.get("environment_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} environment seed is not paired")
        inference_seed, environment_seed = expected_seeds[replicate]
        if any(
            type(cell.get("inference_seed")) is not int
            or cell.get("inference_seed") != inference_seed
            or type(cell.get("environment_seed")) is not int
            or cell.get("environment_seed") != environment_seed
            for cell in block
        ):
            raise PilotError(f"replicate {replicate} seed identities have drifted")
        for cell in block:
            expected_id = (
                f"rep{replicate:02d}-{weight_ids[cell['snapshot']]}-rec-"
                f"{'on' if cell['recovery'] else 'off'}"
            )
            if cell["cell_id"] != expected_id:
                raise PilotError(
                    f"{cell['cell_id']}: ID does not match its registered arm"
                )
        offset = (2 * (replicate - 1)) % len(WEIGHTS)
        weight_order = list(WEIGHTS[offset:] + WEIGHTS[:offset])
        expected_order = []
        for weight in weight_order:
            recovery_order = sorted(
                (False, True),
                key=lambda recovery: hashlib.sha256(
                    (
                        f"{pilot_id}|{schedule_seed}|{replicate}|{weight}|"
                        f"recovery={str(recovery).lower()}"
                    ).encode()
                ).hexdigest(),
            )
            expected_order.extend(
                (weight, recovery) for recovery in recovery_order
            )
        actual_order = [
            (cell["snapshot"], cell["recovery"])
            for cell in sorted(block, key=lambda cell: cell["schedule_index"])
        ]
        if actual_order != expected_order:
            raise PilotError(
                f"replicate {replicate} recovery-factorial schedule hash does not match"
            )


def load_manifest(path: Path) -> tuple[dict, str]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError(f"cannot read pilot manifest: {path}") from exc
    if not isinstance(raw, dict):
        raise PilotError("pilot manifest must be a JSON object")
    _validate_schedule(raw)
    return raw, _sha256_file(path)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection((ENDPOINT_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _read_health(process: subprocess.Popen, timeout_seconds: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not contacted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PilotError(f"local endpoint exited during startup ({process.returncode})")
        try:
            request = Request(
                f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}/health",
                headers={"Accept": "application/json"},
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
            if payload.get("status") == "ok" and isinstance(
                payload.get("attestation"), dict
            ):
                return payload
            last_error = "invalid health payload"
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = type(exc).__name__
        time.sleep(0.25)
    raise PilotError(f"local endpoint did not become ready: {last_error}")


def _start_endpoint(
    *,
    snapshot: str,
    api_model: str,
    mlx_python: Path,
    snapshots_root: Path,
    log_path: Path,
) -> tuple[subprocess.Popen, dict]:
    if _port_open(ENDPOINT_PORT) or _port_open(BACKEND_PORT):
        raise PilotError("pilot endpoint ports are already in use")
    command = [
        *isolated_python_command(
            mlx_python,
            repo_root=REPO,
            environment_root=mlx_python.absolute().parent.parent,
            script=REPO / "scripts/local_mlx_endpoint.py",
            target_args=(
                "--snapshot",
                snapshot,
                "--api-model",
                api_model,
                "--snapshots-root",
                str(snapshots_root),
                "--port",
                str(ENDPOINT_PORT),
                "--backend-port",
                str(BACKEND_PORT),
            ),
        ),
    ]
    handle = log_path.open("w")
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=clean_python_environment(dict(os.environ)),
        )
    finally:
        handle.close()
    try:
        return process, _read_health(process)
    except Exception:
        _stop_process(process)
        raise


def _load_game_attestation(game_dir: Path, game_revision: str) -> dict:
    path = game_dir / "packages/server/dist/kaetram-build-attestation.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError("game build attestation is missing or invalid") from exc
    if record.get("schema") != "kaetram-server-build-attestation/v1":
        raise PilotError("unsupported game build attestation")
    if record.get("gameRevision") != game_revision:
        raise PilotError("compiled game server does not attest the clean checkout")
    entrypoint = game_dir / str(record.get("entrypoint", ""))
    expected = record.get("entrypointSha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        raise PilotError("game build attestation has no valid entrypoint digest")
    if not entrypoint.is_file() or _sha256_file(entrypoint) != expected:
        raise PilotError("compiled game server digest differs from its attestation")
    return record


def _write_json(path: Path, value: object) -> str:
    payload = canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def build_artifact_inventory(root: Path) -> dict:
    """Hash every retained cell artifact without following mutable symlinks."""
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise PilotError(f"cell artifact is a symlink: {path.relative_to(root)}")
        if not path.is_file() or path.name == "artifact-inventory.json":
            continue
        records.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    return {
        "schema_version": "kaetram.local-weight-pilot-artifacts.v1",
        "file_count": len(records),
        "files": records,
        "tree_sha256": sha256_json(records),
    }


def _preflight_endpoints(
    manifest: dict,
    mlx_python: Path,
    snapshots_root: Path,
    output_root: Path,
    mlx_environment_receipt_sha256: str,
) -> dict[str, dict]:
    receipts: dict[str, dict] = {}
    for snapshot in WEIGHTS:
        process = None
        try:
            process, health = _start_endpoint(
                snapshot=snapshot,
                api_model=manifest["models"][snapshot]["api_model"],
                mlx_python=mlx_python,
                snapshots_root=snapshots_root,
                log_path=output_root / f"preflight-{snapshot}.log",
            )
            receipts[snapshot] = health
        finally:
            _stop_process(process)
    tokenizer_digests = {
        receipt["attestation"].get("tokenizer_sha256")
        for receipt in receipts.values()
    }
    render_digests = {
        receipt["attestation"].get("render_contract_sha256")
        for receipt in receipts.values()
    }
    if len(tokenizer_digests) != 1 or not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in tokenizer_digests
    ):
        raise PilotError("preflight endpoints do not share one tokenizer")
    if len(render_digests) != 1 or not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in render_digests
    ):
        raise PilotError("preflight endpoints do not share one render contract")
    if any(
        receipt["attestation"].get("runtime_environment_receipt_sha256")
        != mlx_environment_receipt_sha256
        for receipt in receipts.values()
    ):
        raise PilotError("preflight endpoint MLX environment identity mismatch")
    if manifest["schema_version"] == RECOVERY_FACTORIAL_SCHEMA_VERSION:
        contract = manifest["artifact_contract"]
        for snapshot, receipt in receipts.items():
            attestation = receipt["attestation"]
            expected = {
                "checkpoint_sha256": manifest["models"][snapshot][
                    "checkpoint_sha256"
                ],
                "tokenizer_sha256": contract["tokenizer_sha256"],
                "render_contract_sha256": contract["render_contract_sha256"],
                "chat_template_sha256": contract["chat_template_sha256"],
            }
            mismatches = {
                key: {"expected": value, "actual": attestation.get(key)}
                for key, value in expected.items()
                if attestation.get(key) != value
            }
            if mismatches:
                raise PilotError(
                    f"{snapshot}: live endpoint differs from registration {mismatches}"
                )
    return receipts


def _cell_recovery(manifest: dict, cell: dict) -> bool:
    value = cell.get("recovery", manifest["protocol"].get("recovery"))
    if not isinstance(value, bool):
        raise PilotError(f"{cell['cell_id']}: recovery assignment is not Boolean")
    return value


def _ledger_schema_versions(manifest: dict) -> tuple[str, str]:
    if manifest["schema_version"] == RECOVERY_FACTORIAL_SCHEMA_VERSION:
        return RECOVERY_PRELAUNCH_SCHEMA_VERSION, RECOVERY_INVENTORY_SCHEMA_VERSION
    return PILOT_PRELAUNCH_SCHEMA_VERSION, PILOT_INVENTORY_SCHEMA_VERSION


def _username(cell: dict) -> str:
    weight = {"base_2b": "B", "opd_r2_2b": "R2", "opd_r3_2b": "R3"}[
        cell["snapshot"]
    ]
    recovery = (
        f"{'Y' if cell['recovery'] else 'N'}"
        if "recovery" in cell
        else ""
    )
    return f"Pilot{weight}{recovery}R{cell['replicate']:02d}"


def build_eval_command(
    *,
    manifest: dict,
    manifest_sha256: str,
    cell: dict,
    cell_root: Path,
    endpoint_attestation_sha256: str,
    endpoint_attestation: dict,
    game_attestation: dict,
    game_database_attestation: dict,
) -> list[str]:
    protocol = manifest["protocol"]
    attestation = endpoint_attestation["attestation"]
    return isolated_python_command(
        sys.executable,
        repo_root=REPO,
        environment_root=Path(sys.executable).absolute().parent.parent,
        script=REPO / "eval_harness.py",
        target_args=(
        "--models-env",
        f"{cell['cell_id']}={ENDPOINT_ENV}",
        "--episodes",
        "1",
        "--scenario",
        protocol["scenario"],
        "--duration-seconds",
        str(protocol["duration_seconds"]),
        "--protocol-id",
        manifest["pilot_id"],
        "--experiment-manifest-sha256",
        manifest_sha256,
        "--endpoint-attestation-sha256",
        endpoint_attestation_sha256,
        "--checkpoint-sha256",
        attestation["checkpoint_sha256"],
        "--game-database-attestation-sha256",
        game_database_attestation["attestation_sha256"],
        "--tokenizer-sha256",
        attestation["tokenizer_sha256"],
        "--render-contract-sha256",
        attestation["render_contract_sha256"],
        "--output-dir",
        str(cell_root / "eval"),
        "--server-port",
        str(9901 + 2 * cell["schedule_index"]),
        "--username",
        _username(cell),
        "--prompt-agent-name",
        protocol["prompt_agent_name"],
        "--project-dir",
        str(REPO),
        "--sandbox",
        str(cell_root / "sandbox"),
        "--model-api-name",
        manifest["models"][cell["snapshot"]]["api_model"],
        "--personality",
        protocol["personality"],
        "--inference-seed",
        str(cell["inference_seed"]),
        "--factorial-schedule-algorithm",
        protocol["schedule_algorithm"],
        "--factorial-schedule-seed",
        str(protocol["schedule_seed"]),
        "--factorial-schedule-index",
        str(cell["schedule_index"]),
        "--factorial-batch-index",
        str(cell["replicate"] - 1),
        "--factorial-cluster-id",
        f"pilot-rep{cell['replicate']:02d}",
        "--factorial-pair-id",
        (
            f"pilot-rep{cell['replicate']:02d}-"
            f"{WEIGHT_LABEL[cell['snapshot']]}"
        ),
        "--tool-recovery-enabled",
        "on" if _cell_recovery(manifest, cell) else "off",
        "--environment-seed-mechanism",
        protocol["environment_seed_mechanism"],
        "--environment-seed",
        str(cell["environment_seed"]),
        "--environment-rng-algorithm",
        protocol["environment_rng_algorithm"],
        "--environment-game-revision",
        game_attestation["gameRevision"],
        "--environment-game-bundle-sha256",
        game_attestation["entrypointSha256"],
        "--environment-seed-reason",
        protocol["environment_seed_reason"],
        ),
    )


def build_eval_environment(
    base: dict[str, str],
    *,
    manifest: dict,
    cell: dict,
    game_dir: Path,
    node_binary: Path,
) -> dict[str, str]:
    """Pin DB/schema/recovery lanes instead of inheriting ambient test state."""
    env = clean_python_environment(base)
    env[ENDPOINT_ENV] = f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}/v1"
    env["KAETRAM_GAME_DIR"] = str(game_dir)
    env["KAETRAM_NODE_BINARY"] = str(node_binary)
    env["KAETRAM_MONGO_DB"] = manifest["protocol"]["mongo_database"]
    env["KAETRAM_TOOL_SCHEMA_SOURCE"] = manifest["protocol"]["tool_schema_source"]
    env.pop("KAETRAM_MCP_PYTHON", None)
    if _cell_recovery(manifest, cell):
        env["KAETRAM_TOOL_RECOVERY"] = "1"
    else:
        env.pop("KAETRAM_TOOL_RECOVERY", None)
    return env


def validate_effective_recovery(
    results: dict,
    results_path: Path,
    *,
    expected: bool,
    cell_id: str,
) -> bool:
    meta = results.get("meta")
    if not isinstance(meta, dict) or meta.get("tool_recovery_enabled") is not expected:
        raise PilotError(f"{cell_id}: results recovery identity mismatch")
    raw_dir = results_path.parent / "episode_001_raw"
    template_path = raw_dir / "harness_meta_template.json"
    try:
        template = json.loads(template_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError(f"{cell_id}: missing recovery harness template") from exc
    if (
        not isinstance(template, dict)
        or template.get("tool_recovery_enabled") is not expected
    ):
        raise PilotError(f"{cell_id}: harness-template recovery identity mismatch")
    session_logs = sorted(raw_dir.glob("session_*.log"))
    session_meta_paths = sorted(raw_dir.glob("session_*.meta.json"))
    expected_meta_paths = {path.with_suffix(".meta.json") for path in session_logs}
    if not session_logs or set(session_meta_paths) != expected_meta_paths:
        raise PilotError(f"{cell_id}: no retained session recovery receipts")
    for path in session_meta_paths:
        try:
            session_meta = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError(
                f"{cell_id}: invalid session recovery receipt {path.name}"
            ) from exc
        if (
            not isinstance(session_meta, dict)
            or session_meta.get("tool_recovery_enabled") is not expected
        ):
            raise PilotError(
                f"{cell_id}: session recovery identity mismatch in {path.name}"
            )
    return expected


def run_pilot(
    manifest_path: Path,
    *,
    output_root: Path,
    snapshots_root: Path,
    game_dir: Path,
    mlx_python: Path,
    node_binary: Path,
    confirmation: str,
) -> int:
    manifest, manifest_sha256 = load_manifest(manifest_path)
    if confirmation != manifest["pilot_id"]:
        raise PilotError("--confirm must exactly match pilot_id")
    source_revision = _require_clean_git(REPO, "arena")
    game_revision = _require_clean_git(game_dir, "game")
    game_attestation = _load_game_attestation(game_dir, game_revision)
    try:
        game_database_attestation = attest_game_database_configuration(
            game_dir, manifest["protocol"]["mongo_database"]
        )
    except RuntimeError as exc:
        raise PilotError(str(exc)) from exc
    if manifest["schema_version"] == RECOVERY_FACTORIAL_SCHEMA_VERSION:
        contract = manifest["artifact_contract"]
        expected_game = {
            "gameRevision": contract["game_revision"],
            "entrypointSha256": contract["game_bundle_sha256"],
        }
        if any(
            game_attestation.get(key) != value
            for key, value in expected_game.items()
        ):
            raise PilotError("live game build differs from the registration")
        resolved_prompt = resolve_system_prompt(
            str(REPO),
            _username(manifest["cells"][0]),
            manifest["protocol"]["personality"],
            include_game_knowledge=manifest["protocol"]["include_game_knowledge"],
            prompt_agent_name=manifest["protocol"]["prompt_agent_name"],
        )
        resolved_prompt_sha256 = hashlib.sha256(resolved_prompt.encode()).hexdigest()
        if resolved_prompt_sha256 != contract["system_prompt_sha256"]:
            raise PilotError("resolved system prompt differs from the registration")
    else:
        resolved_prompt_sha256 = None
    if output_root in {Path("/"), Path.home().resolve()}:
        raise PilotError("output root is too broad")
    for protected, label in (
        (REPO.resolve(), "arena repository"),
        (game_dir.resolve(), "game repository"),
        (snapshots_root.resolve(), "model snapshots"),
    ):
        try:
            output_root.relative_to(protected)
        except ValueError:
            pass
        else:
            raise PilotError(f"output root must be outside the {label}")
    if output_root.exists():
        raise PilotError("output root already exists; pilot outputs are append-forbidden")
    if not snapshots_root.is_dir():
        raise PilotError("snapshots root does not exist")
    if not mlx_python.is_file() or not node_binary.is_file():
        raise PilotError("MLX Python and Node binary must be explicit existing files")
    node_version = subprocess.run(
        [str(node_binary), "--version"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not node_version.startswith("v20."):
        raise PilotError(f"Node 20 is required; found {node_version!r}")

    (
        eval_environment_receipt,
        mlx_environment_receipt,
    ) = verify_python_environment_receipts(source_revision, mlx_python)
    playwright_runtime_receipt = attest_playwright_runtime()
    mongodb_runtime_receipt = attest_mongodb_runtime(
        manifest["protocol"]["mongo_database"]
    )

    output_root.mkdir(parents=True)
    endpoint_receipts = _preflight_endpoints(
        manifest,
        mlx_python,
        snapshots_root,
        output_root,
        str(mlx_environment_receipt["receipt_sha256"]),
    )
    refreshed_eval, refreshed_mlx = verify_python_environment_receipts(
        source_revision, mlx_python
    )
    refreshed_playwright = attest_playwright_runtime()
    refreshed_mongodb = attest_mongodb_runtime(
        manifest["protocol"]["mongo_database"]
    )
    if (
        refreshed_eval != eval_environment_receipt
        or refreshed_mlx != mlx_environment_receipt
        or refreshed_playwright != playwright_runtime_receipt
        or refreshed_mongodb != mongodb_runtime_receipt
    ):
        raise PilotError("local runtime identity drifted during model preflight")
    prelaunch_schema, inventory_schema = _ledger_schema_versions(manifest)
    prelaunch = {
        "schema_version": prelaunch_schema,
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "source_git_commit": source_revision,
        "game_git_commit": game_revision,
        "game_build_attestation": game_attestation,
        "game_database_attestation": game_database_attestation,
        "resolved_system_prompt_sha256": resolved_prompt_sha256,
        "endpoint_receipts": endpoint_receipts,
        "cells": manifest["cells"],
        "runtime": {
            "eval_python": sys.executable,
            "mlx_python": str(mlx_python),
            "node_binary": str(node_binary),
            "node_version": node_version,
            "eval_environment": eval_environment_receipt,
            "mlx_environment": mlx_environment_receipt,
            "playwright": playwright_runtime_receipt,
            "mongodb": mongodb_runtime_receipt,
        },
    }
    _write_json(output_root / "prelaunch.json", prelaunch)

    inventory: list[dict] = []
    for cell in manifest["cells"]:
        cell_root = output_root / cell["cell_id"]
        cell_root.mkdir()
        process = None
        status = "invalid"
        returncode = None
        error = ""
        effective_recovery = None
        try:
            current_eval, current_mlx = verify_python_environment_receipts(
                source_revision, mlx_python
            )
            if (
                current_eval != eval_environment_receipt
                or current_mlx != mlx_environment_receipt
                or attest_playwright_runtime() != playwright_runtime_receipt
                or attest_mongodb_runtime(
                    manifest["protocol"]["mongo_database"]
                ) != mongodb_runtime_receipt
            ):
                raise PilotError("local runtime identity drifted after prelaunch")
            process, health = _start_endpoint(
                snapshot=cell["snapshot"],
                api_model=manifest["models"][cell["snapshot"]]["api_model"],
                mlx_python=mlx_python,
                snapshots_root=snapshots_root,
                log_path=cell_root / "endpoint.log",
            )
            if health != endpoint_receipts[cell["snapshot"]]:
                raise PilotError("live endpoint identity drifted after preflight")
            endpoint_sha = _write_json(
                cell_root / "endpoint-attestation.json", health
            )
            command = build_eval_command(
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                cell=cell,
                cell_root=cell_root,
                endpoint_attestation_sha256=endpoint_sha,
                endpoint_attestation=health,
                game_attestation=game_attestation,
                game_database_attestation=game_database_attestation,
            )
            env = build_eval_environment(
                dict(os.environ),
                manifest=manifest,
                cell=cell,
                game_dir=game_dir,
                node_binary=node_binary,
            )
            with (cell_root / "eval.log").open("w") as log:
                completed = subprocess.run(
                    command,
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            returncode = completed.returncode
            results_path = (
                cell_root / "eval" / cell["cell_id"] / "results.json"
            )
            if returncode != 0:
                error = f"eval_harness exited {returncode}"
            elif not results_path.is_file():
                error = "eval_harness produced no results.json"
            else:
                results = json.loads(results_path.read_text())
                episodes = results.get("episodes")
                if not isinstance(episodes, list) or len(episodes) != 1:
                    error = "results do not contain exactly one episode"
                elif episodes[0].get("status") != "ok":
                    error = f"episode status is {episodes[0].get('status')!r}"
                else:
                    effective_recovery = validate_effective_recovery(
                        results,
                        results_path,
                        expected=_cell_recovery(manifest, cell),
                        cell_id=cell["cell_id"],
                    )
                    status = "valid"
        except Exception as exc:  # preserve the failed cell and continue
            error = f"{type(exc).__name__}: {exc}"
        finally:
            _stop_process(process)
        receipt = {
            "cell_id": cell["cell_id"],
            "snapshot": cell["snapshot"],
            "recovery_assignment": _cell_recovery(manifest, cell),
            "tool_recovery_enabled": effective_recovery,
            "schedule_index": cell["schedule_index"],
            "status": status,
            "returncode": returncode,
            "error": error,
        }
        _write_json(cell_root / "cell-status.json", receipt)
        try:
            artifact_inventory_sha256 = _write_json(
                cell_root / "artifact-inventory.json",
                build_artifact_inventory(cell_root),
            )
        except PilotError as exc:
            receipt["status"] = "invalid"
            receipt["error"] = (
                f"{receipt['error']}; {exc}" if receipt["error"] else str(exc)
            )
            artifact_inventory_sha256 = ""
            _write_json(cell_root / "cell-status.json", receipt)
        receipt["artifact_inventory_sha256"] = artifact_inventory_sha256
        inventory.append(receipt)

    completed = {
        "schema_version": inventory_schema,
        "pilot_id": manifest["pilot_id"],
        "manifest_sha256": manifest_sha256,
        "valid_cells": sum(item["status"] == "valid" for item in inventory),
        "invalid_cells": sum(item["status"] != "valid" for item in inventory),
        "cells": inventory,
        "claim_boundary": manifest["claim_boundary"],
    }
    _write_json(output_root / "completed-inventory.json", completed)
    return 0 if completed["invalid_cells"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=REPO / "research/experiments/local-weight-pilot.json",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--snapshots-root", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--mlx-python", type=Path)
    parser.add_argument("--node-binary", type=Path)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    try:
        manifest, manifest_sha256 = load_manifest(args.manifest)
        if not args.launch:
            print(json.dumps({
                "mode": "dry_run",
                "pilot_id": manifest["pilot_id"],
                "manifest_sha256": manifest_sha256,
                "cell_count": len(manifest["cells"]),
                "duration_seconds_per_cell": manifest["protocol"]["duration_seconds"],
                "nominal_runtime_seconds": (
                    len(manifest["cells"])
                    * manifest["protocol"]["duration_seconds"]
                ),
                "confirmatory": False,
                "nothing_launched": True,
            }, indent=2, sort_keys=True))
            return 0
        require_isolated_execution()
        required = {
            "--output-root": args.output_root,
            "--snapshots-root": args.snapshots_root,
            "--game-dir": args.game_dir,
            "--mlx-python": args.mlx_python,
            "--node-binary": args.node_binary,
        }
        missing = [flag for flag, value in required.items() if value is None]
        if missing:
            raise PilotError("launch requires " + ", ".join(missing))
        return run_pilot(
            args.manifest.resolve(),
            output_root=args.output_root.resolve(),
            snapshots_root=args.snapshots_root.resolve(),
            game_dir=args.game_dir.resolve(),
            mlx_python=preserve_invoked_path(args.mlx_python),
            node_binary=preserve_invoked_path(args.node_binary),
            confirmation=args.confirm,
        )
    except (PilotError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
