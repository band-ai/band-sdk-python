"""Slack Socket Mode transport peers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp
from band.testing.platform import platform_connection_stub
from tests.integrations.slack.wrappingkit import (
    SlackReplyBrain,
    _make_rest_mock,
)


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


class FakeSocketModeClient:
    """Stand-in for ``slack_sdk.socket_mode.aiohttp.SocketModeClient``.

    Captures registered listeners, exposes ``connect`` / ``close`` /
    ``send_socket_mode_response`` as AsyncMocks so the test can fire a
    synthetic envelope at the listener and assert what happened.
    """

    def __init__(self) -> None:
        self.socket_mode_request_listeners: list[Any] = []
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self.send_socket_mode_response = AsyncMock()


def _build_adapter_with_socket(
    *,
    apps: list[SlackApp] | None = None,
    inner: SlackReplyBrain | None = None,
) -> tuple[
    SlackAdapter,
    SlackReplyBrain,
    dict[str, FakeSocketModeClient],
    dict[str, AsyncMock],
    Any,
]:
    apps = apps or [_socket_app()]
    inner = inner or SlackReplyBrain(reply=None)

    web_mocks: dict[str, AsyncMock] = {}
    for app in apps:
        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "x"})
        client.assistant_threads_setStatus = AsyncMock(return_value={"ok": True})
        client.conversations_replies = AsyncMock(return_value={"messages": []})
        web_mocks[app.slug] = client

    rest = _make_rest_mock(["room-1", "room-2"])
    adapter = SlackAdapter(
        SlackAdapterConfig(apps=tuple(apps), transport="socket"),
        inner=inner,
        rest_client=rest,
        web_client_factory=lambda a: web_mocks[a.slug],
    )
    adapter.platform = platform_connection_stub(agent_id="bridge-uuid")

    socket_clients: dict[str, FakeSocketModeClient] = {
        a.slug: FakeSocketModeClient() for a in apps
    }
    return adapter, inner, socket_clients, web_mocks, rest


def _events_api_request(
    *,
    envelope_id: str = "env-1",
    event: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> SimpleNamespace:
    """A minimal SocketModeRequest stand-in. The real class has more on
    it, but our listener only touches ``envelope_id``, ``type``, and
    ``payload``."""
    payload: dict[str, Any] = {
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
    if event_id is not None:
        payload["event_id"] = event_id
    return SimpleNamespace(envelope_id=envelope_id, type="events_api", payload=payload)
