#!/usr/bin/env python3
"""Create or verify the pinned zero-cost Apple-silicon MLX environment.

The environment is deliberately separate from the evaluation/launcher
environment. Bootstrap accepts only a new direct child of the repository
named ``.venv-local-mlx*``; it never removes or reuses an environment. Check
accepts only an environment carrying the marker written for the current clean
Git commit, dependency lock, Python runtime, and supported platform.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from installed_environment_identity import (  # noqa: E402
    IdentityError,
    measure_installed_environment,
)
from isolated_python_entry import isolated_python_command  # noqa: E402


LOCK_PATH = REPO_ROOT / "requirements" / "local-mlx.lock"
MARKER_NAME = ".kaetram-local-mlx-environment.json"
PYTHON_SERIES = (3, 12)
PIP_VERSION = "26.1.2"
SUPPORTED_PLATFORM = "darwin"
SUPPORTED_MACHINE = "arm64"
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
    if target.parent != root or not target.name.startswith(".venv-local-mlx"):
        raise BootstrapError(
            "--venv must be a direct repository child named .venv-local-mlx*"
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


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def venv_python(target: Path) -> Path:
    candidates = (target / "bin" / "python", target / "Scripts" / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BootstrapError(f"managed environment has no Python interpreter: {target}")


def runtime_identity(interpreter: str | Path) -> dict[str, object]:
    program = (
        "import json, platform, sys;"
        "print(json.dumps({'python_version': platform.python_version(),"
        "'sys_platform': sys.platform, 'machine': platform.machine().lower()}))"
    )
    result = _run(
        [str(interpreter), "-I", "-S", "-B", "-c", program], capture=True
    )
    try:
        identity = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError("could not inspect selected Python runtime") from exc
    if not isinstance(identity, dict):
        raise BootstrapError("selected Python runtime returned invalid identity")
    return identity


def require_supported_runtime(interpreter: str | Path) -> dict[str, str]:
    identity = runtime_identity(interpreter)
    version = identity.get("python_version")
    actual_platform = identity.get("sys_platform")
    actual_machine = identity.get("machine")
    if not all(
        isinstance(value, str) for value in (version, actual_platform, actual_machine)
    ):
        raise BootstrapError("selected Python runtime returned invalid identity fields")
    try:
        series = tuple(int(part) for part in version.split(".")[:2])
    except ValueError as exc:
        raise BootstrapError(f"could not parse Python version: {version!r}") from exc
    if series != PYTHON_SERIES:
        raise BootstrapError(
            f"Python {PYTHON_SERIES[0]}.{PYTHON_SERIES[1]} is required; found {version}"
        )
    if (actual_platform, actual_machine) != (SUPPORTED_PLATFORM, SUPPORTED_MACHINE):
        raise BootstrapError(
            "the pinned MLX runtime supports only Apple silicon "
            f"({SUPPORTED_PLATFORM}/{SUPPORTED_MACHINE}); found "
            f"{actual_platform}/{actual_machine}"
        )
    return {
        "python_version": version,
        "sys_platform": actual_platform,
        "machine": actual_machine,
    }


def installed_environment_identity() -> dict[str, object]:
    require_supported_runtime(sys.executable)
    try:
        runtime_paths = [
            Path(item)
            for item in sys.path
            if item
            and not Path(item).resolve().is_relative_to(REPO_ROOT.resolve())
        ]
        return measure_installed_environment(
            parse_lock(),
            pip_version=PIP_VERSION,
            runtime_search_paths=runtime_paths,
        )
    except IdentityError as exc:
        raise BootstrapError(str(exc)) from exc


def managed_environment_identity(interpreter: str | Path) -> dict[str, object]:
    target = Path(interpreter).absolute().parent.parent
    result = _run(
        isolated_python_command(
            interpreter,
            repo_root=REPO_ROOT,
            environment_root=target,
            script=Path(__file__).resolve(),
            target_args=("_identity",),
        ),
        capture=True,
    )
    try:
        identity = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError("managed environment returned invalid content identity") from exc
    if (
        not isinstance(identity, dict)
        or identity.get("schema_version") != "kaetram.installed-python-tree.v2"
        or not isinstance(identity.get("distribution_count"), int)
        or not isinstance(identity.get("file_count"), int)
        or not isinstance(identity.get("tree_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", identity["tree_sha256"]) is None
        or not isinstance(identity.get("runtime_search_path_count"), int)
        or not isinstance(identity.get("runtime_tree_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", identity["runtime_tree_sha256"]) is None
    ):
        raise BootstrapError("managed environment returned malformed content identity")
    return identity


def marker_payload(commit: str, target: Path) -> dict[str, object]:
    interpreter = venv_python(target)
    identity = require_supported_runtime(interpreter)
    content_identity = managed_environment_identity(interpreter)
    return {
        "schema_version": "kaetram.local-mlx-environment.v3",
        "git_commit": commit,
        "lock_sha256": lock_sha256(),
        "python_version": identity["python_version"],
        "python_executable_sha256": _sha256_file(interpreter.resolve()),
        "pip_version": PIP_VERSION,
        "sys_platform": identity["sys_platform"],
        "machine": identity["machine"],
        "installed_distribution_count": content_identity["distribution_count"],
        "installed_file_count": content_identity["file_count"],
        "installed_tree_sha256": content_identity["tree_sha256"],
        "runtime_search_path_count": content_identity["runtime_search_path_count"],
        "runtime_tree_sha256": content_identity["runtime_tree_sha256"],
    }


def verify_marker(target: Path, commit: str) -> None:
    marker_path = target / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("marker is not a JSON object")
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"missing or invalid bootstrap marker: {marker_path}") from exc
    expected = marker_payload(commit, target)
    mismatches = {
        key: {"expected": value, "actual": marker.get(key)}
        for key, value in expected.items()
        if marker.get(key) != value
    }
    if mismatches:
        raise BootstrapError(f"environment marker mismatch: {mismatches}")


def verify_installed_environment() -> None:
    """Run inside the managed venv and require the exact locked inventory."""
    installed_environment_identity()


def verified_environment_receipt(target: Path, commit: str) -> dict[str, object]:
    verify_marker(target, commit)
    marker_bytes = (target / MARKER_NAME).read_bytes()
    marker = json.loads(marker_bytes)
    record = {
        "schema_version": "kaetram.pinned-python-environment-receipt.v1",
        "environment_kind": "local_mlx",
        "marker_sha256": _sha256_json(marker),
        "marker": marker,
    }
    return {**record, "receipt_sha256": _sha256_json(record)}


def verified_current_environment_receipt() -> dict[str, object]:
    commit = require_clean_checkout()
    target = safe_venv_path(Path(sys.executable).absolute().parent.parent)
    expected = venv_python(target).absolute()
    if Path(sys.executable).absolute() != expected:
        raise BootstrapError(
            f"active interpreter is not the managed environment Python: {sys.executable}"
        )
    return verified_environment_receipt(target, commit)


def run_checks(target: Path, commit: str) -> None:
    verify_marker(target, commit)
    _run(
        isolated_python_command(
            venv_python(target),
            repo_root=REPO_ROOT,
            environment_root=target,
            script=Path(__file__).resolve(),
            target_args=("_verify",),
        )
    )
    print("Pinned zero-cost Apple-silicon MLX environment verified.")


def bootstrap(target: Path, selected_python: str) -> None:
    commit = require_clean_checkout()
    if target.exists() or target.is_symlink():
        raise BootstrapError(
            f"refusing to reuse existing path: {target}; choose a new .venv-local-mlx* name"
        )
    interpreter = shutil.which(selected_python)
    if not interpreter or not Path(interpreter).is_file():
        raise BootstrapError(f"Python interpreter not found: {selected_python}")
    require_supported_runtime(interpreter)
    parse_lock()
    _run([str(interpreter), "-m", "venv", str(target)])
    managed_python = venv_python(target)
    _run([
        str(managed_python), "-m", "pip", "install", "--isolated",
        "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
        "--only-binary=:all:", f"pip=={PIP_VERSION}",
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
        command.add_argument("--venv", default=".venv-local-mlx")
        if name == "bootstrap":
            command.add_argument(
                "--python",
                default=os.environ.get("KAETRAM_MLX_PYTHON", "python3.12"),
                help="Apple-silicon Python 3.12 used to create the new environment",
            )
    subparsers.add_parser("_verify", help=argparse.SUPPRESS)
    subparsers.add_parser("_identity", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "_verify":
            verify_installed_environment()
        elif args.command == "_identity":
            print(json.dumps(installed_environment_identity(), sort_keys=True))
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
