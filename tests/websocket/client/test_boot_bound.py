"""A boot that never connects keeps retrying at the session's capped backoff
until ``connect_timeout`` or the caller gives up; then every attempt stops."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any

import pytest

from band.client.streaming import WebSocketClient
from band.client.streaming.client import DEFAULT_CONNECT_TIMEOUT_S
from band.platform.link import BandLink
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID
from tests.sessionpolicies import BEYOND_ANY_RETRY_S, DEFAULT_POLICY_FIELDS
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries

Boot = Callable[
    [str, float | None],
    tuple[WebSocketClient | BandLink, Coroutine[Any, Any, object]],
]


def enter_client(
    url: str, connect_timeout: float | None
) -> tuple[WebSocketClient, Coroutine[Any, Any, object]]:
    client = WebSocketClient(url, "test-key", AGENT_ID, connect_timeout=connect_timeout)
    return client, client.__aenter__()


def connect_link(
    url: str, connect_timeout: float | None
) -> tuple[BandLink, Coroutine[Any, Any, object]]:
    link = BandLink(
        agent_id=AGENT_ID,
        api_key="test-key",
        ws_url=url,
        rest_url="https://test.invalid",
        connect_timeout=connect_timeout,
    )
    return link, link.connect()


BOOTS = pytest.mark.parametrize(
    "boot",
    [pytest.param(enter_client, id="client"), pytest.param(connect_link, id="link")],
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestBootBound:
    @BOOTS
    async def test_a_boot_that_never_connects_times_out_at_connect_timeout(
        self, boot: Boot
    ) -> None:
        loop = asyncio.get_running_loop()
        async with controlled_clock() as clock:
            before = asyncio.all_tasks()
            async with fake_phoenix_server() as server:
                start = loop.time()
                subject, entry = boot(server.url, DEFAULT_CONNECT_TIMEOUT_S)
                with (
                    observe_retries() as retries,
                    server.refusing_upgrades(HTTPStatus.SERVICE_UNAVAILABLE),
                ):
                    async with background(entry) as entering:
                        while loop.time() - start < DEFAULT_CONNECT_TIMEOUT_S:
                            await clock.elapse(await retries.get())
                        with pytest.raises(TimeoutError, match="did not connect"):
                            await entering
                    attempts = len(server.upgrades)
                    await clock.elapse(BEYOND_ANY_RETRY_S)

                    # Refusals never trip the rapid-disconnect breaker.
                    assert attempts > DEFAULT_POLICY_FIELDS["rapid_threshold"]
                    assert len(server.upgrades) == attempts
                    assert subject.last_disconnect_reason is None
            assert asyncio.all_tasks() == before

    @BOOTS
    async def test_an_unbounded_boot_retries_until_the_caller_cancels(
        self, boot: Boot
    ) -> None:
        loop = asyncio.get_running_loop()
        async with controlled_clock() as clock:
            before = asyncio.all_tasks()
            async with fake_phoenix_server() as server:
                start = loop.time()
                subject, entry = boot(server.url, None)
                with (
                    observe_retries() as retries,
                    server.refusing_upgrades(HTTPStatus.SERVICE_UNAVAILABLE),
                ):
                    async with background(entry) as entering:
                        while loop.time() - start < 2 * DEFAULT_CONNECT_TIMEOUT_S:
                            await clock.elapse(await retries.get())
                        assert not entering.done()
                        entering.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await entering
                    attempts = len(server.upgrades)
                    await clock.elapse(BEYOND_ANY_RETRY_S)

                    assert len(server.upgrades) == attempts
                    assert subject.last_disconnect_reason is None
            assert asyncio.all_tasks() == before
