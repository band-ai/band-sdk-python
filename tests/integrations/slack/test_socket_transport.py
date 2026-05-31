"""Tests for the Socket Mode transport.

Covers:

- Constructor validation per transport (HTTP needs signing_secret,
  Socket Mode needs app_token).
- ``router`` is unavailable when transport='socket'.
- Socket Mode lifecycle: connect on ``on_started``, disconnect on
  ``close()``; failure-on-stop is logged not raised.
- An ``events_api`` Socket Mode envelope is acked AND routed through
  the same ``_dispatch_event`` path as the HTTP transport.
- Non-events_api envelopes are acked and ignored.
- Bot-authored events still dropped on the socket path (parity with HTTP).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from thenvoi.integrations.slack.adapter import SlackAdapter
from thenvoi.integrations.slack.socket import (
    SlackSocketListener,
    start_socket_listeners,
)
from thenvoi.integrations.slack.types import SlackApp

from tests.integrations.slack.test_wrapping import (
    _SlackReplyBrain,
    _make_rest_mock,
)


# ── Fixtures / helpers ──────────────────────────────────────────────────────


def _socket_app(slug: str = "dev") -> SlackApp:
    return SlackApp(
        slug=slug,
        bot_token=f"xoxb-{slug}",
        app_token=f"xapp-{slug}",
    )


def _http_app(slug: str = "dev") -> SlackApp:
    return SlackApp(
        slug=slug,
        bot_token=f"xoxb-{slug}",
        signing_secret="test-secret",
    )


class _FakeSocketModeClient:
    """Stand-in for ``slack_sdk.socket_mode.aiohttp.SocketModeClient``.

    Captures registered listeners, exposes ``connect`` / ``disconnect`` /
    ``send_socket_mode_response`` as AsyncMocks so the test can fire a
    synthetic envelope at the listener and assert what happened.
    """

    def __init__(self) -> None:
        self.socket_mode_request_listeners: list[Any] = []
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.send_socket_mode_response = AsyncMock()


def _build_adapter_with_socket(
    *,
    apps: list[SlackApp] | None = None,
    inner: _SlackReplyBrain | None = None,
) -> tuple[
    SlackAdapter,
    _SlackReplyBrain,
    dict[str, _FakeSocketModeClient],
    dict[str, AsyncMock],
    Any,
]:
    apps = apps or [_socket_app()]
    inner = inner or _SlackReplyBrain(reply=None)

    web_mocks: dict[str, AsyncMock] = {}
    for app in apps:
        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "x"})
        client.assistant_threads_setStatus = AsyncMock(return_value={"ok": True})
        client.conversations_replies = AsyncMock(return_value={"messages": []})
        web_mocks[app.slug] = client

    rest = _make_rest_mock(["room-1", "room-2"])
    adapter = SlackAdapter(
        inner=inner,
        apps=apps,
        api_key="k",
        transport="socket",
        rest_client=rest,
        web_client_factory=lambda a: web_mocks[a.slug],
    )
    adapter._thenvoi_agent_id = "bridge-uuid"  # type: ignore[attr-defined]

    socket_clients: dict[str, _FakeSocketModeClient] = {
        a.slug: _FakeSocketModeClient() for a in apps
    }
    return adapter, inner, socket_clients, web_mocks, rest


def _events_api_request(
    *,
    envelope_id: str = "env-1",
    event: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """A minimal SocketModeRequest stand-in. The real class has more on
    it, but our listener only touches ``envelope_id``, ``type``, and
    ``payload``."""
    payload = {
        "type": "event_callback",
        "event": event
        or {
            "type": "app_mention",
            "channel": "C123",
            "ts": "1700000000.0001",
            "thread_ts": "1700000000.0001",
            "text": "<@U001> hello",
            "user": "U999",
        },
    }
    return SimpleNamespace(envelope_id=envelope_id, type="events_api", payload=payload)


# ── Constructor validation ──────────────────────────────────────────────────


def test_http_transport_requires_signing_secret():
    bad = SlackApp(slug="x", bot_token="xoxb", signing_secret="")
    with pytest.raises(ValueError, match="signing_secret"):
        SlackAdapter(
            inner=_SlackReplyBrain(),
            apps=[bad],
            api_key="k",
            rest_client=MagicMock(),
        )


def test_socket_transport_requires_app_token():
    bad = SlackApp(slug="x", bot_token="xoxb", signing_secret="ignored")
    with pytest.raises(ValueError, match="app_token"):
        SlackAdapter(
            inner=_SlackReplyBrain(),
            apps=[bad],
            api_key="k",
            transport="socket",
            rest_client=MagicMock(),
        )


def test_socket_transport_accepts_apps_without_signing_secret():
    """Signing secret is unused over the websocket — must not be required."""
    adapter = SlackAdapter(
        inner=_SlackReplyBrain(),
        apps=[_socket_app()],
        api_key="k",
        transport="socket",
        rest_client=MagicMock(),
        web_client_factory=lambda a: AsyncMock(),
    )
    assert adapter.transport == "socket"


def test_unknown_transport_rejected():
    with pytest.raises(ValueError, match="Unknown transport"):
        SlackAdapter(
            inner=_SlackReplyBrain(),
            apps=[_http_app()],
            api_key="k",
            transport="banana",  # type: ignore[arg-type]
            rest_client=MagicMock(),
        )


def test_router_unavailable_in_socket_mode():
    adapter = SlackAdapter(
        inner=_SlackReplyBrain(),
        apps=[_socket_app()],
        api_key="k",
        transport="socket",
        rest_client=MagicMock(),
        web_client_factory=lambda a: AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="transport='http'"):
        _ = adapter.router


# ── start_socket_listeners ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_socket_listeners_connects_one_per_app():
    apps = [_socket_app("dev"), _socket_app("prod")]
    fakes: dict[str, _FakeSocketModeClient] = {
        a.slug: _FakeSocketModeClient() for a in apps
    }

    def factory(app: SlackApp, web_client: Any) -> _FakeSocketModeClient:
        return fakes[app.slug]

    dispatcher = AsyncMock()
    listeners = await start_socket_listeners(
        apps=apps,
        web_client_factory=lambda a: AsyncMock(),
        dispatcher=dispatcher,
        client_factory=factory,
    )

    assert len(listeners) == 2
    for slug, fake in fakes.items():
        assert fake.connect.await_count == 1
        assert len(fake.socket_mode_request_listeners) == 1
    assert {listener.app.slug for listener in listeners} == {"dev", "prod"}


@pytest.mark.asyncio
async def test_socket_listener_acks_and_dispatches_events_api():
    apps = [_socket_app("dev")]
    fake = _FakeSocketModeClient()
    dispatcher = AsyncMock()
    await start_socket_listeners(
        apps=apps,
        web_client_factory=lambda a: AsyncMock(),
        dispatcher=dispatcher,
        client_factory=lambda app, wc: fake,
    )

    handler = fake.socket_mode_request_listeners[0]
    req = _events_api_request(envelope_id="env-42")
    await handler(fake, req)

    # Ack first, dispatch second.
    fake.send_socket_mode_response.assert_awaited_once()
    response_arg = fake.send_socket_mode_response.await_args.args[0]
    assert response_arg.envelope_id == "env-42"

    dispatcher.assert_awaited_once()
    app_arg, payload_arg = dispatcher.await_args.args
    assert app_arg.slug == "dev"
    assert payload_arg["event"]["type"] == "app_mention"


@pytest.mark.asyncio
async def test_socket_listener_ignores_non_events_api_but_still_acks():
    apps = [_socket_app("dev")]
    fake = _FakeSocketModeClient()
    dispatcher = AsyncMock()
    await start_socket_listeners(
        apps=apps,
        web_client_factory=lambda a: AsyncMock(),
        dispatcher=dispatcher,
        client_factory=lambda app, wc: fake,
    )
    handler = fake.socket_mode_request_listeners[0]

    slash = SimpleNamespace(
        envelope_id="env-slash", type="slash_commands", payload={"command": "/x"}
    )
    await handler(fake, slash)

    fake.send_socket_mode_response.assert_awaited_once()
    dispatcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_socket_listener_swallows_dispatcher_exception():
    """A crashing dispatcher must not propagate up to slack-sdk's loop."""
    apps = [_socket_app("dev")]
    fake = _FakeSocketModeClient()
    boom = AsyncMock(side_effect=RuntimeError("kaboom"))
    await start_socket_listeners(
        apps=apps,
        web_client_factory=lambda a: AsyncMock(),
        dispatcher=boom,
        client_factory=lambda app, wc: fake,
    )

    handler = fake.socket_mode_request_listeners[0]
    # Should NOT raise.
    await handler(fake, _events_api_request())
    boom.assert_awaited_once()


