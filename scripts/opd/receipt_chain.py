"""Recursive validation for OPD builder and transformation receipts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath

try:
    from .opd_data_manifest import BUILDER_RELATIVE_PATH, MANIFEST_SCHEMA_VERSION
    from .record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
    )
except ImportError:  # direct `python scripts/opd/...` execution
    from opd_data_manifest import (  # type: ignore[no-redef]
        BUILDER_RELATIVE_PATH,
        MANIFEST_SCHEMA_VERSION,
    )
    from record_schema import (  # type: ignore[no-redef]
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
    )


UNIFORM_MANIFEST_SCHEMA_VERSION = "uniform-advantages-manifest-v3"
RESAMPLE_MANIFEST_SCHEMA_VERSION = "resampled-records-manifest-v3"
SUPPORTED_MANIFESTS = {
    MANIFEST_SCHEMA_VERSION,
    UNIFORM_MANIFEST_SCHEMA_VERSION,
    RESAMPLE_MANIFEST_SCHEMA_VERSION,
}
BUILD_SOURCE_PATHS = (
    "bootstrap.py",
    "canonical_start.py",
    "eval_harness.py",
    "inference_seed.py",
    "finetune/render.py",
    "scripts/opd/canonicalize.py",
    "heldout_guard.py",
    "port_probe.py",
    "run_manifest.py",
    "scripts/isolated_python_entry.py",
    "tool_surface.py",
    "scripts/opd/opd_2b_data.py",
    "scripts/opd/opd_data_manifest.py",
    "scripts/opd/opd_probe.py",
    "scripts/opd/opd_round1.py",
    "scripts/opd/opd_wall_probe.py",
    "scripts/opd/record_schema.py",
    "scripts/opd/receipt_chain.py",
    "scripts/log_analysis/parse.py",
    "prompts/game_knowledge.md",
    "prompts/personalities/completionist.md",
    "prompts/personalities/explorer_tinkerer.md",
    "prompts/personalities/grinder.md",
    "prompts/system.md",
    "research/experiments/heldout-quest-v2.json",
    "research/experiments/heldout-quest.json",
)
SCRIPT_PATHS = {
    MANIFEST_SCHEMA_VERSION: BUILDER_RELATIVE_PATH,
    UNIFORM_MANIFEST_SCHEMA_VERSION: "scripts/opd/make_uniform_advantages.py",
    RESAMPLE_MANIFEST_SCHEMA_VERSION: "scripts/opd/resample_records.py",
}


class ReceiptChainError(ValueError):
    """Raised when a provenance receipt or its parent chain is invalid."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptChainError(message)


def _source_path(repo_root: Path, relative: str) -> Path:
    if relative == "finetune/render.py":
        try:
            import render

            return Path(render.__file__).resolve()
        except ImportError:
            pass
    return repo_root / relative


def _verify_attestation(value: object, label: str) -> None:
    _require(isinstance(value, dict), f"{label} endpoint attestation is missing")
    _require(
        set(value) == {
            "deployment_id",
            "api_model",
            "checkpoint_sha256",
            "tokenizer_sha256",
            "render_contract_sha256",
        },
        f"{label} endpoint attestation fields are invalid",
    )
    _require(
        all(
            isinstance(value[field], str)
            and bool(value[field])
            and "://" not in value[field]
            for field in ("deployment_id", "api_model")
        )
        and all(
            is_digest(value[field])
            for field in (
                "checkpoint_sha256",
                "tokenizer_sha256",
                "render_contract_sha256",
            )
        ),
        f"{label} endpoint attestation values are invalid",
    )


def receipt_chain_contains(receipt: object, schema_version: str) -> bool:
    """Return whether an embedded ancestry contains ``schema_version``."""
    current = receipt
    depth = 0
    while isinstance(current, dict):
        if current.get("schema_version") == schema_version:
            return True
        current = current.get("parent_manifest")
        depth += 1
        _require(depth <= 32, "receipt chain is unreasonably deep")
    return False


