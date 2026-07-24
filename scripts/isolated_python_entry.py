#!/usr/bin/env python3
"""Run one reviewed script or module without site, .pth, or ambient Python paths."""
from __future__ import annotations

import argparse
import importlib.machinery
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path


ENTRYPOINT = Path(__file__).resolve()
DISABLED_CACHE_DIRECTORY = ".kaetram-disabled-pycache"


class IsolationError(RuntimeError):
    """The process was not launched through the reviewed isolated contract."""


def isolated_contract_active(environment_root: Path) -> bool:
    """Return whether this exact process is inside the reviewed contract."""
    environment = environment_root.resolve()
    disabled_cache = environment / DISABLED_CACHE_DIRECTORY
    expected_python = (environment / "bin" / "python").absolute()
    return bool(
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.no_user_site
        and sys.flags.no_site
        and sys.flags.dont_write_bytecode
        and Path(sys.executable).absolute() == expected_python
        and sys.pycache_prefix is not None
        and Path(sys.pycache_prefix).absolute() == disabled_cache.absolute()
        and not disabled_cache.exists()
        and not disabled_cache.is_symlink()
    )


def isolated_python_command(
    interpreter: str | Path,
    *,
    repo_root: Path,
    environment_root: Path,
    script: Path | None = None,
    module: str | None = None,
    target_args: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Build the only supported command shape for a result-bearing process."""
    if (script is None) == (module is None):
        raise IsolationError("select exactly one script or module")
    environment = environment_root.absolute()
    command = [
        str(Path(interpreter).absolute()),
        "-I",
        "-S",
        "-B",
        "-X",
        f"pycache_prefix={environment / DISABLED_CACHE_DIRECTORY}",
        str(ENTRYPOINT),
        "--repo-root",
        str(repo_root.absolute()),
        "--environment-root",
        str(environment),
    ]
    if script is not None:
        command.extend(["--script", str(script.absolute())])
    else:
        command.extend(["--module", str(module)])
    command.append("--")
    command.extend(target_args)
    return command


def require_tracked_repository_imports(repo_root: Path) -> None:
    """Reject ignored/untracked files that Python could import from the repo."""
    repo = repo_root.resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise IsolationError("cannot inventory repository import files") from exc
    if result.returncode != 0:
        raise IsolationError("repository import root is not a readable Git checkout")
    tracked = {
        Path(raw.decode("utf-8", errors="strict"))
        for raw in result.stdout.split(b"\0")
        if raw
    }
    extension_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    violations: list[str] = []
    for current, directory_names, file_names in os.walk(repo, followlinks=False):
        current_path = Path(current)
        relative_directory = current_path.relative_to(repo)
        if relative_directory.parts and not all(
            part.isidentifier() for part in relative_directory.parts
        ):
            directory_names[:] = []
            continue
        retained_directories = []
        for name in directory_names:
            path = current_path / name
            relative = path.relative_to(repo)
            if name == "__pycache__" or not name.isidentifier():
                continue
            if path.is_symlink():
                violations.append(relative.as_posix() + "/")
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            path = current_path / name
            relative = path.relative_to(repo)
            import_active = (
                name.endswith(".py")
                or name.endswith(".pyc")
                or name.endswith(extension_suffixes)
            )
            if not import_active:
                continue
            if path.is_symlink() or relative not in tracked:
                violations.append(relative.as_posix())
    if violations:
        preview = ", ".join(sorted(violations)[:12])
        suffix = f" (+{len(violations) - 12} more)" if len(violations) > 12 else ""
        raise IsolationError(
            "untracked, ignored, or symlinked repository import candidates: "
            + preview
            + suffix
        )


def prepare_import_path(repo_root: Path, environment_root: Path) -> Path:
    repo = repo_root.resolve()
    environment = environment_root.resolve()
    if repo_root.is_symlink() or environment_root.is_symlink():
        raise IsolationError("repository and environment roots must not be symlinks")
    if not isolated_contract_active(environment):
        raise IsolationError(
            "entrypoint requires the exact managed interpreter, Python flags "
            "-I -S -B, and an absent environment-local pycache prefix"
        )
    require_tracked_repository_imports(repo)
    series = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = (
        environment / "lib" / series / "site-packages",
        environment / "Lib" / "site-packages",
    )
    site_roots = [path for path in candidates if path.is_dir()]
    if len(site_roots) != 1 or site_roots[0].is_symlink():
        raise IsolationError("managed environment has no unique regular site-packages")
    site_root = site_roots[0].resolve()

    for key in list(os.environ):
        if key.startswith("PYTHON"):
            os.environ.pop(key, None)
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path[:] = [
        str(repo),
        str(site_root),
        *[
            item
            for item in sys.path
            if item
            and Path(item).resolve() not in {repo, site_root}
        ],
    ]
    return site_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--environment-root", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--script", type=Path)
    target.add_argument("--module")
    parser.add_argument("target_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        prepare_import_path(args.repo_root, args.environment_root)
        target_args = list(args.target_args)
        if target_args[:1] == ["--"]:
            target_args.pop(0)
        if args.script is not None:
            invoked = args.script
            if invoked.is_symlink():
                raise IsolationError(f"refusing symlinked target script: {invoked}")
            script = invoked.resolve()
            try:
                script.relative_to(args.repo_root.resolve())
            except ValueError as exc:
                raise IsolationError("target script must be inside the repository") from exc
            if not script.is_file():
                raise IsolationError(f"target script does not exist: {script}")
            sys.argv = [str(script), *target_args]
            runpy.run_path(str(script), run_name="__main__")
        else:
            if not isinstance(args.module, str) or re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]*", args.module
            ) is None:
                raise IsolationError(f"unsafe module name: {args.module!r}")
            sys.argv = [args.module, *target_args]
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
    except IsolationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
