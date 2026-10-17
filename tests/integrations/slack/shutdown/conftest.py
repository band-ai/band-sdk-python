"""Real Socket Mode connections to a local WebSocket peer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from aiohttp import web
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient

from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp
from tests.integrations.slack.shutdown.kit import GatedBrain
from tests.integrations.slack.socketkit import _socket_app
from tests.integrations.slack.wrappingkit import _make_rest_mock


@pytest_asyncio.fixture(loop_scope="function")
async def connected_socket_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[SlackAdapter, GatedBrain, SocketModeClient]]:
    async def websocket(request: web.Request) -> web.WebSocketResponse:
        peer = web.WebSocketResponse()
        await peer.prepare(request)
        async for _ in peer:
            pass
        return peer

    server = web.Application()
    server.router.add_get("/socket", websocket)
    runner = web.AppRunner(server)
    await runner.setup()
    clients: list[SocketModeClient] = []
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]

        def factory(app: SlackApp, web_client: AsyncWebClient) -> SocketModeClient:
            client = SocketModeClient(
                app_token=app.app_token,
                web_client=web_client,
                auto_reconnect_enabled=False,
            )
            client.proxy = None
            client.wss_uri = f"http://127.0.0.1:{port}/socket"
            clients.append(client)
            return client

        monkeypatch.setattr(
            "band.integrations.slack.socket._default_client_factory", factory
        )
        brain = GatedBrain()
        adapter = SlackAdapter(
            SlackAdapterConfig(apps=(_socket_app(),), transport="socket"),
            inner=brain,
            rest_client=_make_rest_mock([]),
            web_client_factory=lambda app: AsyncWebClient(token=app.bot_token),
        )
        await adapter.on_started("Bridge", "")
        yield adapter, brain, clients[0]
    finally:
        for client in clients:
            await client.close()
        await runner.cleanup()