def _verify_root(receipt: dict, repo_root: Path) -> None:
    _require(
        receipt.get("builder") == BUILDER_RELATIVE_PATH,
        "builder receipt has the wrong builder",
    )
    inventory = receipt.get("source_logs")
    _require(isinstance(inventory, list) and bool(inventory), "source_logs are empty")
    paths: list[str] = []
    meta_paths: list[str] = []
    run_ids: set[str] = set()
    for item in inventory:
        _require(isinstance(item, dict), "source log entry is not an object")
        path = item.get("path")
        run_id = item.get("run_id")
        _require(
            isinstance(path, str)
            and bool(path)
            and not PurePosixPath(path).is_absolute()
            and ".." not in PurePosixPath(path).parts
            and isinstance(run_id, str)
            and bool(run_id)
            and is_digest(item.get("sha256"))
            and isinstance(item.get("size_bytes"), int)
            and not isinstance(item.get("size_bytes"), bool)
            and item["size_bytes"] >= 0,
            "source log entry is invalid",
        )
        _require(
            isinstance(item.get("meta_path"), str)
            and bool(item["meta_path"])
            and PurePosixPath(item["meta_path"])
            == PurePosixPath(path).with_suffix(".meta.json")
            and not PurePosixPath(item["meta_path"]).is_absolute()
            and ".." not in PurePosixPath(item["meta_path"]).parts
            and is_digest(item.get("meta_sha256"))
            and isinstance(item.get("meta_size_bytes"), int)
            and not isinstance(item.get("meta_size_bytes"), bool)
            and item["meta_size_bytes"] >= 0,
            "source meta entry is invalid",
        )
        _require(
            isinstance(item.get("personality_prompt_path"), str)
            and item["personality_prompt_path"] in BUILD_SOURCE_PATHS
            and PurePosixPath(item["personality_prompt_path"]).parent
            == PurePosixPath("prompts/personalities"),
            "source personality prompt is not bound",
        )
        paths.append(path)
        meta_paths.append(item["meta_path"])
        run_ids.add(run_id)
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), "source logs not unique/sorted")
    _require(
        len(meta_paths) == len(set(meta_paths)),
        "source metadata paths are not unique",
    )
    declared_runs = receipt.get("source_runs")
    _require(
        isinstance(declared_runs, list)
        and bool(declared_runs)
        and len(declared_runs) == len(set(declared_runs))
        and set(declared_runs) == run_ids,
        "source run coverage does not match the source-log inventory",
    )
    _require(
        receipt.get("source_sha256") == canonical_sha256(inventory),
        "source-log inventory digest mismatch",
    )
    _require(
        isinstance(receipt.get("n_records"), int)
        and receipt["n_records"] > 0
        and isinstance(receipt.get("n_heldout"), int)
        and receipt["n_heldout"] >= 0
        and is_digest(receipt.get("heldout_sha256")),
        "builder record/heldout counts are invalid",
    )
    candidate_states = receipt.get("candidate_states")
    status_counts = receipt.get("status_counts")
    excluded_states = receipt.get("excluded_states")
    _require(
        isinstance(candidate_states, int)
        and not isinstance(candidate_states, bool)
        and candidate_states > 0
        and is_digest(receipt.get("candidate_states_sha256"))
        and isinstance(status_counts, dict)
        and bool(status_counts)
        and set(status_counts)
        <= {"ok", "ok_cf", "holdout", "overlong"}
        and all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            for value in status_counts.values()
        )
        and sum(status_counts.values()) == candidate_states
        and status_counts.get("ok", 0) + status_counts.get("ok_cf", 0)
        == receipt["n_records"]
        and status_counts.get("holdout", 0) == receipt["n_heldout"],
        "builder candidate/status accounting is invalid",
    )
    _require(
        isinstance(excluded_states, list)
        and len(excluded_states) == status_counts.get("overlong", 0)
        and receipt.get("excluded_states_sha256")
        == canonical_sha256(excluded_states),
        "builder exclusion accounting is invalid",
    )
    exclusion_identities: list[tuple[object, ...]] = []
    for exclusion in excluded_states:
        _require(
            isinstance(exclusion, dict)
            and set(exclusion)
            == {
                "source_run",
                "source_log",
                "session",
                "turn_idx",
                "verb",
                "frontier",
                "holdout",
                "status",
            }
            and exclusion.get("status") == "overlong"
            and all(
                isinstance(exclusion.get(field), str)
                and bool(exclusion[field])
                for field in (
                    "source_run",
                    "source_log",
                    "session",
                    "verb",
                    "frontier",
                )
            )
            and isinstance(exclusion.get("turn_idx"), int)
            and exclusion["turn_idx"] >= 0
            and isinstance(exclusion.get("holdout"), bool),
            "builder exclusion entry is invalid",
        )
        exclusion_identities.append(
            tuple(exclusion[field] for field in sorted(exclusion))
        )
    _require(
        len(exclusion_identities) == len(set(exclusion_identities)),
        "builder exclusions are not unique",
    )
    build_sources = receipt.get("build_sources")
    _require(
        isinstance(build_sources, dict)
        and set(build_sources) == set(BUILD_SOURCE_PATHS),
        "builder source inventory is incomplete",
    )
    for relative in BUILD_SOURCE_PATHS:
        path = _source_path(repo_root, relative)
        _require(
            path.is_file() and build_sources[relative] == sha256_path(path),
            f"builder source identity mismatch: {relative}",
        )
    parameters = receipt.get("parameters")
    _require(isinstance(parameters, dict), "builder parameters are missing")
    _require(
        set(parameters) == {
            "student_endpoint_attestation",
            "teacher_endpoint_attestation",
            "tokenizer_sha256",
            "tokenizer_snapshot_sha256",
            "runtime_versions",
            "max_history_messages",
            "max_sequence_tokens",
            "kl_coefficient",
            "holdout_every",
            "early_weight",
            "malformed_parameter_pattern",
            "counterfactual_grading",
            "limit",
        },
        "builder parameter fields are invalid",
    )
    _verify_attestation(parameters.get("student_endpoint_attestation"), "student")
    _verify_attestation(parameters.get("teacher_endpoint_attestation"), "teacher")
    tokenizer_sha = parameters.get("tokenizer_sha256")
    _require(
        is_digest(tokenizer_sha)
        and is_digest(parameters.get("tokenizer_snapshot_sha256"))
        and parameters["student_endpoint_attestation"]["tokenizer_sha256"] == tokenizer_sha
        and parameters["teacher_endpoint_attestation"]["tokenizer_sha256"] == tokenizer_sha
        and parameters.get("max_history_messages") == 28
        and parameters.get("max_sequence_tokens") == 16384
        and parameters.get("kl_coefficient") == 1.0
        and parameters.get("holdout_every") == 10
        and parameters.get("early_weight") == 1.5
        and parameters.get("malformed_parameter_pattern")
        == r"<parameter=[^>\n]*=[^>\n]*>"
        and parameters.get("counterfactual_grading") is True
        and parameters.get("limit") == 0,
        "builder parameter values are invalid",
    )
    runtime_versions = parameters.get("runtime_versions")
    _require(
        isinstance(runtime_versions, dict)
        and set(runtime_versions) == {"python", "httpx", "transformers", "tokenizers"}
        and all(isinstance(value, str) and bool(value) for value in runtime_versions.values()),
        "builder runtime-version receipt is invalid",
    )


