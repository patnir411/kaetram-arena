"""Shared validation and derivation for deterministic inference sampling."""
from __future__ import annotations

import hashlib


MAX_INFERENCE_SEED = 2**31 - 1
REQUEST_SEED_ALGORITHM = "sha256-session-turn-v1"


def validate_inference_seed(value: object, *, label: str = "inference seed") -> int:
    """Return a valid non-negative 31-bit seed or raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not 0 <= value <= MAX_INFERENCE_SEED:
        raise ValueError(f"{label} must be between 0 and {MAX_INFERENCE_SEED}")
    return value


def derive_request_seed(base_seed: int, session: int, turn: int) -> int:
    """Derive a stable request seed without reusing one across warm sessions/turns."""
    seed = validate_inference_seed(base_seed)
    if isinstance(session, bool) or not isinstance(session, int) or session < 1:
        raise ValueError("session must be a positive integer")
    if isinstance(turn, bool) or not isinstance(turn, int) or turn < 1:
        raise ValueError("turn must be a positive integer")
    material = f"{REQUEST_SEED_ALGORITHM}:{seed}:{session}:{turn}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big") & MAX_INFERENCE_SEED
