"""Ephemeral port reservation shared across the test suite.

Deliberately a plain module, matching ``tests/paths.py`` and
``tests/loaders.py`` — a single, tiny, import-time helper, not conftest
fixtures.
"""

from __future__ import annotations

import socket

__all__ = ["reserve_port"]


def reserve_port(host: str = "127.0.0.1") -> int:
    """Take one ephemeral port from the OS, then release it for the caller.

    The close-then-rebind gap is the standard ephemeral-port reservation
    race — acceptable for a single local server or subprocess. For a
    dual-port case with host-specific quirks (e.g. Parlant's hardcoded
    tool-service host), see
    ``band.integrations.parlant.ports.reserve_server_ports`` instead — that
    one is shaped around Parlant's own constraints, not a general-purpose
    counterpart to this.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]
