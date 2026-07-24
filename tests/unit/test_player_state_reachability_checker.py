from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.opd.check_player_state_reachability import (
    ADAPTER_ID,
    CHECKER_PROTOCOL,
    EXECUTION_ENVIRONMENT,
    PERSISTENT_DIGEST_SCHEMA,
    REQUIRED_INVARIANTS,
    STATE_DIGEST_SCHEMA,
    CheckerError,
    digest,
    main,
    require_git_object_id,
    verify_artifact,
)
from scripts.opd.kaetram_replay_service import ToolResult, _inventory_slots


REPO = Path(__file__).resolve().parents[2]
RUNTIME = {
    "adapter_id": ADAPTER_ID,
    "harness_git_revision": "a" * 40,
    "game_git_revision": "b" * 40,
    "state_digest_schema": STATE_DIGEST_SCHEMA,
    "persistent_digest_schema": PERSISTENT_DIGEST_SCHEMA,
}


def _snapshot(x: int) -> dict:
    return {
        "position": [x, 20],
        "hit_points": 100,
        "mana": 20,
        "inventory": [],
        "bank": [],
        "equipment": [],
        "quests": [{"key": "foresting", "stage": x}],
        "achievements": [],
        "skills": [],
        "statistics": {},
        "player_info_overrides": {},
    }


class FakeServiceAdapter:
    environment_kind = EXECUTION_ENVIRONMENT
    adapter_id = ADAPTER_ID

    def __init__(
        self, observations: list[dict], results: list[dict], *,
        runtime: dict | None = None, persistent_match: bool = True,
    ) -> None:
        self.observations = observations
        self.results = results
        self.runtime = runtime or RUNTIME
        self.persistent_match = persistent_match
        self.index = 0
        self.prepared = None
        self.actions: list[tuple[str, dict]] = []
        self.closed = False

    async def runtime_metadata(self) -> dict[str, str]:
        return self.runtime

    async def prepare(self, canonical_start: dict, target: dict) -> None:
        self.prepared = (canonical_start, target)

    async def observe(self) -> dict:
        return self.observations[self.index]

    async def call_tool(self, tool: str, arguments: dict) -> dict:
        self.actions.append((tool, arguments))
        result = self.results[self.index]
        self.index += 1
        return result

    async def finalize(self, target: dict) -> dict:
        expected = "c" * 64
        return {
            "schema": PERSISTENT_DIGEST_SCHEMA,
            "actual_sha256": expected if self.persistent_match else "d" * 64,
            "expected_sha256": expected,
            "matches_target": self.persistent_match,
        }

    async def close(self) -> None:
        self.closed = True


def _artifact(tmp_path: Path, *, method: str = "witness_trajectory") -> tuple[Path, dict, list[dict], list[dict]]:
    start = _snapshot(1)
    target = _snapshot(2)
    observations = [
        {"pos": {"x": 1, "y": 20}, "active_quests": [{"key": "foresting", "stage": 1}]},
        {"pos": {"x": 2, "y": 20}, "active_quests": [{"key": "foresting", "stage": 2}]},
    ]
    results = [{"is_error": False, "payload": {"arrived": True}, "text_sha256": "e" * 64}]
    transition = {
        "action": {"tool": "navigate", "arguments": {"x": 2, "y": 20}},
        "before_observation_sha256": digest(observations[0]),
        "tool_result_sha256": digest(results[0]),
        "after_observation_sha256": digest(observations[1]),
    }
    payload = {
        "schema_version": 2,
        "checker_protocol": CHECKER_PROTOCOL,
        "method": method,
        "canonical_start_sha256": digest(start),
        "target_snapshot_sha256": digest(target),
        "canonical_start_snapshot": start,
        "target_snapshot": target,
        "path_state_sha256s": [digest(start), digest(target)],
        "initial_observation_sha256": digest(observations[0]),
        "transitions": [transition],
        "runtime": RUNTIME,
    }
    if method == "invariant_certificate":
        payload["invariants"] = list(REQUIRED_INVARIANTS)
    path = tmp_path / "reachability.json"
    path.write_text(json.dumps(payload, sort_keys=True))
    return path, payload, observations, results


def _verify(path: Path, payload: dict, adapter: FakeServiceAdapter) -> dict:
    return asyncio.run(verify_artifact(
        path,
        method=payload["method"],
        canonical_start_sha256=payload["canonical_start_sha256"],
        target_snapshot_sha256=payload["target_snapshot_sha256"],
        adapter=adapter,
    ))


