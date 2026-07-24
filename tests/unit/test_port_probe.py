from __future__ import annotations

import socket

import pytest

import eval_harness
from port_probe import is_tcp_port_open


def test_port_probe_detects_open_and_closed_local_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    try:
        assert is_tcp_port_open("127.0.0.1", port)
    finally:
        listener.close()

    assert not is_tcp_port_open("127.0.0.1", port)


@pytest.mark.parametrize("port", [0, -1, 65_536])
def test_port_probe_rejects_invalid_ports(port):
    with pytest.raises(ValueError, match="between 1 and 65535"):
        is_tcp_port_open("127.0.0.1", port)


def test_require_node20_accepts_explicit_binary(monkeypatch, tmp_path):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nprintf 'v20.19.4\\n'\n")
    node.chmod(0o755)
    monkeypatch.setenv("KAETRAM_NODE_BINARY", str(node))

    assert eval_harness.require_node20_binary() == str(node)


def test_require_node20_rejects_other_major(monkeypatch, tmp_path):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nprintf 'v22.1.0\\n'\n")
    node.chmod(0o755)
    monkeypatch.setenv("KAETRAM_NODE_BINARY", str(node))

    with pytest.raises(RuntimeError, match="requires Node 20"):
        eval_harness.require_node20_binary()
