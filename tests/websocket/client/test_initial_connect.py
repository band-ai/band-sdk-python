"""Entering a WebSocketClient connects through the session's own decisions:
retryable refusals wait the session's delay, terminal ones end the entry."""

from __future__ import annotations

import logging
from http import HTTPStatus

import pytest
from band_sdk_core import DeadReason

from band.client.streaming import WebSocketClient, WebSocketUpgradeError
from band.client.streaming.disconnect import http_refusal_reason
from band.client.streaming.errors import CONNECTION_CONFLICT_CODE
from band.client.streaming.session_decider import logger as decider_logger
from band.core.types import ConflictPolicy
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import observe_retries, upgrade_error_body

RETRY_AFTER_S = 12
INVALID_ON_CONFLICT = "invalid_on_conflict"


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
        ("status", "body", "code", "reason"),
        [
            pytest.param(
                HTTPStatus.BAD_REQUEST,
                upgrade_error_body(INVALID_ON_CONFLICT, status=HTTPStatus.BAD_REQUEST),
                INVALID_ON_CONFLICT,
                INVALID_ON_CONFLICT,
                id="400",
            ),
            pytest.param(
                HTTPStatus.FORBIDDEN,
                b"Forbidden",
                None,
                http_refusal_reason(HTTPStatus.FORBIDDEN),
                id="403",
            ),
            pytest.param(
                HTTPStatus.CONFLICT,
                upgrade_error_body(
                    CONNECTION_CONFLICT_CODE, status=HTTPStatus.CONFLICT
                ),
                CONNECTION_CONFLICT_CODE,
                CONNECTION_CONFLICT_CODE,
                id="409",
            ),
        ],
    )
    async def test_a_terminal_refusal_ends_the_entry_after_one_upgrade(
        self,
        status: HTTPStatus,
        body: bytes,
        code: str | None,
        reason: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async with fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with (
                caplog.at_level(logging.WARNING, logger=decider_logger.name),
                server.refusing_upgrades(status, body=body.decode()),
                pytest.raises(WebSocketUpgradeError) as refused,
            ):
                await client.__aenter__()

        assert (refused.value.status_code, refused.value.code) == (status, code)
        assert client.last_disconnect_reason is not None
        assert client.last_disconnect_reason.reason == reason
        assert len(server.upgrades) == 1
        # The boot's verdict is logged once, and nothing was retried.
        assert [
            record.args
            for record in caplog.records
            if record.name == decider_logger.name
        ] == [(reason, DeadReason.Classified, refused.value.message)]

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
