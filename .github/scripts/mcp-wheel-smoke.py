"""Exercise the installed MCP CLI and SDK engine without platform credentials."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from http import HTTPStatus
from importlib.metadata import version

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)
AGENT_KEY = "band_a_smoke"
IDENTITY_REQUEST = ("GET", "/api/v1/agent/me", AGENT_KEY)
IDENTITY = {
    "data": {
        "id": "00000000-0000-4000-8000-000000000001",
        "name": "Wheel smoke",
        "handle": "smoke/wheel",
        "owner_uuid": "00000000-0000-4000-8000-000000000002",
        "inserted_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "feature_flags": {},
    }
}


async def respond(
    requests: list[tuple[str, str, str]],
    status: HTTPStatus,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        request = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
        line, *headers = request.split("\r\n")
        method, path, _ = line.split()
        fields = {
            name.lower(): value.strip()
            for name, _, value in (
                header.partition(":") for header in headers if header
            )
        }
        observed = (method, path, fields.get("x-api-key", ""))
        requests.append(observed)
        if observed != IDENTITY_REQUEST:
            status = HTTPStatus.NOT_FOUND
        body = json.dumps(
            IDENTITY if status == HTTPStatus.OK else {"error": status.phrase}
        ).encode()
        writer.write(
            (
                f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Content-Type: application/json\r\nConnection: close\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def accept(
    tasks: asyncio.TaskGroup,
    requests: list[tuple[str, str, str]],
    status: HTTPStatus,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    tasks.create_task(respond(requests, status, reader, writer))


@asynccontextmanager
async def platform_peer(
    status: HTTPStatus = HTTPStatus.OK,
) -> AsyncIterator[tuple[str, list[tuple[str, str, str]]]]:
    requests: list[tuple[str, str, str]] = []
    async with asyncio.TaskGroup() as tasks:
        server = await asyncio.start_server(
            partial(accept, tasks, requests, status), "127.0.0.1", 0
        )
        async with server:
            port = server.sockets[0].getsockname()[1]
            yield f"http://127.0.0.1:{port}", requests


async def exercise_cli(base_url: str) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "band_mcp.server", "--agent-key", AGENT_KEY],
        env={"BAND_MCP_SCOPE": "agent", "BAND_BASE_URL": base_url},
    )
    async with asyncio.timeout(30):
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            by_name = {tool.name: tool for tool in tools.tools}
            assert "band_send_message" in by_name
            assert by_name["band_send_message"].inputSchema["properties"]["content"]
            assert "band_no_reply" in by_name
            result = await session.call_tool("band_no_reply", {})
            assert result == CallToolResult(
                content=[TextContent(type="text", text='{"status": "no_reply"}')],
                isError=False,
            ), result


async def main() -> None:
    async with platform_peer() as (base_url, requests):
        await exercise_cli(base_url)
        assert requests == [IDENTITY_REQUEST], requests
    logger.info(
        "Installed MCP %s with SDK %s: initialize, schemas and CLI tool call passed",
        version("band-mcp"),
        version("band-sdk"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
