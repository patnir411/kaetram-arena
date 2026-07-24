"""Small cross-platform TCP listener probe used by local launchers."""

from __future__ import annotations

import argparse
import socket


def is_tcp_port_open(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 0.25,
) -> bool:
    """Return whether a TCP listener accepts connections at ``host:port``."""
    if not 1 <= port <= 65_535:
        raise ValueError(f"port must be between 1 and 65535, got {port}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--timeout-seconds", default=0.25, type=float)
    args = parser.parse_args()

    try:
        is_open = is_tcp_port_open(
            args.host,
            args.port,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))

    return 0 if is_open else 1


if __name__ == "__main__":
    raise SystemExit(main())
