"""
Tests for WebSocket payload validation.

These tests ensure the SDK handles invalid payloads gracefully by logging
errors and skipping malformed events, rather than crashing the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from opentelemetry.sdk.trace import TracerProvider
from phoenix_channels_python_client.exceptions import PHXConnectionError
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import band_sdk_core
from band_sdk_core import DeadReason, SessionState

from band.credentials import PROXY_MANAGED_API_KEY
import band.client.streaming.wire as wire_module
from band.client.streaming import (
    DeliveryStatus,
    MessageCreatedPayload,
    SupersedePayload,
    WebSocketDisconnectReason,
    WebSocketUpgradeError,
    WireEvent,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    WebSocketClient,
)
from tests.websocket.conftest import SUCCEEDS, fast_session_policy

# Shared valid payload used by multiple tests
VALID_MESSAGE_CREATED_PAYLOAD: dict = {
    "id": "msg-123",
    "content": "@TestBot hi",
    "message_type": "text",
    "metadata": {
        "mentions": [{"id": "agent-123", "handle": "testbot", "name": "TestBot"}],
        "status": "sent",
    },
    "sender_id": "user-456",
    "sender_type": "User",
    "chat_room_id": "room-123",
    "thread_id": None,
    "inserted_at": "2025-11-17T11:20:10.284136Z",
    "updated_at": "2025-11-17T11:20:10.284136Z",
}


async def dispatch(client: WebSocketClient, event: str, payload: dict) -> Any:
    """Feed one event through _handle_events via a single registered
    callback; return what that callback received (None if never called)."""
    callback = AsyncMock()
    await client._handle_events(
        SimpleNamespace(event=event, payload=payload), {event: callback}
    )
    return callback.await_args.args[0] if callback.await_args else None


def _upgrade_exception(
    status_code: int, body: bytes, headers: dict[str, str] | None = None
):
    return InvalidStatus(
        Response(
            status_code=status_code,
            reason_phrase="error",
            headers=Headers(headers or {}),
            body=body,
        )
    )


# --- Invalid payload tests: verify graceful handling (log + skip) ---


async def test_skips_invalid_message_created_payload(caplog):
    """Should log error and skip when message_created payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing: content, sender_id, sender_type, etc.
        received = await dispatch(client, "message_created", {"id": "msg-123"})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid message_created payload" in caplog.text


async def test_trace_context_round_trips_through_band_sdk_core_to_the_log(caplog):
    """A real OpenTelemetry span's traceparent reaches the real band_sdk_core
    call, comes back on the raised ValueError, and lands on the seam's own
    log record -- through the actual OTel propagation and band_sdk_core
    binding, neither faked. Uses a standalone TracerProvider instance (never
    ``trace.set_tracer_provider``), so this doesn't touch the process-global
    provider other tests may depend on.
    """
    tracer = TracerProvider().get_tracer("test")
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        with tracer.start_as_current_span("probe") as span:
            span_context = span.get_span_context()
            # Missing required fields -- a genuine band_sdk_core rejection.
            received = await dispatch(client, "message_created", {"id": "msg-123"})

    assert received is None
    record = next(
        r for r in caplog.records if "Invalid message_created" in r.getMessage()
    )
    assert record.trace_context is not None
    assert format(span_context.trace_id, "032x") in record.trace_context
    assert format(span_context.span_id, "016x") in record.trace_context


async def test_skips_invalid_room_added_payload(caplog):
    """Should log error and skip when room_added payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing required fields: id, inserted_at, updated_at
        received = await dispatch(client, "room_added", {"title": "Test Room"})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid room_added payload" in caplog.text


async def test_rejects_room_added_missing_timestamps(caplog):
    """Regression test for INT-186: room_added without inserted_at/updated_at must be rejected."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing required: inserted_at, updated_at
        received = await dispatch(
            client, "room_added", {"id": "room-123", "title": "Test Room"}
        )

    assert received is None, "Callback should not be called without timestamps"
    assert "Invalid room_added payload" in caplog.text


