"""Guards the pinned transport's behavior that ``end_transport`` exists for:
the transport reads ``auto_reconnect`` once per close, before its backoff, so
clearing the flag during the backoff cannot stop the reconnect."""

from __future__ import annotations

import asyncio

import pytest
from websockets.frames import CloseCode

from band.testing import fake_phoenix_server
from tests.conftest import spy_on_reconciliation_drain
from tests.platform.linkclose.kit import (
    HANG_GUARD_S,
    connected,
    during_the_next_backoff,
    make_link,
)


@pytest.mark.usefixtures("millisecond_reconnects")
class TestReconnectFlagRace:
    async def test_clearing_the_reconnect_flag_during_the_backoff_is_too_late(
        self,
    ) -> None:
        """The window a terminal supersede must close by ending the transport."""
        async with fake_phoenix_server() as server:
            link = make_link(server.url)

            async def clear_reconnect_flag() -> None:
                ws = link._ws
                assert ws is not None and ws.client is not None
                ws.client.auto_reconnect = False

            async with (
                during_the_next_backoff(link, clear_reconnect_flag),
                connected(link),
            ):
                reconnected = spy_on_reconciliation_drain(link)

                await server.close_connection(code=CloseCode.GOING_AWAY)
                await asyncio.wait_for(reconnected.wait(), HANG_GUARD_S)

                assert server.connection_count == 2
