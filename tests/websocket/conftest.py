from __future__ import annotations

import pytest

from band_sdk_core import SessionPolicy

SUCCEEDS = object()
"""Sentinel meaning a scripted connect attempt or probe finds no error."""


class Script:
    """Advances through a fixed sequence of outcomes, one per `next()`
    call; the last entry repeats once the sequence is exhausted."""

    def __init__(self, outcomes: tuple[Exception | object, ...]):
        self._outcomes = outcomes
        self.calls = 0

    def next(self) -> Exception | object:
        self.calls += 1
        return self._outcomes[min(self.calls, len(self._outcomes)) - 1]


class ScriptedPHXClient:
    """Fake PHXChannelsClient driven by a script of outcomes: each
    __aenter__ call raises the next scripted exception, or succeeds on
    SUCCEEDS."""

    def __init__(self, *script: Exception | object):
        self._script = Script(script)
        self.auto_reconnect: bool | None = None
        self.channel_socket_url = "wss://test/socket"

    @property
    def attempts(self) -> int:
        return self._script.calls

    def __call__(self, *args, **kwargs):
        self.auto_reconnect = kwargs["auto_reconnect"]
        return self

    async def __aenter__(self):
        outcome = self._script.next()
        if outcome is SUCCEEDS:
            return self
        raise outcome

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


@pytest.fixture
def scripted_connect(monkeypatch):
    """Patch PHXChannelsClient with a ScriptedPHXClient driven by `script`."""

    def use(*script: Exception | object) -> ScriptedPHXClient:
        client = ScriptedPHXClient(*script)
        monkeypatch.setattr("band.client.streaming.client.PHXChannelsClient", client)
        return client

    return use


class ScriptedProbeConnection:
    def __init__(self, outcome: Exception | object):
        self._outcome = outcome

    async def __aenter__(self):
        if self._outcome is SUCCEEDS:
            return self
        raise self._outcome

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class ScriptedProbe:
    """Fake live-socket probe (the `connect()` call inside
    probe_upgrade_error) driven by a script of outcomes: each call raises
    the next scripted exception, or finds a clean handshake on SUCCEEDS."""

    def __init__(self, *script: Exception | object):
        self._script = Script(script)
        self.probed_urls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, open_timeout: float) -> ScriptedProbeConnection:
        self.probed_urls.append((url, open_timeout))
        return ScriptedProbeConnection(self._script.next())


@pytest.fixture
def scripted_probe(monkeypatch):
    """Patch errors.connect with a ScriptedProbe driven by `script`."""

    def use(*script: Exception | object) -> ScriptedProbe:
        probe = ScriptedProbe(*script)
        monkeypatch.setattr("band.client.streaming.errors.connect", probe)
        return probe

    return use


@pytest.fixture
def no_real_sleep(monkeypatch) -> list[float]:
    """Patch asyncio.sleep to return immediately, recording each delay."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("band.client.streaming.client.asyncio.sleep", fake_sleep)
    return delays


def fast_session_policy(
    *, heartbeat_interval_s: float, dead_threshold_s: float
) -> SessionPolicy:
    """A SessionPolicy with real reconnect-backoff defaults (mirroring
    SessionPolicy.default()) but a fast heartbeat/dead-threshold pair, so
    watchdog/reconnect tests run in real fractional seconds instead of
    production's 30s/60s."""
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
