"""A supersede push ends the session or schedules a retry, and reports at
most one verdict per session."""

from __future__ import annotations

import logging

import pytest
from band_sdk_core import DeadReason

from band.client.streaming import WebSocketDisconnectReason
from band.client.streaming.session_decider import VERDICT_LOG, SupersedeVerdict
from band.client.streaming.session_decider import logger as decider_logger
from tests.conftest import RETRYABLE_SUPERSEDE, SUPERSEDED
from tests.sessionpolicies import LOW_RAPID_THRESHOLD, session_policy
from tests.websocket.client.sessiondecider.kit import (
    REDACTED,
    RETRYABLE,
    SECRET,
    STOPS,
    TERMINAL,
    DeciderProbe,
    refusal,
    verdict,
)


@pytest.mark.looptime
class TestSupersede:
    async def test_a_terminal_supersede_ends_the_session_once(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()

        assert probe.supersede(TERMINAL) is SupersedeVerdict.END
        assert verdict(probe.closed(None)) == STOPS
        assert probe.reported == [SUPERSEDED]
        assert probe.decider.terminal == SUPERSEDED

    async def test_a_retryable_supersede_delays_the_next_attempt(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()

        assert probe.supersede(RETRYABLE) is SupersedeVerdict.RETRY
        decision = probe.closed(None)

        assert decision.should_reconnect
        assert decision.delay_s >= RETRYABLE.retry_after
        assert probe.reported == []

    async def test_a_retryable_supersede_also_delays_a_failed_handshake(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1000)
        probe.attempt()
        probe.supersede(RETRYABLE)

        decision = probe.failed(refusal(503))

        assert decision.should_reconnect
        assert decision.delay_s >= RETRYABLE.retry_after
        assert probe.reported == []

    async def test_a_retryable_supersede_ends_a_session_that_keeps_dropping(
        self,
    ) -> None:
        probe = DeciderProbe(session_policy(rapid_threshold=LOW_RAPID_THRESHOLD))
        verdicts = []
        for _ in range(LOW_RAPID_THRESHOLD):
            probe.connect()
            verdicts.append(probe.supersede(RETRYABLE))
            probe.closed(None)

        assert verdicts == [SupersedeVerdict.RETRY] * (LOW_RAPID_THRESHOLD - 1) + [
            SupersedeVerdict.END
        ]
        assert probe.reported == [
            WebSocketDisconnectReason(
                **RETRYABLE_SUPERSEDE, dead_reason=DeadReason.RapidDisconnect
            )
        ]

    async def test_a_supersede_after_a_verdict_reports_nothing_more(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1008)
        [verdict_reason] = probe.reported

        assert probe.supersede(TERMINAL) is SupersedeVerdict.ALREADY_ENDED
        assert probe.reported == [verdict_reason]
        assert probe.decider.terminal == verdict_reason

    async def test_a_supersede_verdict_logs_only_redacted_text(
        self, probe: DeciderProbe, caplog: pytest.LogCaptureFixture
    ) -> None:
        probe.connect()
        payload = TERMINAL.model_copy(update={"message": f"evicted by {SECRET}"})

        with caplog.at_level(logging.WARNING, logger=decider_logger.name):
            probe.supersede(payload)

        [record] = caplog.records
        assert record.msg == VERDICT_LOG
        assert record.args == (
            SUPERSEDED.reason,
            SUPERSEDED.dead_reason,
            SUPERSEDED.correlation_id,
            f"evicted by {REDACTED}",
        )