def test_fake_service_contract_replays_and_records_every_digest(tmp_path: Path) -> None:
    path, payload, observations, results = _artifact(tmp_path)
    adapter = FakeServiceAdapter(observations, results)
    result = _verify(path, payload, adapter)
    assert result["status"] == "passed"
    assert result["execution_environment"] == EXECUTION_ENVIRONMENT
    assert result["runtime"] == RUNTIME
    assert result["replayed_transition_count"] == 1
    assert [
        {key: value for key, value in row.items() if key != "index"}
        for row in result["executed_trace"]
    ] == payload["transitions"]
    assert result["final_persistent_player_state"]["matches_target"] is True
    assert adapter.actions == [("navigate", {"x": 2, "y": 20})]
    assert adapter.closed is True


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("before_observation_sha256", "f" * 64, "pre-state digest diverged"),
        ("tool_result_sha256", "f" * 64, "tool-result digest diverged"),
        ("after_observation_sha256", "f" * 64, "post-state digest diverged"),
    ],
)
def test_replay_rejects_any_trace_divergence(
    tmp_path: Path, field: str, replacement: str, message: str,
) -> None:
    path, payload, observations, results = _artifact(tmp_path)
    payload["transitions"][0][field] = replacement
    path.write_text(json.dumps(payload, sort_keys=True))
    adapter = FakeServiceAdapter(observations, results)
    with pytest.raises(CheckerError, match=message):
        _verify(path, payload, adapter)
    assert adapter.closed is True


def test_runtime_revision_and_persistent_target_divergence_fail_closed(tmp_path: Path) -> None:
    path, payload, observations, results = _artifact(tmp_path)
    changed_runtime = {**RUNTIME, "game_git_revision": "9" * 40}
    with pytest.raises(CheckerError, match="runtime revision divergence"):
        _verify(path, payload, FakeServiceAdapter(observations, results, runtime=changed_runtime))

    adapter = FakeServiceAdapter(observations, results, persistent_match=False)
    with pytest.raises(CheckerError, match="persistent player state diverged"):
        _verify(path, payload, adapter)
    assert adapter.closed is True


def test_invariant_mode_executes_only_complete_allowlist(tmp_path: Path) -> None:
    path, payload, observations, results = _artifact(tmp_path, method="invariant_certificate")
    result = _verify(path, payload, FakeServiceAdapter(observations, results))
    assert result["verification_kind"] == "executed_invariant_checker"
    assert result["checked_invariants"] == list(REQUIRED_INVARIANTS)

    payload["invariants"] = ["candidate_supplied_shape_check"]
    path.write_text(json.dumps(payload, sort_keys=True))
    with pytest.raises(CheckerError, match="exactly the allowlisted invariants"):
        _verify(path, payload, FakeServiceAdapter(observations, results))


def test_non_model_visible_action_is_never_executed(tmp_path: Path) -> None:
    path, payload, observations, results = _artifact(tmp_path)
    payload["transitions"][0]["action"]["tool"] = "__test_login"
    path.write_text(json.dumps(payload, sort_keys=True))
    adapter = FakeServiceAdapter(observations, results)
    with pytest.raises(CheckerError, match="not an allowlisted state-changing MCP call"):
        _verify(path, payload, adapter)
    assert adapter.actions == []


def test_cli_refuses_offline_before_importing_live_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path, payload, _, _ = _artifact(tmp_path)
    monkeypatch.delenv("KAETRAM_REACHABILITY_LIVE", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "check_player_state_reachability.py",
        "--artifact", str(path),
        "--method", payload["method"],
        "--canonical-start-sha256", payload["canonical_start_sha256"],
        "--target-snapshot-sha256", payload["target_snapshot_sha256"],
    ])
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "offline refusal" in captured.err


def test_git_object_ids_and_example_checker_pin_are_exact() -> None:
    assert require_git_object_id("a" * 40, "revision") == "a" * 40
    assert require_git_object_id("b" * 64, "revision") == "b" * 64
    with pytest.raises(CheckerError, match="Git object ID"):
        require_git_object_id("c" * 39, "revision")

    config_path = REPO / "research" / "experiments" / "targeted-state-selection.example.json"
    config = json.loads(config_path.read_text())
    for checker in config["reachability_checkers"].values():
        checker_path = (config_path.parent / checker["path"]).resolve()
        assert checker_path == REPO / "scripts" / "opd" / "check_player_state_reachability.py"
        assert hashlib.sha256(checker_path.read_bytes()).hexdigest() == checker["sha256"]


def test_production_adapter_is_independent_of_test_modules_and_fails_on_lossy_seed() -> None:
    checker_source = (REPO / "scripts" / "opd" / "check_player_state_reachability.py").read_text()
    service_source = (REPO / "scripts" / "opd" / "kaetram_replay_service.py").read_text()
    assert "from tests." not in checker_source
    assert "from tests." not in service_source

    parsed = ToolResult(False, 'observe: {"pos":{"x":1,"y":2}}\n\nSTUCK_CHECK:\n{}')
    assert parsed.json() == {"pos": {"x": 1, "y": 2}}
    with pytest.raises(RuntimeError, match="exceeds 25 encoded slots"):
        _inventory_slots([{"index": 24, "key": "logs", "count": 2}])
