"""A boot that never connects keeps retrying at the session's capped backoff
until ``connect_timeout`` or the caller gives up; then every attempt stops."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any

import pytest

from band.client.streaming import WebSocketClient, WebSocketUpgradeError
from band.client.streaming.client import DEFAULT_CONNECT_TIMEOUT_S
from band.platform.link import BandLink
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID
from tests.sessionpolicies import (
    BEYOND_ANY_RETRY_S,
    DEFAULT_POLICY_FIELDS,
    session_policy,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background, tasks_started_since
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import expire_after, observe_retries

Boot = Callable[
    [str, float | None],
    tuple[WebSocketClient | BandLink, Coroutine[Any, Any, object]],
]


def enter_client(
    url: str, connect_timeout: float | None
) -> tuple[WebSocketClient, Coroutine[Any, Any, object]]:
    client = WebSocketClient(
        url,
        "test-key",
        AGENT_ID,
        session_policy=session_policy(),
        connect_timeout=connect_timeout,
    )
    return client, client.__aenter__()


def connect_link(
    url: str, connect_timeout: float | None
) -> tuple[BandLink, Coroutine[Any, Any, object]]:
    link = BandLink(
        agent_id=AGENT_ID,
        api_key="test-key",
        ws_url=url,
        rest_url="https://test.invalid",
        session_policy=session_policy(),
        connect_timeout=connect_timeout,
    )
    return link, link.connect()


# Enough loop iterations to span a refusal's arrival through its verdict.
RACE_HOPS = 16

BOOTS = pytest.mark.parametrize(
    "boot",
    [pytest.param(enter_client, id="client"), pytest.param(connect_link, id="link")],
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestBootBound:
    @BOOTS
    @pytest.mark.parametrize("connect_timeout", [0, -1, math.nan, math.inf])
    def test_a_bound_that_never_or_always_expires_is_rejected(
        self, boot: Boot, connect_timeout: float
    ) -> None:
        with pytest.raises(ValueError, match="connect_timeout"):
            boot("ws://test.invalid", connect_timeout)

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
                            assert not entering.done()
                            await clock.elapse(await retries.get())
                        with pytest.raises(TimeoutError, match="did not connect"):
                            await entering
                    attempts = len(server.upgrades)
                    await clock.elapse(BEYOND_ANY_RETRY_S)

                    # Refusals never trip the rapid-disconnect breaker.
                    assert attempts > DEFAULT_POLICY_FIELDS["rapid_threshold"]
                    assert len(server.upgrades) == attempts
                    assert subject.last_disconnect_reason is None
            assert tasks_started_since(before) == set()

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
            assert tasks_started_since(before) == set()

    @BOOTS
    async def test_a_cancel_as_the_bound_expires_stays_a_cancellation(
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
                        loop.call_at(start + DEFAULT_CONNECT_TIMEOUT_S, entering.cancel)
                        while loop.time() - start < DEFAULT_CONNECT_TIMEOUT_S:
                            await clock.elapse(await retries.get())
                        with pytest.raises(asyncio.CancelledError):
                            await entering
                    attempts = len(server.upgrades)
                    await clock.elapse(BEYOND_ANY_RETRY_S)

                    assert len(server.upgrades) == attempts
                    assert subject.last_disconnect_reason is None
            assert tasks_started_since(before) == set()

    # Sweeps the bound across the loop iterations in which the refusal turns
    # into the session's verdict; whichever lands first must be what is raised.
    @BOOTS
    @pytest.mark.parametrize("hops", range(RACE_HOPS))
    async def test_a_refusal_verdict_reached_as_the_bound_expires_wins(
        self, boot: Boot, hops: int
    ) -> None:
        loop = asyncio.get_running_loop()
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            deadline = loop.time() + DEFAULT_CONNECT_TIMEOUT_S
            subject, entry = boot(server.url, DEFAULT_CONNECT_TIMEOUT_S)
            with (
                server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                server.holding_upgrades() as handshake,
            ):
                async with background(entry) as entering:
                    await handshake.arrived.wait()
                    handshake.release()
                    expire_after(clock, deadline, hops)
                    [outcome] = await asyncio.gather(entering, return_exceptions=True)

        verdict_reached = subject.last_disconnect_reason is not None
        assert (type(outcome), verdict_reached) in {
            (WebSocketUpgradeError, True),
            (TimeoutError, False),
        }
