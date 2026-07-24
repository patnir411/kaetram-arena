"""Content identity for an isolated, exactly pinned Python environment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path


class IdentityError(RuntimeError):
    """The active environment is incomplete, ambiguous, or mutable."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise IdentityError("installed distribution has no valid Name metadata")
    return re.sub(r"[-_.]+", "-", value).lower()


def _require_under_root(path: Path, root: Path) -> str:
    current = path
    while current != root:
        if current.is_symlink():
            raise IdentityError(f"installed distribution file is symlinked: {path}")
        parent = current.parent
        if parent == current:
            raise IdentityError(f"installed distribution file escapes environment: {path}")
        current = parent
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise IdentityError(
            f"installed distribution file escapes environment: {path}"
        ) from exc


def measure_installed_environment(
    expected: dict[str, str],
    *,
    pip_version: str,
    runtime_search_paths: list[Path] | None = None,
) -> dict[str, object]:
    """Validate the complete importable environment and active stdlib trees."""
    root = Path(sys.executable).absolute().parent.parent.resolve()
    series = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_candidates = (
        root / "lib" / series / "site-packages",
        root / "Lib" / "site-packages",
    )
    site_roots = [path for path in site_candidates if path.is_dir()]
    if len(site_roots) != 1 or site_roots[0].is_symlink():
        raise IdentityError(
            f"expected one regular virtualenv site-packages tree under {root}"
        )
    site_root = site_roots[0]

    installed: dict[str, str] = {}
    duplicate_packages: list[str] = []
    claimed_site_files: set[str] = set()
    distributions: list[dict[str, object]] = []
    for distribution in importlib.metadata.distributions(path=[str(site_root)]):
        package = _normalized_name(distribution.metadata["Name"])
        if package in installed:
            duplicate_packages.append(package)
            continue
        installed[package] = distribution.version
        declared_files = distribution.files
        if declared_files is None:
            raise IdentityError(f"{package}: distribution has no installed-file record")
        file_records = []
        local_paths: set[str] = set()
        for declared in declared_files:
            located = Path(distribution.locate_file(declared))
            if not located.exists() or not located.is_file():
                raise IdentityError(f"{package}: missing installed file {declared}")
            relative = _require_under_root(located, root)
            if relative in local_paths:
                raise IdentityError(
                    f"{package}: duplicate installed-file record {relative}"
                )
            local_paths.add(relative)
            try:
                located.resolve().relative_to(site_root.resolve())
            except ValueError:
                pass
            else:
                claimed_site_files.add(relative)
            file_records.append({
                "path": relative,
                "size_bytes": located.stat().st_size,
                "sha256": _sha256_file(located),
            })
        file_records.sort(key=lambda item: item["path"])
        distributions.append({
            "name": package,
            "version": distribution.version,
            "file_count": len(file_records),
            "tree_sha256": _sha256_json(file_records),
        })

    errors = []
    if duplicate_packages:
        errors.append(
            "duplicate installed distributions: "
            + ", ".join(sorted(set(duplicate_packages)))
        )
    for package, version in expected.items():
        if installed.get(package) != version:
            errors.append(
                f"{package}: expected {version}, found {installed.get(package, 'not installed')}"
            )
    allowed = set(expected) | {"pip"}
    unexpected = sorted(set(installed) - allowed)
    if unexpected:
        errors.append(f"unexpected packages: {', '.join(unexpected)}")
    if installed.get("pip") != pip_version:
        errors.append(
            f"pip: expected {pip_version}, found {installed.get('pip', 'not installed')}"
        )
    if errors:
        raise IdentityError("environment verification failed:\n  - " + "\n  - ".join(errors))

    actual_site_files = set()
    for path in site_root.rglob("*"):
        if path.is_symlink():
            raise IdentityError(f"undeclared or mutable site-packages symlink: {path}")
        if path.is_file():
            actual_site_files.add(path.resolve().relative_to(root).as_posix())
        elif not path.is_dir():
            raise IdentityError(f"non-regular site-packages entry: {path}")
    undeclared = sorted(actual_site_files - claimed_site_files)
    if undeclared:
        preview = ", ".join(undeclared[:12])
        suffix = f" (+{len(undeclared) - 12} more)" if len(undeclared) > 12 else ""
        raise IdentityError(
            "undeclared import-active files in site-packages: " + preview + suffix
        )

    search_paths = (
        [Path(item) for item in sys.path if item]
        if runtime_search_paths is None
        else runtime_search_paths
    )
    runtime_records = []
    seen_runtime_paths: set[Path] = set()
    for index, candidate in enumerate(search_paths):
        if not candidate.exists():
            runtime_records.append({
                "index": index,
                "kind": "absent",
                "name": candidate.name,
            })
            continue
        resolved = candidate.resolve()
        if resolved == site_root.resolve() or resolved in seen_runtime_paths:
            continue
        seen_runtime_paths.add(resolved)
        if resolved.is_file():
            runtime_records.append({
                "index": index,
                "kind": "file",
                "name": resolved.name,
                "symlink_target": (
                    str(candidate.readlink()) if candidate.is_symlink() else None
                ),
                "size_bytes": resolved.stat().st_size,
                "sha256": _sha256_file(resolved),
            })
            continue
        if not resolved.is_dir():
            raise IdentityError(f"non-regular Python runtime search path: {resolved}")
        files = []
        for path in resolved.rglob("*"):
            relative_path = path.relative_to(resolved)
            if (
                relative_path.parts[:1] in {("site-packages",), ("__pycache__",)}
                or "__pycache__" in relative_path.parts
            ):
                continue
            if path.is_symlink():
                if path.is_dir():
                    raise IdentityError(
                        f"symlinked Python runtime directory: {path}"
                    )
                target = path.resolve()
                if not target.is_file():
                    raise IdentityError(f"broken Python runtime symlink: {path}")
                files.append({
                    "path": relative_path.as_posix(),
                    "symlink_target": str(path.readlink()),
                    "size_bytes": target.stat().st_size,
                    "sha256": _sha256_file(target),
                })
            elif path.is_file():
                files.append({
                    "path": relative_path.as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                })
            elif not path.is_dir():
                raise IdentityError(f"non-regular Python runtime file: {path}")
        files.sort(key=lambda item: item["path"])
        runtime_records.append({
            "index": index,
            "kind": "directory",
            "name": resolved.name,
            "file_count": len(files),
            "tree_sha256": _sha256_json(files),
        })

    distributions.sort(key=lambda item: item["name"])
    return {
        "schema_version": "kaetram.installed-python-tree.v2",
        "distribution_count": len(distributions),
        "file_count": sum(
            int(distribution["file_count"]) for distribution in distributions
        ),
        "tree_sha256": _sha256_json(distributions),
        "runtime_search_path_count": len(runtime_records),
        "runtime_tree_sha256": _sha256_json(runtime_records),
    }