def validate_receipt_chain(
    receipt: object,
    *,
    expected_output_sha256: str,
    repo_root: Path,
) -> dict:
    """Validate one receipt and recursively validate every embedded parent."""
    _require(isinstance(receipt, dict), "receipt must be a JSON object")
    schema = receipt.get("schema_version")
    _require(schema in SUPPORTED_MANIFESTS, f"unsupported records manifest: {schema!r}")
    _require(
        receipt.get("output_sha256") == expected_output_sha256
        and is_digest(expected_output_sha256),
        "receipt output SHA-256 mismatch",
    )
    _require(
        receipt.get("record_schema_version") == OPD_TRAIN_RECORD_SCHEMA_VERSION
        and receipt.get("record_schema_sha256") == OPD_TRAIN_RECORD_SCHEMA_SHA256
        and receipt.get("record_schema_validator_sha256")
        == OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "record schema/validator identity mismatch",
    )
    script_path = _source_path(repo_root, SCRIPT_PATHS[schema])
    _require(
        script_path.is_file() and receipt.get("script_sha256") == sha256_path(script_path),
        "receipt producer identity mismatch",
    )
    if schema == MANIFEST_SCHEMA_VERSION:
        _verify_root(receipt, repo_root)
    else:
        parent = receipt.get("parent_manifest")
        _require(isinstance(parent, dict), "transformation parent manifest is missing")
        _require(
            receipt.get("parent_manifest_sha256") == canonical_sha256(parent),
            "transformation parent-manifest digest mismatch",
        )
        source_sha = receipt.get("source_sha256")
        _require(is_digest(source_sha), "transformation source SHA-256 is invalid")
        validate_receipt_chain(
            parent,
            expected_output_sha256=source_sha,
            repo_root=repo_root,
        )
        if schema == UNIFORM_MANIFEST_SCHEMA_VERSION:
            _require(
                receipt.get("control") == "uniform-clipped-self-imitation"
                and isinstance(receipt.get("c"), (int, float))
                and not isinstance(receipt.get("c"), bool)
                and math.isfinite(float(receipt["c"]))
                and float(receipt["c"]) > 0
                and receipt.get("c_rule")
                == "corpus mean |advantage| over nonzero tokens"
                and isinstance(receipt.get("n_records"), int)
                and receipt["n_records"] > 0
                and isinstance(receipt.get("n_nonzero_tokens"), int)
                and receipt["n_nonzero_tokens"] > 0
                and isinstance(receipt.get("n_zero_tokens_kept"), int)
                and receipt["n_zero_tokens_kept"] >= 0,
                "uniform transformation parameters are invalid",
            )
            _require(
                not receipt_chain_contains(parent, UNIFORM_MANIFEST_SCHEMA_VERSION),
                "multiple uniform transformations are not supported",
            )
        else:
            original = receipt.get("original_records")
            resampled = receipt.get("resampled_records")
            target = receipt.get("target_records")
            _require(
                isinstance(original, int)
                and original > 0
                and isinstance(resampled, int)
                and resampled > 0
                and isinstance(target, int)
                and target == original + resampled
                and isinstance(receipt.get("seed"), int)
                and not isinstance(receipt.get("seed"), bool)
                and is_digest(receipt.get("sampled_indices_sha256"))
                and receipt.get("sampling")
                == "uniform-with-replacement-after-originals",
                "resample transformation parameters are invalid",
            )
    return receipt
