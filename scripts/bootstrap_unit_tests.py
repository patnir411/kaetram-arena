#!/usr/bin/env python3
"""Create or verify the pinned, local-only Kaetram unit-test environment.

The script never removes or reuses an environment. Bootstrap accepts only a
new, direct child of the repository named ``.venv-unit-tests*``. Check accepts
only an environment carrying the marker written by bootstrap at the current
clean Git commit and current lock-file digest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "requirements" / "unit-tests.lock"
MARKER_NAME = ".kaetram-unit-test-environment.json"
PYTHON_SERIES = (3, 12)
PIP_VERSION = "26.1.2"
PACKAGE_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")


class BootstrapError(RuntimeError):
    """The requested operation is unsafe, ambiguous, or unreproducible."""


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=capture,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BootstrapError(f"executable not found: {command[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BootstrapError(
            f"command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def _git(*args: str) -> str:
    return _run(["git", *args], capture=True).stdout.strip()


def require_clean_checkout() -> str:
    top = Path(_git("rev-parse", "--show-toplevel")).resolve()
    if top != REPO_ROOT.resolve():
        raise BootstrapError(f"script repository mismatch: expected {REPO_ROOT}, found {top}")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise BootstrapError(
            "refusing a dirty checkout; commit/stash all tracked and untracked changes first"
        )
    return _git("rev-parse", "HEAD")


def safe_venv_path(raw_path: str | Path) -> Path:
    raw = Path(raw_path)
    candidate = REPO_ROOT / raw if not raw.is_absolute() else raw
    if candidate.is_symlink():
        raise BootstrapError(f"refusing symlink environment path: {candidate}")
    target = candidate.resolve()
    root = REPO_ROOT.resolve()
    if target.parent != root or not target.name.startswith(".venv-unit-tests"):
        raise BootstrapError(
            "--venv must be a direct repository child named .venv-unit-tests*"
        )
    if target == root or target == Path("/") or target == Path.home().resolve():
        raise BootstrapError(f"refusing unsafe environment path: {target}")
    return target


def parse_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PACKAGE_RE.fullmatch(line)
        if not match:
            raise BootstrapError(f"unsupported lock entry at {path}:{line_number}: {line}")
        normalized = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if normalized in packages:
            raise BootstrapError(f"duplicate lock package: {normalized}")
        packages[normalized] = match.group(2)
    if not packages:
        raise BootstrapError(f"empty dependency lock: {path}")
    return packages


def lock_sha256() -> str:
    return hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()


def venv_python(target: Path) -> Path:
    candidates = (target / "bin" / "python", target / "Scripts" / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BootstrapError(f"managed environment has no Python interpreter: {target}")


def python_version(interpreter: str | Path) -> str:
    result = _run(
        [str(interpreter), "-c", "import platform; print(platform.python_version())"],
        capture=True,
    )
    version = result.stdout.strip()
    try:
        series = tuple(int(part) for part in version.split(".")[:2])
    except ValueError as exc:
        raise BootstrapError(f"could not parse Python version: {version!r}") from exc
    if series != PYTHON_SERIES:
        raise BootstrapError(
            f"Python {PYTHON_SERIES[0]}.{PYTHON_SERIES[1]} is required; found {version}"
        )
    return version


def marker_payload(commit: str, target: Path) -> dict[str, str]:
    interpreter = venv_python(target)
    return {
        "schema_version": "kaetram.local-unit-tests.v1",
        "git_commit": commit,
        "lock_sha256": lock_sha256(),
        "python_version": python_version(interpreter),
        "pip_version": PIP_VERSION,
    }


def verify_marker(target: Path, commit: str) -> None:
    marker_path = target / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("marker is not a JSON object")
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"missing or invalid bootstrap marker: {marker_path}") from exc
    expected = {
        "schema_version": "kaetram.local-unit-tests.v1",
        "git_commit": commit,
        "lock_sha256": lock_sha256(),
        "python_version": python_version(venv_python(target)),
        "pip_version": PIP_VERSION,
    }
    mismatches = {
        key: {"expected": value, "actual": marker.get(key)}
        for key, value in expected.items()
        if marker.get(key) != value
    }
    if mismatches:
        raise BootstrapError(f"environment marker mismatch: {mismatches}")


def verify_installed_environment() -> None:
    """Run inside the managed venv and require the exact locked inventory."""
    python_version(sys.executable)
    expected = parse_lock()
    installed = {
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(): distribution.version
        for distribution in importlib.metadata.distributions()
    }
    errors = []
    for package, version in expected.items():
        if installed.get(package) != version:
            errors.append(
                f"{package}: expected {version}, found {installed.get(package, 'not installed')}"
            )
    allowed = set(expected) | {"pip"}
    unexpected = sorted(set(installed) - allowed)
    if unexpected:
        errors.append(f"unexpected packages: {', '.join(unexpected)}")
    if installed.get("pip") != PIP_VERSION:
        errors.append(
            f"pip: expected {PIP_VERSION}, found {installed.get('pip', 'not installed')}"
        )
    if errors:
        raise BootstrapError("environment verification failed:\n  - " + "\n  - ".join(errors))


def run_checks(target: Path, commit: str) -> None:
    verify_marker(target, commit)
    interpreter = venv_python(target)
    _run([str(interpreter), "-I", str(Path(__file__).resolve()), "_verify"])
    _run([str(interpreter), "-I", "-m", "pytest", "-q", "tests/unit"])
    print("Pinned local unit-test environment and full unit suite verified.")


def bootstrap(target: Path, selected_python: str) -> None:
    commit = require_clean_checkout()
    if target.exists() or target.is_symlink():
        raise BootstrapError(
            f"refusing to reuse existing path: {target}; choose a new .venv-unit-tests* name"
        )
    interpreter = shutil.which(selected_python)
    if not interpreter or not Path(interpreter).is_file():
        raise BootstrapError(f"Python interpreter not found: {selected_python}")
    python_version(interpreter)
    parse_lock()
    _run([str(interpreter), "-m", "venv", str(target)])
    managed_python = venv_python(target)
    _run([
        str(managed_python), "-m", "pip", "install", "--isolated",
        "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
        "--only-binary=:all:",
        f"pip=={PIP_VERSION}",
    ])
    _run([
        str(managed_python), "-m", "pip", "install", "--isolated",
        "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
        "--only-binary=:all:", "--no-deps", "-r", str(LOCK_PATH),
    ])
    marker_path = target / MARKER_NAME
    with marker_path.open("x", encoding="utf-8") as handle:
        json.dump(marker_payload(commit, target), handle, indent=2, sort_keys=True)
        handle.write("\n")
    run_checks(target, commit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "check"):
        command = subparsers.add_parser(name)
        command.add_argument("--venv", default=".venv-unit-tests")
        if name == "bootstrap":
            command.add_argument(
                "--python",
                default=os.environ.get("KAETRAM_UNIT_TEST_PYTHON", "python3.12"),
                help="Python 3.12 interpreter used to create the new environment",
            )
    subparsers.add_parser("_verify", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "_verify":
            verify_installed_environment()
        elif args.command == "bootstrap":
            bootstrap(safe_venv_path(args.venv), args.python)
        else:
            commit = require_clean_checkout()
            target = safe_venv_path(args.venv)
            if not target.is_dir():
                raise BootstrapError(f"managed environment does not exist: {target}")
            run_checks(target, commit)
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
