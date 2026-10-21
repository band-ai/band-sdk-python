"""Which WebSocket closes a live BandLink reconnects from: a close code is
never Band's stop signal, and only the host's own ``disconnect()`` ends the
session locally."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from websockets.frames import CloseCode

from band.platform.link import BandLink
from band.testing import FakePhoenixServer, close_transport, fake_phoenix_server
from tests.conftest import spy_on_reconciliation_drain
from tests.platform.linkclose.kit import HANG_GUARD_S, connected, make_link, running

CloseAction = Callable[[FakePhoenixServer, BandLink], Awaitable[None]]


async def server_closes_normally(server: FakePhoenixServer, link: BandLink) -> None:
    await server.close_connection(code=CloseCode.NORMAL_CLOSURE)


async def server_closes_going_away(server: FakePhoenixServer, link: BandLink) -> None:
    await server.close_connection(code=CloseCode.GOING_AWAY)


async def client_closes_normally(server: FakePhoenixServer, link: BandLink) -> None:
    await close_transport(link)


@pytest.mark.usefixtures("millisecond_reconnects")
class TestCloseReconnect:
    @pytest.mark.parametrize(
        "close",
        [server_closes_normally, server_closes_going_away, client_closes_normally],
        ids=["server-1000", "server-1001", "client-normal-close"],
    )
    async def test_a_normal_close_reconnects(self, close: CloseAction) -> None:
        async with (
            fake_phoenix_server() as server,
            connected(make_link(server.url)) as link,
        ):
            reconnected = spy_on_reconciliation_drain(link)

            await close(server, link)
            await asyncio.wait_for(reconnected.wait(), HANG_GUARD_S)

            assert server.connection_count == 2

    async def test_a_local_disconnect_never_reconnects(self) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            await link.connect()

            async with running(link) as run:
                await link.disconnect()
                await asyncio.wait_for(run, HANG_GUARD_S)

            assert server.connection_count == 1