async def test_skips_invalid_room_removed_payload(caplog):
    """Should log error and skip when room_removed payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing required field: id
        received = await dispatch(client, "room_removed", {"status": "closed"})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid room_removed payload" in caplog.text


async def test_skips_invalid_room_deleted_payload(caplog):
    """Should log error and skip when room_deleted payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        received = await dispatch(client, "room_deleted", {})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid room_deleted payload" in caplog.text


async def test_skips_invalid_participant_added_payload(caplog):
    """Should log error and skip when participant_added payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing required fields: name, type (only id is provided)
        received = await dispatch(client, "participant_added", {"id": "p-123"})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid participant_added payload" in caplog.text


async def test_skips_invalid_participant_removed_payload(caplog):
    """Should log error and skip when participant_removed payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with caplog.at_level(logging.ERROR):
        # Missing: id
        received = await dispatch(client, "participant_removed", {})

    assert received is None, "Callback should not be called for invalid payload"
    assert "Invalid participant_removed payload" in caplog.text


async def test_supersede_event_records_terminal_reason_and_disables_reconnect():
    """agent_control supersede should record the server reason and stop reconnects."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = type("MockPhoenix", (), {"auto_reconnect": True})()
    received_payload = None

    async def on_supersede(payload: SupersedePayload):
        nonlocal received_payload
        received_payload = payload
        outcome = client.handle_supersede(payload.retryable, payload.retry_after)
        client.record_terminal_disconnect(payload.to_disconnect_reason(outcome))

    class MockMessage:
        event = "supersede"
        payload = {
            "reason": "session.already_connected",
            "message": "This connection has been superseded by a newer session for this agent.",
            "retryable": False,
            "retry_after": 15,
            "target_socket_id": "agent_socket:agent-123",
            "correlation_id": "evict-123",
        }

    await client._handle_events(MockMessage(), {"supersede": on_supersede})

    assert isinstance(received_payload, SupersedePayload)
    assert client.client.auto_reconnect is False
    assert client.last_disconnect_reason == WebSocketDisconnectReason(
        reason="session.already_connected",
        message="This connection has been superseded by a newer session for this agent.",
        retryable=False,
        retry_after=15,
        target_socket_id="agent_socket:agent-123",
        correlation_id="evict-123",
        dead_reason=DeadReason.Classified,
    )


def test_parses_distinct_upgrade_errors_from_http_json_response():
    cases = [
        (
            409,
            b'{"error":{"code":"connection_conflict","message":"already connected","request_id":"req-409"}}',
            "connection_conflict",
            None,
        ),
        (
            400,
            b'{"error":{"code":"invalid_on_conflict","message":"bad on_conflict","request_id":"req-400"}}',
            "invalid_on_conflict",
            None,
        ),
        (
            503,
            b'{"error":{"code":"tracking_failed","message":"tracking unavailable","request_id":"req-503"}}',
            "tracking_failed",
            None,
        ),
        (
            429,
            b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-429","retry_after":12}}',
            "too_many_requests",
            12,
        ),
    ]

    for status_code, body, code, retry_after in cases:
        err = WebSocketUpgradeError.from_exception(
            _upgrade_exception(status_code, body, {"Retry-After": "30"})
        )

        assert err is not None
        assert err.status_code == status_code
        assert err.code == code
        assert err.request_id == f"req-{status_code}"
        assert err.retry_after == retry_after


def test_uses_retry_after_header_for_429_upgrade_error():
    err = WebSocketUpgradeError.from_exception(
        _upgrade_exception(
            429,
            b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-header"}}',
            {"Retry-After": "30"},
        )
    )

    assert err is not None
    assert err.retry_after == 30


def test_ignores_generic_auth_upgrade_error_without_json_contract():
    err = WebSocketUpgradeError.from_exception(_upgrade_exception(403, b""))

    assert err is None


async def test_aenter_wraps_upgrade_error(monkeypatch):
    upgrade_exc = _upgrade_exception(
        409,
        b'{"error":{"code":"connection_conflict","message":"already connected","request_id":"req-409"}}',
    )

    class FailingPHXClient:
        def __init__(self, *args, **kwargs):
            self.channel_socket_url = "wss://test/socket"

        async def __aenter__(self):
            raise upgrade_exc

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FailingPHXClient
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(WebSocketUpgradeError) as exc_info:
        await client.__aenter__()

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "connection_conflict"
    assert exc_info.value.request_id == "req-409"
    # WebSocketUpgradeError is a new object distinct from the raw exception
    # PHXChannelsClient raised -- explicit chaining keeps that link visible
    # in tracebacks and error trackers.
    assert exc_info.value.__cause__ is upgrade_exc


async def test_aenter_probes_initial_phx_connection_error_then_retries_and_succeeds(
    scripted_connect, scripted_probe, no_real_sleep
):
    """A 429 upgrade rejection -- recovered via _classify_connect_failure's
    probe fallback, since the raw PHXConnectionError carries no HTTP status
    itself -- classifies as Retry (classify_upgrade marks only 400/409
    Terminal), so it retries using Retry-After as a delay floor instead of
    raising on the first attempt, then succeeds once the rate limit clears."""
    upgrade_exc = _upgrade_exception(
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
    scripted_connect, scripted_probe, no_real_sleep
):
    """The live-socket probe recovering a PHXConnectionError's hidden upgrade
    rejection blocks for up to open_timeout=5s -- rerunning it on every
    backoff retry of the very same rejection would stack a fresh network
    round trip onto every computed delay. A repeat bare PHXConnectionError
    must reuse the first probe's classification instead of probing again,
    as long as that classification carries no retry_after to go stale."""
    upgrade_exc = _upgrade_exception(
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
    scripted_connect, scripted_probe, no_real_sleep
):
    """A repeated 429 always gets a fresh probe, even with an unchanged
    wrapped message: the wrapper's message is just "HTTP 429" -- it can't
    carry Retry-After -- so a cached 429 could silently reuse a stale delay
    while the server's current Retry-After has since grown."""
    stale = _upgrade_exception(429, b"", {"Retry-After": "5"})
    current = _upgrade_exception(429, b"", {"Retry-After": "60"})
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
    scripted_connect, scripted_probe, no_real_sleep
):
    """A repeat PHXConnectionError with a *different* message may be a
    genuinely different failure, not a repeat of the last one -- reusing
    the earlier probe's classification for it would hide a real, terminal
    rejection behind a stale retryable one."""
    conflict_exc = _upgrade_exception(
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


async def test_aenter_restores_reconnect_after_successful_initial_connect(monkeypatch):
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

    # A successful __aenter__ starts the watchdog against this double, which
    # has no real `.connection` -- __aexit__ stops it before returning so it
    # can never fire `_force_close_if_stale` against a torn-down test.
    await client.__aexit__(None, None, None)


async def test_aenter_reusable_after_aexit_ends_the_session(monkeypatch):
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
    scripted_connect, scripted_probe, no_real_sleep
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

    # no_real_sleep never advances the clock; an un-stopped watchdog's
    # deadline-check loop would spin on it with no real yield point.
    await client.__aexit__(None, None, None)


async def test_aenter_trips_dead_after_rapid_disconnect_threshold(
    scripted_connect, scripted_probe, no_real_sleep
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
    monkeypatch, scripted_connect
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

    async def slow_probe(websocket_url):
        await asyncio.sleep(probe_delay_s)
        return None

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

    monkeypatch.setattr("band.client.streaming.client.Session", DeadOnArrivalSession)

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(RuntimeError, match="no longer connectable"):
        await client.__aenter__()

    assert client.last_disconnect_reason is not None
    assert client.last_disconnect_reason.retryable is False


async def test_resolve_failed_connect_attempt_raises_when_retry_after_s_missing(
    monkeypatch, scripted_connect, scripted_probe
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


async def test_aenter_reraises_unrecognized_upgrade_error(monkeypatch):
    original_exc = RuntimeError("socket exploded")

    class FailingPHXClient:
        def __init__(self, *args, **kwargs):
            self.channel_socket_url = "wss://test/socket"

        async def __aenter__(self):
            raise original_exc

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FailingPHXClient
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(RuntimeError, match="socket exploded"):
        await client.__aenter__()


# --- Upgrade wire-shape test: real SDK connect against an in-process peer ---


@asynccontextmanager
async def upgrade_peer():
    """In-process WebSocket peer that captures the first upgrade request.

    Accepts the connection and records the query params and handshake headers —
    it speaks no Phoenix and imitates no proxy. Its sole job is to observe the
    real wire shape the SDK and its dependency produce on connect. Entered inside
    the test so the server shares the test's event loop. Yields
    ``(ws_url, upgrade)``, where ``upgrade`` is a future resolved with
    ``(query_params, headers)``.
    """
    upgrade: asyncio.Future[tuple[dict[str, list[str]], Headers]] = (
        asyncio.get_running_loop().create_future()
    )

    async def handler(conn: ServerConnection) -> None:
        if not upgrade.done():
            query = parse_qs(urlsplit(conn.request.path).query)
            upgrade.set_result((query, conn.request.headers))
        await conn.wait_closed()

    # Bind and connect on 127.0.0.1 explicitly: "localhost" on a dual-stack host
    # binds both ::1 and 127.0.0.1 on independently-chosen ephemeral ports, so
    # picking one socket's port then resolving "localhost" can hit the other.
    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}/socket/websocket", upgrade


async def test_upgrade_carries_api_key_in_query_and_x_api_key_header():
    """The proxy-managed sentinel rides both the `api_key` query parameter and
    the `x-api-key` handshake header on the real upgrade (alongside vsn and
    agent_id). The header is what the sandbox proxy substitutes and the platform
    authenticates off (with precedence); the query is retained for back-compat."""
    async with upgrade_peer() as (ws_url, upgrade):
        async with WebSocketClient(ws_url, PROXY_MANAGED_API_KEY, "agent-xyz"):
            params, headers = await asyncio.wait_for(upgrade, timeout=5)

    assert params["api_key"] == [PROXY_MANAGED_API_KEY]
    assert params["agent_id"] == ["agent-xyz"]
    assert params.get("vsn")  # protocol version retained alongside the sentinel
    assert headers["x-api-key"] == PROXY_MANAGED_API_KEY


# --- Valid payload tests ---


async def test_accepts_valid_message_created_payload():
    """Should accept valid message_created payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(client, "message_created", VALID_MESSAGE_CREATED_PAYLOAD)
    assert isinstance(received, MessageCreatedPayload)
    assert received.id == "msg-123"


# A message_updated frame as observed from the real backend: same shape as
# message_created, with per-recipient processing state under
# metadata.delivery_status keyed by the recipient (agent) id.
VALID_MESSAGE_UPDATED_PAYLOAD: dict = {
    "id": "msg-123",
    "content": "@TestBot hi",
    "message_type": "text",
    "metadata": {
        "mentions": [{"id": "agent-123", "handle": "testbot", "name": "TestBot"}],
        "status": "sent",
        "delivery_status": {
            "agent-123": {
                "status": "processed",
                "delivered_at": "2025-11-17T11:20:11.000000Z",
                "processed_at": "2025-11-17T11:20:13.000000Z",
                "attempts": [{"attempt_number": 1, "status": "success"}],
            }
        },
    },
    "sender_id": "user-456",
    "sender_type": "User",
    "chat_room_id": "room-123",
    "inserted_at": "2025-11-17T11:20:10.284136Z",
    "updated_at": "2025-11-17T11:20:13.000000Z",
}


async def test_accepts_message_updated_payload_with_delivery_status():
    """message_updated parses into MessageCreatedPayload and exposes delivery_status."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(client, "message_updated", VALID_MESSAGE_UPDATED_PAYLOAD)
    assert isinstance(received, MessageCreatedPayload)
    assert received.metadata is not None
    assert received.metadata.delivery_status == {
        "agent-123": {
            "status": "processed",
            "delivered_at": "2025-11-17T11:20:11.000000Z",
            "processed_at": "2025-11-17T11:20:13.000000Z",
            "attempts": [{"attempt_number": 1, "status": "success"}],
        }
    }


async def test_routes_message_created_and_updated_to_distinct_handlers():
    """When both handlers are registered, each event reaches only its own one."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    created_calls: list[str] = []
    updated_calls: list[str] = []

    async def on_created(payload):
        created_calls.append(payload.id)

    async def on_updated(payload):
        updated_calls.append(payload.id)

    handlers = {"message_created": on_created, "message_updated": on_updated}

    class CreatedMsg:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    class UpdatedMsg:
        event = "message_updated"
        payload = VALID_MESSAGE_UPDATED_PAYLOAD

    await client._handle_events(CreatedMsg(), handlers)
    await client._handle_events(UpdatedMsg(), handlers)

    assert created_calls == ["msg-123"]
    assert updated_calls == ["msg-123"]


def test_delivery_status_enum_matches_backend_values():
    """Drift guard: these are the recipient delivery states the backend emits
    (thenvoi-platform chat_message.ex). The barrier relies on the exact strings."""
    assert {s.value for s in DeliveryStatus} == {
        "delivered",
        "processing",
        "processed",
        "failed",
    }


def test_wire_event_matches_band_sdk_core_event_type():
    """Drift guard: WireEvent's members through AGENT_CONTROL are meant to
    mirror band_sdk_core.EventType's wire-name vocabulary exactly (they're
    kept as separate literals, not derived, since EventType is an opaque
    PyO3 type that can't be a StrEnum member's value)."""
    core_wire_names = {
        getattr(band_sdk_core.EventType, name).wire_name
        for name in dir(band_sdk_core.EventType)
        if isinstance(getattr(band_sdk_core.EventType, name), band_sdk_core.EventType)
    }
    assert core_wire_names <= {member.value for member in WireEvent}


async def test_ignores_message_updated_when_no_handler_registered(caplog):
    """Back-compat: a message_updated frame is dropped (not an error) when only
    message_created is wired up, as before the optional handler was added."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    created_calls: list[str] = []

    async def on_created(payload):
        created_calls.append(payload.id)

    class UpdatedMsg:
        event = "message_updated"
        payload = VALID_MESSAGE_UPDATED_PAYLOAD

    with caplog.at_level(logging.WARNING):
        await client._handle_events(UpdatedMsg(), {"message_created": on_created})

    assert created_calls == []  # not misrouted to the message_created handler


async def test_accepts_valid_room_added_payload():
    """Should accept valid room_added payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client,
        "room_added",
        {
            "id": "room-123",
            "title": "Test Room",
            "task_id": None,
            "inserted_at": "2025-11-17T09:05:35.642172Z",
            "updated_at": "2025-11-17T09:05:35.642172Z",
        },
    )
    assert isinstance(received, RoomAddedPayload)
    assert received.id == "room-123"


async def test_accepts_valid_room_removed_payload():
    """Should accept valid room_removed payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client,
        "room_removed",
        {
            "id": "room-123",
            "title": "Test Room",
            "task_id": "task-1",
            "inserted_at": "2025-11-17T09:05:35Z",
            "updated_at": "2025-11-17T11:26:59Z",
        },
    )
    assert isinstance(received, RoomRemovedPayload)
    assert received.id == "room-123"


