"""A failed connect retries, or ends the session on a terminal refusal; only a
verdict after the first accepted socket is reported."""

from __future__ import annotations

import logging
from unittest.mock import ANY

import pytest
from band_sdk_core import DeadReason

from band.client.streaming.disconnect import WebSocketDisconnectReason
from band.client.streaming.session_decider import connect_failed_message
from band.client.streaming.session_decider import logger as decider_logger
from tests.conftest import CLOSED_1008
from tests.websocket.client.sessiondecider.kit import (
    REDACTED,
    SECRET,
    DeciderProbe,
    refusal,
    verdict,
)

RETRY_AFTER_S = 12


@pytest.mark.looptime
class TestConnectFailures:
    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(refusal(401), id="401"),
            pytest.param(refusal(429), id="429"),
            pytest.param(refusal(503), id="503"),
            pytest.param(
                refusal(503, headers={"Retry-After": "-1"}), id="503-bad-hint"
            ),
            pytest.param(OSError("connection refused"), id="network"),
            pytest.param(TimeoutError(), id="handshake-timeout"),
        ],
    )
    async def test_a_retryable_failure_retries(
        self, probe: DeciderProbe, error: Exception
    ) -> None:
        probe.attempt()

        decision = probe.failed(error)

        assert verdict(decision) == (True, ANY, None)
        assert decision.delay_s > 0
        assert (probe.decider.terminal, probe.decider.terminal_refusal) == (None, None)

    async def test_a_retry_after_hint_floors_the_delay(
        self, probe: DeciderProbe
    ) -> None:
        probe.attempt()

        decision = probe.failed(
            refusal(429, headers={"Retry-After": str(RETRY_AFTER_S)})
        )

        assert decision.delay_s >= RETRY_AFTER_S

    @pytest.mark.parametrize("status", [400, 403, 409])
    async def test_a_terminal_refusal_at_boot_is_recorded_but_not_reported(
        self, probe: DeciderProbe, status: int
    ) -> None:
        probe.attempt()
        failure = refusal(
            status, code="no", headers={"Retry-After": str(RETRY_AFTER_S)}
        )

        decision = verdict(probe.failed(failure))

        # The body carries the kit's "refused <code>" message and "req-<status>" id.
        assert probe.decider.terminal == WebSocketDisconnectReason(
            reason="no",
            message="refused no",
            retryable=False,
            retry_after=RETRY_AFTER_S,
            correlation_id=f"req-{status}",
            dead_reason=DeadReason.Classified,
        )
        assert decision == (False, 0.0, "refused no")
        assert probe.decider.terminal_refusal is not None
        assert probe.decider.terminal_refusal.status_code == status
        assert probe.reported == []

    async def test_a_terminal_refusal_on_a_reconnect_is_reported(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1000)
        probe.attempt()

        probe.failed(refusal(403, code="forbidden"))

        # The body carries the kit's "refused <code>" message and "req-<status>" id.
        expected = WebSocketDisconnectReason(
            reason="forbidden",
            message="refused forbidden",
            retryable=False,
            correlation_id="req-403",
            dead_reason=DeadReason.Classified,
        )
        assert probe.reported == [expected]
        assert probe.decider.terminal == expected

    async def test_a_refusal_reports_only_redacted_text(
        self, probe: DeciderProbe
    ) -> None:
        probe.attempt()

        decision = verdict(probe.failed(refusal(403, code=SECRET)))

        assert probe.decider.terminal is not None
        assert probe.decider.terminal.reason == REDACTED
        assert SECRET not in repr((decision, probe.decider.terminal))

    async def test_a_later_verdict_carries_no_earlier_refusal(
        self, probe: DeciderProbe
    ) -> None:
        probe.attempt()
        probe.failed(refusal(503))
        probe.connect()

        probe.closed(1008)

        assert probe.decider.terminal == CLOSED_1008
        assert probe.decider.terminal_refusal is None

    async def test_a_failed_connect_logs_its_redacted_cause(
        self, probe: DeciderProbe, caplog: pytest.LogCaptureFixture
    ) -> None:
        probe.attempt()

        with caplog.at_level(logging.WARNING, logger=decider_logger.name):
            probe.failed(OSError(f"connect to {SECRET} refused"))

        [record] = caplog.records
        assert record.args == (
            connect_failed_message("OSError", f"connect to {REDACTED} refused"),
            ANY,
        )
