"""After a local end, every decision stops quietly and nothing is reported."""

from __future__ import annotations

import pytest

from band.client.streaming.session_decider import SupersedeVerdict
from tests.websocket.client.sessiondecider.kit import (
    STOPS,
    TERMINAL,
    DeciderProbe,
    refusal,
    verdict,
)


@pytest.mark.looptime
class TestLocalEnd:
    async def test_a_close_after_a_local_end_stops_quietly(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.decider.end()

        assert verdict(probe.closed(1008)) == STOPS
        assert verdict(probe.attempt()) == STOPS
        assert probe.reported == []

    async def test_a_refusal_after_a_local_end_stops_quietly(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1000)
        probe.attempt()
        probe.decider.end()

        assert verdict(probe.failed(refusal(403))) == STOPS
        assert probe.decider.terminal is None
        assert probe.reported == []

    async def test_a_supersede_after_a_local_end_reports_nothing(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.decider.end()

        assert probe.supersede(TERMINAL) is SupersedeVerdict.ALREADY_ENDED
        assert probe.reported == []
