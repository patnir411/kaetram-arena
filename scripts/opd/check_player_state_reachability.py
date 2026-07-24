#!/usr/bin/env python3
"""Replay and verify persistent player-state reachability against isolated Kaetram services.

This checker never certifies reachability from an artifact's shape. Its CLI uses
the live MCP/Mongo adapter and fails unless an explicitly enabled isolated test
lane is reachable. The adapter seam exists so the execution contract can be
tested without pretending a fake service is production evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tool_surface import MODEL_VISIBLE_TOOL_NAMES  # noqa: E402
from scripts.opd.kaetram_replay_service import (  # noqa: E402
    PlayerStateStore,
    cold_mcp_session,
)


CHECKER_PROTOCOL = "kaetram-live-player-state-replay-v1"
RESULT_SCHEMA = "kaetram-player-state-reachability-check-v1"
EXECUTION_ENVIRONMENT = "live-isolated-service"
ADAPTER_ID = "kaetram-mcp-mongo-isolated-v1"
STATE_DIGEST_SCHEMA = "kaetram-mcp-observation-canonical-json-v1"
PERSISTENT_DIGEST_SCHEMA = "kaetram-seeded-player-collections-v1"
REQUIRED_INVARIANTS = (
    "canonical_start_loaded",
    "runtime_revisions_exact",
    "every_transition_exact",
    "target_persistent_player_state_exact",
)
SHA256_CHARS = frozenset("0123456789abcdef")


class CheckerError(RuntimeError):
    pass


class ReplayAdapter(Protocol):
    environment_kind: str
    adapter_id: str

    async def runtime_metadata(self) -> dict[str, str]: ...
    async def prepare(self, canonical_start: dict[str, Any], target: dict[str, Any]) -> None: ...
    async def observe(self) -> dict[str, Any]: ...
    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...
    async def finalize(self, target: dict[str, Any]) -> dict[str, Any]: ...
    async def close(self) -> None: ...


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in SHA256_CHARS for char in value):
        raise CheckerError(f"{label} must be a lowercase SHA-256 digest")
    return value


def require_git_object_id(value: Any, label: str) -> str:
    if (
        not isinstance(value, str) or len(value) not in {40, 64}
        or any(char not in SHA256_CHARS for char in value)
    ):
        raise CheckerError(f"{label} must be a full 40- or 64-character lowercase Git object ID")
    return value


def _git_revision(path: Path, label: str) -> str:
    try:
        revision = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        tracked_dirty = any(
            subprocess.run(command, check=False, capture_output=True, timeout=10).returncode != 0
            for command in (
                ["git", "-C", str(path), "diff", "--quiet", "--exit-code"],
                ["git", "-C", str(path), "diff", "--cached", "--quiet", "--exit-code"],
            )
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckerError(f"cannot attest {label} git revision: {exc}") from exc
    require_git_object_id(revision, f"{label} git revision")
    if tracked_dirty:
        raise CheckerError(f"{label} repository has tracked changes; exact revision is not attestable")
    return revision


def _persistent_projection(raw: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Project Mongo rows onto exactly the persistent fields represented by a candidate."""
    info = raw.get("player_info") or {}
    info_keys = {"x", "y", "hitPoints", "mana", *target["player_info_overrides"].keys()}

    def body(collection: str, field: str | None = None) -> Any:
        document = raw.get(collection)
        if not isinstance(document, dict):
            return None
        if field is not None:
            return document.get(field)
        return {key: value for key, value in document.items() if key != "username"}

    return {
        "player_info": {key: info.get(key) for key in sorted(info_keys)},
        "inventory": body("player_inventory", "slots"),
        "bank": body("player_bank", "slots"),
        "equipment": body("player_equipment", "equipments"),
        "quests": body("player_quests", "quests"),
        "achievements": body("player_achievements", "achievements"),
        "skills": body("player_skills", "skills"),
        "statistics": body("player_statistics"),
    }


