"""Kaetram AI Agent Dashboard — modular package.

The server import is intentionally lazy. Evaluation only needs
``dashboard.db``'s pure Mongo summarizers and must not require the optional
WebSocket/UI dependency stack.
"""


def start_dashboard(*args, **kwargs):
    from dashboard.server import start_dashboard as _start_dashboard

    return _start_dashboard(*args, **kwargs)


__all__ = ["start_dashboard"]
