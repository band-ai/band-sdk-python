"""The gates (an attempt starting, a socket coming up) proceed or stop."""

from __future__ import annotations

import pytest
from band_sdk_core import StaleReason

from band.client.streaming.disconnect import TerminalReason, WebSocketDisconnectReason
from band.client.streaming.session_decider import (
    STALE_CONNECTION_MESSAGE,
    SupersedeVerdict,
)
from tests.conftest import SUPERSEDED
from tests.websocket.client.sessiondecider.kit import (
    PROCEEDS,
    RETRYABLE,
    STOPS,
    TERMINAL,
    DeciderProbe,
    verdict,
)


@pytest.mark.looptime
class TestGates:
    async def test_the_first_attempt_and_its_socket_proceed(
        self, probe: DeciderProbe
    ) -> None:
        assert (verdict(probe.attempt()), verdict(probe.connected())) == (
            PROCEEDS,
            PROCEEDS,
        )
        assert probe.first_connections == 1

    async def test_only_the_first_accepted_socket_counts_as_first(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1000)
        probe.connect()

        assert probe.first_connections == 1

    async def test_an_attempt_after_a_verdict_stops_without_an_error(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1008)

        assert verdict(probe.attempt()) == STOPS

    async def test_a_socket_up_after_a_terminal_supersede_is_refused_quietly(
        self, probe: DeciderProbe
    ) -> None:
        probe.connect()
        probe.closed(1000)
        probe.attempt()

        assert probe.supersede(TERMINAL) is SupersedeVerdict.END
        assert verdict(probe.connected()) == STOPS
        assert probe.reported == [SUPERSEDED]

    async def test_a_socket_up_after_a_retryable_supersede_ends_the_session(
        self, probe: DeciderProbe
    ) -> None:
        """A stated limit: the retried epoch is gone, so the socket is stale."""
        probe.connect()
        probe.closed(1000)
        probe.attempt()
        probe.supersede(RETRYABLE)

        decision = verdict(probe.connected())

        stale = WebSocketDisconnectReason(
            reason=TerminalReason.STALE_CONNECTION,
            message=STALE_CONNECTION_MESSAGE,
            retryable=False,
            stale_reason=StaleReason.WrongEpoch,
        )
        assert decision == (False, 0.0, STALE_CONNECTION_MESSAGE)
        assert probe.reported == [stale]
