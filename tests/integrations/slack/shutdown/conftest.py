"""Real Socket Mode connections to a local WebSocket peer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from aiohttp import web
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient

from band.agent import Agent
from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp
from tests.adaptercleanup import Host, HostKind, PassiveAdapter, host_for
from tests.adapters.hold import Hold
from tests.integrations.slack.shutdown.kit import GatedBrain
from tests.integrations.slack.shutdown.socketresource import (
    SocketResourceHost,
    socket_resource,
)
from tests.integrations.slack.socketkit import _socket_app
from tests.integrations.slack.wrappingkit import _make_rest_mock


@pytest.fixture
def metadata_hold(monkeypatch: pytest.MonkeyPatch) -> Callable[[Host, bool], Hold]:
    def install(host: Host, fail: bool = False) -> Hold:
        hold = Hold()
        target = host.owner._runtime if isinstance(host.owner, Agent) else host.owner
        method = (
            "initialize" if isinstance(host.owner, Agent) else "_fetch_agent_metadata"
        )
        original = getattr(target, method)

        async def metadata() -> object:
            await hold.wait()
            if fail:
                raise LookupError("Metadata failed")
            return await original()

        monkeypatch.setattr(target, method, metadata)
        return hold

    return install


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


@pytest_asyncio.fixture(loop_scope="function", params=list(HostKind))
async def socket_resource_host(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SocketResourceHost]:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "")
    async with socket_resource() as peer:
        adapter = SlackAdapter(
            SlackAdapterConfig(apps=(_socket_app(),), transport="socket"),
            inner=PassiveAdapter(),
            web_client_factory=lambda app: peer.web_client,
        )
        adapter.add_cleanup(peer.session.close)
        async with host_for(adapter, request.param) as host:
            await host.start()
            client = adapter._socket_listeners[0].client
            try:
                yield SocketResourceHost(peer, host, client, request.param)
            finally:
                peer.release.set()
                await host.stop()
                await client.close()
