"""Fail-closed promotion and resolution of the latest evaluation run."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath


POINTER_NAME = "latest-run.txt"
_RUN_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_MAX_POINTER_BYTES = 512


class LatestEvalPointerError(ValueError):
    """Raised when the latest-run pointer is unsafe or malformed."""


def _eval_root(eval_dir: str | os.PathLike[str]) -> Path:
    raw_root = Path(eval_dir)
    if raw_root.is_symlink():
        raise LatestEvalPointerError("evaluation root must not be a symlink")
    try:
        root = raw_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise LatestEvalPointerError("evaluation root does not exist") from exc
    if not root.is_dir():
        raise LatestEvalPointerError("evaluation root is not a directory")
    runs_root = root / "runs"
    if runs_root.is_symlink() or not runs_root.is_dir():
        raise LatestEvalPointerError("evaluation runs root must be a real directory")
    return root


def _parse_pointer(value: str) -> PurePosixPath:
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise LatestEvalPointerError("latest-run pointer must contain exactly one path")
    relative = PurePosixPath(lines[0])
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "runs"
        or not _RUN_TAG_RE.fullmatch(relative.parts[1])
    ):
        raise LatestEvalPointerError(
            "latest-run pointer must be a direct runs/<run-tag> path"
        )
    return relative


def _validate_run_target(root: Path, relative: PurePosixPath) -> Path:
    runs_root = root / "runs"
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink():
        raise LatestEvalPointerError("latest evaluation run must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise LatestEvalPointerError("latest evaluation run does not exist") from exc
    if not resolved.is_dir() or resolved.parent != runs_root.resolve(strict=True):
        raise LatestEvalPointerError(
            "latest evaluation run must be a direct child of the runs directory"
        )
    return resolved


def resolve_latest_eval_dir(
    eval_dir: str | os.PathLike[str],
) -> Path | None:
    """Resolve a validated regular-file pointer, or return None when absent."""

    root = _eval_root(eval_dir)
    pointer = root / POINTER_NAME
    try:
        pointer_stat = pointer.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(pointer_stat.st_mode):
        raise LatestEvalPointerError("latest-run pointer must be a regular file")
    if pointer_stat.st_size > _MAX_POINTER_BYTES:
        raise LatestEvalPointerError("latest-run pointer is unexpectedly large")
    relative = _parse_pointer(pointer.read_text(encoding="utf-8"))
    return _validate_run_target(root, relative)


def promote_latest_eval_run(
    eval_dir: str | os.PathLike[str],
    run_dir: str | os.PathLike[str],
) -> str:
    """Atomically promote one direct child of ``runs`` using a text pointer."""

    root = _eval_root(eval_dir)
    raw_run = Path(run_dir)
    if raw_run.is_symlink():
        raise LatestEvalPointerError("latest evaluation run must not be a symlink")
    try:
        resolved_run = raw_run.resolve(strict=True)
        relative_path = resolved_run.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise LatestEvalPointerError(
            "run directory must exist beneath the evaluation root"
        ) from exc
    relative = _parse_pointer(PurePosixPath(relative_path).as_posix())
    _validate_run_target(root, relative)

    pointer = root / POINTER_NAME
    if pointer.is_symlink():
        raise LatestEvalPointerError("refusing to replace a symlink pointer")

    payload = f"{relative.as_posix()}\n".encode("utf-8")
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=".latest-run.", suffix=".tmp", dir=root
    )
    try:
        os.fchmod(temporary_fd, 0o644)
        with os.fdopen(temporary_fd, "wb", closefd=True) as temporary_file:
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_fd = -1
        os.replace(temporary_name, pointer)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass

    if not stat.S_ISREG(pointer.lstat().st_mode):
        raise LatestEvalPointerError("promoted latest-run pointer is not regular")
    return relative.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote = subparsers.add_parser("promote")
    promote.add_argument("--eval-dir", required=True)
    promote.add_argument("--run-dir", required=True)
    args = parser.parse_args()

    if args.command == "promote":
        print(promote_latest_eval_run(args.eval_dir, args.run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