async def test_accepts_minimal_room_removed_payload():
    """Should accept room_removed with only its required fields (title/task_id optional)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client,
        "room_removed",
        {
            "id": "room-456",
            "inserted_at": "2025-11-17T09:05:35Z",
            "updated_at": "2025-11-17T09:05:35Z",
        },
    )
    assert isinstance(received, RoomRemovedPayload)
    assert received.id == "room-456"
    assert received.title is None
    assert received.task_id is None


async def test_accepts_minimal_room_deleted_payload():
    """Should accept room_deleted with only required `id` field."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(client, "room_deleted", {"id": "room-789"})
    assert isinstance(received, RoomDeletedPayload)
    assert received.id == "room-789"


async def test_accepts_valid_participant_added_payload():
    """Should accept valid participant_added payload and pass typed model to callback."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client,
        "participant_added",
        {
            "id": "p-123",
            "name": "Test Agent",
            "type": "Agent",
            "is_remote": True,
            "is_external": True,
        },
    )
    assert isinstance(received, ParticipantAddedPayload)
    assert received.id == "p-123"
    assert received.name == "Test Agent"
    assert received.is_remote is True
    assert received.is_external is True


async def test_accepts_valid_participant_removed_payload():
    """Should accept valid participant_removed payload and pass typed model to callback."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client,
        "participant_removed",
        {"id": "p-123", "name": "Test Agent", "type": "Agent"},
    )
    assert isinstance(received, ParticipantRemovedPayload)
    assert received.id == "p-123"


