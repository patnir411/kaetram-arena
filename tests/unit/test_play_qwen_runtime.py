from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import play_qwen


def test_mcp_runtime_defaults_to_active_interpreter(monkeypatch) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    monkeypatch.delenv("KAETRAM_MCP_PYTHON", raising=False)
    assert play_qwen.resolve_mcp_python() == os.path.abspath(sys.executable)


def test_mcp_runtime_allows_an_explicit_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o755)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(interpreter))
    assert play_qwen.resolve_mcp_python() == os.path.abspath(interpreter)


def test_mcp_runtime_preserves_virtualenv_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    base = tmp_path / "base-python"
    base.write_text("#!/bin/sh\nexit 0\n")
    base.chmod(0o755)
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    invoked = venv / "python"
    invoked.symlink_to(base)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(invoked))
    assert play_qwen.resolve_mcp_python() == os.path.abspath(invoked)
    assert play_qwen.resolve_mcp_python() != str(invoked.resolve())


@pytest.mark.parametrize("value", ["missing-python", "not-executable"])
def test_mcp_runtime_rejects_invalid_override(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    candidate = tmp_path / value
    if value == "not-executable":
        candidate.write_text("not executable\n")
        candidate.chmod(0o644)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(candidate))
    with pytest.raises(RuntimeError, match="KAETRAM_MCP_PYTHON"):
        play_qwen.resolve_mcp_python()


def test_isolated_eval_rejects_mcp_interpreter_override(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: True
    )
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(tmp_path / "alternate"))
    with pytest.raises(RuntimeError, match="forbidden"):
        play_qwen.resolve_mcp_python()


def test_isolated_mcp_command_preserves_full_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: True
    )
    environment = tmp_path / ".venv-unit-tests-a"
    server = tmp_path / "repo" / "mcp_game_server.py"
    command = play_qwen.build_mcp_server_command(
        str(environment / "bin/python"), str(server)
    )
    assert command[0] == str(environment / "bin/python")
    assert command[1:4] == ["-I", "-S", "-B"]
    assert command[command.index("--script") + 1] == str(server)
