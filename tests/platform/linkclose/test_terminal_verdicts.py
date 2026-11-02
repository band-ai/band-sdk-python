"""A live session the platform ends for good (close 1008, rapid disconnects)
reports exactly one disconnect event with its reason, and ``run_forever``
raises."""

from __future__ import annotations

import pytest
from band_sdk_core import DeadReason
from phoenix_channels_python_client.exceptions import PHXConnectionError
from websockets.frames import CloseCode

from band.client.streaming.disconnect import TerminalReason
from band.platform.event import ReconnectedEvent, WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import (
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
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries


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
                    WebSocketDisconnectedEvent(payload=link.last_disconnect_reason)
                ]
                assert link.last_disconnect_reason is not None
                assert (
                    link.last_disconnect_reason.reason,
                    link.last_disconnect_reason.dead_reason,
                ) == (TerminalReason.SOCKET_CLOSED, DeadReason.Classified)
                assert not link.is_connected
                assert len(server.upgrades) == 1

    async def test_a_supersede_queued_behind_a_verdict_reports_nothing_more(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            async with connected(link), running(link) as run:
                await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                with pytest.raises(PHXConnectionError):
                    await run
                verdict = link.last_disconnect_reason

                await handle_supersede(link)

                assert drain_events(link) == [
                    WebSocketDisconnectedEvent(payload=verdict)
                ]
                assert link.last_disconnect_reason == verdict
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

                    assert link.last_disconnect_reason is not None
                    assert drain_events(link) == [
                        *[ReconnectedEvent()] * (LOW_RAPID_THRESHOLD - 1),
                        WebSocketDisconnectedEvent(payload=link.last_disconnect_reason),
                    ]
                    assert (
                        link.last_disconnect_reason.reason,
                        link.last_disconnect_reason.dead_reason,
                    ) == (TerminalReason.RAPID_DISCONNECT, DeadReason.RapidDisconnect)
                    assert server.connection_count == LOW_RAPID_THRESHOLD
