"""Entering a WebSocketClient connects through the session's own decisions:
retryable refusals wait the session's delay, terminal ones end the entry."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from band.client.streaming import WebSocketClient, WebSocketUpgradeError
from band.core.types import ConflictPolicy
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries, upgrade_error_body

RETRY_AFTER_S = 12


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestInitialConnect:
    @pytest.mark.parametrize(
        ("status", "headers"),
        [
            pytest.param(HTTPStatus.UNAUTHORIZED, {}, id="401"),
            pytest.param(HTTPStatus.SERVICE_UNAVAILABLE, {}, id="503"),
            pytest.param(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"Retry-After": "-1"},
                id="503-negative-retry-after",
            ),
        ],
    )
    async def test_a_retryable_refusal_waits_the_session_delay_then_connects(
        self, status: HTTPStatus, headers: dict[str, str]
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with observe_retries() as retries:
                async with background(client.__aenter__()) as entering:
                    with server.refusing_upgrades(status, headers=headers):
                        delay = await retries.get()
                    await clock.elapse(delay / 2)
                    assert len(server.upgrades) == 1
                    await clock.elapse(delay / 2)
                    await entering
            try:
                assert (len(server.upgrades), server.connection_count) == (2, 1)
                assert client.last_disconnect_reason is None
            finally:
                await client.__aexit__(None, None, None)

    async def test_a_retry_after_hint_floors_the_delay(self) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with observe_retries() as retries:
                async with background(client.__aenter__()) as entering:
                    with server.refusing_upgrades(
                        HTTPStatus.TOO_MANY_REQUESTS,
                        headers={"Retry-After": str(RETRY_AFTER_S)},
                    ):
                        delay = await retries.get()
                    await clock.elapse(RETRY_AFTER_S - 1)
                    assert len(server.upgrades) == 1
                    await clock.elapse(delay - (RETRY_AFTER_S - 1))
                    await entering
            try:
                assert delay >= RETRY_AFTER_S
                assert server.connection_count == 1
            finally:
                await client.__aexit__(None, None, None)

    @pytest.mark.parametrize(
        ("status", "code", "reason"),
        [
            (HTTPStatus.BAD_REQUEST, "invalid_on_conflict", "invalid_on_conflict"),
            (HTTPStatus.FORBIDDEN, None, "http_403"),
            (HTTPStatus.CONFLICT, "connection_conflict", "connection_conflict"),
        ],
    )
    async def test_a_terminal_refusal_ends_the_entry_after_one_upgrade(
        self, status: HTTPStatus, code: str | None, reason: str
    ) -> None:
        body = upgrade_error_body(code, status=status).decode() if code else "Forbidden"
        async with fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with (
                observe_retries() as retries,
                server.refusing_upgrades(status, body=body),
                pytest.raises(WebSocketUpgradeError) as refused,
            ):
                await client.__aenter__()

        assert (refused.value.status_code, refused.value.code) == (status, code)
        assert client.last_disconnect_reason is not None
        assert client.last_disconnect_reason.reason == reason
        assert len(server.upgrades) == 1
        assert retries.empty()

    async def test_the_conflict_policy_rides_every_upgrade_until_one_is_accepted(
        self,
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            client = WebSocketClient(
                server.url, "test-key", AGENT_ID, conflict_policy=ConflictPolicy.REJECT
            )
            with observe_retries() as retries:
                async with background(client.__aenter__()) as entering:
                    with server.refusing_upgrades(HTTPStatus.SERVICE_UNAVAILABLE):
                        delay = await retries.get()
                    await clock.elapse(delay)
                    await entering
                try:
                    await server.abort_connection()
                    await clock.elapse(await retries.get())
                    await server.wait_for_connection(2)
                finally:
                    await client.__aexit__(None, None, None)

        assert [upgrade.query.get("on_conflict") for upgrade in server.upgrades] == [
            ConflictPolicy.REJECT,
            ConflictPolicy.REJECT,
            None,
        ]

    async def test_a_reused_client_enters_a_fresh_session(self) -> None:
        async with fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with (
                server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                pytest.raises(WebSocketUpgradeError),
            ):
                await client.__aenter__()
            async with client:
                assert client.last_disconnect_reason is None
            async with client:
                assert server.connection_count == 2
