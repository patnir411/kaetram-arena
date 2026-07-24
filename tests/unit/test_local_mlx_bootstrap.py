"""Safety and lock-contract tests for the zero-cost local MLX bootstrap."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap_local_mlx.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_local_mlx", SCRIPT_PATH)
assert SPEC and SPEC.loader
bootstrap_local_mlx = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap_local_mlx)


def test_complete_lock_has_only_exact_unique_pins() -> None:
    lock = bootstrap_local_mlx.parse_lock()
    assert len(lock) == 34
    assert lock["mlx"] == "0.32.0"
    assert lock["mlx-lm"] == "0.31.3"
    assert lock["transformers"] == "5.14.1"
    assert lock["tokenizers"] == "0.22.2"
    assert lock["sentencepiece"] == "0.2.2"


def test_safe_venv_path_accepts_only_named_direct_child(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(bootstrap_local_mlx, "REPO_ROOT", tmp_path)
    accepted = bootstrap_local_mlx.safe_venv_path(".venv-local-mlx-audit")
    assert accepted == tmp_path / ".venv-local-mlx-audit"

    for unsafe in (tmp_path, tmp_path / "nested" / ".venv-local-mlx", tmp_path / "venv"):
        with pytest.raises(bootstrap_local_mlx.BootstrapError):
            bootstrap_local_mlx.safe_venv_path(unsafe)


def test_safe_venv_path_rejects_symlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_local_mlx, "REPO_ROOT", tmp_path)
    destination = tmp_path / ".venv-local-mlx-real"
    destination.mkdir()
    link = tmp_path / ".venv-local-mlx-link"
    link.symlink_to(destination, target_is_directory=True)

    with pytest.raises(bootstrap_local_mlx.BootstrapError, match="symlink"):
        bootstrap_local_mlx.safe_venv_path(link)


def test_clean_checkout_guard_rejects_user_changes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(bootstrap_local_mlx, "REPO_ROOT", tmp_path)

    def fake_git(*args: str) -> str:
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[0] == "status":
            return " M user-file.txt"
        raise AssertionError(args)

    monkeypatch.setattr(bootstrap_local_mlx, "_git", fake_git)
    with pytest.raises(bootstrap_local_mlx.BootstrapError, match="dirty checkout"):
        bootstrap_local_mlx.require_clean_checkout()


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (
            {"python_version": "3.11.9", "sys_platform": "darwin", "machine": "arm64"},
            "Python 3.12",
        ),
        (
            {"python_version": "3.12.12", "sys_platform": "linux", "machine": "x86_64"},
            "Apple silicon",
        ),
        (
            {"python_version": "3.12.12", "sys_platform": "darwin", "machine": "x86_64"},
            "Apple silicon",
        ),
        (
            {"python_version": None, "sys_platform": "darwin", "machine": "arm64"},
            "invalid identity fields",
        ),
    ],
)
def test_runtime_contract_rejects_unsupported_identity(
    identity: dict[str, object], message: str, monkeypatch
) -> None:
    monkeypatch.setattr(
        bootstrap_local_mlx, "runtime_identity", lambda _interpreter: identity
    )
    with pytest.raises(bootstrap_local_mlx.BootstrapError, match=message):
        bootstrap_local_mlx.require_supported_runtime("python")


def test_runtime_contract_accepts_python312_on_apple_silicon(monkeypatch) -> None:
    identity = {
        "python_version": "3.12.12",
        "sys_platform": "darwin",
        "machine": "arm64",
    }
    monkeypatch.setattr(
        bootstrap_local_mlx, "runtime_identity", lambda _interpreter: identity
    )
    assert bootstrap_local_mlx.require_supported_runtime("python") == identity


def test_marker_binds_commit_lock_runtime_and_platform(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / ".venv-local-mlx-audit"
    target.mkdir()
    interpreter = target / "bin" / "python"
    interpreter.parent.mkdir()
    interpreter.touch()
    identity = {
        "python_version": "3.12.12",
        "sys_platform": "darwin",
        "machine": "arm64",
    }
    monkeypatch.setattr(
        bootstrap_local_mlx, "require_supported_runtime", lambda _interpreter: identity
    )
    monkeypatch.setattr(
        bootstrap_local_mlx,
        "managed_environment_identity",
        lambda _interpreter: {
            "schema_version": "kaetram.installed-python-tree.v2",
            "distribution_count": 35,
            "file_count": 1000,
            "tree_sha256": "c" * 64,
            "runtime_search_path_count": 3,
            "runtime_tree_sha256": "d" * 64,
        },
    )
    monkeypatch.setattr(bootstrap_local_mlx, "lock_sha256", lambda: "a" * 64)
    marker = bootstrap_local_mlx.marker_payload("b" * 40, target)
    assert marker == {
        "schema_version": "kaetram.local-mlx-environment.v3",
        "git_commit": "b" * 40,
        "lock_sha256": "a" * 64,
        "python_version": "3.12.12",
        "python_executable_sha256": hashlib.sha256(b"").hexdigest(),
        "pip_version": "26.1.2",
        "sys_platform": "darwin",
        "machine": "arm64",
        "installed_distribution_count": 35,
        "installed_file_count": 1000,
        "installed_tree_sha256": "c" * 64,
        "runtime_search_path_count": 3,
        "runtime_tree_sha256": "d" * 64,
    }

    (target / bootstrap_local_mlx.MARKER_NAME).write_text(
        json.dumps({**marker, "machine": "x86_64"}), encoding="utf-8"
    )
    with pytest.raises(bootstrap_local_mlx.BootstrapError, match="marker mismatch"):
        bootstrap_local_mlx.verify_marker(target, "b" * 40)


def test_exact_inventory_rejects_duplicate_normalized_distributions(
    monkeypatch,
) -> None:
    identity = {
        "python_version": "3.12.12",
        "sys_platform": "darwin",
        "machine": "arm64",
    }
    monkeypatch.setattr(
        bootstrap_local_mlx, "require_supported_runtime", lambda _interpreter: identity
    )
    monkeypatch.setattr(
        bootstrap_local_mlx, "parse_lock", lambda: {"mlx-lm": "0.31.3"}
    )
    monkeypatch.setattr(
        bootstrap_local_mlx,
        "measure_installed_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bootstrap_local_mlx.IdentityError(
                "duplicate installed distributions: mlx-lm"
            )
        ),
    )

    with pytest.raises(
        bootstrap_local_mlx.BootstrapError,
        match="duplicate installed distributions: mlx-lm",
    ):
        bootstrap_local_mlx.verify_installed_environment()


def test_managed_environment_names_are_gitignored() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".venv-local-mlx*/" in ignore
