#!/usr/bin/env python3
"""Create and validate immutable Kaetram run provenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from run_manifest import (  # noqa: E402
    ManifestError,
    atomic_write_json,
    build_input_provenance,
    build_run_manifest,
    capture_git_state,
    hash_path,
    load_json,
    remote_content,
    utc_now,
    validate_git_constraints,
    validate_input_provenance,
    validate_manifest_files,
    validate_manifest_shape,
)


def _parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name or not raw_path:
        raise argparse.ArgumentTypeError("expected non-empty NAME=PATH")
    return name, Path(raw_path)


def _require_clean(git: dict, allow_dirty: bool) -> None:
    if git["dirty"] and not allow_dirty:
        raise ManifestError(
            "refusing to create provenance from a dirty worktree; "
            "commit/stash changes or pass --allow-dirty for an explicitly dirty record"
        )


def cmd_hash(args: argparse.Namespace) -> int:
    print(json.dumps(hash_path(args.path, root=args.root), indent=2, sort_keys=True))
    return 0


def cmd_input(args: argparse.Namespace) -> int:
    if args.kind == "dataset" and args.path is None:
        raise ManifestError("dataset provenance requires --path; remote-only datasets are not verifiable")
    git = capture_git_state(args.repo_root)
    _require_clean(git, args.allow_dirty)
    content = (
        hash_path(args.path, root=args.artifact_root)
        if args.path is not None
        else remote_content(args.reference, args.sha256)
    )
    record = build_input_provenance(
        kind=args.kind,
        name=args.name,
        reference=args.reference,
        content=content,
        source=args.source,
        created_at_utc=utc_now(),
        producer_git=git,
    )
    atomic_write_json(args.output, record)
    print(f"created immutable {args.kind} provenance: {args.output}")
    return 0


def _load_provenance(path: Path, kind: str, root: Path) -> tuple[dict, dict]:
    record = load_json(path)
    errors = validate_input_provenance(record)
    if errors:
        raise ManifestError(f"invalid {kind} provenance {path}: {'; '.join(errors)}")
    if record["kind"] != kind:
        raise ManifestError(f"{path} describes {record['kind']!r}, expected {kind!r}")
    descriptor = hash_path(path, root=root)
    content = record["content"]
    if content["kind"] != "remote":
        content_path = Path(content["path"])
        if not content_path.is_absolute():
            content_path = root / content_path
        actual = hash_path(content_path, root=root)
        if actual["sha256"] != content["sha256"]:
            raise ManifestError(
                f"{kind} content digest changed: recorded={content['sha256']}, actual={actual['sha256']}"
            )
    return record, descriptor


def cmd_create(args: argparse.Namespace) -> int:
    artifact_root = args.artifact_root.resolve()
    git = capture_git_state(args.repo_root.resolve())
    _require_clean(git, args.allow_dirty)
    dataset, dataset_file = _load_provenance(
        args.dataset_provenance, "dataset", artifact_root
    )
    checkpoint, checkpoint_file = _load_provenance(
        args.checkpoint_provenance, "checkpoint", artifact_root
    )
    for kind, record in (("dataset", dataset), ("checkpoint", checkpoint)):
        if record["producer_git"].get("dirty") and not args.allow_dirty:
            raise ManifestError(
                f"refusing {kind} provenance produced from a dirty worktree; "
                "rebuild it from a clean commit or pass --allow-dirty for diagnostics"
            )
    try:
        reproduction_argv = json.loads(args.reproduce_argv)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"--reproduce-argv must be a JSON array: {exc}") from exc
    if not isinstance(reproduction_argv, list):
        raise ManifestError("--reproduce-argv must be a JSON array")

    artifacts = []
    for name, path in args.artifact:
        descriptor = hash_path(path, root=artifact_root)
        descriptor["name"] = name
        artifacts.append(descriptor)
    manifest = build_run_manifest(
        run={
            "run_id": args.run_id,
            "model": args.model,
            "harness": args.harness,
            "scenario": args.scenario,
            "recovery_enabled": args.recovery_enabled,
        },
        source_git=git,
        prompt=hash_path(args.prompt, root=artifact_root),
        config=hash_path(args.config, root=artifact_root),
        dataset_record=dataset,
        dataset_provenance_file=dataset_file,
        checkpoint_record=checkpoint,
        checkpoint_provenance_file=checkpoint_file,
        artifacts=artifacts,
        reproduction_argv=reproduction_argv,
        created_at_utc=utc_now(),
    )
    atomic_write_json(args.output, manifest)
    print(f"created immutable run manifest: {args.output}")
    print(f"manifest_sha256: {manifest['manifest_sha256']}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_json(args.manifest)
    errors = validate_manifest_shape(manifest)
    if not errors and not args.structure_only:
        errors.extend(validate_manifest_files(manifest, args.artifact_root))
        errors.extend(validate_git_constraints(
            manifest,
            args.repo_root,
            require_commit_match=args.require_git_match,
            require_clean=args.require_clean,
        ))
    if errors:
        print("manifest validation FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"manifest valid: {args.manifest}")
    print(f"manifest_sha256: {manifest['manifest_sha256']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    hash_parser = sub.add_parser("hash", help="hash a file or directory")
    hash_parser.add_argument("path", type=Path)
    hash_parser.add_argument("--root", type=Path, default=REPO_ROOT)
    hash_parser.set_defaults(func=cmd_hash)

    input_parser = sub.add_parser("input", help="create dataset/checkpoint provenance")
    input_parser.add_argument("--kind", choices=("dataset", "checkpoint"), required=True)
    input_parser.add_argument("--name", required=True)
    input_parser.add_argument("--reference", required=True)
    input_parser.add_argument("--source", required=True)
    content = input_parser.add_mutually_exclusive_group(required=True)
    content.add_argument("--path", type=Path)
    content.add_argument("--sha256")
    input_parser.add_argument("--output", type=Path, required=True)
    input_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    input_parser.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    input_parser.add_argument("--allow-dirty", action="store_true")
    input_parser.set_defaults(func=cmd_input)

    create = sub.add_parser("create", help="seal a completed run manifest")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    create.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    create.add_argument("--run-id", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--harness", required=True)
    create.add_argument("--scenario", required=True)
    create.add_argument("--recovery-enabled", action="store_true")
    create.add_argument("--prompt", type=Path, required=True)
    create.add_argument("--config", type=Path, required=True)
    create.add_argument("--dataset-provenance", type=Path, required=True)
    create.add_argument("--checkpoint-provenance", type=Path, required=True)
    create.add_argument(
        "--artifact", action="append", type=_parse_named_path, required=True,
        metavar="NAME=PATH", help="hashed output artifact; repeat for every output",
    )
    create.add_argument(
        "--reproduce-argv", required=True,
        help='JSON argv array, e.g. ["python3","scripts/analyze.py","--run","..."]',
    )
    create.add_argument("--allow-dirty", action="store_true")
    create.set_defaults(func=cmd_create)

    validate = sub.add_parser("validate", help="validate schema and provenance hashes")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    validate.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    validate.add_argument("--structure-only", action="store_true")
    validate.add_argument("--require-git-match", action="store_true")
    validate.add_argument("--require-clean", action="store_true")
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