# ── SlackAdapter lifecycle (transport='socket') ─────────────────────────────


@pytest.mark.asyncio
async def test_on_started_connects_socket_listeners(monkeypatch):
    adapter, _, socket_clients, _, _ = _build_adapter_with_socket()

    captured: dict[str, Any] = {}

    async def fake_start_socket_listeners(
        *, apps, web_client_factory, dispatcher, client_factory=None
    ):
        captured["dispatcher"] = dispatcher
        listeners = []
        for app in apps:
            fake = socket_clients[app.slug]
            fake.socket_mode_request_listeners.append(lambda *_a, **_kw: None)
            await fake.connect()
            listeners.append(SlackSocketListener(app=app, client=fake))
        return listeners

    monkeypatch.setattr(
        "thenvoi.integrations.slack.socket.start_socket_listeners",
        fake_start_socket_listeners,
    )

    await adapter.on_started("MyBot", "")

    for fake in socket_clients.values():
        assert fake.connect.await_count == 1
    assert callable(captured["dispatcher"])
    assert len(adapter._socket_listeners) == 1


@pytest.mark.asyncio
async def test_close_disconnects_all_listeners():
    adapter, _, socket_clients, _, _ = _build_adapter_with_socket(
        apps=[_socket_app("dev"), _socket_app("prod")],
    )
    # Manually wire up listeners as if on_started had run.
    for slug, fake in socket_clients.items():
        app = next(a for a in adapter.apps if a.slug == slug)
        adapter._socket_listeners.append(SlackSocketListener(app=app, client=fake))

    await adapter.close()
    for fake in socket_clients.values():
        assert fake.disconnect.await_count == 1
    assert adapter._socket_listeners == []


