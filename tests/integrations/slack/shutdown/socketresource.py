"""A real Slack API/WebSocket peer sharing the custom tool's HTTP session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiohttp
from aiohttp import web
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient

from tests.adaptercleanup import Host, HostKind


@dataclass
class SocketResource:
    session: aiohttp.ClientSession
    web_client: AsyncWebClient
    sockets: asyncio.Queue[web.WebSocketResponse]
    reconnecting: asyncio.Event
    finalizing: asyncio.Event
    release: asyncio.Event
    finished: asyncio.Event
    base_url: str

    async def read(self) -> str:
        async with self.session.get(f"{self.base_url}/resource") as response:
            return (await response.json())["value"]

    async def reconnect(self, *, refresh: bool) -> None:
        socket = await self.sockets.get()
        if refresh:
            await socket.send_json(
                {"type": "disconnect", "reason": "refresh_requested"}
            )
        else:
            await socket.close()
        await self.reconnecting.wait()


@dataclass
class SocketResourceHost:
    peer: SocketResource
    host: Host
    client: SocketModeClient
    kind: HostKind


@asynccontextmanager
async def socket_resource() -> AsyncIterator[SocketResource]:
    sockets: asyncio.Queue[web.WebSocketResponse] = asyncio.Queue()
    reconnecting, release_connection_request = asyncio.Event(), asyncio.Event()
    finalizing, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    connection_attempts = 0
    base_url = ""

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        sockets.put_nowait(socket)
        async for _ in socket:
            pass
        return socket

    async def connection_url(request: web.Request) -> web.Response:
        nonlocal connection_attempts
        connection_attempts += 1
        if connection_attempts > 1:
            reconnecting.set()
            await release_connection_request.wait()
        return web.json_response({"ok": True, "url": f"{base_url}/socket"})

    async def resource(request: web.Request) -> web.Response:
        return web.json_response({"value": "open"})

    async def finalize_reconnect_request(
        session: aiohttp.ClientSession,
        context: object,
        params: aiohttp.TraceRequestExceptionParams,
    ) -> None:
        if params.url.path.endswith("apps.connections.open") and reconnecting.is_set():
            finalizing.set()
            await release.wait()
            async with session.get(f"{base_url}/resource") as response:
                assert (await response.json())["value"] == "open"
            finished.set()

    server = web.Application()
    server.router.add_get("/socket", websocket)
    server.router.add_post("/api/apps.connections.open", connection_url)
    server.router.add_get("/resource", resource)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base_url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    trace = aiohttp.TraceConfig()
    trace.on_request_exception.append(finalize_reconnect_request)
    try:
        async with aiohttp.ClientSession(trace_configs=[trace]) as session:
            yield SocketResource(
                session=session,
                web_client=AsyncWebClient(
                    token="xoxb-local", session=session, base_url=f"{base_url}/api/"
                ),
                sockets=sockets,
                reconnecting=reconnecting,
                finalizing=finalizing,
                release=release,
                finished=finished,
                base_url=base_url,
            )
    finally:
        release.set()
        release_connection_request.set()
        await runner.cleanup()
