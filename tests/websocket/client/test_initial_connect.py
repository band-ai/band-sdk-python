"""WebSocketClient.__aenter__ retries, classifies and gives up on initial-connect
failures through Session."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from types import SimpleNamespace

import pytest
from band_sdk_core import DeadReason, SessionState
from phoenix_channels_python_client.exceptions import PHXConnectionError

from band.client.streaming import WebSocketClient, WebSocketUpgradeError
from tests.websocket.client.kit import upgrade_exception
from tests.websocket.conftest import SUCCEEDS, fast_session_policy


class TestInitialConnect:
    async def test_aenter_probes_initial_phx_connection_error_then_retries_and_succeeds(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        """A 429 upgrade rejection -- recovered via _classify_connect_failure's
        probe fallback, since the raw PHXConnectionError carries no HTTP status
        itself -- classifies as Retry (classify_upgrade marks only 400/409
        Terminal), so it retries using Retry-After as a delay floor instead of
        raising on the first attempt, then succeeds once the rate limit clears."""
        upgrade_exc = upgrade_exception(
            429,
            b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-429"}}',
            {"Retry-After": "5"},
        )
        connect = scripted_connect(
            PHXConnectionError("Connection supervisor stopped before connecting"),
            SUCCEEDS,
        )
        probe = scripted_probe(upgrade_exc)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await client.__aenter__()

        assert connect.attempts == 2
        assert probe.probed_urls == [("wss://test/socket&agent_id=agent-123", 5)]
        # retry_after_s is a lower bound (Retry-After) with an upper bound of
        # max(max_delay_s, retry_after_s) -- see the design doc's "on_upgrade_rejected"
        # semantics -- rather than asserting one exact jittered value.
        assert len(no_real_sleep) == 1
        assert 5.0 <= no_real_sleep[0] <= 30.0
        assert client.client.auto_reconnect is True

        await client.__aexit__(None, None, None)

    async def test_aenter_caches_probe_result_across_repeated_connect_failures(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        """The live-socket probe recovering a PHXConnectionError's hidden upgrade
        rejection blocks for up to open_timeout=5s -- rerunning it on every
        backoff retry of the very same rejection would stack a fresh network
        round trip onto every computed delay. A repeat bare PHXConnectionError
        must reuse the first probe's classification instead of probing again,
        as long as that classification carries no retry_after to go stale."""
        upgrade_exc = upgrade_exception(
            503,
            b'{"error":{"code":"tracking_failed","message":"tracking unavailable","request_id":"req-503"}}',
        )
        connect = scripted_connect(
            PHXConnectionError("Connection supervisor stopped before connecting"),
            PHXConnectionError("Connection supervisor stopped before connecting"),
            SUCCEEDS,
        )
        probe = scripted_probe(upgrade_exc)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await client.__aenter__()

        assert connect.attempts == 3
        # Two attempts failed with the same unclassifiable PHXConnectionError,
        # but the probe only ran once -- the second failure's classification
        # came from the cache.
        assert probe.probed_urls == [("wss://test/socket&agent_id=agent-123", 5)]

        await client.__aexit__(None, None, None)

    async def test_aenter_reprobes_repeated_429_for_current_retry_after(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        """A repeated 429 always gets a fresh probe, even with an unchanged
        wrapped message: the wrapper's message is just "HTTP 429" -- it can't
        carry Retry-After -- so a cached 429 could silently reuse a stale delay
        while the server's current Retry-After has since grown."""
        stale = upgrade_exception(429, b"", {"Retry-After": "5"})
        current = upgrade_exception(429, b"", {"Retry-After": "60"})
        connect = scripted_connect(
            PHXConnectionError("Connection supervisor stopped before connecting"),
            PHXConnectionError("Connection supervisor stopped before connecting"),
            SUCCEEDS,
        )
        probe = scripted_probe(stale, current)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await client.__aenter__()

        assert connect.attempts == 3
        assert len(probe.probed_urls) == 2
        assert len(no_real_sleep) == 2
        assert no_real_sleep[1] >= 60.0

        await client.__aexit__(None, None, None)

    async def test_aenter_reprobes_when_wrapped_failure_message_changes(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        """A repeat PHXConnectionError with a *different* message may be a
        genuinely different failure, not a repeat of the last one -- reusing
        the earlier probe's classification for it would hide a real, terminal
        rejection behind a stale retryable one."""
        conflict_exc = upgrade_exception(
            409,
            b'{"error":{"code":"conflict","message":"stale session","request_id":"req-409"}}',
        )
        connect = scripted_connect(
            PHXConnectionError("temporary network failure"),
            PHXConnectionError("temporary network failure"),
            PHXConnectionError("connection reset by peer"),
        )
        probe = scripted_probe(SUCCEEDS, conflict_exc)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(WebSocketUpgradeError, match="stale session") as exc_info:
            await client.__aenter__()

        # Attempts 1-2 shared one message and one probe call (a clean result,
        # cached); attempt 3's different message forced a second probe, which
        # recovered the real terminal rejection instead of reusing attempt 1's
        # cached clean result.
        assert connect.attempts == 3
        assert len(probe.probed_urls) == 2
        assert exc_info.value.status_code == 409
        assert client.last_disconnect_reason is not None
        assert client.last_disconnect_reason.dead_reason == DeadReason.Classified

        await client.__aexit__(None, None, None)

    async def test_aenter_restores_reconnect_after_successful_initial_connect(
        self, monkeypatch
    ):
        init_kwargs = {}

        class SuccessfulPHXClient:
            def __init__(self, *args, **kwargs):
                init_kwargs.update(kwargs)
                self.channel_socket_url = "wss://test/socket"
                self.auto_reconnect = kwargs["auto_reconnect"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        monkeypatch.setattr(
            "band.client.streaming.client.PHXChannelsClient", SuccessfulPHXClient
        )

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await client.__aenter__()

        assert init_kwargs["auto_reconnect"] is False
        assert client.client.auto_reconnect is True

        await client.__aexit__(None, None, None)

    async def test_aenter_reusable_after_aexit_ends_the_session(self, monkeypatch):
        """__aexit__ ends self._session (Dead), so a second __aenter__ on the
        same instance must build a fresh Session rather than resuming the
        now-Dead one -- otherwise begin_attempt() returns None and __aenter__
        raises, even though reusing an exited instance across sequential
        `async with` blocks worked before Session existed (a fresh
        PHXChannelsClient was always built per entry)."""

        class SuccessfulPHXClient:
            def __init__(self, *args, **kwargs):
                self.channel_socket_url = "wss://test/socket"
                self.auto_reconnect = kwargs["auto_reconnect"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

        monkeypatch.setattr(
            "band.client.streaming.client.PHXChannelsClient", SuccessfulPHXClient
        )

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        async with client:
            pass

        async with client:
            assert client.client.auto_reconnect is True

    async def test_aenter_retries_unclassified_initial_connection_errors(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        connect = scripted_connect(
            PHXConnectionError("temporary network failure"),
            PHXConnectionError("temporary network failure"),
            SUCCEEDS,
        )
        scripted_probe(SUCCEEDS)

        client = WebSocketClient(
            "ws://localhost",
            "test-key",
            "agent-123",
            session_policy=fast_session_policy(
                heartbeat_interval_s=0.15, dead_threshold_s=0.4
            ),
        )
        await client.__aenter__()

        assert connect.attempts == 3
        # Both retries are the first two rapid disconnects (never having reached
        # Up) -- their delay floors (rapid_first_min_delay_s/rapid_second_min_delay_s)
        # are policy-driven and not jitter-scaled, so this is deterministic.
        assert no_real_sleep == [1.0, 5.0]
        assert client.client.auto_reconnect is True

        await client.__aexit__(None, None, None)

    async def test_aenter_trips_dead_after_rapid_disconnect_threshold(
        self, scripted_connect, scripted_probe, no_real_sleep
    ):
        """A burst of fast-repeating initial-connect failures trips Session
        straight to Dead(RapidDisconnect) once the rolling rapid-disconnect
        window's threshold is reached. Uses SessionPolicy.default()'s real
        rapid_threshold=10 -- asyncio.sleep is mocked, so it costs no
        wall-clock time."""
        connect = scripted_connect(PHXConnectionError("temporary network failure"))
        scripted_probe(SUCCEEDS)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(
            PHXConnectionError, match="temporary network failure"
        ) as exc_info:
            await client.__aenter__()

        # SessionPolicy.default()'s rapid_threshold is 10 -- the 10th rapid
        # disconnect trips Dead(RapidDisconnect) instead of computing another delay.
        assert connect.attempts == 10
        assert client.last_disconnect_reason is not None
        assert client.last_disconnect_reason.dead_reason == DeadReason.RapidDisconnect
        # PHXConnectionError is re-raised as itself (no new exception object),
        # so no `from` chaining applies -- self-referential __cause__ would be
        # incorrect (and misleading) here.
        assert exc_info.value.__cause__ is None

    async def test_resolve_failed_connect_attempt_captures_now_before_probe_latency(
        self, monkeypatch, scripted_connect
    ):
        """`now` must be the wall-clock time the connect attempt actually failed,
        not the time probe_upgrade_error's live-socket probe finished -- otherwise
        a burst of true back-to-back failures each looks slower than it really
        was, understating how rapid the disconnects are to Session's rolling
        rapid-disconnect window."""
        now_s_seen = []

        class RecordingSession:
            def __init__(self, policy):
                pass

            def begin_attempt(self, now_s):
                return 1

            def on_socket_close(self, epoch, now_s, close_code, jitter_sample):
                now_s_seen.append(now_s)
                return SimpleNamespace(
                    state=SessionState.Reconnecting,
                    retry_after_s=0.0,
                    dead_reason=None,
                    stale_reason=None,
                )

            def on_connected(self, epoch, now_s):
                return SimpleNamespace(stale_reason=None)

            def end(self):
                return SessionState.Dead

        monkeypatch.setattr("band.client.streaming.client.Session", RecordingSession)

        probe_delay_s = 0.1

        async def slow_probe(
            websocket_url: str,
            headers: Mapping[str, str],
            *,
            logger: logging.Logger,
            redact: Callable[[str], str],
        ) -> None:
            await asyncio.sleep(probe_delay_s)

        monkeypatch.setattr(
            "band.client.streaming.client.probe_upgrade_error",
            slow_probe,
        )

        scripted_connect(PHXConnectionError("temporary network failure"), SUCCEEDS)

        start = asyncio.get_running_loop().time()
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await client.__aenter__()

        assert len(now_s_seen) == 1
        # The probe alone burns probe_delay_s; if `now` were captured after it,
        # the recorded gap would be at least that large.
        assert now_s_seen[0] - start < probe_delay_s / 2

        await client.__aexit__(None, None, None)

    async def test_aenter_records_terminal_disconnect_when_session_returns_no_epoch(
        self,
        monkeypatch,
    ):
        """begin_attempt() returning None is unreachable given this loop's own
        control flow, but Session's own signature allows it -- if it ever
        happens, last_disconnect_reason must still be populated before raising,
        like every other Dead path in this class, so BandLink.connect() sees a
        terminal reason instead of silently keeping a stale one."""

        class DeadOnArrivalSession:
            def __init__(self, policy):
                pass

            def begin_attempt(self, now_s):
                return None

        monkeypatch.setattr(
            "band.client.streaming.client.Session", DeadOnArrivalSession
        )

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(RuntimeError, match="no longer connectable"):
            await client.__aenter__()

        assert client.last_disconnect_reason is not None
        assert client.last_disconnect_reason.retryable is False

    async def test_resolve_failed_connect_attempt_raises_when_retry_after_s_missing(
        self, monkeypatch, scripted_connect, scripted_probe
    ):
        """A non-Dead SessionOutcome with retry_after_s=None can't happen with the
        real Session today, but if that contract were ever violated, a bare
        `assert` would be stripped under `python -O` and surface as an unhandled
        `asyncio.sleep(None)` TypeError -- this must fail loudly instead, like
        the sibling `epoch is None` check in `__aenter__`."""

        class BadOutcomeSession:
            def __init__(self, policy):
                pass

            def begin_attempt(self, now_s):
                return 1

            def on_socket_close(self, epoch, now_s, close_code, jitter_sample):
                return SimpleNamespace(
                    state=SessionState.Reconnecting,
                    retry_after_s=None,
                    dead_reason=None,
                    stale_reason=None,
                )

        monkeypatch.setattr("band.client.streaming.client.Session", BadOutcomeSession)

        scripted_connect(PHXConnectionError("temporary network failure"))
        scripted_probe(SUCCEEDS)

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with pytest.raises(RuntimeError, match="no retry_after_s"):
            await client.__aenter__()
