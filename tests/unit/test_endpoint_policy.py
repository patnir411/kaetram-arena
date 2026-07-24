from __future__ import annotations

import pytest

from scripts.opd.endpoint_policy import (
    EndpointPolicyError,
    require_zero_spend_endpoints,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8101/v1",
        "http://[::1]:8102/v1/",
        "http://localhost:8103/v1",
    ],
)
def test_zero_spend_policy_accepts_loopback_only(url: str) -> None:
    assert require_zero_spend_endpoints([url]) == [url.rstrip("/")]


def test_zero_spend_policy_rejects_remote_literal() -> None:
    with pytest.raises(EndpointPolicyError, match="non-loopback"):
        require_zero_spend_endpoints(["https://203.0.113.1/v1"])


def test_zero_spend_policy_requires_explicit_remote_authorization() -> None:
    endpoint = "https://203.0.113.1/v1"
    assert require_zero_spend_endpoints(
        [endpoint], allow_metered_remote_endpoints=True
    ) == [endpoint]


@pytest.mark.parametrize(
    "url,match",
    [
        ("ftp://127.0.0.1/x", r"http\(s\)"),
        ("http://user:secret@127.0.0.1/x", "credentials"),
        ("http://127.0.0.1/x?token=secret", "query"),
        ("http://127.0.0.1:99999/x", "invalid port"),
    ],
)
def test_zero_spend_policy_rejects_ambiguous_or_secret_urls(
    url: str, match: str
) -> None:
    with pytest.raises(EndpointPolicyError, match=match):
        require_zero_spend_endpoints([url])
