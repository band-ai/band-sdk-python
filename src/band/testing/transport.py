"""Test helpers for forcing a real WebSocket transport disconnect.

Reaches BandLink's transport directly -- the one place in the SDK that
does -- because production code never needs to force its own disconnect, so
BandLink has no public API for it.
"""

from __future__ import annotations

from websockets import ClientConnection

from band.platform.link import BandLink


def live_connection(link: BandLink) -> ClientConnection:
    """The WebSocket connection currently open under ``link``."""
    ws = link._ws
    if ws is None or ws.client is None or ws.client.connection is None:
        raise RuntimeError("BandLink has no active transport connection to close")
    return ws.client.connection


async def force_transport_disconnect(link: BandLink) -> None:
    """Abort the live WebSocket connection under ``link``.

    Drops the connection with no close handshake -- what a real network
    failure looks like to the client -- so its own reconnect logic fires.
    """
    live_connection(link).transport.abort()


async def close_transport(link: BandLink) -> None:
    """Close the live WebSocket connection under ``link`` with a normal close.

    The client starts the close and the server answers it with 1000 -- what a
    close started by the client or a proxy looks like. Bypasses the transport's
    own ``close_connection``, whose forced close is never classified.
    """
    await live_connection(link).close()
