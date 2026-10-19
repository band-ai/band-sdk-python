"""A terminal ``supersede`` ends a live BandLink's session for good: the
transport never reconnects, ``run_forever`` returns, and the host can still
disconnect cleanly."""

from __future__ import annotations

import asyncio

import pytest
from websockets.frames import CloseCode

from band.client.streaming.client import WireEvent
from band.platform.event import WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE, spy_on_reconciliation_drain
from tests.platform.linkclose.kit import (
    CONTROL_TOPIC,
    HANG_GUARD_S,
    SUPERSEDED,
    connected,
    during_the_next_backoff,
    handle_supersede,
    make_link,
    running,
)


class TestSupersedeEndsSession:
    async def test_a_terminal_supersede_ends_the_session(self) -> None:
        async with (
            fake_phoenix_server() as server,
            connected(make_link(server.url)) as link,
            running(link) as run,
        ):
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            event = await asyncio.wait_for(anext(link), HANG_GUARD_S)
            await server.close_connection(code=CloseCode.GOING_AWAY)
            await asyncio.wait_for(run, HANG_GUARD_S)

            assert event == WebSocketDisconnectedEvent(payload=SUPERSEDED)
            assert link.last_disconnect_reason == SUPERSEDED
            assert server.connection_count == 1

    async def test_run_forever_entered_after_a_supersede_returns(self) -> None:
        async with (
            fake_phoenix_server() as server,
            connected(make_link(server.url)) as link,
        ):
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            await asyncio.wait_for(anext(link), HANG_GUARD_S)
            await server.close_connection(code=CloseCode.GOING_AWAY)

            await asyncio.wait_for(
                link.run_forever(install_signal_handlers=False), HANG_GUARD_S
            )

            assert server.connection_count == 1

    async def test_a_cancelled_run_leaves_the_link_able_to_reconnect(self) -> None:
        """The host gives up on ``run_forever`` while the transport is still
        shutting down; its cancellation must not abort that shared shutdown."""
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            await link.connect()

            with server.stalled():
                await server.push(
                    CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE
                )
                await asyncio.wait_for(anext(link), HANG_GUARD_S)
                with pytest.raises(TimeoutError):
                    # Cancelled at its first wait, inside the transport shutdown.
                    async with asyncio.timeout(0):
                        await link.run_forever(install_signal_handlers=False)

            await asyncio.wait_for(link.disconnect(), HANG_GUARD_S)
            async with connected(link):
                assert link.is_connected
            assert server.connection_count == 2

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

    async def test_a_supersede_handled_during_the_backoff_ends_the_session(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with (
                during_the_next_backoff(link, lambda: handle_supersede(link)),
                connected(link),
                running(link) as run,
            ):
                await server.close_connection(code=CloseCode.GOING_AWAY)
                await asyncio.wait_for(run, HANG_GUARD_S)

                assert link.last_disconnect_reason == SUPERSEDED
                assert server.connection_count == 1
