"""Feasibility spike: prove the FastMCP-embedding mount recipe.

Prototypes mounting a bare ``FastMCP`` instance's ``sse_app()`` and
``streamable_http_app()`` onto a host Starlette app served by a copy of
``LocalMCPServer``'s existing socket-reservation/uvicorn lifecycle, with the
host lifespan entering ``session_manager.run()`` itself (mounting drops
``streamable_http_app()``'s own lifespan -- only the top-level ASGI app the
server was given ever receives lifespan events).

This gates the rest of the MCP engine migration: if this recipe did not
work end-to-end, the
"one engine, two front doors" design would not be buildable. Once step 9
builds the real ``local_server.py``, this file's helper is superseded by
that module and this test either moves onto it or is deleted -- it is a
feasibility gate, not permanent product code.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

HOST = "127.0.0.1"


def _build_mcp() -> FastMCP:
    """A fresh FastMCP instance -- rebuilt per start(), never reused.

    ``StreamableHTTPSessionManager.run()`` raises ``RuntimeError`` on a
    second call, so a start/stop/start cycle must rebuild the whole app,
    not just restart the server around a stale one.
    """
    mcp = FastMCP(name="spike-engine", host=HOST)

    @mcp.tool()
    async def echo(message: str) -> str:
        return message

    return mcp


def _mounted_app(mcp: FastMCP) -> Starlette:
    """Mount sse_app()'s and streamable_http_app()'s routes onto one host app.

    ``streamable_http_app()`` lazily creates ``mcp._session_manager`` (public
    accessor: ``mcp.session_manager``) and returns its own Starlette app whose
    lifespan runs it -- but a mounted sub-app's lifespan is never invoked by
    the ASGI server, only the top-level app's is. So the host app below wires
    that lifespan itself.
    """
    sse_routes = list(mcp.sse_app().routes)
    http_routes = list(mcp.streamable_http_app().routes)

    async def healthz(_: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            *sse_routes,
            *http_routes,
            Route("/healthz", endpoint=healthz, methods=["GET"]),
        ],
    )


class _RunningApp:
    """Minimal stand-in for LocalMCPServer's socket-reserve + uvicorn lifecycle."""

    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self.port: int | None = None

    async def start(self) -> None:
        mcp = _build_mcp()
        app = _mounted_app(mcp)

        reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reserved.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reserved.bind((HOST, 0))
        port = reserved.getsockname()[1]
        reserved.listen(2048)
        reserved.setblocking(False)

        server = uvicorn.Server(
            uvicorn.Config(
                app, host=HOST, port=port, lifespan="on", log_level="warning"
            )
        )
        serve_task = asyncio.create_task(server.serve(sockets=[reserved]))

        deadline = asyncio.get_running_loop().time() + 5.0
        while not server.started:
            if serve_task.done():
                await serve_task
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("spike server did not start in time")
            await asyncio.sleep(0.02)

        self._socket = reserved
        self._server = server
        self._serve_task = serve_task
        self.port = port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            with suppress(asyncio.CancelledError):
                await self._serve_task
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._server = None
        self._serve_task = None
        self.port = None

    @property
    def sse_url(self) -> str:
        return f"http://{HOST}:{self.port}/sse"

    @property
    def http_url(self) -> str:
        return f"http://{HOST}:{self.port}/mcp"

    @property
    def healthz_url(self) -> str:
        return f"http://{HOST}:{self.port}/healthz"


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_sse_and_streamable_http_and_health_mount_simultaneously() -> None:
    app = _RunningApp()
    await app.start()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(app.healthz_url)
        assert response.status_code == 200
        assert response.text == "ok"

        async with sse_client(app.sse_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                assert [tool.name for tool in tools_result.tools] == ["echo"]
                result = await session.call_tool("echo", {"message": "hi-sse"})
                assert not result.isError
                assert result.structuredContent == {"result": "hi-sse"}

        async with streamablehttp_client(app.http_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                assert [tool.name for tool in tools_result.tools] == ["echo"]
                result = await session.call_tool("echo", {"message": "hi-http"})
                assert not result.isError
                assert result.structuredContent == {"result": "hi-http"}
    finally:
        await app.stop()


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_start_stop_start_cycle_rebuilds_session_manager() -> None:
    """Session managers are single-use; a second start() must not resurrect
    the old FastMCP/session-manager instance, or its second .run() call
    raises RuntimeError."""
    app = _RunningApp()

    await app.start()
    first_port = app.port
    async with streamablehttp_client(f"http://{HOST}:{first_port}/mcp") as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            await session.list_tools()
    await app.stop()

    # Second cycle: _build_mcp() inside start() constructs a brand-new
    # FastMCP, so its session manager has never had .run() called on it yet.
    await app.start()
    try:
        async with streamablehttp_client(app.http_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                assert [tool.name for tool in tools_result.tools] == ["echo"]
    finally:
        await app.stop()


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_loopback_bind_auto_dns_rebinding_protection_accepts_real_clients() -> (
    None
):
    """FastMCP auto-enables DNS-rebinding protection on a loopback host
    (divergence-matrix row 17). A real client's default Host header
    (``127.0.0.1:<port>``) must be accepted -- not 421'd -- since the SDK's
    embedded adapters (opencode, letta, acp) all bind loopback by default."""
    app = _RunningApp()
    await app.start()
    try:
        async with streamablehttp_client(app.http_url) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
    finally:
        await app.stop()


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_loopback_bind_auto_dns_rebinding_protection_rejects_spoofed_host() -> (
    None
):
    """Same protection must actually reject a spoofed Host header -- proving
    it is live, not silently disabled by the mount."""
    app = _RunningApp()
    await app.start()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                app.http_url,
                headers={
                    "Host": "evil.example.com",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "spike", "version": "0"},
                    },
                },
            )
        assert response.status_code == 421
    finally:
        await app.stop()
