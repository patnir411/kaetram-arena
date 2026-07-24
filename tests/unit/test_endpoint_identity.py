from __future__ import annotations

import pytest

from finetune.endpoint_identity import endpoint_attestation


def test_endpoint_identity_is_unresolved_when_no_deployment_values_exist() -> None:
    assert endpoint_attestation("2b-base", {}) is None


def test_endpoint_identity_requires_a_complete_deployment_record() -> None:
    with pytest.raises(RuntimeError, match="partial endpoint identity"):
        endpoint_attestation(
            "2b-base",
            {"KAETRAM_ENDPOINT_DEPLOYMENT_ID": "modal:kaetram-qwen-2b"},
        )


@pytest.mark.parametrize(
    "field",
    [
        "KAETRAM_ENDPOINT_CHECKPOINT_SHA256",
        "KAETRAM_ENDPOINT_TOKENIZER_SHA256",
        "KAETRAM_ENDPOINT_RENDER_CONTRACT_SHA256",
    ],
)
def test_endpoint_identity_rejects_malformed_digests(field: str) -> None:
    environ = {
        "KAETRAM_ENDPOINT_DEPLOYMENT_ID": "modal:kaetram-qwen-2b",
        "KAETRAM_ENDPOINT_CHECKPOINT_SHA256": "a" * 64,
        "KAETRAM_ENDPOINT_TOKENIZER_SHA256": "b" * 64,
        "KAETRAM_ENDPOINT_RENDER_CONTRACT_SHA256": "c" * 64,
    }
    environ[field] = "not-a-digest"
    with pytest.raises(RuntimeError, match="lowercase SHA-256"):
        endpoint_attestation("2b-base", environ)


def test_endpoint_identity_returns_launcher_compatible_payload() -> None:
    assert endpoint_attestation(
        "2b-base",
        {
            "KAETRAM_ENDPOINT_DEPLOYMENT_ID": "modal:kaetram-qwen-2b@abc123",
            "KAETRAM_ENDPOINT_CHECKPOINT_SHA256": "a" * 64,
            "KAETRAM_ENDPOINT_TOKENIZER_SHA256": "b" * 64,
            "KAETRAM_ENDPOINT_RENDER_CONTRACT_SHA256": "c" * 64,
        },
    ) == {
        "deployment_id": "modal:kaetram-qwen-2b@abc123",
        "api_model": "2b-base",
        "checkpoint_sha256": "a" * 64,
        "tokenizer_sha256": "b" * 64,
        "render_contract_sha256": "c" * 64,
    }
