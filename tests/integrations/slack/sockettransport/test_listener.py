"""Socket listener behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.integrations.slack.dedup import SeenEvents
from band.integrations.slack.socket import (
    SlackSocketListener,
    _make_request_handler,
    start_socket_listeners,
)
from band.integrations.slack.types import SlackApp
from tests.integrations.slack.socketkit import (
    FakeSocketModeClient,
    _build_adapter_with_socket,
    _events_api_request,
    _socket_app,
)


class TestSocketListener:
    @pytest.mark.asyncio
    async def test_start_socket_listeners_connects_one_per_app(self):
        apps = [_socket_app("dev"), _socket_app("prod")]
        fakes: dict[str, FakeSocketModeClient] = {
            a.slug: FakeSocketModeClient() for a in apps
        }

        def factory(app: SlackApp, web_client: Any) -> FakeSocketModeClient:
            return fakes[app.slug]

        dispatcher = AsyncMock()
        listeners = await start_socket_listeners(
            apps=apps,
            web_client_factory=lambda a: AsyncMock(),
            dispatcher=dispatcher,
            client_factory=factory,
        )

        assert len(listeners) == 2
        for fake in fakes.values():
            assert fake.connect.await_count == 1
            assert len(fake.socket_mode_request_listeners) == 1
        assert {listener.app.slug for listener in listeners} == {"dev", "prod"}

    @pytest.mark.asyncio
    async def test_socket_listener_acks_and_dispatches_events_api(self):
        apps = [_socket_app("dev")]
        fake = FakeSocketModeClient()
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
    async def test_socket_listener_ignores_non_events_api_but_still_acks(self):
        apps = [_socket_app("dev")]
        fake = FakeSocketModeClient()
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
    async def test_socket_listener_swallows_dispatcher_exception(self):
        """A crashing dispatcher must not propagate up to slack-sdk's loop."""
        apps = [_socket_app("dev")]
        fake = FakeSocketModeClient()
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

    @pytest.mark.asyncio
    async def test_socket_listener_drops_bot_events(self, monkeypatch):
        """Bot-authored events must be ignored on the Socket path too."""
        adapter, inner, socket_clients, _, _ = _build_adapter_with_socket()
        fake = next(iter(socket_clients.values()))

        async def fake_start_socket_listeners(
            *, apps, web_client_factory, dispatcher, client_factory=None
        ):

            for app in apps:
                fake.socket_mode_request_listeners.append(
                    _make_request_handler(
                        app=app, dispatcher=dispatcher, seen_events=SeenEvents()
                    )
                )
                await fake.connect()
            return [SlackSocketListener(app=adapter.config.apps[0], client=fake)]

        monkeypatch.setattr(
            "band.integrations.slack.socket.start_socket_listeners",
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

    @pytest.mark.asyncio
    async def test_socket_listener_drops_duplicate_event_id(self):
        """A redelivered event (same event_id) is acked but dispatched once.

        Socket Mode can replay events across reconnects; like the HTTP route,
        the listener dedups on ``event_id`` so the brain isn't invoked twice.
        """

        dispatcher = AsyncMock()
        client = SimpleNamespace(send_socket_mode_response=AsyncMock())
        app = SlackApp(slug="dev", bot_token="xoxb-x", app_token="xapp-x")
        handler = _make_request_handler(
            app=app, dispatcher=dispatcher, seen_events=SeenEvents()
        )

        await handler(client, _events_api_request(envelope_id="e1", event_id="Ev123"))
        await handler(client, _events_api_request(envelope_id="e2", event_id="Ev123"))

        # Both envelopes acked (so Slack stops), but the brain runs once.
        assert client.send_socket_mode_response.await_count == 2
        dispatcher.assert_awaited_once()
        assert dispatcher.await_args.args[1]["event_id"] == "Ev123"
