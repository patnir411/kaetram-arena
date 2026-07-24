"""The reviewed Python entrypoint must make import controls explicit."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.isolated_python_entry import (
    IsolationError,
    isolated_python_command,
    require_tracked_repository_imports,
)


def test_command_binds_interpreter_environment_and_disables_default_pycache(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    environment = repo / ".venv-unit-tests-a"
    script = repo / "task.py"
    command = isolated_python_command(
        environment / "bin/python",
        repo_root=repo,
        environment_root=environment,
        script=script,
        target_args=("--value", "1"),
    )
    assert command[0] == str(environment / "bin/python")
    assert command[1:4] == ["-I", "-S", "-B"]
    assert command[4:6] == [
        "-X",
        f"pycache_prefix={environment / '.kaetram-disabled-pycache'}",
    ]
    assert command[command.index("--script") + 1] == str(script)
    assert command[-3:] == ["--", "--value", "1"]


def test_command_requires_exactly_one_target(tmp_path: Path) -> None:
    with pytest.raises(IsolationError, match="exactly one"):
        isolated_python_command(
            tmp_path / "venv/bin/python",
            repo_root=tmp_path,
            environment_root=tmp_path / "venv",
        )


def test_repository_scan_rejects_ignored_sourceless_import(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore", "tracked.py"],
        check=True,
    )
    (tmp_path / "mlx_lm.pyc").write_bytes(b"ignored attack")

    with pytest.raises(IsolationError, match="mlx_lm.pyc"):
        require_tracked_repository_imports(tmp_path)


def test_repository_scan_ignores_default_cache_that_contract_cannot_read(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore", "tracked.py"],
        check=True,
    )
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "tracked.cpython-312.pyc").write_bytes(b"unread cache")

    require_tracked_repository_imports(tmp_path)


def test_repository_scan_rejects_tracked_external_package_symlink(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "payload.py").write_text("value = 42\n", encoding="utf-8")
    (tmp_path / "shadow").symlink_to(outside, target_is_directory=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "shadow"],
        check=True,
    )

    with pytest.raises(IsolationError, match="shadow/"):
        require_tracked_repository_imports(tmp_path)