async def test_join_room_participants_channel_allows_omitted_room_deleted_handler():
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = AsyncMock()

    async def on_participant_added(payload):
        pass

    async def on_participant_removed(payload):
        pass

    await client.join_room_participants_channel(
        "room-123",
        on_participant_added=on_participant_added,
        on_participant_removed=on_participant_removed,
    )

    client.client.subscribe_to_topic.assert_awaited_once()
    topic, message_handler = client.client.subscribe_to_topic.await_args.args
    assert topic == "room_participants:room-123"

    class MockMessage:
        event = "room_deleted"
        payload = {"id": "room-123"}

    await message_handler(MockMessage())


async def test_join_room_participants_channel_routes_room_deleted_handler():
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = AsyncMock()
    received_payload = None

    async def on_participant_added(payload):
        pass

    async def on_participant_removed(payload):
        pass

    async def on_room_deleted(payload):
        nonlocal received_payload
        received_payload = payload

    await client.join_room_participants_channel(
        "room-123",
        on_participant_added=on_participant_added,
        on_participant_removed=on_participant_removed,
        on_room_deleted=on_room_deleted,
    )

    _, message_handler = client.client.subscribe_to_topic.await_args.args

    class MockMessage:
        event = "room_deleted"
        payload = {"id": "room-123"}

    await message_handler(MockMessage())

    assert isinstance(received_payload, RoomDeletedPayload)
    assert received_payload.id == "room-123"


