"""Test helper for forcing a real WebSocket transport disconnect.

Exists for reconnect-behavior E2E tests: closes the live socket directly so
the transport's own reconnect logic (and BandLink._on_reconnected) runs for
real, instead of going through the clean disconnect()/connect() lifecycle a
test could otherwise only simulate. Production code never needs to force its
own disconnect this way, so BandLink has no public API for it — this is the
one place that reaches its transport directly, for that reason.
"""

from __future__ import annotations

from band.platform.link import BandLink


async def force_transport_disconnect(link: BandLink) -> None:
    """Close the live WebSocket connection under ``link``.

    Raises ``RuntimeError`` if ``link`` has no active transport to close —
    a caller-side setup mistake (never connected, or already disconnected),
    not a condition to swallow.
    """
    ws = link._ws
    if ws is None or ws.client is None or ws.client.connection is None:
        raise RuntimeError("BandLink has no active transport connection to close")
    await ws.client.connection.close()
