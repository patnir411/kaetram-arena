"""Installed-file identity must detect package ambiguity and byte drift."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "installed_environment_identity.py"
SPEC = importlib.util.spec_from_file_location(
    "installed_environment_identity_under_test", SCRIPT_PATH
)
assert SPEC and SPEC.loader
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)


class FakeDistribution:
    def __init__(self, root: Path, name: str, version: str, files: list[str]):
        self.root = root
        self.metadata = {"Name": name}
        self.version = version
        self.files = files

    def locate_file(self, relative: str) -> Path:
        return self.root / relative


def _prepare(
    tmp_path: Path, monkeypatch
) -> tuple[Path, FakeDistribution, FakeDistribution]:
    root = tmp_path / "venv"
    interpreter = root / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    site = root / f"lib/python{identity.sys.version_info.major}.{identity.sys.version_info.minor}/site-packages"
    site.mkdir(parents=True)
    (site / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (site / "pip.py").write_text("pip = True\n", encoding="utf-8")
    demo = FakeDistribution(site, "demo_pkg", "1.2.3", ["demo.py"])
    pip = FakeDistribution(site, "pip", "26.1.2", ["pip.py"])
    monkeypatch.setattr(identity.sys, "executable", str(interpreter))
    monkeypatch.setattr(
        identity.importlib.metadata,
        "distributions",
        lambda **_kwargs: [demo, pip],
    )
    return site, demo, pip


def test_content_identity_changes_when_installed_bytes_change(
    tmp_path: Path, monkeypatch
) -> None:
    root, _, _ = _prepare(tmp_path, monkeypatch)
    first = identity.measure_installed_environment(
        {"demo-pkg": "1.2.3"}, pip_version="26.1.2", runtime_search_paths=[]
    )
    (root / "demo.py").write_text("value = 2\n", encoding="utf-8")
    second = identity.measure_installed_environment(
        {"demo-pkg": "1.2.3"}, pip_version="26.1.2", runtime_search_paths=[]
    )
    assert second["tree_sha256"] != first["tree_sha256"]


def test_duplicate_normalized_distribution_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    root, demo, pip = _prepare(tmp_path, monkeypatch)
    duplicate_path = root / "duplicate.py"
    duplicate_path.write_text("duplicate = True\n", encoding="utf-8")
    duplicate = FakeDistribution(root, "demo.pkg", "1.2.3", ["duplicate.py"])
    monkeypatch.setattr(
        identity.importlib.metadata,
        "distributions",
        lambda **_kwargs: [demo, duplicate, pip],
    )
    with pytest.raises(identity.IdentityError, match="duplicate installed distributions"):
        identity.measure_installed_environment(
            {"demo-pkg": "1.2.3"},
            pip_version="26.1.2",
            runtime_search_paths=[],
        )


def test_symlinked_distribution_file_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    root, _, pip = _prepare(tmp_path, monkeypatch)
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n", encoding="utf-8")
    (root / "link.py").symlink_to(outside)
    linked = FakeDistribution(root, "demo_pkg", "1.2.3", ["link.py"])
    monkeypatch.setattr(
        identity.importlib.metadata,
        "distributions",
        lambda **_kwargs: [linked, pip],
    )
    with pytest.raises(identity.IdentityError, match="symlinked|escapes"):
        identity.measure_installed_environment(
            {"demo-pkg": "1.2.3"},
            pip_version="26.1.2",
            runtime_search_paths=[],
        )


def test_undeclared_import_hook_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    site, _, _ = _prepare(tmp_path, monkeypatch)
    (site / "sitecustomize.py").write_text("raise RuntimeError()\n", encoding="utf-8")
    with pytest.raises(identity.IdentityError, match="undeclared import-active"):
        identity.measure_installed_environment(
            {"demo-pkg": "1.2.3"},
            pip_version="26.1.2",
            runtime_search_paths=[],
        )
