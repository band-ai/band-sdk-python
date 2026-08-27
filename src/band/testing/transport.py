"""Test helper for forcing a real WebSocket transport disconnect.

Reaches BandLink's transport directly -- the one place in the SDK that
does -- because production code never needs to force its own disconnect, so
BandLink has no public API for it.
"""

from __future__ import annotations

from band.platform.link import BandLink


async def force_transport_disconnect(link: BandLink) -> None:
    """Abort the live WebSocket connection under ``link``.

    Aborts the raw transport rather than the connection's own ``close()``:
    a graceful close sends code 1000, which the reconnect policy
    (``ReconnectPolicy.reconnect_on_normal_close`` defaults ``False``)
    treats as intentional and never reconnects from. Aborting drops the
    connection with no close handshake -- a real network failure looks the
    same to the client, so its own reconnect logic actually fires.
    """
    ws = link._ws
    if ws is None or ws.client is None or ws.client.connection is None:
        raise RuntimeError("BandLink has no active transport connection to close")
    ws.client.connection.transport.abort()
