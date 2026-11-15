"""A platform outage after the agent is up never ends the session: refused
reconnects retry at the capped backoff and the agent rejoins when it ends."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest

from band.platform.event import ReconnectedEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import connected, make_link, running
from tests.sessionpolicies import DEFAULT_POLICY_FIELDS, session_policy
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries

# Longer than the window rapid disconnects are counted in.
OUTAGE_S = 2 * DEFAULT_POLICY_FIELDS["rapid_window_s"]


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestOutage:
    async def test_refused_reconnects_ride_out_an_outage(self) -> None:
        loop = asyncio.get_running_loop()
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url, session_policy=session_policy())
            with observe_retries() as retries:
                async with connected(link), running(link) as run:
                    start = loop.time()
                    with server.refusing_upgrades(HTTPStatus.SERVICE_UNAVAILABLE):
                        await server.abort_connection()
                        delays = [await retries.get()]
                        while loop.time() - start < OUTAGE_S:
                            await clock.elapse(delays[-1])
                            delays.append(await retries.get())
                    await clock.elapse(delays[-1])

                    assert await anext(link) == ReconnectedEvent()
                    assert max(delays) <= DEFAULT_POLICY_FIELDS["max_delay_s"]
                    assert (
                        len(server.upgrades) - 1
                        > DEFAULT_POLICY_FIELDS["rapid_threshold"]
                    )
                    assert server.connection_count == 2
                    assert link.last_disconnect_reason is None
                    assert link.is_connected
                    assert not run.done()
