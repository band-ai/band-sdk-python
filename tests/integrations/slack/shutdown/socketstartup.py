"""Two actual Socket Mode clients with the second connection held at its API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web
from slack_sdk.web.async_client import AsyncWebClient

from band.integrations.slack.socketclient import SettlingSocketModeClient
from band.integrations.slack.types import SlackApp
from tests.adapters.hold import Hold


@dataclass
class SocketStartupPeer:
    session: aiohttp.ClientSession
    web_client: AsyncWebClient
    second_connection: Hold
    clients: list[SettlingSocketModeClient] = field(default_factory=list)

    def make_client(
        self, app: SlackApp, web_client: AsyncWebClient
    ) -> SettlingSocketModeClient:
        client = SettlingSocketModeClient(
            app_token=app.app_token, web_client=web_client
        )
        client.proxy = None
        self.clients.append(client)
        return client

    @property
    def clients_settled(self) -> bool:
        return all(
            client.closed
            and client.aiohttp_client_session.closed
            and (client.current_session is None or client.current_session.closed)
            and all(
                task is None or task.done()
                for task in (
                    client.message_processor,
                    client.message_receiver,
                    client.current_session_monitor,
                )
            )
            for client in self.clients
        )

    async def close_clients(self) -> None:
        for client in self.clients:
            await client.close()


@asynccontextmanager
async def socket_startup_peer() -> AsyncIterator[SocketStartupPeer]:
    hold = Hold()
    base_url = ""

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for _ in socket:
            pass
        return socket

    async def connection_url(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") == "Bearer xapp-second":
            await hold.wait()
        return web.json_response({"ok": True, "url": f"{base_url}/socket"})

    server = web.Application()
    server.router.add_get("/socket", websocket)
    server.router.add_post("/api/apps.connections.open", connection_url)
    runner = web.AppRunner(server)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        base_url = f"http://127.0.0.1:{runner.addresses[0][1]}"
        async with aiohttp.ClientSession() as session:
            peer = SocketStartupPeer(
                session=session,
                web_client=AsyncWebClient(
                    token="xoxb-local", session=session, base_url=f"{base_url}/api/"
                ),
                second_connection=hold,
            )
            try:
                yield peer
            finally:
                hold.released.set()
                await peer.close_clients()
    finally:
        hold.released.set()
        await runner.cleanup()
