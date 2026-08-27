"""Test helper for forcing a real WebSocket transport disconnect.

Exists for reconnect-behavior E2E tests: severs the live socket directly so
the transport's own reconnect logic (and BandLink._on_reconnected) runs for
real, instead of going through the clean disconnect()/connect() lifecycle a
test could otherwise only simulate. Production code never needs to force its
own disconnect this way, so BandLink has no public API for it — this is the
one place that reaches its transport directly, for that reason.
"""

from __future__ import annotations

from band.platform.link import BandLink


async def force_transport_disconnect(link: BandLink) -> None:
    """Abort the live WebSocket connection under ``link``.

    Aborts the raw asyncio transport rather than calling the WebSocket
    connection's own ``close()``: a graceful close sends close code 1000
    ("normal closure"), which the transport's reconnect policy correctly
    treats as an intentional shutdown and never reconnects from
    (``ReconnectPolicy.reconnect_on_normal_close`` defaults ``False`` and is
    never overridden here) — so it would silently fail to exercise the
    reconnect path at all. Aborting severs the connection with no close
    handshake, which is what a real network drop looks like to the client,
    so its own auto-reconnect logic actually triggers.

    Raises ``RuntimeError`` if ``link`` has no active transport to abort —
    a caller-side setup mistake (never connected, or already disconnected),
    not a condition to swallow.
    """
    ws = link._ws
    if ws is None or ws.client is None or ws.client.connection is None:
        raise RuntimeError("BandLink has no active transport connection to close")
    ws.client.connection.transport.abort()