@pytest.mark.asyncio
async def test_close_logs_but_does_not_raise_on_disconnect_failure(caplog):
    adapter, _, socket_clients, _, _ = _build_adapter_with_socket()
    fake = next(iter(socket_clients.values()))
    fake.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
    app = adapter.apps[0]
    adapter._socket_listeners.append(SlackSocketListener(app=app, client=fake))

    with caplog.at_level("ERROR"):
        await adapter.close()
    assert any("Failed to stop" in r.message for r in caplog.records)
    assert adapter._socket_listeners == []


@pytest.mark.asyncio
async def test_close_is_safe_when_no_listeners():
    adapter = SlackAdapter(
        inner=_SlackReplyBrain(),
        apps=[_http_app()],
        api_key="k",
        rest_client=MagicMock(),
    )
    await adapter.close()  # Must not raise.


# ── End-to-end: Socket Mode envelope through SlackAdapter ────────────────────


@pytest.mark.asyncio
async def test_events_api_envelope_routes_through_dispatch_event(monkeypatch):
    """A Socket Mode envelope reaches ``SlackAdapter._dispatch_event`` with
    the same ``(app, payload)`` shape the HTTP webhook produces."""
    adapter, inner, socket_clients, web_mocks, _ = _build_adapter_with_socket()
    fake = next(iter(socket_clients.values()))

    async def fake_start_socket_listeners(
        *, apps, web_client_factory, dispatcher, client_factory=None
    ):
        listeners = []
        for app in apps:
            client = socket_clients[app.slug]
            # Build the real per-app handler.
            from thenvoi.integrations.slack.socket import _make_request_handler

            client.socket_mode_request_listeners.append(
                _make_request_handler(app=app, dispatcher=dispatcher)
            )
            await client.connect()
            listeners.append(SlackSocketListener(app=app, client=client))
        return listeners

    monkeypatch.setattr(
        "thenvoi.integrations.slack.socket.start_socket_listeners",
        fake_start_socket_listeners,
    )

    await adapter.on_started("MyBot", "")
    handler = fake.socket_mode_request_listeners[0]
    await handler(fake, _events_api_request())
    await adapter.wait_idle()

    # Same downstream as HTTP: room created, brain invoked.
    assert len(inner.invocations) == 1
    assert inner.invocations[0]["msg"].content == "<@U001> hello"
    # Ack was sent.
    fake.send_socket_mode_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_socket_listener_drops_bot_events(monkeypatch):
    """Bot-authored events must be ignored on the Socket path too."""
    adapter, inner, socket_clients, _, _ = _build_adapter_with_socket()
    fake = next(iter(socket_clients.values()))

    async def fake_start_socket_listeners(
        *, apps, web_client_factory, dispatcher, client_factory=None
    ):
        from thenvoi.integrations.slack.socket import _make_request_handler

        for app in apps:
            fake.socket_mode_request_listeners.append(
                _make_request_handler(app=app, dispatcher=dispatcher)
            )
            await fake.connect()
        return [SlackSocketListener(app=adapter.apps[0], client=fake)]

    monkeypatch.setattr(
        "thenvoi.integrations.slack.socket.start_socket_listeners",
        fake_start_socket_listeners,
    )

    await adapter.on_started("MyBot", "")
    handler = fake.socket_mode_request_listeners[0]
    bot_req = _events_api_request(
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "ts": "1.0",
            "text": "echo: previous",
            "bot_id": "B1",
        },
    )
    await handler(fake, bot_req)
    await adapter.wait_idle()

    assert inner.invocations == []
    # Still acked though, so Slack doesn't retry.
    fake.send_socket_mode_response.assert_awaited_once()
