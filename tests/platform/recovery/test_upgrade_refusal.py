"""Initial refusals belong to Session; background 403 terminates run_forever."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from phoenix_channels_python_client.exceptions import PHXConnectionError

from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import connected, make_link
from tests.platform.recovery.kit import initial_retries, observe_disconnects
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe


@pytest.mark.looptime(resolution=binary_clock_resolution())
@pytest.mark.usefixtures("millisecond_reconnects")
class TestUpgradeRefusal:
    async def test_initial_forbidden_uses_session_backoff_then_connects(self) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            probe = ControlProbe()
            link.on_control = probe.receive
            with initial_retries() as retries:
                async with background(link.connect()) as starting:
                    with server.refusing_upgrades(HTTPStatus.FORBIDDEN):
                        delay = await retries.get()
                        assert delay == 1.0
                        assert not starting.done()
                        assert len(server.upgrades) == 2
                        assert not link.is_connected
                        assert link.last_disconnect_reason is None
                    await clock.elapse(delay)
                    await starting
            try:
                await probe.roundtrip(server)
                assert link.is_connected
                assert server.connection_count == 1
            finally:
                await link.disconnect()

    async def test_background_forbidden_surfaces_through_link_run_forever(self) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            disconnected = observe_disconnects(link)
            async with (
                connected(link),
                background(link.run_forever(install_signal_handlers=False)) as run,
            ):
                with server.refusing_upgrades(HTTPStatus.FORBIDDEN):
                    await server.abort_connection()
                    await disconnected.get()
                    await clock.elapse(0.01)
                    with pytest.raises(PHXConnectionError, match="403") as failure:
                        await run
                assert "test-key" not in str(failure.value)
                assert server.connection_count == 1
                assert len(server.upgrades) == 2
                # Terminal completion remains observable to a later host waiter.
                with pytest.raises(PHXConnectionError, match="403"):
                    await link.run_forever(install_signal_handlers=False)
                await clock.elapse(120)
                assert len(server.upgrades) == 2