class LiveKaetramAdapter:
    """Isolated live adapter using the repository's seeder and stdio MCP harness."""

    environment_kind = EXECUTION_ENVIRONMENT
    adapter_id = ADAPTER_ID

    def __init__(self) -> None:
        self.username = os.environ.get("KAETRAM_REACHABILITY_USERNAME", "").lower()
        self.shadow_username = f"{self.username}_target"
        self.game_repo_raw = os.environ.get("KAETRAM_GAME_REPO", "")
        self._session_context: Any = None
        self._session: Any = None
        self._prepared = False
        self._validate_isolation()
        self._store = PlayerStateStore(
            uri=os.environ["KAETRAM_MONGO_URI"], database=os.environ["KAETRAM_MONGO_DB"],
        )

    def _validate_isolation(self) -> None:
        if os.environ.get("KAETRAM_REACHABILITY_LIVE") != "1":
            raise CheckerError(
                "offline refusal: KAETRAM_REACHABILITY_LIVE=1 is required for service-backed replay"
            )
        if os.environ.get("KAETRAM_LIVE_SUITE", "").lower() in {"1", "true", "yes"}:
            raise CheckerError("warm-session mode is forbidden; replay requires isolated cold sessions")
        database = os.environ.get("KAETRAM_MONGO_DB", "").lower()
        if not database or not any(token in database for token in ("e2e", "test", "reachability")):
            raise CheckerError("KAETRAM_MONGO_DB must identify an isolated e2e/test/reachability database")
        for variable in ("KAETRAM_MONGO_URI", "KAETRAM_PORT", "KAETRAM_CLIENT_URL"):
            if not os.environ.get(variable):
                raise CheckerError(f"{variable} must be explicit for isolated replay")
        if os.environ["KAETRAM_PORT"] == "9001":
            raise CheckerError("the data-collection game port 9001 is forbidden for reachability replay")
        if not self.username.startswith("reachability_"):
            raise CheckerError("KAETRAM_REACHABILITY_USERNAME must start with 'reachability_'")
        if not self.game_repo_raw:
            raise CheckerError("KAETRAM_GAME_REPO is required for exact game revision attestation")

    async def runtime_metadata(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "harness_git_revision": _git_revision(REPO, "harness"),
            "game_git_revision": _git_revision(Path(self.game_repo_raw).resolve(), "game"),
            "state_digest_schema": STATE_DIGEST_SCHEMA,
            "persistent_digest_schema": PERSISTENT_DIGEST_SCHEMA,
        }

    async def _canonicalize_shadow(self, target: dict[str, Any]) -> None:
        self._store.seed(self.shadow_username, target)
        async with cold_mcp_session(
            username=self.shadow_username,
            client_url=os.environ["KAETRAM_CLIENT_URL"],
            server_port=os.environ["KAETRAM_PORT"],
        ):
            pass

    async def prepare(self, canonical_start: dict[str, Any], target: dict[str, Any]) -> None:
        await self._canonicalize_shadow(target)
        self._store.seed(self.username, canonical_start)
        self._session_context = cold_mcp_session(
            username=self.username,
            client_url=os.environ["KAETRAM_CLIENT_URL"],
            server_port=os.environ["KAETRAM_PORT"],
        )
        self._session = await self._session_context.__aenter__()
        self._prepared = True

    async def observe(self) -> dict[str, Any]:
        if self._session is None:
            raise CheckerError("live adapter has no active MCP session")
        result = await self._session.call_tool("observe", {})
        parser = getattr(result, "json", None)
        if not callable(parser):
            raise CheckerError("live MCP result does not expose the required json() parser")
        try:
            payload = parser()
        except Exception as exc:  # noqa: BLE001 - adapter boundary must translate and fail closed
            raise CheckerError(f"live MCP observe json() parser failed: {exc}") from exc
        if result.is_error or not isinstance(payload, dict):
            raise CheckerError(f"live observe failed: {result.text[:500]}")
        return payload

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise CheckerError("live adapter has no active MCP session")
        result = await self._session.call_tool(tool, arguments)
        parser = getattr(result, "json", None)
        if not callable(parser):
            raise CheckerError("live MCP result does not expose the required json() parser")
        try:
            payload = parser()
        except Exception as exc:  # noqa: BLE001 - adapter boundary must translate and fail closed
            raise CheckerError(f"live MCP action json() parser failed: {exc}") from exc
        canonical_result = {
            "is_error": bool(result.is_error),
            "payload": payload,
            "text_sha256": hashlib.sha256(result.text.encode()).hexdigest(),
        }
        if result.is_error or payload is None:
            raise CheckerError(f"MCP action {tool!r} failed: {result.text[:500]}")
        return canonical_result

    async def finalize(self, target: dict[str, Any]) -> dict[str, Any]:
        if self._session_context is None:
            raise CheckerError("live adapter was not prepared")
        await self._session_context.__aexit__(None, None, None)
        self._session_context = None
        self._session = None
        actual = _persistent_projection(self._store.snapshot(self.username), target)
        expected = _persistent_projection(self._store.snapshot(self.shadow_username), target)
        actual_digest = digest(actual)
        expected_digest = digest(expected)
        return {
            "schema": PERSISTENT_DIGEST_SCHEMA,
            "actual_sha256": actual_digest,
            "expected_sha256": expected_digest,
            "matches_target": actual_digest == expected_digest,
        }

    async def close(self) -> None:
        errors: list[str] = []
        if self._session_context is not None:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - continue cleanup, then fail closed
                errors.append(f"MCP session close failed: {exc}")
            finally:
                self._session_context = None
                self._session = None
        for username in (self.username, self.shadow_username):
            if not username:
                continue
            try:
                self._store.cleanup(username)
            except Exception as exc:  # noqa: BLE001 - attempt every isolated-player cleanup
                errors.append(f"cleanup for {username!r} failed: {exc}")
        self._prepared = False
        if errors:
            raise CheckerError("; ".join(errors))


