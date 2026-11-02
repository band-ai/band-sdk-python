"""A socket's close retries at the session's delay unless it ends the session."""

from __future__ import annotations

import logging
from unittest.mock import ANY

import pytest

from band.client.streaming.session_decider import VERDICT_LOG
from band.client.streaming.session_decider import logger as decider_logger
from tests.conftest import CLOSED_1008, KEPT_DROPPING
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
        self, probe: DeciderProbe, caplog: pytest.LogCaptureFixture
    ) -> None:
        probe.connect()

        with caplog.at_level(logging.WARNING, logger=decider_logger.name):
            decision = verdict(probe.closed(1008))
            probe.closed(1008)

        assert decision == (False, 0.0, CLOSED_1008.message)
        assert probe.reported == [CLOSED_1008]
        assert probe.decider.terminal == CLOSED_1008
        assert [(record.msg, record.args) for record in caplog.records] == [
            (
                VERDICT_LOG,
                (
                    CLOSED_1008.reason,
                    CLOSED_1008.dead_reason,
                    None,
                    CLOSED_1008.message,
                ),
            )
        ]

    async def test_sockets_that_keep_dropping_at_once_end_the_session(self) -> None:
        probe = DeciderProbe(session_policy(rapid_threshold=LOW_RAPID_THRESHOLD))
        decisions = []
        for _ in range(LOW_RAPID_THRESHOLD):
            probe.connect()
            decisions.append(probe.closed(1000).should_reconnect)

        assert decisions == [True] * (LOW_RAPID_THRESHOLD - 1) + [False]
        assert probe.reported == [KEPT_DROPPING]
