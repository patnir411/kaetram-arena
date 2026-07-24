from __future__ import annotations

import pytest

from eval_harness import normalize_server_port


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("9001", 9001),
        (9001, 9001),
        ("1", 1),
        (65_535, 65_535),
    ],
)
def test_normalize_server_port_accepts_cli_and_manifest_values(
    value: str | int,
    expected: int,
) -> None:
    assert normalize_server_port(value) == expected


@pytest.mark.parametrize("value", ["", "not-a-port", 0, "65536", None])
def test_normalize_server_port_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="server port"):
        normalize_server_port(value)  # type: ignore[arg-type]