@pytest.mark.parametrize(
    ("event_name", "base_payload", "expected_type"),
    [
        pytest.param(
            "message_created",
            {
                "id": "msg-123",
                "content": "hi",
                "message_type": "text",
                "metadata": {
                    "mentions": [{"id": "a-1", "handle": "bot", "name": "Bot"}],
                    "status": "sent",
                },
                "sender_id": "u-1",
                "sender_type": "User",
                "chat_room_id": "r-1",
                "thread_id": None,
                "inserted_at": "2025-11-17T11:20:10Z",
                "updated_at": "2025-11-17T11:20:10Z",
            },
            MessageCreatedPayload,
            id="message_created",
        ),
        pytest.param(
            "room_added",
            {
                "id": "room-123",
                "owner": {"id": "u-1", "name": "User", "type": "User"},
                "status": "active",
                "type": "direct",
                "title": "Room",
                "inserted_at": "2025-11-17T09:05:35Z",
                "updated_at": "2025-11-17T09:05:35Z",
                "participant_role": "member",
            },
            RoomAddedPayload,
            id="room_added",
        ),
        pytest.param(
            "room_removed",
            {
                "id": "room-123",
                "title": "Room",
                "inserted_at": "2025-11-17T09:05:35Z",
                "updated_at": "2025-11-17T11:26:59Z",
            },
            RoomRemovedPayload,
            id="room_removed",
        ),
        pytest.param(
            "room_deleted",
            {"id": "room-123"},
            RoomDeletedPayload,
            id="room_deleted",
        ),
        pytest.param(
            "participant_added",
            {"id": "p-123", "name": "Agent", "type": "Agent"},
            ParticipantAddedPayload,
            id="participant_added",
        ),
        pytest.param(
            "participant_removed",
            {"id": "p-123", "name": "Agent", "type": "Agent"},
            ParticipantRemovedPayload,
            id="participant_removed",
        ),
    ],
)
async def test_allows_extra_fields_in_payload(event_name, base_payload, expected_type):
    """Should accept payloads with extra fields (forward compatibility)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    extra_fields = {"extra_field_1": "some value", "extra_field_2": 42}
    received = await dispatch(client, event_name, {**base_payload, **extra_fields})
    assert isinstance(received, expected_type)


async def test_skips_unknown_event_without_handler(caplog):
    """Should warn when receiving an event with no registered handler."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "unknown_event"
        payload = {"data": "test"}

    with caplog.at_level(logging.WARNING):
        await client._handle_events(MockMessage(), {})

    assert "no handler registered" in caplog.text


