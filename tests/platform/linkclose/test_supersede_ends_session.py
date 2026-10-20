"""A terminal ``supersede`` ends a live BandLink's session for good: the
transport never reconnects, ``run_forever`` returns, and the host can still
disconnect cleanly."""

from __future__ import annotations

import asyncio
import logging

import pytest
from websockets.frames import CloseCode

from band.client.streaming.client import WireEvent
from band.platform.event import WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    CONTROL_TOPIC,
    HANG_GUARD_S,
    SUPERSEDED,
    connected,
    during_connect,
    during_the_next_backoff,
    handle_supersede,
    make_link,
    running,
)


@pytest.mark.usefixtures("millisecond_reconnects")
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

    async def test_disconnecting_an_ended_session_logs_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            await link.connect()
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            await asyncio.wait_for(anext(link), HANG_GUARD_S)
            await asyncio.wait_for(
                link.run_forever(install_signal_handlers=False), HANG_GUARD_S
            )
            caplog.clear()

            with caplog.at_level(logging.WARNING):
                await asyncio.wait_for(link.disconnect(), HANG_GUARD_S)

            assert caplog.records == []

    async def test_a_cancelled_run_does_not_abort_the_shutdown(self) -> None:
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

            await asyncio.wait_for(
                link.run_forever(install_signal_handlers=False), HANG_GUARD_S
            )
            await asyncio.wait_for(link.disconnect(), HANG_GUARD_S)
            async with connected(link):
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

    async def test_a_supersede_handled_while_connecting_ends_the_session(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)

            async def supersede_over_the_wire() -> None:
                await server.push(
                    CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE
                )
                await asyncio.wait_for(anext(link), HANG_GUARD_S)

            async with (
                during_connect(link, supersede_over_the_wire),
                connected(link),
                running(link) as run,
            ):
                await server.close_connection(code=CloseCode.GOING_AWAY)
                await asyncio.wait_for(run, HANG_GUARD_S)

                assert link.last_disconnect_reason == SUPERSEDED
                assert not link.is_connected
                assert server.connection_count == 1
