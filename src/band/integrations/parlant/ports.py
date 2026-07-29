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


def reserve_server_ports(host: str | None = None) -> ServerPorts:
    """Reserve two distinct free ports and log them.

    Each port is reserved on the host that will later bind it, since a port free on
    one host is not necessarily free on another — a wildcard bind loses to a port
    already held on *any* single interface, which a loopback-only reservation would
    never have seen.

    * The tool service is always reserved on ``127.0.0.1``: Parlant hardcodes that
      host for it.
    * The API port is reserved on ``host`` — the ``host`` passed to ``p.Server``.
      ``None`` means Parlant's own default, all interfaces.

    Reserving on all interfaces costs nothing in exposure: the socket is never
    ``listen()``ed, so it accepts no connections and a SYN gets an RST.

    Logged so that a refused connection or a stuck listener can be traced back to a
    known pair — with the ports varying per run, there is otherwise nothing to match
    against ``lsof`` output or a Parlant error.
    """
    # Parlant's p.Server binds host="0.0.0.0" unless told otherwise.
    api_host = "0.0.0.0" if host is None else host

    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as api,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tool_service,
    ):
        # Both are held open at once so the OS cannot hand back the same port twice.
        api.bind((api_host, 0))
        tool_service.bind(("127.0.0.1", 0))
        ports = ServerPorts(api.getsockname()[1], tool_service.getsockname()[1])

    logger.info(
        "Parlant server ports: api=%d, tool_service=%d",
        ports.port,
        ports.tool_service_port,
    )
    return ports