async def test_event_created_without_handler_logs_at_debug_not_warning(caplog):
    """event_created has no handler/PlatformEvent anywhere yet (event rows are read
    back over REST instead) -- this is expected, so it must not warn like a
    genuinely unregistered event does, but it should still be visible at debug."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "event_created"
        payload = {"data": "test"}

    with caplog.at_level(logging.DEBUG, logger="band.client.streaming.client"):
        await client._handle_events(MockMessage(), {})

    unhandled = [r for r in caplog.records if "no handler registered" in r.message]
    assert len(unhandled) == 1
    assert unhandled[0].levelno == logging.DEBUG


async def test_passes_raw_dict_for_unknown_event_types():
    """Should pass raw payload dict for event types without Pydantic models."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received = await dispatch(
        client, "task_created", {"task_id": "t-123", "status": "pending"}
    )
    assert received == {"task_id": "t-123", "status": "pending"}


# --- Validation error counter tests ---


async def test_validation_error_count_increments_on_invalid_payload(caplog):
    """Should increment validation_error_count when a payload fails validation."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    assert client.validation_error_count == 0

    invalid_payload = {"id": "msg-123"}  # Missing required fields
    with caplog.at_level(logging.ERROR):
        await dispatch(client, "message_created", invalid_payload)

    assert client.validation_error_count == 1

    # Send another invalid payload to verify it keeps incrementing
    with caplog.at_level(logging.ERROR):
        await dispatch(client, "message_created", invalid_payload)

    assert client.validation_error_count == 2


async def test_validation_error_count_stays_zero_on_valid_payload():
    """Should not increment validation_error_count for valid payloads."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    await dispatch(client, "message_created", VALID_MESSAGE_CREATED_PAYLOAD)
    assert client.validation_error_count == 0


