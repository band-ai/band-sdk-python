"""A real local MCP server outside Band that only accepts its current bearer
grant, recording every request it sees (for connection-time resolver tests)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP


@dataclass
class ExternalMcp:
    url: str
    grants: set[str]
    # (grant presented, HTTP status) per request.
    receipts: list[tuple[str, int]] = field(default_factory=list)

    def server(self, grant: str, **extra: Any) -> dict[str, Any]:
        """The native config a resolver returns for this server and grant."""
        return {
            "type": "http",
            "url": self.url,
            "headers": {"Authorization": f"Bearer {grant}"},
            **extra,
        }


@asynccontextmanager
async def external_mcp(*grants: str) -> AsyncIterator[ExternalMcp]:
    mcp = FastMCP("external", stateless_http=True)

    @mcp.tool()
    def echo(text: str) -> str:
        return text

    inner = mcp.streamable_http_app()
    state = ExternalMcp(url="", grants=set(grants))

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        auth = dict(scope["headers"]).get(b"authorization", b"").decode()
        grant = auth.removeprefix("Bearer ")
        if grant not in state.grants:
            state.receipts.append((grant, 401))
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        status: dict[str, int] = {}

        async def recording(message: Any) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await inner(scope, receive, recording)
        state.receipts.append((grant, status.get("code", 0)))

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        state.url = f"http://127.0.0.1:{port}/mcp"
        yield state
    finally:
        server.should_exit = True
        await serving
