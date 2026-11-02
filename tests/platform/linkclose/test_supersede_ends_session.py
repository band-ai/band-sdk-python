"""A terminal ``supersede`` ends a live BandLink's session for good: the
transport never reconnects, ``run_forever`` returns, and the host can still
disconnect cleanly."""

from __future__ import annotations

import asyncio
import logging

import pytest
from websockets.frames import CloseCode

from band.client.streaming.client import WireEvent
from band.platform.event import ReconnectedEvent, WebSocketDisconnectedEvent
from band.platform.link import DROPPED_EVENTS_LOG
from band.platform.link import logger as link_logger
from band.testing import fake_phoenix_server
from tests.conftest import RETRYABLE_SUPERSEDE, SUPERSEDED, TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    CONTROL_TOPIC,
    HANG_GUARD_S,
    connected,
    drain_events,
    during_connect,
    handle_supersede,
    make_link,
    park_supersedes,
    running,
)
from tests.platform.recovery.kit import observe_disconnects
from tests.sessionpolicies import (
    BEYOND_ANY_RETRY_S,
    session_policy,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries


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

    async def test_a_new_session_drops_the_ended_one_s_unread_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            await link.connect()
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            await server.close_connection(code=CloseCode.GOING_AWAY)
            await asyncio.wait_for(
                link.run_forever(install_signal_handlers=False), HANG_GUARD_S
            )
            await link.disconnect()

            with caplog.at_level(logging.INFO, logger=link_logger.name):
                async with connected(link):
                    assert drain_events(link) == []
                    assert link.is_connected

            assert [
                record.args
                for record in caplog.records
                if record.msg == DROPPED_EVENTS_LOG
            ] == [(1,)]

    async def test_a_supersede_handled_during_disconnect_reports_nothing(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            supersede = park_supersedes(link)
            await link.connect()
            connection = await server.wait_for_connection(1)
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            await asyncio.wait_for(supersede.arrived.wait(), HANG_GUARD_S)

            async with background(link.disconnect()) as disconnecting:
                await asyncio.wait_for(
                    connection.wait_for_leave(CONTROL_TOPIC), HANG_GUARD_S
                )
                supersede.released.set()
                await asyncio.wait_for(supersede.handled.wait(), HANG_GUARD_S)
                await asyncio.wait_for(disconnecting, HANG_GUARD_S)

            assert drain_events(link) == []
            assert link.last_disconnect_reason is None

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

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_supersede_handled_during_the_backoff_ends_the_session(
        self,
    ) -> None:
        loop = asyncio.get_running_loop()
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            disconnected = observe_disconnects(link)
            async with connected(link), running(link) as run:
                await server.abort_connection()
                # The transport decides right after this callback and then
                # waits out the backoff.
                await disconnected.get()
                superseded_at = loop.time()

                await handle_supersede(link)
                await run

                assert loop.time() == superseded_at
                await clock.elapse(BEYOND_ANY_RETRY_S)
                assert link.last_disconnect_reason == SUPERSEDED
                assert len(server.upgrades) == 1

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_supersede_handled_during_the_reconnect_handshake_ends_the_session(
        self,
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            with observe_retries() as retries:
                async with connected(link), running(link) as run:
                    await server.abort_connection()
                    with server.holding_upgrades() as handshake:
                        await clock.elapse(await retries.get())
                        await handshake.arrived.wait()

                        await handle_supersede(link)
                        handshake.release()
                        await run

                    await clock.elapse(BEYOND_ANY_RETRY_S)
                    assert link.last_disconnect_reason == SUPERSEDED
                    assert (len(server.upgrades), server.connection_count) == (2, 2)
                    assert server.connections[1].joined_topics == set()

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_retryable_supersede_reconnects_after_its_retry_after(
        self,
    ) -> None:
        retry_after = RETRYABLE_SUPERSEDE["retry_after"]
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            with observe_retries() as retries:
                async with connected(link), running(link) as run:
                    await server.push(
                        CONTROL_TOPIC, WireEvent.SUPERSEDE, RETRYABLE_SUPERSEDE
                    )
                    delay = await retries.get()
                    await clock.elapse(retry_after - 1)
                    assert len(server.upgrades) == 1

                    await clock.elapse(delay - (retry_after - 1))

                    assert await anext(link) == ReconnectedEvent()
                    assert delay >= retry_after
                    assert link.last_disconnect_reason is None
                    assert not run.done()

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
