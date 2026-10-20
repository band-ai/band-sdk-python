"""Socket lifecycle behavior."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.dedup import SeenEvents
from band.integrations.slack.socket import (
    SlackSocketListener,
    _make_request_handler,
)
from tests.integrations.slack.socketkit import (
    _build_adapter_with_socket,
    _events_api_request,
    _http_app,
    _socket_app,
)
from tests.integrations.slack.wrappingkit import (
    SlackReplyBrain,
)


class TestSocketLifecycle:
    @pytest.mark.asyncio
    async def test_on_started_connects_socket_listeners(self, monkeypatch):
        adapter, _, socket_clients, _, _ = _build_adapter_with_socket()

        captured: dict[str, Any] = {}

        async def fake_start_socket_listeners(
            *, apps, listeners, web_client_factory, dispatcher, client_factory=None
        ):
            captured["dispatcher"] = dispatcher
            for app in apps:
                fake = socket_clients[app.slug]
                fake.socket_mode_request_listeners.append(lambda *_a, **_kw: None)
                await fake.connect()
                listeners.append(SlackSocketListener(app=app, client=fake))

        monkeypatch.setattr(
            "band.integrations.slack.socket.start_socket_listeners",
            fake_start_socket_listeners,
        )

        await adapter.on_started("MyBot", "")

        for fake in socket_clients.values():
            assert fake.connect.await_count == 1
        assert callable(captured["dispatcher"])
        assert len(adapter._socket_listeners) == 1

    @pytest.mark.asyncio
    async def test_close_stops_all_listeners(self):
        adapter, _, socket_clients, _, _ = _build_adapter_with_socket(
            apps=[_socket_app("dev"), _socket_app("prod")],
        )
        # Manually wire up listeners as if on_started had run.
        for slug, fake in socket_clients.items():
            app = next(a for a in adapter.config.apps if a.slug == slug)
            adapter._socket_listeners.append(SlackSocketListener(app=app, client=fake))

        await adapter.close()
        for fake in socket_clients.values():
            assert fake.close.await_count == 1
        assert adapter._socket_listeners == []

    @pytest.mark.asyncio
    async def test_close_logs_but_does_not_raise_on_sdk_close_failure(self, caplog):
        adapter, _, socket_clients, _, _ = _build_adapter_with_socket()
        fake = next(iter(socket_clients.values()))
        fake.close = AsyncMock(side_effect=[RuntimeError("boom"), None])
        app = adapter.config.apps[0]
        adapter._socket_listeners.append(SlackSocketListener(app=app, client=fake))

        with caplog.at_level("ERROR"):
            await adapter.close()
        record = next(r for r in caplog.records if "Failed to stop" in r.message)
        assert record.levelno == logging.ERROR
        await adapter.close()
        assert fake.close.await_count == 2
        await adapter.close()
        assert fake.close.await_count == 2

    @pytest.mark.asyncio
    async def test_close_is_safe_when_no_listeners(self):
        adapter = SlackAdapter(
            SlackAdapterConfig(apps=(_http_app(),)),
            inner=SlackReplyBrain(),
            rest_client=MagicMock(),
        )
        await adapter.close()  # Must not raise.

    @pytest.mark.asyncio
    async def test_events_api_envelope_routes_through_dispatch_event(self, monkeypatch):
        """A Socket Mode envelope reaches ``SlackAdapter._dispatch_event`` with
        the same ``(app, payload)`` shape the HTTP webhook produces."""
        adapter, inner, socket_clients, _web_mocks, _ = _build_adapter_with_socket()
        fake = next(iter(socket_clients.values()))

        async def fake_start_socket_listeners(
            *, apps, listeners, web_client_factory, dispatcher, client_factory=None
        ):
            for app in apps:
                client = socket_clients[app.slug]
                # Build the real per-app handler.

                client.socket_mode_request_listeners.append(
                    _make_request_handler(
                        app=app, dispatcher=dispatcher, seen_events=SeenEvents()
                    )
                )
                await client.connect()
                listeners.append(SlackSocketListener(app=app, client=client))

        monkeypatch.setattr(
            "band.integrations.slack.socket.start_socket_listeners",
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