def _validate_artifact(
    artifact: dict[str, Any], *, method: str, canonical_start_sha256: str,
    target_snapshot_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    if artifact.get("schema_version") != 2 or artifact.get("checker_protocol") != CHECKER_PROTOCOL:
        raise CheckerError(f"artifact must use schema 2 and checker_protocol {CHECKER_PROTOCOL!r}")
    if artifact.get("method") != method:
        raise CheckerError("artifact method does not match CLI method")
    canonical_start = artifact.get("canonical_start_snapshot")
    target = artifact.get("target_snapshot")
    if not isinstance(canonical_start, dict) or digest(canonical_start) != canonical_start_sha256:
        raise CheckerError("canonical_start_snapshot does not match its pinned digest")
    if not isinstance(target, dict) or digest(target) != target_snapshot_sha256:
        raise CheckerError("target_snapshot does not match its pinned digest")
    if artifact.get("canonical_start_sha256") != canonical_start_sha256:
        raise CheckerError("artifact canonical start digest mismatch")
    if artifact.get("target_snapshot_sha256") != target_snapshot_sha256:
        raise CheckerError("artifact target snapshot digest mismatch")
    runtime = artifact.get("runtime")
    if not isinstance(runtime, dict):
        raise CheckerError("artifact runtime attestation is required")
    required_runtime = {
        "adapter_id", "harness_git_revision", "game_git_revision",
        "state_digest_schema", "persistent_digest_schema",
    }
    if runtime.keys() != required_runtime:
        raise CheckerError(f"artifact runtime keys must be exactly {sorted(required_runtime)}")
    for key in ("harness_git_revision", "game_git_revision"):
        require_git_object_id(runtime[key], f"runtime.{key}")
    if runtime.get("adapter_id") != ADAPTER_ID:
        raise CheckerError("artifact adapter_id is not the production allowlisted adapter")
    if runtime.get("state_digest_schema") != STATE_DIGEST_SCHEMA:
        raise CheckerError("artifact state digest schema mismatch")
    if runtime.get("persistent_digest_schema") != PERSISTENT_DIGEST_SCHEMA:
        raise CheckerError("artifact persistent digest schema mismatch")
    transitions = artifact.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise CheckerError("artifact requires a nonempty executable transition trace")
    if method == "invariant_certificate":
        invariants = artifact.get("invariants")
        if not isinstance(invariants, list) or tuple(sorted(invariants)) != tuple(sorted(REQUIRED_INVARIANTS)):
            raise CheckerError(
                f"invariant_certificate must request exactly the allowlisted invariants {list(REQUIRED_INVARIANTS)}"
            )
    return canonical_start, target, transitions, runtime


async def verify_artifact(
    artifact_path: Path, *, method: str, canonical_start_sha256: str,
    target_snapshot_sha256: str, adapter: ReplayAdapter,
) -> dict[str, Any]:
    require_sha256(canonical_start_sha256, "canonical start")
    require_sha256(target_snapshot_sha256, "target snapshot")
    raw_artifact = artifact_path.read_bytes()
    try:
        artifact = json.loads(raw_artifact)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckerError(f"artifact is not valid JSON: {exc}") from exc
    if not isinstance(artifact, dict):
        raise CheckerError("artifact must be a JSON object")
    canonical_start, target, transitions, expected_runtime = _validate_artifact(
        artifact, method=method, canonical_start_sha256=canonical_start_sha256,
        target_snapshot_sha256=target_snapshot_sha256,
    )
    if adapter.environment_kind != EXECUTION_ENVIRONMENT or adapter.adapter_id != ADAPTER_ID:
        raise CheckerError("adapter is not an allowlisted live isolated-service adapter")
    observed_runtime = await adapter.runtime_metadata()
    if observed_runtime != expected_runtime:
        raise CheckerError(
            f"runtime revision divergence: expected {expected_runtime}, observed {observed_runtime}"
        )

    trace: list[dict[str, Any]] = []
    checks = {
        "runtime_revisions_exact": True,
        "canonical_start_loaded": False,
        "every_transition_exact": False,
        "target_persistent_player_state_exact": False,
    }
    try:
        await adapter.prepare(canonical_start, target)
        observation = await adapter.observe()
        observation_sha256 = digest(observation)
        if observation_sha256 != artifact.get("initial_observation_sha256"):
            raise CheckerError(
                "canonical-start observation divergence: "
                f"expected {artifact.get('initial_observation_sha256')}, observed {observation_sha256}"
            )
        checks["canonical_start_loaded"] = True
        for index, expected in enumerate(transitions):
            if not isinstance(expected, dict):
                raise CheckerError(f"transition {index} must be an object")
            expected_keys = {
                "action", "before_observation_sha256", "tool_result_sha256",
                "after_observation_sha256",
            }
            if set(expected) != expected_keys:
                raise CheckerError(f"transition {index} keys must be exactly {sorted(expected_keys)}")
            action = expected.get("action")
            if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
                raise CheckerError(f"transition {index} action must contain exactly tool and arguments")
            tool = action.get("tool")
            arguments = action.get("arguments")
            if tool not in MODEL_VISIBLE_TOOL_NAMES or tool == "observe" or not isinstance(arguments, dict):
                raise CheckerError(f"transition {index} action is not an allowlisted state-changing MCP call")
            if expected.get("before_observation_sha256") != observation_sha256:
                raise CheckerError(f"transition {index} pre-state digest diverged")
            result = await adapter.call_tool(tool, arguments)
            result_sha256 = digest(result)
            if expected.get("tool_result_sha256") != result_sha256:
                raise CheckerError(f"transition {index} tool-result digest diverged")
            after = await adapter.observe()
            after_sha256 = digest(after)
            if expected.get("after_observation_sha256") != after_sha256:
                raise CheckerError(f"transition {index} post-state digest diverged")
            trace.append({
                "index": index,
                "action": action,
                "before_observation_sha256": observation_sha256,
                "tool_result_sha256": result_sha256,
                "after_observation_sha256": after_sha256,
            })
            observation = after
            observation_sha256 = after_sha256
        checks["every_transition_exact"] = True
        persistent = await adapter.finalize(target)
        if (
            not isinstance(persistent, dict)
            or persistent.get("schema") != PERSISTENT_DIGEST_SCHEMA
            or persistent.get("matches_target") is not True
            or persistent.get("actual_sha256") != persistent.get("expected_sha256")
        ):
            raise CheckerError(f"final persistent player state diverged from target: {persistent}")
        checks["target_persistent_player_state_exact"] = True
    finally:
        await adapter.close()

    result = {
        "schema_version": RESULT_SCHEMA,
        "status": "passed",
        "method": method,
        "checker_sha256": file_digest(Path(__file__)),
        "artifact_sha256": hashlib.sha256(raw_artifact).hexdigest(),
        "canonical_start_sha256": canonical_start_sha256,
        "target_snapshot_sha256": target_snapshot_sha256,
        "execution_environment": EXECUTION_ENVIRONMENT,
        "runtime": observed_runtime,
        "verification_kind": (
            "transition_replay" if method == "witness_trajectory" else "executed_invariant_checker"
        ),
        "replayed_transition_count": len(trace),
        "executed_trace": trace,
        "final_persistent_player_state": persistent,
    }
    if method == "invariant_certificate":
        if not all(checks[name] for name in REQUIRED_INVARIANTS):
            raise CheckerError("not every allowlisted invariant executed successfully")
        result["checked_invariants"] = list(REQUIRED_INVARIANTS)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--method", choices=("witness_trajectory", "invariant_certificate"), required=True)
    parser.add_argument("--canonical-start-sha256", required=True)
    parser.add_argument("--target-snapshot-sha256", required=True)
    args = parser.parse_args()
    try:
        adapter = LiveKaetramAdapter()
        result = asyncio.run(verify_artifact(
            args.artifact.resolve(), method=args.method,
            canonical_start_sha256=args.canonical_start_sha256,
            target_snapshot_sha256=args.target_snapshot_sha256, adapter=adapter,
        ))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed on service/adapter errors
        print(f"reachability check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
