"""Tests for HeartbeatWatchdog in isolation (no real socket needed -- the
wiring between it and WebSocketClient/PHXChannelsClient is covered by the
real-wire tests in test_client.py)."""

from __future__ import annotations

import asyncio

from band_sdk_core import SessionPolicy

from band.client.streaming.watchdog import HeartbeatWatchdog


def _fast_session_policy(
    *, heartbeat_interval_s: float, dead_threshold_s: float
) -> SessionPolicy:
    """A SessionPolicy with real reconnect-backoff defaults (mirroring
    SessionPolicy.default()) but a fast heartbeat/dead-threshold pair, so
    watchdog tests run in real fractional seconds instead of production's
    30s/60s."""
    return SessionPolicy(
        {
            "base_delay_s": 1.0,
            "factor": 2.0,
            "max_delay_s": 30.0,
            "stable_reset_s": 60.0,
            "rapid_disconnect_uptime_s": 10.0,
            "rapid_window_s": 300.0,
            "rapid_first_min_delay_s": 1.0,
            "rapid_second_min_delay_s": 5.0,
            "rapid_cooldown_base_s": 10.0,
            "rapid_cooldown_step_s": 10.0,
            "rapid_cooldown_max_s": 60.0,
            "rapid_threshold": 10,
            "heartbeat_interval_s": heartbeat_interval_s,
            "dead_threshold_s": dead_threshold_s,
        }
    )


class FakePHXClient:
    def __init__(self, connection: object | None = object()) -> None:
        self.connection = connection
        self.close_calls: list[str] = []

    async def close_connection(self, reason: str) -> None:
        self.close_calls.append(reason)


async def test_start_cancelled_cleanly_on_stop():
    """stop() cancels the watchdog task cleanly -- no dangling task
    survives shutdown."""
    policy = _fast_session_policy(heartbeat_interval_s=0.05, dead_threshold_s=5.0)
    watchdog = HeartbeatWatchdog(policy)
    watchdog.start(FakePHXClient())

    task = watchdog._task
    assert task is not None
    assert not task.done()

    await watchdog.stop()

    assert task.done()
    assert task.cancelled()
    assert watchdog._task is None


async def test_stale_watchdog_cannot_close_a_superseded_connection():
    """A watchdog task stays bound to the specific PHXChannelsClient it was
    started with. If it fires late -- after the caller has already moved on
    to a newer instance, as happens when an initial-connect attempt is
    superseded by the next one in `WebSocketClient.__aenter__`'s retry loop
    -- it must only ever act on its own (stale) instance, never the
    replacement."""
    policy = _fast_session_policy(heartbeat_interval_s=0.01, dead_threshold_s=0.05)
    watchdog = HeartbeatWatchdog(policy)
    first, second = FakePHXClient(), FakePHXClient()

    watchdog.start(first)
    # The retry loop moving on to the next attempt: nothing re-points this
    # watchdog at `second` -- a fresh HeartbeatWatchdog would be started for
    # attempt 2, so this instance stays bound to `first`.

    await asyncio.sleep(
        0.2
    )  # comfortably past dead_threshold_s (may trip more than once)
    await watchdog.stop()

    assert first.close_calls
    assert all(
        reason == "Heartbeat dead-threshold exceeded" for reason in first.close_calls
    )
    assert second.close_calls == []


async def test_survives_a_close_connection_failure():
    """A `close_connection` failure must not kill the watchdog task -- it is
    the only monitor for the rest of this client's lifetime, so one bad
    close must not silently disable dead-threshold enforcement forever."""

    class FlakyThenFakePHXClient(FakePHXClient):
        async def close_connection(self, reason: str) -> None:
            self.close_calls.append(reason)
            if len(self.close_calls) == 1:
                raise RuntimeError("close boom")

    policy = _fast_session_policy(heartbeat_interval_s=0.01, dead_threshold_s=0.05)
    watchdog = HeartbeatWatchdog(policy)
    fake = FlakyThenFakePHXClient()
    watchdog.start(fake)

    await asyncio.sleep(0.2)  # comfortably past two dead_threshold_s windows
    await watchdog.stop()

    assert len(fake.close_calls) >= 2


async def test_does_not_warn_or_close_while_already_disconnected():
    """No live connection to force-close means nothing to warn about either
    -- an extended reconnect backoff must not spam misleading 'forcing
    reconnect' warnings for a connection that's already down."""
    policy = _fast_session_policy(heartbeat_interval_s=0.01, dead_threshold_s=0.05)
    watchdog = HeartbeatWatchdog(policy)
    fake = FakePHXClient(connection=None)
    watchdog.start(fake)

    await asyncio.sleep(0.15)
    await watchdog.stop()

    assert fake.close_calls == []


async def test_reset_deadline_lets_a_fresh_socket_survive_a_reconnect():
    """A reconnect must reset the watchdog deadline immediately -- otherwise
    it can inherit a stale deadline (from the disconnected-state polling
    above) that expires before the fresh socket's first heartbeat cycle,
    force-closing a healthy connection."""
    policy = _fast_session_policy(heartbeat_interval_s=0.1, dead_threshold_s=0.12)
    watchdog = HeartbeatWatchdog(policy)
    fake = FakePHXClient()

    # The disconnected-state watchdog polling has already scheduled a trip
    # unrelated to when reconnect actually completes.
    watchdog.reset_deadline()
    await asyncio.sleep(policy.dead_threshold_s * 0.8)

    # Reconnect completes just before that already-scheduled deadline (what
    # WebSocketClient._handle_reconnect does on a real reconnect).
    watchdog.reset_deadline()

    watchdog.start(fake)
    # Wait less than heartbeat_interval_s: the fresh connection hasn't had
    # time to send or ack its first heartbeat yet.
    await asyncio.sleep(policy.heartbeat_interval_s * 0.5)
    await watchdog.stop()

    assert fake.close_calls == []
