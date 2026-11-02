"""A live session the platform ends for good (close 1008, rapid disconnects)
reports exactly one disconnect event with its reason and ``run_forever``
raises; disconnecting after any verdict sends nothing on the ending socket."""

from __future__ import annotations

import asyncio
import logging

import pytest
from phoenix_channels_python_client.exceptions import PHXConnectionError
from phoenix_channels_python_client.phx_messages import PHXEvent
from websockets.frames import CloseCode

from band.client.streaming.client import WireEvent
from band.platform.event import ReconnectedEvent, WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.conftest import CLOSED_1008, KEPT_DROPPING, SUPERSEDED, TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    CONTROL_TOPIC,
    HANG_GUARD_S,
    connected,
    drain_events,
    handle_supersede,
    make_link,
    running,
)
from tests.sessionpolicies import (
    BEYOND_ANY_RETRY_S,
    LOW_RAPID_THRESHOLD,
    session_policy,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries

ROOM_ID = "room-1"


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestTerminalVerdicts:
    async def test_a_policy_violation_close_ends_the_session(self) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            async with connected(link), running(link) as run:
                await server.close_connection(code=CloseCode.POLICY_VIOLATION)

                with pytest.raises(PHXConnectionError, match="1008"):
                    await run
                await clock.elapse(BEYOND_ANY_RETRY_S)

                assert drain_events(link) == [
                    WebSocketDisconnectedEvent(payload=CLOSED_1008)
                ]
                assert link.last_disconnect_reason == CLOSED_1008
                assert not link.is_connected
                assert len(server.upgrades) == 1

    async def test_a_verdict_during_the_control_join_fails_the_connect(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            with server.defer_join(CONTROL_TOPIC) as deferred:
                async with background(link.connect()) as connecting:
                    await deferred.ready.wait()
                    await server.close_connection(code=CloseCode.POLICY_VIOLATION)

                    with pytest.raises(PHXConnectionError):
                        await connecting

            assert link.last_disconnect_reason == CLOSED_1008
            assert not link.is_connected
            # The ended session's event is not left for the next one.
            assert drain_events(link) == []
            async with connected(link):
                assert link.is_connected

    async def test_a_supersede_queued_behind_a_verdict_reports_nothing_more(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            async with connected(link), running(link) as run:
                await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                with pytest.raises(PHXConnectionError):
                    await run

                await handle_supersede(link)

                assert drain_events(link) == [
                    WebSocketDisconnectedEvent(payload=CLOSED_1008)
                ]
                assert link.last_disconnect_reason == CLOSED_1008
                with pytest.raises(PHXConnectionError, match="1008"):
                    await link.run_forever(install_signal_handlers=False)

    async def test_sockets_that_keep_dropping_at_once_end_the_session(self) -> None:
        policy = session_policy(rapid_threshold=LOW_RAPID_THRESHOLD)
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=policy)
            with observe_retries() as retries:
                async with connected(link), running(link) as run:
                    for number in range(2, LOW_RAPID_THRESHOLD + 1):
                        await server.abort_connection()
                        await clock.elapse(await retries.get())
                        await server.wait_for_connection(number)
                    await server.abort_connection()

                    with pytest.raises(PHXConnectionError):
                        await run
                    await clock.elapse(BEYOND_ANY_RETRY_S)

                    assert drain_events(link) == [
                        *[ReconnectedEvent()] * (LOW_RAPID_THRESHOLD - 1),
                        WebSocketDisconnectedEvent(payload=KEPT_DROPPING),
                    ]
                    assert link.last_disconnect_reason == KEPT_DROPPING
                    assert server.connection_count == LOW_RAPID_THRESHOLD

    async def test_disconnecting_right_after_a_verdict_skips_the_control_leave(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            await link.connect()
            await link.subscribe_room(ROOM_ID)
            await server.push(CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE)
            event = await asyncio.wait_for(anext(link), HANG_GUARD_S)

            # The transport is still shutting down when the host disconnects.
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                await asyncio.wait_for(link.disconnect(), HANG_GUARD_S)

            assert event == WebSocketDisconnectedEvent(payload=SUPERSEDED)
            assert [
                message.topic
                for message in server.received
                if message.event == PHXEvent.leave
            ] == []
            assert caplog.records == []
            assert not link.is_room_subscribed(ROOM_ID)
            assert drain_events(link) == []
