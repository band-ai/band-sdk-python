"""A socket's close retries at the session's delay unless it ends the session."""

from __future__ import annotations

from unittest.mock import ANY

import pytest
from band_sdk_core import DeadReason

from band.client.streaming.disconnect import TerminalReason
from tests.sessionpolicies import LOW_RAPID_THRESHOLD, session_policy
from tests.websocket.client.sessiondecider.kit import DeciderProbe, verdict


@pytest.mark.looptime
class TestCloses:
    @pytest.mark.parametrize("close_code", [None, 1000, 1001, 1011, 4000])
    async def test_a_close_retries_after_the_session_delay(
        self, probe: DeciderProbe, close_code: int | None
    ) -> None:
        probe.connect()

        decision = probe.closed(close_code)

        assert verdict(decision) == (True, ANY, None)
        assert decision.delay_s > 0
        assert probe.reported == []
        assert probe.decider.terminal is None

    async def test_a_policy_violation_close_ends_the_session_once(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()

        decision = verdict(probe.closed(1008))

        [reason] = probe.reported
        assert decision == (False, 0.0, "WebSocket closed with code 1008")
        assert (reason.reason, reason.dead_reason, reason.retryable) == (
            TerminalReason.SOCKET_CLOSED,
            DeadReason.Classified,
            False,
        )
        assert probe.decider.terminal == reason

    async def test_sockets_that_keep_dropping_at_once_end_the_session(self) -> None:
        probe = DeciderProbe(session_policy(rapid_threshold=LOW_RAPID_THRESHOLD))
        decisions = []
        for _ in range(LOW_RAPID_THRESHOLD):
            probe.connect()
            decisions.append(probe.closed(1000).should_reconnect)

        [reason] = probe.reported
        assert decisions == [True] * (LOW_RAPID_THRESHOLD - 1) + [False]
        assert (reason.reason, reason.dead_reason) == (
            TerminalReason.RAPID_DISCONNECT,
            DeadReason.RapidDisconnect,
        )
