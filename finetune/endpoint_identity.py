"""Fail-closed identity payloads for deployed model endpoints.

The values are intentionally supplied at deployment time rather than inferred
from model labels or mutable paths. They are public provenance identifiers, not
credentials. An incomplete identity returns ``None`` so `/health` remains
useful for operations while the confirmatory launcher refuses the endpoint.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping


SHA256_RE = re.compile(r"[0-9a-f]{64}")
ENV_KEYS = {
    "deployment_id": "KAETRAM_ENDPOINT_DEPLOYMENT_ID",
    "checkpoint_sha256": "KAETRAM_ENDPOINT_CHECKPOINT_SHA256",
    "tokenizer_sha256": "KAETRAM_ENDPOINT_TOKENIZER_SHA256",
    "render_contract_sha256": "KAETRAM_ENDPOINT_RENDER_CONTRACT_SHA256",
}


def endpoint_attestation(
    api_model: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Return an exact endpoint identity, or ``None`` when unresolved.

    Partially configured or malformed identities raise immediately. This
    prevents a deployment typo from silently looking like a merely unresolved
    endpoint.
    """
    source = os.environ if environ is None else environ
    values = {field: source.get(env_key, "").strip() for field, env_key in ENV_KEYS.items()}
    present = {field for field, value in values.items() if value}
    if not present:
        return None
    missing = sorted(set(ENV_KEYS) - present)
    if missing:
        raise RuntimeError(
            "partial endpoint identity; missing deployment values for "
            + ", ".join(missing)
        )
    if not api_model.strip():
        raise RuntimeError("endpoint api_model must be non-empty")
    if any(
        not SHA256_RE.fullmatch(values[field])
        for field in ("checkpoint_sha256", "tokenizer_sha256", "render_contract_sha256")
    ):
        raise RuntimeError("endpoint identity digests must be lowercase SHA-256 values")
    return {
        "deployment_id": values["deployment_id"],
        "api_model": api_model,
        "checkpoint_sha256": values["checkpoint_sha256"],
        "tokenizer_sha256": values["tokenizer_sha256"],
        "render_contract_sha256": values["render_contract_sha256"],
    }
