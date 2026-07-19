"""Versioned, immutable provenance manifests for Kaetram runs.

The module deliberately has no third-party dependencies and keeps construction
separate from discovery. ``build_run_manifest`` is pure: callers provide the
timestamp, git record, and already-hashed inputs. Filesystem and git helpers are
small adapters around that deterministic core.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS


MANIFEST_SCHEMA = "kaetram.run-manifest.v1"
INPUT_PROVENANCE_SCHEMA = "kaetram.input-provenance.v1"
TOOL_SCHEMA_SOURCE = "tool_surface.MODEL_VISIBLE_TOOL_DEFINITIONS"
SHA256_LENGTH = 64
SENSITIVE_ARG_RE = re.compile(
    r"(?i)(?:^|[-_])(?:api[-_]?key|access[-_]?token|auth(?:orization)?|bearer|password|secret)(?:$|[=:_-])"
)
HTTP_URL_RE = re.compile(r"(?i)^https?://")


class ManifestError(ValueError):
    """Raised when provenance is incomplete, inconsistent, or mutable."""


def utc_now() -> str:
    """Return a timezone-aware, second-resolution UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for identity and directory hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != SHA256_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _valid_git_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _sanitize_repository(value: str) -> str:
    """Strip credentials, query strings, and fragments from a Git remote."""
    if "://" in value:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parts.port
        except ValueError as exc:
            raise ManifestError(f"invalid repository URL: {value!r}") from exc
        netloc = f"{hostname}:{port}" if port is not None else hostname
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    # SCP-style Git remotes commonly start with ``git@``. Removing the user
    # preserves repository identity and prevents an accidental token-like user.
    if "@" in value and ":" in value.split("@", 1)[1]:
        user, remainder = value.split("@", 1)
        # ``git@host:owner/repo`` is the standard credential-free SSH form.
        # Other user components may accidentally contain deploy credentials.
        return value if user == "git" else remainder
    return value


