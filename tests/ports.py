"""Ephemeral port reservation shared across the test suite.

Deliberately a plain module, matching ``tests/paths.py`` and
``tests/loaders.py`` — a single, tiny, import-time helper, not conftest
fixtures.

``reserve_port`` is the close-then-rebind form: it returns a number the
OS just reported free, then releases the socket so the caller can bind
it. That gap is the standard ephemeral-port race — acceptable for a
single local server when the suite is serial. Concurrent workers need
``HeldPort`` / ``HeldAdjacentPorts``, which keep the socket(s) bound
until ``release()``. Callers that then rebind *by number* (an MCP
server given ``port_min``) still have a residual race after release;
holding only shrinks the window in which another bind(0) can steal the
port. It does not claim share-nothing for those servers.

For a dual-port case with host-specific quirks (e.g. Parlant's hardcoded
tool-service host), see ``band.integrations.parlant.ports.reserve_server_ports``
instead — that one is shaped around Parlant's own constraints, not a
general-purpose counterpart to this.
"""

from __future__ import annotations

import socket
from typing import Self

HIGHEST_TCP_PORT = 65535
ADJACENT_PORT_ATTEMPTS = 100

__all__ = [
    "ADJACENT_PORT_ATTEMPTS",
    "HIGHEST_TCP_PORT",
    "HeldAdjacentPorts",
    "HeldPort",
    "reserve_port",
]


def reserve_port(host: str = "127.0.0.1") -> int:
    """Take one ephemeral port from the OS, then release it for the caller."""
    with HeldPort(host) as held:
        return held.port


class HeldPort:
    """An ephemeral TCP port kept bound until ``release()``.

    ``bind((host, 0))`` on a live socket is what stops another process's
    ``bind((host, 0))`` from receiving the same number. Closing the
    socket (via ``release`` or context-manager exit) returns it to the OS.
    """

    def __init__(self, host: str = "127.0.0.1", *, port: int = 0) -> None:
        self.host = host
        self._sock: socket.socket | None = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM
        )
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]

    def release(self) -> None:
        """Close the holding socket if it is still open."""
        sock = self._sock
        if sock is None:
            return
        self._sock = None
        sock.close()

    @property
    def held(self) -> bool:
        """Whether the socket is still occupying ``port``."""
        return self._sock is not None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class HeldAdjacentPorts:
    """``port`` and ``port + 1`` kept bound until ``release()``."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        *,
        attempts: int = ADJACENT_PORT_ATTEMPTS,
    ) -> None:
        self.host = host
        self.base: HeldPort | None = None
        self.neighbor: HeldPort | None = None
        for _ in range(attempts):
            base = HeldPort(host)
            if base.port == HIGHEST_TCP_PORT:
                base.release()
                continue
            try:
                neighbor = HeldPort(host, port=base.port + 1)
            except OSError:
                base.release()
                continue
            self.base = base
            self.neighbor = neighbor
            self.port = base.port
            return
        raise RuntimeError(
            f"no free adjacent port pair on {host} after {attempts} attempts"
        )

    def release(self) -> None:
        """Close both holding sockets if they are still open."""
        if self.neighbor is not None:
            self.neighbor.release()
        if self.base is not None:
            self.base.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
