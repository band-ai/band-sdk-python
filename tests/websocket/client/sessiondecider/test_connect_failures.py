"""A failed connect retries, or ends the session on a terminal refusal; only a
verdict after the first accepted socket is reported."""

from __future__ import annotations

from unittest.mock import ANY

import pytest
from band_sdk_core import DeadReason

from band.client.streaming.disconnect import TerminalReason
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

        decision = verdict(probe.failed(refusal(status, code="no")))

        terminal = probe.decider.terminal
        assert terminal is not None
        assert decision == (False, 0.0, terminal.message)
        assert (terminal.reason, terminal.dead_reason) == ("no", DeadReason.Classified)
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

        assert probe.reported == [probe.decider.terminal]
        assert probe.reported[0].reason == "forbidden"

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

        assert probe.decider.terminal is not None
        assert probe.decider.terminal.reason == TerminalReason.SOCKET_CLOSED
        assert probe.decider.terminal_refusal is None