def _portable_path(path: Path, root: Path | None) -> str:
    resolved = path.resolve()
    if root is not None:
        root_resolved = root.resolve()
        try:
            return resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            pass
    return str(resolved)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def hash_path(path: str | Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """Hash a file or directory into a portable artifact descriptor.

    Directory identity covers each relative filename, byte length, and file
    digest in sorted order. Symlinks are rejected because their target may be
    outside the bundle or change independently of the recorded path.
    """
    source = Path(path)
    root_path = Path(root) if root is not None else None
    if not source.exists():
        raise ManifestError(f"provenance path does not exist: {source}")
    if source.is_symlink():
        raise ManifestError(f"symlinks are not valid immutable inputs: {source}")

    if source.is_file():
        digest, size = _hash_file(source)
        return {
            "kind": "file",
            "path": _portable_path(source, root_path),
            "sha256": digest,
            "size_bytes": size,
            "file_count": 1,
        }
    if not source.is_dir():
        raise ManifestError(f"only regular files and directories can be hashed: {source}")

    entries: list[dict[str, Any]] = []
    total_size = 0
    for child in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        if child.is_symlink():
            raise ManifestError(f"directory contains a symlink: {child}")
        if not child.is_file():
            continue
        digest, size = _hash_file(child)
        total_size += size
        entries.append({
            "path": child.relative_to(source).as_posix(),
            "sha256": digest,
            "size_bytes": size,
        })
    return {
        "kind": "directory",
        "path": _portable_path(source, root_path),
        "sha256": sha256_json(entries),
        "size_bytes": total_size,
        "file_count": len(entries),
    }


def remote_content(reference: str, sha256: str) -> dict[str, Any]:
    """Describe content that is not mounted locally, such as a Modal checkpoint."""
    if not reference.strip():
        raise ManifestError("remote content requires a non-empty reference")
    if not _valid_sha256(sha256):
        raise ManifestError("remote content requires a lowercase SHA-256 digest")
    return {
        "kind": "remote",
        "reference": reference,
        "sha256": sha256,
        "size_bytes": None,
        "file_count": None,
    }


def tool_schema_record() -> dict[str, Any]:
    """Hash the complete model-visible schema, not only the tool names."""
    return {
        "source": TOOL_SCHEMA_SOURCE,
        "sha256": sha256_json(MODEL_VISIBLE_TOOL_DEFINITIONS),
        "tool_count": len(MODEL_VISIBLE_TOOL_DEFINITIONS),
    }


def capture_git_state(repo_root: str | Path) -> dict[str, Any]:
    """Capture the exact repository revision and dirty paths."""
    root = Path(repo_root).resolve()

    def git(*args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ManifestError(f"git {' '.join(args)} failed: {detail}")
        # Preserve the two leading columns in ``git status --porcelain``.
        # Callers already tolerate an empty trailing value.
        return result.stdout.rstrip("\r\n")

    top = Path(git("rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ManifestError(f"repo root must be the git toplevel: expected {top}, got {root}")
    commit = git("rev-parse", "HEAD")
    if len(commit) != 40:
        raise ManifestError(f"unexpected git commit id: {commit!r}")
    branch = git("symbolic-ref", "--short", "-q", "HEAD", check=False) or None
    dirty_lines = [line for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines() if line]
    dirty_paths = sorted(line[3:] if len(line) > 3 else line for line in dirty_lines)
    repository = git("remote", "get-url", "upstream", check=False)
    if not repository:
        repository = git("remote", "get-url", "origin", check=False)
    repository = _sanitize_repository(repository) if repository else None
    return {
        "repository": repository or None,
        "commit": commit,
        "branch": branch,
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths,
    }


def build_input_provenance(
    *,
    kind: str,
    name: str,
    reference: str,
    content: Mapping[str, Any],
    source: str,
    created_at_utc: str,
    producer_git: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a dataset/checkpoint sidecar without reading global state."""
    if kind not in {"dataset", "checkpoint"}:
        raise ManifestError("input provenance kind must be dataset or checkpoint")
    if not name.strip() or not reference.strip() or not source.strip():
        raise ManifestError("input provenance requires name, reference, and source")
    record = {
        "schema_version": INPUT_PROVENANCE_SCHEMA,
        "kind": kind,
        "name": name,
        "reference": reference,
        "content": dict(content),
        "source": source,
        "created_at_utc": created_at_utc,
        "producer_git": dict(producer_git),
    }
    errors = validate_input_provenance(record)
    if errors:
        raise ManifestError("; ".join(errors))
    return record


def validate_input_provenance(record: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["input provenance must be a JSON object"]
    if record.get("schema_version") != INPUT_PROVENANCE_SCHEMA:
        errors.append(f"input provenance schema_version must be {INPUT_PROVENANCE_SCHEMA}")
    if record.get("kind") not in {"dataset", "checkpoint"}:
        errors.append("input provenance kind must be dataset or checkpoint")
    for field in ("name", "reference", "source", "created_at_utc"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(f"input provenance {field} must be a non-empty string")
    content = record.get("content")
    if not isinstance(content, dict) or not _valid_sha256(content.get("sha256")):
        errors.append("input provenance content must contain a lowercase SHA-256")
    elif record.get("kind") == "dataset" and content.get("kind") not in {"file", "directory"}:
        errors.append("dataset content must be a locally hashed file or directory")
    elif content.get("kind") in {"file", "directory"}:
        if not isinstance(content.get("path"), str) or not content["path"].strip():
            errors.append("local input provenance content must have a path")
        if not isinstance(content.get("size_bytes"), int) or content["size_bytes"] <= 0:
            errors.append("local input provenance content must be non-empty")
    git = record.get("producer_git")
    if not isinstance(git, dict) or not _valid_git_sha(git.get("commit")):
        errors.append("input provenance producer_git.commit must be a full git SHA")
    return errors


def _input_bundle(record: Mapping[str, Any], provenance_file: Mapping[str, Any]) -> dict[str, Any]:
    return {"record": dict(record), "provenance_file": dict(provenance_file)}


def build_run_manifest(
    *,
    run: Mapping[str, Any],
    source_git: Mapping[str, Any],
    prompt: Mapping[str, Any],
    config: Mapping[str, Any],
    dataset_record: Mapping[str, Any],
    dataset_provenance_file: Mapping[str, Any],
    checkpoint_record: Mapping[str, Any],
    checkpoint_provenance_file: Mapping[str, Any],
    artifacts: Iterable[Mapping[str, Any]],
    reproduction_argv: Iterable[str],
    created_at_utc: str,
) -> dict[str, Any]:
    """Build a deterministic v1 run manifest from explicit inputs."""
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "created_at_utc": created_at_utc,
        "run": dict(run),
        "source_git": dict(source_git),
        "provenance": {
            "prompt": dict(prompt),
            "tool_schema": tool_schema_record(),
            "config": dict(config),
            "dataset": _input_bundle(dataset_record, dataset_provenance_file),
            "checkpoint": _input_bundle(checkpoint_record, checkpoint_provenance_file),
        },
        "artifacts": [dict(item) for item in artifacts],
        "reproduction": {"argv": list(reproduction_argv)},
    }
    errors = validate_manifest_shape(manifest, check_identity=False)
    if errors:
        raise ManifestError("; ".join(errors))
    manifest["manifest_sha256"] = sha256_json(manifest)
    return manifest


def _descriptor_errors(descriptor: object, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(descriptor, dict):
        return [f"{label} must be an artifact descriptor"]
    if descriptor.get("kind") not in {"file", "directory", "remote"}:
        errors.append(f"{label}.kind must be file, directory, or remote")
    if not _valid_sha256(descriptor.get("sha256")):
        errors.append(f"{label}.sha256 must be a lowercase SHA-256")
    if descriptor.get("kind") == "remote":
        if not isinstance(descriptor.get("reference"), str) or not descriptor["reference"].strip():
            errors.append(f"{label}.reference must be non-empty for remote content")
    elif not isinstance(descriptor.get("path"), str) or not descriptor["path"].strip():
        errors.append(f"{label}.path must be non-empty")
    return errors


def _reproduction_argv_errors(argv: object) -> list[str]:
    """Reject credentials and live endpoints from portable manifests.

    This intentionally errs on the side of false positives. Reproduction
    commands must name environment variables or local configuration files,
    never embed their resolved values.
    """
    if not isinstance(argv, list):
        return []
    errors: list[str] = []
    for index, arg in enumerate(argv):
        if not isinstance(arg, str):
            continue
        if HTTP_URL_RE.match(arg):
            errors.append(
                f"reproduction.argv[{index}] must not contain an HTTP(S) endpoint; "
                "use an environment variable name or local config path"
            )
        if SENSITIVE_ARG_RE.search(arg):
            errors.append(
                f"reproduction.argv[{index}] looks secret-bearing; "
                "use an environment variable name or local config path"
            )
    return errors


def validate_manifest_shape(manifest: object, *, check_identity: bool = True) -> list[str]:
    """Validate the v1 schema without touching files or git."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        errors.append(f"schema_version must be {MANIFEST_SCHEMA}")
    if not isinstance(manifest.get("created_at_utc"), str):
        errors.append("created_at_utc must be a string")

    run = manifest.get("run")
    if not isinstance(run, dict):
        errors.append("run must be an object")
    else:
        for field in ("run_id", "model", "harness", "scenario"):
            if not isinstance(run.get(field), str) or not run[field].strip():
                errors.append(f"run.{field} must be a non-empty string")
        if not isinstance(run.get("recovery_enabled"), bool):
            errors.append("run.recovery_enabled must be boolean")

    git = manifest.get("source_git")
    if not isinstance(git, dict):
        errors.append("source_git must be an object")
    else:
        if not _valid_git_sha(git.get("commit")):
            errors.append("source_git.commit must be a full git SHA")
        repository = git.get("repository")
        if not isinstance(repository, str) or not repository.strip():
            errors.append("source_git.repository must be a non-empty clone source")
        else:
            try:
                sanitized_repository = _sanitize_repository(repository)
            except ManifestError as exc:
                errors.append(str(exc))
            else:
                if sanitized_repository != repository:
                    errors.append(
                        "source_git.repository must not contain credentials, query strings, or fragments"
                    )
        if not isinstance(git.get("dirty"), bool):
            errors.append("source_git.dirty must be boolean")
        if not isinstance(git.get("dirty_paths"), list):
            errors.append("source_git.dirty_paths must be a list")

    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        errors.extend(_descriptor_errors(provenance.get("prompt"), "provenance.prompt"))
        errors.extend(_descriptor_errors(provenance.get("config"), "provenance.config"))
        schema = provenance.get("tool_schema")
        if not isinstance(schema, dict) or not _valid_sha256(schema.get("sha256")):
            errors.append("provenance.tool_schema must contain a lowercase SHA-256")
        for kind in ("dataset", "checkpoint"):
            bundle = provenance.get(kind)
            if not isinstance(bundle, dict):
                errors.append(f"provenance.{kind} must be an object")
                continue
            record = bundle.get("record")
            errors.extend(
                f"provenance.{kind}.record: {error}" for error in validate_input_provenance(record)
            )
            if isinstance(record, dict) and record.get("kind") != kind:
                errors.append(f"provenance.{kind}.record.kind must be {kind}")
            errors.extend(_descriptor_errors(
                bundle.get("provenance_file"), f"provenance.{kind}.provenance_file"
            ))

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts must contain at least one hashed output")
    else:
        names: set[str] = set()
        for index, artifact in enumerate(artifacts):
            label = f"artifacts[{index}]"
            errors.extend(_descriptor_errors(artifact, label))
            if not isinstance(artifact, dict):
                continue
            name = artifact.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{label}.name must be non-empty")
            elif name in names:
                errors.append(f"duplicate artifact name: {name}")
            else:
                names.add(name)

    reproduction = manifest.get("reproduction")
    argv = reproduction.get("argv") if isinstance(reproduction, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
        errors.append("reproduction.argv must be a non-empty string array")
    else:
        errors.extend(_reproduction_argv_errors(argv))

    if check_identity:
        identity = manifest.get("manifest_sha256")
        if not _valid_sha256(identity):
            errors.append("manifest_sha256 must be a lowercase SHA-256")
        else:
            payload = dict(manifest)
            payload.pop("manifest_sha256", None)
            if sha256_json(payload) != identity:
                errors.append("manifest_sha256 does not match manifest contents")
    return errors


def resolve_descriptor_path(descriptor: Mapping[str, Any], root: str | Path) -> Path | None:
    if descriptor.get("kind") == "remote":
        return None
    raw = Path(str(descriptor["path"]))
    return raw if raw.is_absolute() else Path(root) / raw


def verify_descriptor(descriptor: Mapping[str, Any], root: str | Path, label: str) -> list[str]:
    if descriptor.get("kind") == "remote":
        return []
    path = resolve_descriptor_path(descriptor, root)
    try:
        actual = hash_path(path, root=root)
    except ManifestError as exc:
        return [f"{label}: {exc}"]
    errors = []
    for field in ("kind", "sha256", "size_bytes", "file_count"):
        if actual.get(field) != descriptor.get(field):
            errors.append(
                f"{label}.{field} mismatch: recorded={descriptor.get(field)!r}, actual={actual.get(field)!r}"
            )
    return errors


def validate_manifest_files(manifest: Mapping[str, Any], root: str | Path) -> list[str]:
    """Verify all local inputs, sidecars, and outputs against the manifest."""
    errors: list[str] = []
    provenance = manifest["provenance"]
    errors.extend(verify_descriptor(provenance["prompt"], root, "provenance.prompt"))
    errors.extend(verify_descriptor(provenance["config"], root, "provenance.config"))
    if provenance["tool_schema"] != tool_schema_record():
        errors.append("provenance.tool_schema does not match the current full tool schema")

    for kind in ("dataset", "checkpoint"):
        bundle = provenance[kind]
        record = bundle["record"]
        errors.extend(verify_descriptor(
            bundle["provenance_file"], root, f"provenance.{kind}.provenance_file"
        ))
        sidecar_path = resolve_descriptor_path(bundle["provenance_file"], root)
        if sidecar_path is not None and sidecar_path.exists():
            try:
                sidecar_record = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"provenance.{kind}.provenance_file is not valid JSON: {exc}")
            else:
                if sidecar_record != record:
                    errors.append(f"provenance.{kind}.record differs from its sidecar file")
        errors.extend(verify_descriptor(
            record["content"], root, f"provenance.{kind}.record.content"
        ))

    for index, artifact in enumerate(manifest["artifacts"]):
        errors.extend(verify_descriptor(artifact, root, f"artifacts[{index}]"))
    return errors


def validate_git_constraints(
    manifest: Mapping[str, Any],
    root: str | Path,
    *,
    require_commit_match: bool,
    require_clean: bool,
) -> list[str]:
    if not require_commit_match and not require_clean:
        return []
    try:
        current = capture_git_state(root)
    except ManifestError as exc:
        return [str(exc)]
    errors: list[str] = []
    recorded = manifest["source_git"]
    if require_commit_match and current["commit"] != recorded["commit"]:
        errors.append(
            f"git commit mismatch: recorded={recorded['commit']}, current={current['commit']}"
        )
    if require_clean:
        if recorded["dirty"]:
            errors.append("manifest was created from a dirty git worktree")
        if current["dirty"]:
            errors.append(f"git worktree is dirty: {', '.join(current['dirty_paths'])}")
    return errors


def load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text())
    except OSError as exc:
        raise ManifestError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in {path}: {exc}") from exc


def atomic_write_json(path: str | Path, value: Any) -> None:
    """Create a JSON artifact atomically and refuse to overwrite it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ManifestError(f"refusing to overwrite immutable file: {target}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError as exc:
            raise ManifestError(f"refusing to overwrite immutable file: {target}") from exc
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)
