"""A 403 refusal ends the session alike at boot and on a reconnect: one
upgrade, no retry, and the reason recorded."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from band_sdk_core import DeadReason
from phoenix_channels_python_client.exceptions import PHXConnectionError

from band.client.streaming import WebSocketUpgradeError
from band.client.streaming.disconnect import (
    WebSocketDisconnectReason,
    http_refusal_reason,
)
from band.platform.event import WebSocketDisconnectedEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import connected, drain_events, make_link
from tests.platform.recovery.kit import observe_disconnects
from tests.sessionpolicies import BEYOND_ANY_RETRY_S
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries

# A bare 403 carries no platform body, so the reason is named by its status.
FORBIDDEN = WebSocketDisconnectReason(
    reason=http_refusal_reason(HTTPStatus.FORBIDDEN),
    message=f"WebSocket upgrade failed with HTTP {HTTPStatus.FORBIDDEN.value}",
    retryable=False,
    dead_reason=DeadReason.Classified,
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestUpgradeRefusal:
    async def test_a_forbidden_boot_ends_after_one_upgrade(self) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            with (
                server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                pytest.raises(WebSocketUpgradeError) as refused,
            ):
                await link.connect()
            await clock.elapse(BEYOND_ANY_RETRY_S)

            assert refused.value.status_code == HTTPStatus.FORBIDDEN
            assert link.last_disconnect_reason == FORBIDDEN
            assert not link.is_connected
            assert drain_events(link) == []
            assert len(server.upgrades) == 1

    async def test_a_forbidden_reconnect_ends_the_session_with_one_event(
        self,
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            disconnected = observe_disconnects(link)
            async with (
                connected(link),
                background(link.run_forever(install_signal_handlers=False)) as run,
            ):
                with (
                    observe_retries() as retries,
                    server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                ):
                    await server.abort_connection()
                    await disconnected.get()
                    await clock.elapse(await retries.get())
                    with pytest.raises(PHXConnectionError, match="403") as failure:
                        await run
                assert "test-key" not in str(failure.value)
                # Terminal completion remains observable to a later host waiter.
                with pytest.raises(PHXConnectionError, match="403"):
                    await link.run_forever(install_signal_handlers=False)
                await clock.elapse(BEYOND_ANY_RETRY_S)

                assert link.last_disconnect_reason == FORBIDDEN
                assert drain_events(link) == [
                    WebSocketDisconnectedEvent(payload=FORBIDDEN)
                ]
                assert not link.is_connected
                assert (server.connection_count, len(server.upgrades)) == (1, 2)