async def test_hydration_shape_mismatch_is_dropped_and_counted_not_raised(
    monkeypatch, caplog
):
    """A payload band-sdk-core accepts but whose shape can't be hydrated must be
    dropped and counted like any other invalid event, never escape the seam.

    Fault-injects a shape band-sdk-core's own rules don't happen to catch, to
    exercise the seam's `except (TypeError, AttributeError)` branch: `_hydrate`
    raises `TypeError` walking a non-dict mention item, which escapes
    `_handle_events` uncaught without that branch.
    """

    def fake_validate(event_type, raw, trace_context=None):
        return {**raw, "metadata": {"mentions": [1]}}

    monkeypatch.setattr(
        wire_module.band_sdk_core, "validate_event_payload", fake_validate
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    with caplog.at_level(logging.ERROR):
        received = await dispatch(
            client, WireEvent.MESSAGE_CREATED, VALID_MESSAGE_CREATED_PAYLOAD
        )

    assert received is None
    assert client.validation_error_count == 1
    assert "message_created payload passed band-sdk-core but failed to hydrate" in (
        caplog.text
    )


async def test_reset_validation_error_count_returns_previous_value():
    """Should reset validation_error_count back to zero and return old value."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    # Drive the counter up
    await dispatch(client, "message_created", {"id": "msg-123"})  # Missing fields
    assert client.validation_error_count == 1

    old_count = client.reset_validation_error_count()
    assert old_count == 1
    assert client.validation_error_count == 0


async def test_callback_exception_does_not_crash_handler(caplog):
    """Should log exception and not propagate when callback raises."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    async def failing_callback(payload):
        raise RuntimeError("callback boom")

    with caplog.at_level(logging.ERROR):
        await client._handle_events(
            MockMessage(), {"message_created": failing_callback}
        )

    assert "Callback error for message_created event" in caplog.text
    assert client.validation_error_count == 0


async def test_cancelled_error_propagates_through_callback():
    """CancelledError raised in callback must propagate (not be swallowed)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    async def cancelling_callback(payload):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await client._handle_events(
            MockMessage(), {"message_created": cancelling_callback}
        )


# --- Heartbeat dead-threshold watchdog tests (INT-1323) ---


@asynccontextmanager
async def phoenix_peer(*, ack_heartbeats: bool = True):
    """In-process peer that completes the WS upgrade and, when
    ack_heartbeats, replies to every V2 heartbeat frame (topic "phoenix")
    with a matching phx_reply -- a real wire heartbeat round-trip drives
    on_heartbeat_ack, nothing about it is mocked. Yields ``(ws_url,
    connected)``, where ``connected`` resolves with the first accepted
    ServerConnection.
    """
    connected: asyncio.Future[ServerConnection] = (
        asyncio.get_running_loop().create_future()
    )

    async def handler(conn: ServerConnection) -> None:
        if not connected.done():
            connected.set_result(conn)
        async for raw in conn:
            if not ack_heartbeats:
                continue
            join_ref, ref, topic, _event, _payload = json.loads(raw)
            if topic == "phoenix":
                await conn.send(
                    json.dumps(
                        [
                            join_ref,
                            ref,
                            "phoenix",
                            "phx_reply",
                            {"status": "ok", "response": {}},
                        ]
                    )
                )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}/socket/websocket", connected


async def test_watchdog_ack_keeps_connection_alive_across_heartbeat_cycles():
    """A real heartbeat/ack round-trip resets the watchdog deadline each
    cycle, so the connection survives well past a single dead_threshold_s
    window without the watchdog ever tripping."""
    policy = fast_session_policy(heartbeat_interval_s=0.15, dead_threshold_s=0.4)
    async with phoenix_peer() as (ws_url, connected):
        async with WebSocketClient(
            ws_url, "test-key", "agent-123", session_policy=policy
        ) as client:
            await asyncio.wait_for(connected, timeout=5)
            await asyncio.sleep(1.0)
            assert client.client is not None
            assert client.client.connection is not None
            assert client.client.connection.close_code is None


async def test_watchdog_forces_close_and_reconnect_when_ack_withheld():
    """A withheld ack forces close_connection at (approximately)
    dead_threshold_s, and the forced close flows into the client's own
    reconnect path -- on_reconnect fires once the new connection lands."""
    policy = fast_session_policy(heartbeat_interval_s=0.15, dead_threshold_s=0.4)
    reconnected = asyncio.Event()

    async def on_reconnect() -> None:
        reconnected.set()

    async with phoenix_peer(ack_heartbeats=False) as (ws_url, connected):
        async with WebSocketClient(
            ws_url,
            "test-key",
            "agent-123",
            on_reconnect=on_reconnect,
            session_policy=policy,
        ):
            await asyncio.wait_for(connected, timeout=5)
            start = asyncio.get_running_loop().time()
            await asyncio.wait_for(reconnected.wait(), timeout=5)
            elapsed = asyncio.get_running_loop().time() - start

    assert elapsed >= policy.dead_threshold_s * 0.5


async def test_watchdog_task_cancelled_cleanly_on_aexit():
    """__aexit__ stops the watchdog -- no dangling task survives shutdown.

    (HeartbeatWatchdog's own cancellation behavior is covered directly in
    test_watchdog.py; this test only checks WebSocketClient wires __aexit__
    to it.)"""
    policy = fast_session_policy(heartbeat_interval_s=0.05, dead_threshold_s=5.0)
    async with phoenix_peer() as (ws_url, connected):
        client = WebSocketClient(ws_url, "test-key", "agent-123", session_policy=policy)
        async with client:
            await asyncio.wait_for(connected, timeout=5)
            watchdog_task = client._watchdog._task
            assert watchdog_task is not None
            assert not watchdog_task.done()

    assert watchdog_task.done()
    assert watchdog_task.cancelled()
    assert client._watchdog._task is None
