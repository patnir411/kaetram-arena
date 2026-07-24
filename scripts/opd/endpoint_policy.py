"""Zero-spend endpoint policy for experiment probes."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class EndpointPolicyError(ValueError):
    """Raised when a probe could contact a non-local or ambiguous endpoint."""


def require_zero_spend_endpoints(
    endpoints: list[str],
    *,
    allow_metered_remote_endpoints: bool = False,
) -> list[str]:
    """Validate URLs and, by default, require every resolved address to loop back."""
    normalized: list[str] = []
    for raw in endpoints:
        value = raw.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EndpointPolicyError(f"endpoint must be an http(s) URL: {raw!r}")
        if parsed.username or parsed.password:
            raise EndpointPolicyError("endpoint URLs must not contain credentials")
        if parsed.query or parsed.fragment:
            raise EndpointPolicyError("endpoint URLs must not contain a query or fragment")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise EndpointPolicyError(f"endpoint has an invalid port: {raw!r}") from exc

        if not allow_metered_remote_endpoints:
            host = parsed.hostname
            try:
                addresses = {ipaddress.ip_address(host)}
            except ValueError:
                try:
                    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                except socket.gaierror as exc:
                    raise EndpointPolicyError(
                        f"endpoint host did not resolve locally: {host!r}"
                    ) from exc
                addresses = {ipaddress.ip_address(item[4][0]) for item in answers}
            if not addresses or not all(address.is_loopback for address in addresses):
                rendered = ", ".join(sorted(str(address) for address in addresses))
                raise EndpointPolicyError(
                    f"zero-spend policy rejected non-loopback endpoint {raw!r} "
                    f"(resolved: {rendered}); only use "
                    "--allow-metered-remote-endpoints with explicit authorization"
                )
        normalized.append(value)
    return normalized
