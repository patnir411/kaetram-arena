"""Safety and lock-contract tests for the local unit-test bootstrap."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap_unit_tests.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_unit_tests", SCRIPT_PATH)
assert SPEC and SPEC.loader
bootstrap_unit_tests = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap_unit_tests)


def test_direct_dependencies_are_exactly_pinned_in_complete_lock() -> None:
    lock = bootstrap_unit_tests.parse_lock()
    direct = {}
    for raw_line in (
        REPO_ROOT / "requirements" / "unit-tests.in"
    ).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s]+)", line)
        assert match, f"direct dependency is not exactly pinned: {line}"
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        direct[name] = match.group(2)
    assert direct
    assert {name: lock.get(name) for name in direct} == direct


def test_complete_lock_has_only_exact_unique_pins() -> None:
    lock = bootstrap_unit_tests.parse_lock()
    assert len(lock) == 59
    assert lock["pytest"] == "9.1.1"
    assert lock["mcp"] == "1.28.1"
    assert lock["openai"] == "2.46.0"
    assert lock["playwright"] == "1.61.0"
    assert lock["pymongo"] == "4.17.0"
    assert lock["dnspython"] == "2.8.0"
    assert lock["tokenizers"] == "0.22.2"
    assert lock["numpy"] == "2.5.1"
    assert lock["scipy"] == "1.18.0"


def test_safe_venv_path_accepts_only_named_direct_child(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_unit_tests, "REPO_ROOT", tmp_path)
    accepted = bootstrap_unit_tests.safe_venv_path(".venv-unit-tests-audit")
    assert accepted == tmp_path / ".venv-unit-tests-audit"

    for unsafe in (tmp_path, tmp_path / "nested" / ".venv-unit-tests", tmp_path / "venv"):
        with pytest.raises(bootstrap_unit_tests.BootstrapError):
            bootstrap_unit_tests.safe_venv_path(unsafe)


def test_safe_venv_path_rejects_symlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_unit_tests, "REPO_ROOT", tmp_path)
    destination = tmp_path / ".venv-unit-tests-real"
    destination.mkdir()
    link = tmp_path / ".venv-unit-tests-link"
    link.symlink_to(destination, target_is_directory=True)

    with pytest.raises(bootstrap_unit_tests.BootstrapError, match="symlink"):
        bootstrap_unit_tests.safe_venv_path(link)


def test_clean_checkout_guard_rejects_user_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_unit_tests, "REPO_ROOT", tmp_path)

    def fake_git(*args: str) -> str:
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[0] == "status":
            return " M user-file.txt"
        raise AssertionError(args)

    monkeypatch.setattr(bootstrap_unit_tests, "_git", fake_git)
    with pytest.raises(bootstrap_unit_tests.BootstrapError, match="dirty checkout"):
        bootstrap_unit_tests.require_clean_checkout()


def test_managed_environment_names_are_gitignored() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".venv-unit-tests*/" in ignore


def test_ci_uses_the_same_bootstrap_with_immutable_action_pins() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "unit-tests.yml").read_text(
        encoding="utf-8"
    )
    assert "scripts/bootstrap_unit_tests.py bootstrap --python python" in workflow
    assert "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd" in workflow
    assert "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405" in workflow
    assert "permissions:\n  contents: read" in workflow
