"""SessionLogger semantics + the outer warm-session loop in play_qwen.

Locks the contract: each call to `open_next_session()` increments the
on-disk `.session_counter`, opens a new `session_<N>_<TIMESTAMP>.log` file
in the run dir, and writes a sibling `.meta.json` sidecar with the harness
template merged in. This is the file shape `log_analysis/parse_run_sessions`
+ `dashboard/api.py::send_agents` already glob.

The outer-loop test exercises the rotation + exit semantics by mocking
`_run_inner_loop` to return canned (reason, turns) tuples — without
spinning up MCP/browser/network — and asserts that:
  - context_overflow → next session opens, loop continues
  - api_errors / interrupted / duration_exhausted → loop exits
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import play_qwen


# ────────────────────────────────────────────────────────────────────────
# SessionLogger semantics
# ────────────────────────────────────────────────────────────────────────

def _harness_meta() -> dict:
    return {
        "agent_id": 0,
        "personality": "grinder",
        "harness": "qwen",
        "model": "kaetram-base",
        "username": "QwenGrinder",
        "auth_mode": "subscription",
        "max_budget_usd": None,
    }


def test_session_logger_first_open_increments_from_zero(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    logger = play_qwen.SessionLogger(run_dir, sandbox, _harness_meta())
    assert logger.session_n == 0  # no counter on disk yet

    n = logger.open_next_session()
    assert n == 1
    assert logger.session_n == 1
    counter_file = sandbox / "state" / ".session_counter"
    assert counter_file.read_text().strip() == "1"
    logger.close()


def test_session_logger_resumes_from_counter(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    (sandbox / "state").mkdir()
    (sandbox / "state" / ".session_counter").write_text("7\n")

    logger = play_qwen.SessionLogger(run_dir, sandbox, _harness_meta())
    assert logger.session_n == 7
    n = logger.open_next_session()
    assert n == 8
    logger.close()


def test_session_logger_rotates_files_on_each_open(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    logger = play_qwen.SessionLogger(run_dir, sandbox, _harness_meta())

    logger.open_next_session()
    first_log = logger.session_log_path
    first_meta = logger.session_meta_path
    logger.emit({"type": "marker", "n": 1})
    logger.close()

    logger.open_next_session()
    second_log = logger.session_log_path
    second_meta = logger.session_meta_path
    logger.emit({"type": "marker", "n": 2})
    logger.close()

    assert first_log != second_log
    assert first_meta != second_meta
    # Both files exist + match the session_<N>_*.log pattern.
    assert first_log.is_file() and second_log.is_file()
    assert first_log.name.startswith("session_1_")
    assert second_log.name.startswith("session_2_")
    # Sidecars carry the merged harness template + per-session fields.
    m1 = json.loads(first_meta.read_text())
    assert m1["harness"] == "qwen"
    assert m1["model"] == "kaetram-base"
    assert m1["session"] == 1
    assert m1["log_file"] == first_log.name


def test_session_logger_keeps_run_meta_session_count_in_sync(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    # orchestrate writes run.meta.json at run startup with session_count=0;
    # play_qwen bumps it as sessions roll.
    (run_dir / "run.meta.json").write_text(json.dumps({"session_count": 0}))

    logger = play_qwen.SessionLogger(run_dir, sandbox, _harness_meta())
    logger.open_next_session()
    logger.close()
    logger.open_next_session()
    logger.close()

    rm = json.loads((run_dir / "run.meta.json").read_text())
    assert rm["session_count"] == 2


# ────────────────────────────────────────────────────────────────────────
# Outer warm-session loop semantics
# ────────────────────────────────────────────────────────────────────────

class _FakeMCP:
    """Minimal MCP stub for the outer-loop test — never actually connects."""
    _tools: dict = {}

    async def connect(self):
        return []

    def get_tool_definitions(self, schema_source="live"):
        return []

    async def close(self):
        pass


def _patched_run_inner(reasons: list[tuple[str, int]]):
    """Returns an async function that pops reasons in order on each call."""
    queue = list(reasons)

    async def _impl(*, client, mcp, tool_defs, messages, state_dir, logger, args, deadline):
        if not queue:
            # Defensive — we should never call inner more times than reasons given.
            raise RuntimeError("inner loop called more times than expected")
        return queue.pop(0)

    return _impl


def _run_outer_loop_with_mock(tmp_path: Path, reasons, monkeypatch) -> Path:
    """Drive `play_qwen.run_agent` with a mocked _run_inner_loop. Returns
    the run_dir so the test can inspect produced files."""
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    sys_prompt_file = tmp_path / "system.md"
    sys_prompt_file.write_text("system")
    harness_meta_path = tmp_path / "meta.json"
    harness_meta_path.write_text(json.dumps(_harness_meta()))

    # Patch the heavy bits.
    monkeypatch.setattr(play_qwen, "_run_inner_loop", _patched_run_inner(reasons))
    monkeypatch.setattr(play_qwen, "MCPClient", lambda *a, **kw: _FakeMCP())
    # OpenAI client constructor is called but never used (inner is mocked).
    monkeypatch.setattr(play_qwen, "OpenAI", lambda **kwargs: object())

    args = SimpleNamespace(
        endpoint="https://example/v1",
        model="kaetram-base",
        api_key=None,
        system_prompt=str(sys_prompt_file),
        sandbox=str(sandbox),
        run_dir=str(run_dir),
        harness_meta=str(harness_meta_path),
        max_duration_seconds=0,
        server_port="9001",
        project_dir=str(REPO_ROOT),
        personality="grinder",
    )
    asyncio.run(play_qwen.run_agent(args))
    return run_dir


def test_outer_loop_rotates_on_context_overflow_then_exits(tmp_path: Path, monkeypatch) -> None:
    # Two context_overflow rollovers then api_errors → 3 sessions total.
    run_dir = _run_outer_loop_with_mock(
        tmp_path,
        reasons=[("context_overflow", 5), ("context_overflow", 7), ("api_errors", 3)],
        monkeypatch=monkeypatch,
    )
    session_logs = sorted(run_dir.glob("session_*.log"))
    assert len(session_logs) == 3, [p.name for p in session_logs]
    # Each session log carries: 1 system_init record + 1 result record (no
    # inner emissions because we mocked the inner loop).
    last = json.loads(session_logs[-1].read_text().splitlines()[-1])
    assert last["type"] == "result"
    assert last["terminal_reason"] == "api_errors"


def test_outer_loop_exits_on_first_non_overflow(tmp_path: Path, monkeypatch) -> None:
    # First reason is duration_exhausted → exits immediately, only 1 session.
    run_dir = _run_outer_loop_with_mock(
        tmp_path,
        reasons=[("duration_exhausted", 12)],
        monkeypatch=monkeypatch,
    )
    session_logs = list(run_dir.glob("session_*.log"))
    assert len(session_logs) == 1
    last = json.loads(session_logs[0].read_text().splitlines()[-1])
    assert last["terminal_reason"] == "duration_exhausted"


def test_outer_loop_session_counter_persists_across_rollovers(tmp_path: Path, monkeypatch) -> None:
    sandbox = tmp_path / "sandbox"
    run_dir = tmp_path / "run"
    sandbox.mkdir()
    run_dir.mkdir()
    # Pre-seed the counter to simulate a crash recovery scenario.
    (sandbox / "state").mkdir()
    (sandbox / "state" / ".session_counter").write_text("4")

    sys_prompt_file = tmp_path / "system.md"
    sys_prompt_file.write_text("system")
    harness_meta_path = tmp_path / "meta.json"
    harness_meta_path.write_text(json.dumps(_harness_meta()))

    monkeypatch.setattr(
        play_qwen, "_run_inner_loop",
        _patched_run_inner([("context_overflow", 1), ("api_errors", 1)]),
    )
    monkeypatch.setattr(play_qwen, "MCPClient", lambda *a, **kw: _FakeMCP())
    monkeypatch.setattr(play_qwen, "OpenAI", lambda **kwargs: object())

    args = SimpleNamespace(
        endpoint="https://example/v1",
        model="kaetram-base",
        api_key=None,
        system_prompt=str(sys_prompt_file),
        sandbox=str(sandbox),
        run_dir=str(run_dir),
        harness_meta=str(harness_meta_path),
        max_duration_seconds=0,
        server_port="9001",
        project_dir=str(REPO_ROOT),
        personality="grinder",
    )
    asyncio.run(play_qwen.run_agent(args))

    # Counter should be 4 + 2 = 6 (two new sessions opened).
    counter = (sandbox / "state" / ".session_counter").read_text().strip()
    assert counter == "6"
    # And we should have session_5_*.log + session_6_*.log on disk.
    names = sorted(p.name for p in run_dir.glob("session_*.log"))
    assert any(n.startswith("session_5_") for n in names)
    assert any(n.startswith("session_6_") for n in names)


# ────────────────────────────────────────────────────────────────────────
# _build_session_note — Rick's-traversal carry
# ────────────────────────────────────────────────────────────────────────

def _write_state(tmp_path: Path, state: dict) -> Path:
    (tmp_path / "game_state.json").write_text(json.dumps(state))
    return tmp_path


def test_session_note_ricks_fish_first_when_no_shrimp(tmp_path: Path) -> None:
    # Foresting + Herbalist's finished, Rick's next, holding 0 cooked shrimp →
    # the note must say FISH FIRST and must NOT pin the door (no shrimp to deliver yet).
    state = {
        "pos": {"x": 250, "y": 300},
        "finished_quests": [{"name": "Foresting"},
                            {"name": "Herbalist's Desperation"}],
        "active_quests": [],
        "inventory": [{"key": "fishingpole", "count": 1}],
    }
    note = play_qwen._build_session_note(_write_state(tmp_path, state), "context_overflow")
    assert note is not None
    assert "fish" in note.lower() and "FIRST" in note
    assert "(379,388)" not in note  # don't send it across before it has shrimp


def test_session_note_ricks_cross_when_holding_shrimp(tmp_path: Path) -> None:
    # Holding 5 cooked shrimp → now the note should send it across the door.
    state = {
        "pos": {"x": 250, "y": 300},
        "finished_quests": [{"name": "Foresting"},
                            {"name": "Herbalist's Desperation"}],
        "active_quests": [{"name": "Rick's Roll", "stage": 1}],
        "inventory": [{"key": "cookedshrimp", "count": 5}],
    }
    note = play_qwen._build_session_note(_write_state(tmp_path, state), "context_overflow")
    assert note is not None
    assert "(379,388)" in note and "cross" in note.lower()


def test_session_note_no_ricks_carry_when_not_targeting_ricks(tmp_path: Path) -> None:
    # Foresting still unfinished → next Core 3 is Foresting, not Rick's.
    state = {
        "pos": {"x": 250, "y": 300},
        "finished_quests": [],
        "active_quests": [],
    }
    note = play_qwen._build_session_note(_write_state(tmp_path, state), "context_overflow")
    assert note is None or "Rick's Roll" not in note
