#!/usr/bin/env python3
"""Create the exact local-only Kaetram MongoDB dotenv override."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


DOTENV = """DATABASE=mongodb
SKIP_DATABASE=false
MONGODB_HOST=127.0.0.1
MONGODB_PORT=27017
MONGODB_DATABASE=kaetram_devlopment
MONGODB_TLS=false
MONGODB_SRV=false
MONGODB_USER=
MONGODB_PASSWORD=
MONGODB_AUTH_SOURCE=
"""


class ConfigurationError(RuntimeError):
    """The target cannot be configured without overwriting local state."""


def configure(game_dir: Path) -> Path:
    invoked = game_dir.expanduser()
    if invoked.is_symlink():
        raise ConfigurationError(f"refusing symlinked game directory: {invoked}")
    target = invoked.resolve()
    defaults = target / ".env.defaults"
    output = target / ".env"
    if not target.is_dir():
        raise ConfigurationError(f"game directory does not exist: {target}")
    if not defaults.is_file() or defaults.is_symlink():
        raise ConfigurationError(f"regular .env.defaults is required: {defaults}")
    if output.exists() or output.is_symlink():
        raise ConfigurationError(
            f"refusing to overwrite existing game configuration: {output}"
        )
    with output.open("x", encoding="utf-8") as handle:
        handle.write(DOTENV)
    print(
        f"{output} sha256={hashlib.sha256(DOTENV.encode()).hexdigest()}"
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        configure(args.game_dir)
    except (ConfigurationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
