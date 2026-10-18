"""Watchdog deadlines, connection ownership and retry behavior."""

from __future__ import annotations

import logging

import pytest

from band.client.streaming.watchdog import logger
from tests.adapters.waiting import spin
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.watchdog import (
    CLOSE_REASON,
    DEAD_THRESHOLD_S,
    WatchdogPeer,
    watching,
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestWatchdog:
    async def test_start_cancelled_cleanly_on_stop(self) -> None:
        async with watching(WatchdogPeer()) as watchdog:
            task = watchdog._task
            assert task is not None and not task.done()
        assert task.cancelled()

    async def test_stale_watchdog_cannot_close_a_superseded_connection(self) -> None:
        first, second = WatchdogPeer(), WatchdogPeer()
        async with controlled_clock() as clock, watching(first):
            clock.advance(DEAD_THRESHOLD_S - 1)
            await spin()
            assert first.close_calls == []
            clock.advance(2)
            assert await first.attempts.get() == CLOSE_REASON
            assert first.close_calls == [CLOSE_REASON]
            assert second.close_calls == []

    async def test_survives_a_close_connection_failure(self) -> None:
        peer = WatchdogPeer(fail_first=True)
        async with controlled_clock() as clock, watching(peer):
            clock.advance(DEAD_THRESHOLD_S + 1)
            assert await peer.attempts.get() == CLOSE_REASON
            clock.advance(DEAD_THRESHOLD_S + 1)
            assert await peer.attempts.get() == CLOSE_REASON
            assert peer.close_calls == [CLOSE_REASON, CLOSE_REASON]

    async def test_does_not_warn_or_close_while_already_disconnected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        peer = WatchdogPeer(connected=False)
        caplog.set_level(logging.WARNING, logger=logger.name)
        async with controlled_clock() as clock, watching(peer):
            for _ in range(2):
                clock.advance(DEAD_THRESHOLD_S + 1)
                await spin()
            assert peer.close_calls == []
            assert [
                r.getMessage() for r in caplog.records if r.name == logger.name
            ] == []

    async def test_reset_deadline_lets_a_fresh_socket_survive_a_reconnect(self) -> None:
        peer = WatchdogPeer(connected=False)
        async with controlled_clock() as clock, watching(peer) as watchdog:
            clock.advance(DEAD_THRESHOLD_S - 1)
            await spin()
            peer.connection = object()
            watchdog.reset_deadline()
            clock.advance(2)
            await spin()
            assert peer.close_calls == []
            clock.advance(DEAD_THRESHOLD_S)
            assert await peer.attempts.get() == CLOSE_REASON
            assert peer.close_calls == [CLOSE_REASON]
