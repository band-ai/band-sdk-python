"""Reserve free ports for a Parlant ``Server`` so several can run side by side.

Parlant has two fixed default ports: ``8818`` for the integrated tool service, bound
during ``Server.__aenter__``, and ``8800`` for the API/UI, bound only once the server
starts serving in ``__aexit__``. A second agent on the same host fails to start while
the first holds one. In an agent that stays inside the ``async with`` body — as the
examples do, blocking in ``Agent.run()`` — only the tool service is ever bound, so
that is the collision in practice.

Asking the OS for a port with ``port=0`` does **not** work here: Parlant
string-formats the number it was given into URLs instead of reading it back off the
bound socket. The integrated tool service registers as ``http://127.0.0.1:0`` and
every ``@p.tool`` is routed to it over HTTP, so tool calls would dial port 0; the
readiness poll would likewise target ``http://localhost:0/healthz``, never succeed,
and hang shutdown. Parlant has to be handed real port numbers.

So we reserve them ourselves: bind two sockets, read the ports the OS assigned, and
close them before handing the numbers to Parlant, which re-binds them itself. The
close-then-rebind gap is the standard ephemeral-port reservation race, and far
smaller than the collision risk of two fixed ports.
"""

from __future__ import annotations

import logging
import socket
from typing import NamedTuple

logger = logging.getLogger(__name__)

__all__ = ["ServerPorts", "reserve_server_ports"]


class ServerPorts(NamedTuple):
    """A free port pair for ``p.Server(port=..., tool_service_port=...)``."""

    port: int
    tool_service_port: int


def reserve_server_ports() -> ServerPorts:
    """Reserve two distinct loopback ports and log them.

    Both are reserved on ``127.0.0.1``, never on all interfaces: the tool service —
    the port that actually collides — is bound by Parlant on ``127.0.0.1``
    explicitly, so a loopback reservation is exactly the guarantee it needs. The API
    server instead binds the ``host`` given to ``p.Server``, all interfaces by
    default, for which a loopback reservation is very strong but not airtight: a
    port free here could in principle be held by another process on some other
    interface. Reserving that one on all interfaces to close the gap would mean
    briefly opening a socket to the network for a port we only ever hand back as an
    integer, which is not a trade worth making — and in an agent that stays inside
    the ``async with`` body the API port is never bound at all.

    Logged so that a refused connection or a stuck listener can be traced back to a
    known pair — with the ports varying per run, there is otherwise nothing to match
    against ``lsof`` output or a Parlant error.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as api,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tool_service,
    ):
        # Both are held open at once so the OS cannot hand back the same port twice.
        api.bind(("127.0.0.1", 0))
        tool_service.bind(("127.0.0.1", 0))
        ports = ServerPorts(api.getsockname()[1], tool_service.getsockname()[1])

    logger.info(
        "Parlant server ports: api=%d, tool_service=%d",
        ports.port,
        ports.tool_service_port,
    )
    return ports
