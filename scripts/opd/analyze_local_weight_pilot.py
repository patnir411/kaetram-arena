#!/usr/bin/env python3
"""Verify and descriptively summarize the local matched-weights pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from canonical_start import CANONICAL_INITIAL_STATE  # noqa: E402
from eval_harness import (  # noqa: E402
    compute_episode_metrics,
    parse_log,
    validate_eval_session_terminals,
)
from run_manifest import sha256_json  # noqa: E402
from scripts.opd.canonicalize import is_malformed, recover_tool_calls  # noqa: E402
from scripts.opd.local_weight_pilot import (  # noqa: E402
    INTERMEDIATE_PILOT_PRELAUNCH_SCHEMA_VERSION,
    LEGACY_PILOT_PRELAUNCH_SCHEMA_VERSION,
    MONGO_IMAGE_ID,
    MONGO_IMAGE_REPO_DIGEST,
    PILOT_PRELAUNCH_SCHEMA_VERSION,
    load_manifest,
)
from tool_surface import (  # noqa: E402
    MODEL_VISIBLE_TOOL_DEFINITIONS,
    MODEL_VISIBLE_TOOL_NAMES,
)


class AnalysisError(RuntimeError):
    """Raised when retained pilot evidence does not match its sealed ledger."""


WEIGHT_LABEL = {
    "base_2b": "base",
    "opd_r2_2b": "r2",
    "opd_r3_2b": "r3",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TOOL_SCHEMAS = {
    item["function"]["name"]: item["function"]["parameters"]
    for item in MODEL_VISIBLE_TOOL_DEFINITIONS
}
JSON_TYPES = {
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "string": str,
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"artifact is not a JSON object: {path}")
    return value


def _validate_self_hashed_receipt(
    receipt: object, schema_version: str, *, label: str
) -> dict:
    if not isinstance(receipt, dict):
        raise AnalysisError(f"invalid {label} receipt")
    unsigned = dict(receipt)
    digest = unsigned.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != schema_version
        or not isinstance(digest, str)
        or not SHA256_RE.fullmatch(digest)
        or digest != sha256_json(unsigned)
    ):
        raise AnalysisError(f"invalid {label} receipt")
    return receipt


def _validate_python_environment_receipt(
    receipt: object,
    *,
    kind: str,
    marker_schema: str,
    source_git_commit: str,
) -> dict:
    value = _validate_self_hashed_receipt(
        receipt,
        "kaetram.pinned-python-environment-receipt.v1",
        label=f"{kind} Python environment",
    )
    marker = value.get("marker")
    if not isinstance(marker, dict):
        raise AnalysisError(f"invalid {kind} Python environment marker")
    expected_marker_fields = {
        "schema_version",
        "git_commit",
        "lock_sha256",
        "python_version",
        "python_executable_sha256",
        "pip_version",
        "installed_distribution_count",
        "installed_file_count",
        "installed_tree_sha256",
        "runtime_search_path_count",
        "runtime_tree_sha256",
    }
    if kind == "local_mlx":
        expected_marker_fields |= {"sys_platform", "machine"}
    if (
        value.get("environment_kind") != kind
        or set(value)
        != {
            "schema_version",
            "environment_kind",
            "marker_sha256",
            "marker",
            "receipt_sha256",
        }
        or set(marker) != expected_marker_fields
        or marker.get("schema_version") != marker_schema
        or marker.get("git_commit") != source_git_commit
        or marker.get("pip_version") != "26.1.2"
        or not isinstance(marker.get("python_version"), str)
        or not marker["python_version"].startswith("3.12.")
        or not isinstance(marker.get("installed_distribution_count"), int)
        or marker["installed_distribution_count"] < 1
        or not isinstance(marker.get("installed_file_count"), int)
        or marker["installed_file_count"] < 1
        or not isinstance(marker.get("runtime_search_path_count"), int)
        or marker["runtime_search_path_count"] < 1
        or any(
            not isinstance(marker.get(field), str)
            or not SHA256_RE.fullmatch(marker[field])
            for field in (
                "lock_sha256",
                "python_executable_sha256",
                "installed_tree_sha256",
                "runtime_tree_sha256",
            )
        )
        or value.get("marker_sha256") != sha256_json(marker)
        or (
            kind == "local_mlx"
            and (
                marker.get("sys_platform") != "darwin"
                or marker.get("machine") != "arm64"
            )
        )
    ):
        raise AnalysisError(f"invalid {kind} Python environment marker")
    return value


def _validate_runtime_receipts(prelaunch: dict, manifest: dict) -> dict:
    runtime = prelaunch.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "eval_python",
        "mlx_python",
        "node_binary",
        "node_version",
        "eval_environment",
        "mlx_environment",
        "playwright",
        "mongodb",
    }:
        raise AnalysisError("invalid prelaunch runtime receipt set")
    if (
        not all(
            isinstance(runtime.get(field), str) and runtime[field]
            for field in ("eval_python", "mlx_python", "node_binary")
        )
        or not isinstance(runtime.get("node_version"), str)
        or not runtime["node_version"].startswith("v20.")
    ):
        raise AnalysisError("invalid prelaunch executable identity")
    eval_environment = _validate_python_environment_receipt(
        runtime["eval_environment"],
        kind="local_eval",
        marker_schema="kaetram.local-unit-tests.v3",
        source_git_commit=prelaunch["source_git_commit"],
    )
    mlx_environment = _validate_python_environment_receipt(
        runtime["mlx_environment"],
        kind="local_mlx",
        marker_schema="kaetram.local-mlx-environment.v3",
        source_git_commit=prelaunch["source_git_commit"],
    )
    playwright = _validate_self_hashed_receipt(
        runtime["playwright"],
        "kaetram.playwright-runtime-receipt.v1",
        label="Playwright",
    )
    if (
        set(playwright)
        != {
            "schema_version",
            "browser_name",
            "browser_version",
            "executable_sha256",
            "receipt_sha256",
        }
        or playwright.get("browser_name") != "chromium"
        or not isinstance(playwright.get("browser_version"), str)
        or not playwright["browser_version"]
        or not isinstance(playwright.get("executable_sha256"), str)
        or not SHA256_RE.fullmatch(playwright["executable_sha256"])
    ):
        raise AnalysisError("invalid Playwright receipt")
    mongodb = _validate_self_hashed_receipt(
        runtime["mongodb"],
        "kaetram.mongodb-runtime-receipt.v1",
        label="MongoDB",
    )
    if (
        set(mongodb)
        != {
            "schema_version",
            "container_name",
            "database",
            "host",
            "port",
            "image_id",
            "image_repo_digest",
            "docker_client_version",
            "receipt_sha256",
        }
        or mongodb.get("container_name") != "kaetram-mongo"
        or mongodb.get("database") != manifest["protocol"]["mongo_database"]
        or mongodb.get("host") != "127.0.0.1"
        or mongodb.get("port") != 27017
        or mongodb.get("image_id") != MONGO_IMAGE_ID
        or mongodb.get("image_repo_digest") != MONGO_IMAGE_REPO_DIGEST
        or not isinstance(mongodb.get("docker_client_version"), str)
        or not mongodb["docker_client_version"]
    ):
        raise AnalysisError("invalid MongoDB receipt")
    return {
        "eval_environment": eval_environment,
        "mlx_environment": mlx_environment,
        "playwright": playwright,
        "mongodb": mongodb,
    }


def _verify_artifacts(cell_root: Path, expected_inventory_sha256: str) -> int:
    inventory_path = cell_root / "artifact-inventory.json"
    if _file_sha256(inventory_path) != expected_inventory_sha256:
        raise AnalysisError(f"{cell_root.name}: artifact inventory digest mismatch")
    inventory = _load_json(inventory_path)
    records = inventory.get("files")
    if (
        inventory.get("schema_version")
        != "kaetram.local-weight-pilot-artifacts.v1"
        or not isinstance(records, list)
        or inventory.get("file_count") != len(records)
    ):
        raise AnalysisError(f"{cell_root.name}: malformed artifact inventory")
    if sha256_json(records) != inventory.get("tree_sha256"):
        raise AnalysisError(f"{cell_root.name}: artifact tree digest mismatch")
    retained_paths = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise AnalysisError(f"{cell_root.name}: malformed artifact record")
        relative = PurePosixPath(record["path"])
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or "." in relative.parts
            or record["path"] in retained_paths
        ):
            raise AnalysisError(f"{cell_root.name}: unsafe or duplicate artifact path")
        retained_paths.add(record["path"])
        path = cell_root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise AnalysisError(f"{cell_root.name}: missing or symlinked {record['path']}")
        if path.stat().st_size != record["size_bytes"]:
            raise AnalysisError(f"{cell_root.name}: size drift in {record['path']}")
        if _file_sha256(path) != record["sha256"]:
            raise AnalysisError(f"{cell_root.name}: digest drift in {record['path']}")
    actual_paths = {
        path.relative_to(cell_root).as_posix()
        for path in cell_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != retained_paths | {"artifact-inventory.json"}:
        raise AnalysisError(f"{cell_root.name}: retained file set differs from inventory")
    return len(records)


def _validate_prelaunch(
    manifest: dict,
    prelaunch: dict,
    *,
    expected_schema: str = PILOT_PRELAUNCH_SCHEMA_VERSION,
    intermediate_schema: str = INTERMEDIATE_PILOT_PRELAUNCH_SCHEMA_VERSION,
    legacy_schema: str = LEGACY_PILOT_PRELAUNCH_SCHEMA_VERSION,
    allow_legacy_v1: bool = False,
) -> dict:
    expected_cells = manifest["cells"]
    accepted_schemas = {expected_schema}
    if allow_legacy_v1:
        accepted_schemas.update({intermediate_schema, legacy_schema})
    if (
        prelaunch.get("schema_version") not in accepted_schemas
        or prelaunch.get("pilot_id") != manifest["pilot_id"]
        or prelaunch.get("claim_boundary") != manifest["claim_boundary"]
        or prelaunch.get("cells") != expected_cells
    ):
        raise AnalysisError("prelaunch contract differs from the preregistration")
    receipts = prelaunch.get("endpoint_receipts")
    if not isinstance(receipts, dict) or set(receipts) != set(manifest["models"]):
        raise AnalysisError("prelaunch endpoint set differs from the preregistration")
    tokenizers = set()
    renders = set()
    chat_templates = set()
    checkpoints = {}
    snapshot_trees = {}
    snapshot_locks = set()
    endpoint_runtime_receipts = set()
    require_runtime_identity = prelaunch["schema_version"] == expected_schema
    require_extended_identity = prelaunch["schema_version"] in {
        expected_schema,
        intermediate_schema,
    }
    extended_snapshot_identity = require_extended_identity or any(
        isinstance(receipt, dict)
        and isinstance(receipt.get("attestation"), dict)
        and "snapshot_tree_sha256" in receipt["attestation"]
        for receipt in receipts.values()
    )
    for snapshot, model in manifest["models"].items():
        receipt = receipts[snapshot]
        attestation = receipt.get("attestation") if isinstance(receipt, dict) else None
        if receipt.get("status") != "ok" or not isinstance(attestation, dict):
            raise AnalysisError(f"{snapshot}: invalid prelaunch endpoint receipt")
        expected = {
            "api_model": model["api_model"],
            "fix_mistral_regex": False,
        }
        if any(attestation.get(key) != value for key, value in expected.items()):
            raise AnalysisError(f"{snapshot}: prelaunch endpoint identity mismatch")
        for field in (
            "checkpoint_sha256",
            "tokenizer_sha256",
            "render_contract_sha256",
            "chat_template_sha256",
        ):
            value = attestation.get(field)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                raise AnalysisError(f"{snapshot}: invalid {field}")
        tokenizers.add(attestation["tokenizer_sha256"])
        renders.add(attestation["render_contract_sha256"])
        chat_templates.add(attestation["chat_template_sha256"])
        checkpoints[snapshot] = attestation["checkpoint_sha256"]
        if extended_snapshot_identity:
            for field in ("snapshot_tree_sha256", "snapshot_lock_sha256"):
                value = attestation.get(field)
                if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                    raise AnalysisError(f"{snapshot}: invalid {field}")
            snapshot_trees[snapshot] = attestation["snapshot_tree_sha256"]
            snapshot_locks.add(attestation["snapshot_lock_sha256"])
        if require_runtime_identity:
            runtime_receipt = attestation.get(
                "runtime_environment_receipt_sha256"
            )
            if (
                not isinstance(runtime_receipt, str)
                or not SHA256_RE.fullmatch(runtime_receipt)
            ):
                raise AnalysisError(
                    f"{snapshot}: invalid runtime_environment_receipt_sha256"
                )
            endpoint_runtime_receipts.add(runtime_receipt)
    if len(tokenizers) != 1 or len(renders) != 1 or len(chat_templates) != 1:
        raise AnalysisError("prelaunch endpoints do not share one renderer")
    if extended_snapshot_identity and len(snapshot_locks) != 1:
        raise AnalysisError("prelaunch endpoints do not share one snapshot lock")
    game = prelaunch.get("game_build_attestation")
    if (
        not isinstance(game, dict)
        or game.get("schema") != "kaetram-server-build-attestation/v1"
        or game.get("gameRevision") != prelaunch.get("game_git_commit")
        or not isinstance(game.get("entrypointSha256"), str)
        or not SHA256_RE.fullmatch(game["entrypointSha256"])
    ):
        raise AnalysisError("invalid prelaunch game-build attestation")
    if not isinstance(prelaunch.get("source_git_commit"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", prelaunch["source_git_commit"]
    ):
        raise AnalysisError("invalid prelaunch source commit")
    database = prelaunch.get("game_database_attestation")
    if require_extended_identity and database is None:
        raise AnalysisError("attested prelaunch lacks a game-database attestation")
    if database is not None:
        unsigned_database = dict(database) if isinstance(database, dict) else {}
        database_sha = unsigned_database.pop("attestation_sha256", None)
        config_files = (
            database.get("config_files") if isinstance(database, dict) else None
        )
        node_env = database.get("node_env") if isinstance(database, dict) else None
        config_paths = (
            [
                item.get("path") if isinstance(item, dict) else None
                for item in config_files
            ]
            if isinstance(config_files, list)
            else []
        )
        valid_config_paths = (
            len(config_paths) in {2, 3}
            and config_paths[:2] == [".env.defaults", ".env"]
            and (
                len(config_paths) == 2
                or (bool(node_env) and config_paths[2] == f".env.{node_env}")
            )
        )
        if (
            not isinstance(database, dict)
            or set(database)
            != {
                "schema",
                "expected_database",
                "effective_database",
                "effective_backend",
                "skip_database",
                "effective_host",
                "effective_port",
                "tls",
                "srv",
                "authentication_enabled",
                "node_env",
                "config_files",
                "attestation_sha256",
            }
            or database.get("schema") != "kaetram-game-database-attestation/v2"
            or database.get("expected_database")
            != manifest["protocol"]["mongo_database"]
            or database.get("effective_database")
            != manifest["protocol"]["mongo_database"]
            or database.get("effective_backend") != "mongodb"
            or database.get("skip_database") is not False
            or database.get("effective_host") != "127.0.0.1"
            or database.get("effective_port") != 27017
            or database.get("tls") is not False
            or database.get("srv") is not False
            or database.get("authentication_enabled") is not False
            or not isinstance(node_env, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]*", node_env)
            or not isinstance(config_files, list)
            or not valid_config_paths
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or not isinstance(item.get("sha256"), str)
                or not SHA256_RE.fullmatch(item["sha256"])
                for item in config_files
            )
            or database_sha != sha256_json(unsigned_database)
        ):
            raise AnalysisError("invalid prelaunch game-database attestation")
    runtime_receipts = (
        _validate_runtime_receipts(prelaunch, manifest)
        if require_runtime_identity
        else None
    )
    if require_runtime_identity and endpoint_runtime_receipts != {
        runtime_receipts["mlx_environment"]["receipt_sha256"]
    }:
        raise AnalysisError("endpoint and prelaunch MLX environment receipts differ")
    return {
        "provenance_tier": (
            "prospective_v3_runtime_attested"
            if require_runtime_identity
            else (
                "prospective_v2_attested"
                if require_extended_identity
                else "legacy_v1_unattested"
            )
        ),
        "tokenizer_sha256": next(iter(tokenizers)),
        "render_contract_sha256": next(iter(renders)),
        "chat_template_sha256": next(iter(chat_templates)),
        "checkpoint_sha256": checkpoints,
        "source_git_commit": prelaunch["source_git_commit"],
        "game_revision": game["gameRevision"],
        "game_bundle_sha256": game["entrypointSha256"],
        "snapshot_tree_sha256": snapshot_trees or None,
        "snapshot_lock_sha256": (
            next(iter(snapshot_locks)) if snapshot_locks else None
        ),
        "game_database_attestation": database,
        "game_database_attestation_sha256": (
            database.get("attestation_sha256")
            if isinstance(database, dict)
            else None
        ),
        "runtime_receipts": runtime_receipts,
    }


def _validate_cell_attestation(
    cell_root: Path,
    snapshot: str,
    model: dict,
    preflight: dict,
) -> tuple[dict, str]:
    path = cell_root / "endpoint-attestation.json"
    receipt = _load_json(path)
    attestation = receipt.get("attestation")
    if receipt.get("status") != "ok" or not isinstance(attestation, dict):
        raise AnalysisError(f"{cell_root.name}: invalid endpoint attestation")
    expected = {
        "api_model": model["api_model"],
        "checkpoint_sha256": preflight["checkpoint_sha256"][snapshot],
        "tokenizer_sha256": preflight["tokenizer_sha256"],
        "render_contract_sha256": preflight["render_contract_sha256"],
        "chat_template_sha256": preflight["chat_template_sha256"],
        "fix_mistral_regex": False,
    }
    if preflight.get("snapshot_tree_sha256") is not None:
        expected.update({
            "snapshot_tree_sha256": preflight["snapshot_tree_sha256"][snapshot],
            "snapshot_lock_sha256": preflight["snapshot_lock_sha256"],
        })
    if preflight.get("runtime_receipts") is not None:
        expected["runtime_environment_receipt_sha256"] = preflight[
            "runtime_receipts"
        ]["mlx_environment"]["receipt_sha256"]
    mismatches = {
        key: {"expected": value, "actual": attestation.get(key)}
        for key, value in expected.items()
        if attestation.get(key) != value
    }
    if mismatches:
        raise AnalysisError(
            f"{cell_root.name}: endpoint attestation mismatch {mismatches}"
        )
    return attestation, _file_sha256(path)


def _api_error_count(cell_root: Path) -> int:
    stderr = cell_root / "sandbox" / "debug" / "stderr.log"
    if not stderr.is_file():
        raise AnalysisError(f"{cell_root.name}: retained stderr log is missing")
    return sum(
        "API error:" in line
        for line in stderr.read_text(errors="replace").splitlines()
    )


def _ordered_session_logs(raw_dir: Path) -> list[Path]:
    """Order by the warm-session counter, including session 10 and above."""
    numbered = []
    seen = set()
    for path in raw_dir.glob("session_*.log"):
        match = re.match(r"session_(\d+)_", path.name)
        if match is None:
            raise AnalysisError(f"unrecognized session-log name: {path.name}")
        counter = int(match.group(1))
        if counter in seen:
            raise AnalysisError(f"duplicate session counter: {counter}")
        seen.add(counter)
        numbered.append((counter, path))
    numbered.sort()
    if numbered and [counter for counter, _ in numbered] != list(
        range(1, len(numbered) + 1)
    ):
        raise AnalysisError("session counters are not contiguous from one")
    return [path for _, path in numbered]


def _validate_arguments(name: str, arguments: object) -> dict:
    if not isinstance(arguments, str):
        raise AnalysisError(f"{name}: raw arguments are not a JSON string")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"{name}: raw arguments are not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AnalysisError(f"{name}: raw arguments are not a JSON object")
    schema = TOOL_SCHEMAS[name]
    properties = schema.get("properties", {})
    if any(key not in properties for key in parsed):
        raise AnalysisError(f"{name}: raw arguments contain an unknown property")
    missing = set(schema.get("required", [])) - set(parsed)
    if missing:
        raise AnalysisError(f"{name}: raw arguments omit required properties")
    for key, value in parsed.items():
        expected = JSON_TYPES.get(properties[key].get("type"))
        if expected is not None and (
            not isinstance(value, expected)
            or properties[key].get("type") == "integer"
            and isinstance(value, bool)
        ):
            raise AnalysisError(f"{name}: raw argument {key!r} has the wrong type")
    return parsed


def _validate_raw_emissions(session_logs: list[Path]) -> dict:
    generations = 0
    calls = 0
    with_calls = 0
    action_counts = {}
    malformed_emissions = 0
    recoverable_calls = 0
    recoverable_action_counts = {}
    for path in session_logs:
        for line_number, raw in enumerate(
            path.read_text(errors="replace").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AnalysisError(
                    f"{path.name}:{line_number}: malformed retained JSONL"
                ) from exc
            if event.get("type") != "raw_model_emission":
                continue
            generations += 1
            emitted = event.get("tool_calls")
            if not isinstance(emitted, list):
                raise AnalysisError(f"{path.name}: malformed raw tool-call list")
            content = event.get("content", "")
            if not isinstance(content, str):
                raise AnalysisError(f"{path.name}: malformed raw content")
            if is_malformed(content):
                malformed_emissions += 1
            candidates = recover_tool_calls(content) if not emitted else []
            for candidate in candidates:
                name = candidate.get("name")
                if name not in MODEL_VISIBLE_TOOL_NAMES:
                    raise AnalysisError(
                        f"{path.name}: noncanonical recoverable tool {name!r}"
                    )
                recoverable_calls += 1
                recoverable_action_counts[name] = (
                    recoverable_action_counts.get(name, 0) + 1
                )
            if emitted:
                with_calls += 1
            for call in emitted:
                if not isinstance(call, dict):
                    raise AnalysisError(f"{path.name}: malformed raw tool call")
                name = call.get("name")
                if name not in MODEL_VISIBLE_TOOL_NAMES:
                    raise AnalysisError(f"{path.name}: noncanonical tool {name!r}")
                _validate_arguments(name, call.get("arguments"))
                calls += 1
                action_counts[name] = action_counts.get(name, 0) + 1
    if generations == 0:
        raise AnalysisError("episode contains no raw model emissions")
    return {
        "raw_generations": generations,
        "generations_with_structured_call": with_calls,
        "generations_without_structured_call": generations - with_calls,
        "emitted_structured_calls": calls,
        "raw_action_counts": action_counts,
        "raw_malformed_emissions": malformed_emissions,
        "raw_recoverable_calls": recoverable_calls,
        "raw_recoverable_action_counts": recoverable_action_counts,
    }


def _canonical_start_ok(state: dict) -> bool:
    observed = state.get("canonical_first_observation")
    return isinstance(observed, dict) and observed == CANONICAL_INITIAL_STATE


def _validate_state_boundaries(state: dict, cell_id: str) -> tuple[dict, ...]:
    names = (
        "player_metrics_before",
        "player_metrics_after",
        "quest_achievement_before",
        "quest_achievement_after",
    )
    values = tuple(state.get(name) for name in names)
    if not all(isinstance(value, dict) for value in values):
        raise AnalysisError(f"{cell_id}: missing DB boundary snapshot")
    player_before, player_after, qa_before, qa_after = values
    for label, snapshot in (
        ("player before", player_before),
        ("player after", player_after),
    ):
        if not all(
            isinstance(snapshot.get(key), int)
            for key in ("kills_total", "xp_total", "level")
        ):
            raise AnalysisError(f"{cell_id}: malformed {label} snapshot")
    for label, snapshot in (("quest before", qa_before), ("quest after", qa_after)):
        if not isinstance(snapshot.get("quests"), dict) or not isinstance(
            snapshot.get("achievements"), dict
        ):
            raise AnalysisError(f"{cell_id}: malformed {label} snapshot")
    miners = qa_before["quests"].get("minersquest")
    if (
        not isinstance(miners, dict)
        or miners.get("finished") is not True
        or miners.get("stage") != 2
    ):
        raise AnalysisError(f"{cell_id}: pre-run quest state is not canonical")
    return values


def summarize_rows(rows: list[dict]) -> dict:
    summaries = {}
    for weight in ("base", "r2", "r3"):
        group = [row for row in rows if row["weight"] == weight]
        if len(group) != 3:
            raise AnalysisError(f"{weight}: expected three retained cells")
        summaries[weight] = {
            "n": 3,
            "valid_tools": [row["valid_tools"] for row in group],
            "mean_valid_tools": round(
                statistics.mean(row["valid_tools"] for row in group), 3
            ),
            "mean_valid_tools_per_minute": round(
                statistics.mean(row["valid_tools_per_minute"] for row in group),
                3,
            ),
            "zero_turn_cells": sum(row["turns"] == 0 for row in group),
            "mean_tool_parse_rate": round(
                statistics.mean(row["tool_parse_rate"] for row in group), 3
            ),
            "api_errors": sum(row["api_errors"] for row in group),
            "raw_generations": sum(row["raw_generations"] for row in group),
            "generations_with_structured_call": sum(
                row["generations_with_structured_call"] for row in group
            ),
            "generations_without_structured_call": sum(
                row["generations_without_structured_call"] for row in group
            ),
            "emitted_structured_calls": sum(
                row["emitted_structured_calls"] for row in group
            ),
            "mean_budget_overrun_seconds": round(
                statistics.mean(row["budget_overrun_seconds"] for row in group),
                1,
            ),
            "core3_stages_advanced": [
                row["core3_stages_advanced"] for row in group
            ],
            "quest_stages_advanced": [
                row["quest_stages_advanced"] for row in group
            ],
            "xp_db_delta": [row["xp_db_delta"] for row in group],
            "unique_positions": [row["unique_positions"] for row in group],
        }
    return summaries


def analyze(
    root: Path,
    manifest_path: Path,
    *,
    allow_legacy_v1: bool = False,
) -> dict:
    manifest, manifest_sha256 = load_manifest(manifest_path)
    prelaunch_path = root / "prelaunch.json"
    completed_path = root / "completed-inventory.json"
    prelaunch = _load_json(prelaunch_path)
    completed = _load_json(completed_path)
    if (
        prelaunch.get("manifest_sha256") != manifest_sha256
        or completed.get("manifest_sha256") != manifest_sha256
    ):
        raise AnalysisError("manifest digest differs across sealed ledgers")
    preflight = _validate_prelaunch(
        manifest,
        prelaunch,
        allow_legacy_v1=allow_legacy_v1,
    )
    if (
        completed.get("schema_version")
        != "kaetram.local-weight-pilot-inventory.v1"
        or completed.get("pilot_id") != manifest["pilot_id"]
        or completed.get("claim_boundary") != manifest["claim_boundary"]
        or completed.get("valid_cells") != 9
        or completed.get("invalid_cells") != 0
    ):
        raise AnalysisError("pilot is not a complete nine-valid-cell inventory")
    completed_cells = completed.get("cells")
    if not isinstance(completed_cells, list) or len(completed_cells) != 9:
        raise AnalysisError("completed inventory does not contain nine cells")
    completed_by_id = {
        cell["cell_id"]: cell for cell in completed_cells
    }
    if len(completed_by_id) != len(completed_cells):
        raise AnalysisError("completed inventory contains duplicate cell IDs")
    if set(completed_by_id) != {cell["cell_id"] for cell in manifest["cells"]}:
        raise AnalysisError("completed cell IDs differ from the preregistration")

    rows = []
    files_checked = 0
    for cell in manifest["cells"]:
        cell_id = cell["cell_id"]
        cell_root = root / cell_id
        retained = completed_by_id[cell_id]
        if (
            retained.get("status") != "valid"
            or retained.get("returncode") != 0
            or retained.get("snapshot") != cell["snapshot"]
            or retained.get("schedule_index") != cell["schedule_index"]
        ):
            raise AnalysisError(f"{cell_id}: cell is not valid")
        files_checked += _verify_artifacts(
            cell_root, retained["artifact_inventory_sha256"]
        )
        endpoint_attestation, endpoint_attestation_sha256 = (
            _validate_cell_attestation(
                cell_root,
                cell["snapshot"],
                manifest["models"][cell["snapshot"]],
                preflight,
            )
        )
        results_root = cell_root / "eval" / cell_id
        results = _load_json(results_root / "results.json")
        meta = results.get("meta")
        episodes = results.get("episodes")
        if not isinstance(meta, dict) or not isinstance(episodes, list) or len(episodes) != 1:
            raise AnalysisError(f"{cell_id}: malformed result shape")
        expected_meta = {
            "model": cell_id,
            "scenario": manifest["protocol"]["scenario"],
            "duration_seconds_budget": manifest["protocol"]["duration_seconds"],
            "include_game_knowledge": manifest["protocol"]["include_game_knowledge"],
            "tool_schema_source": "canonical",
            "prompt_agent_name": manifest["protocol"]["prompt_agent_name"],
            "protocol_id": manifest["pilot_id"],
            "experiment_manifest_sha256": manifest_sha256,
            "git_sha": preflight["source_git_commit"],
            "inference_seed": cell["inference_seed"],
            "environment_seed": cell["environment_seed"],
            "endpoint_attestation_sha256": endpoint_attestation_sha256,
            "checkpoint_sha256": endpoint_attestation["checkpoint_sha256"],
            "tokenizer_sha256": preflight["tokenizer_sha256"],
            "render_contract_sha256": preflight["render_contract_sha256"],
            "factorial_schedule_algorithm": manifest["protocol"][
                "schedule_algorithm"
            ],
            "factorial_schedule_seed": manifest["protocol"]["schedule_seed"],
            "factorial_schedule_index": cell["schedule_index"],
            "factorial_cluster_id": f"pilot-rep{cell['replicate']:02d}",
            "factorial_pair_id": f"pilot-rep{cell['replicate']:02d}",
            "environment_seed_mechanism": manifest["protocol"][
                "environment_seed_mechanism"
            ],
            "environment_rng_algorithm": manifest["protocol"][
                "environment_rng_algorithm"
            ],
            "environment_game_revision": preflight["game_revision"],
            "environment_game_bundle_sha256": preflight["game_bundle_sha256"],
        }
        if preflight.get("game_database_attestation") is not None:
            expected_meta.update({
                "game_database_attestation": preflight[
                    "game_database_attestation"
                ],
                "game_database_attestation_sha256": preflight[
                    "game_database_attestation_sha256"
                ],
            })
        mismatches = {
            key: {"expected": value, "actual": meta.get(key)}
            for key, value in expected_meta.items()
            if meta.get(key) != value
        }
        if mismatches:
            raise AnalysisError(f"{cell_id}: result provenance mismatch {mismatches}")
        rng = meta.get("environment_rng_attestation")
        expected_rng = {
            "schema": manifest["protocol"]["environment_seed_mechanism"],
            "algorithm": manifest["protocol"]["environment_rng_algorithm"],
            "gameRevision": preflight["game_revision"],
            "serverBundleSha256": preflight["game_bundle_sha256"],
            "drawsAtAttestation": 0,
        }
        if not isinstance(rng, dict) or any(
            rng.get(key) != value for key, value in expected_rng.items()
        ):
            raise AnalysisError(f"{cell_id}: missing environment RNG attestation")
        expected_seed_sha256 = hashlib.sha256(
            str(cell["environment_seed"]).encode()
        ).hexdigest()
        if rng.get("seedSha256") != expected_seed_sha256:
            raise AnalysisError(f"{cell_id}: invalid environment seed digest")
        episode = episodes[0]
        if episode.get("status") != "ok" or episode.get("returncode") != 0:
            raise AnalysisError(f"{cell_id}: episode terminal status is invalid")
        state = _load_json(results_root / "episode_001_state.json")
        if not _canonical_start_ok(state):
            raise AnalysisError(f"{cell_id}: canonical first observation mismatch")
        player_before, player_after, qa_before, qa_after = (
            _validate_state_boundaries(state, cell_id)
        )
        raw_dir = results_root / "episode_001_raw"
        session_logs = _ordered_session_logs(raw_dir)
        try:
            validate_eval_session_terminals(session_logs)
        except RuntimeError as exc:
            raise AnalysisError(f"{cell_id}: invalid terminal chain: {exc}") from exc
        sub_sessions = len(session_logs)
        if sub_sessions != episode.get("sub_sessions"):
            raise AnalysisError(f"{cell_id}: raw session count mismatch")
        entries = []
        for session_log in session_logs:
            entries.extend(parse_log(session_log))
        recomputed = compute_episode_metrics(
            entries,
            player_before,
            player_after,
            qa_before,
            qa_after,
        )
        metric_mismatches = {
            key: {"expected": value, "actual": episode.get(key)}
            for key, value in recomputed.items()
            if episode.get(key) != value
        }
        if metric_mismatches:
            raise AnalysisError(
                f"{cell_id}: derived episode metrics mismatch {metric_mismatches}"
            )
        raw_metrics = _validate_raw_emissions(session_logs)
        if raw_metrics["raw_action_counts"] != recomputed["action_counts"]:
            raise AnalysisError(
                f"{cell_id}: raw structured actions differ from canonical log"
            )
        api_errors = _api_error_count(cell_root)
        duration = float(episode["duration_seconds"])
        if duration < manifest["protocol"]["duration_seconds"]:
            raise AnalysisError(f"{cell_id}: episode ended before its fixed budget")
        valid_tools = int(episode["tool_calls_valid"])
        rows.append({
            "cell_id": cell_id,
            "replicate": cell["replicate"],
            "weight": WEIGHT_LABEL[cell["snapshot"]],
            "duration_seconds": duration,
            "budget_overrun_seconds": round(duration - 300, 1),
            "turns": int(episode["turns_played"]),
            "valid_tools": valid_tools,
            "valid_tools_per_minute": round(valid_tools / (duration / 60), 3),
            "tool_parse_rate": float(episode["tool_parse_rate"]),
            "api_errors": api_errors,
            "sub_sessions": sub_sessions,
            "core3_stages_advanced": int(episode["core3_stages_advanced"]),
            "quest_stages_advanced": int(episode["quest_stages_advanced"]),
            "xp_db_delta": int(episode["xp_db_delta"]),
            "unique_positions": int(episode["unique_positions"]),
            "action_counts": episode["action_counts"],
            **raw_metrics,
        })

    index_record = {
        "prelaunch_sha256": _file_sha256(prelaunch_path),
        "completed_inventory_sha256": _file_sha256(completed_path),
        "cell_artifact_inventory_sha256": {
            cell_id: completed_by_id[cell_id]["artifact_inventory_sha256"]
            for cell_id in sorted(completed_by_id)
        },
    }
    return {
        "schema_version": "kaetram.local-weight-pilot-analysis.v1",
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_sha256": manifest_sha256,
        "provenance_tier": preflight["provenance_tier"],
        "bundle_index": index_record,
        "bundle_index_sha256": sha256_json(index_record),
        "valid_cells": 9,
        "invalid_cells": 0,
        "files_rehashed": files_checked,
        "rows": rows,
        "by_weight": summarize_rows(rows),
        "overall": {
            "zero_turn_cells": sum(row["turns"] == 0 for row in rows),
            "api_errors": sum(row["api_errors"] for row in rows),
            "raw_generations": sum(row["raw_generations"] for row in rows),
            "generations_with_structured_call": sum(
                row["generations_with_structured_call"] for row in rows
            ),
            "generations_without_structured_call": sum(
                row["generations_without_structured_call"] for row in rows
            ),
            "emitted_structured_calls": sum(
                row["emitted_structured_calls"] for row in rows
            ),
            "all_emitted_structured_calls_valid": True,
            "cells_with_core3_progress": sum(
                row["core3_stages_advanced"] > 0 for row in rows
            ),
            "cells_with_any_quest_progress": sum(
                row["quest_stages_advanced"] > 0 for row in rows
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "research/experiments/local-weight-pilot.json",
    )
    parser.add_argument(
        "--expected-bundle-index-sha256",
        help="Fail if the recomputed sealed-ledger root differs from this digest.",
    )
    parser.add_argument(
        "--allow-legacy-v1",
        action="store_true",
        help=(
            "Explicitly analyze a pre-v2 bundle as legacy_v1_unattested; "
            "never use this for a new launch."
        ),
    )
    args = parser.parse_args(argv)
    try:
        report = analyze(
            args.root.resolve(),
            args.manifest.resolve(),
            allow_legacy_v1=args.allow_legacy_v1,
        )
        if (
            args.expected_bundle_index_sha256 is not None
            and report["bundle_index_sha256"]
            != args.expected_bundle_index_sha256
        ):
            raise AnalysisError("bundle-index digest differs from the expected root")
    except (AnalysisError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
