"""A stop requested while an agent boots into a platform outage takes effect
when the connect bound expires: the run unwinds with nothing left behind."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest

from band.agent import Agent
from band.client.streaming.client import DEFAULT_CONNECT_TIMEOUT_S
from tests.agent.batchstart.kit import SESSION, BareBatchAdapter, Platform
from tests.scenarios.peer import AGENT_ID
from tests.sessionpolicies import BEYOND_ANY_RETRY_S
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestBootOutage:
    async def test_a_stop_during_a_boot_outage_applies_at_the_connect_bound(
        self, platform: Platform
    ) -> None:
        loop = asyncio.get_running_loop()
        agent = Agent.create(
            BareBatchAdapter(),
            agent_id=AGENT_ID,
            api_key="test-key",
            ws_url=platform.server.url,
            rest_url="https://test.invalid",
            session_config=SESSION,
        )
        async with controlled_clock() as clock:
            before = asyncio.all_tasks()
            with (
                observe_retries() as retries,
                platform.server.refusing_upgrades(HTTPStatus.SERVICE_UNAVAILABLE),
            ):
                async with background(
                    agent.run(install_signal_handlers=False)
                ) as running:
                    start = loop.time()
                    delay = await retries.get()

                    assert await agent.stop() is True
                    assert not running.done()

                    await clock.elapse(delay)
                    while loop.time() - start < DEFAULT_CONNECT_TIMEOUT_S:
                        await clock.elapse(await retries.get())
                    with pytest.raises(TimeoutError, match="did not connect"):
                        await running
                attempts = len(platform.server.upgrades)
                await clock.elapse(BEYOND_ANY_RETRY_S)

            assert not agent.is_running
            assert len(platform.server.upgrades) == attempts
            assert asyncio.all_tasks() == before
