"""A supersede push ends the session or schedules a retry, and reports at
most one verdict per session."""

from __future__ import annotations

import pytest

from band.client.streaming.session_decider import SupersedeVerdict
from tests.conftest import SUPERSEDED
from tests.websocket.client.sessiondecider.kit import (
    RETRYABLE,
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

    async def test_a_supersede_after_a_verdict_reports_nothing_more(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1008)
        [verdict_reason] = probe.reported

        assert probe.supersede(TERMINAL) is SupersedeVerdict.ALREADY_ENDED
        assert probe.reported == [verdict_reason]
        assert probe.decider.terminal == verdict_reason
