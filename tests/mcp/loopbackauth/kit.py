"""Kit for the loopback bearer-token guard: real guarded servers, the
credentials a caller can present, and the raw requests that reach each route."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import BaseModel

from band.integrations.mcp.engine import MCPToolRegistration
from band.integrations.mcp.local_server import (
    LOCAL_MCP_AUTH_HEADER,
    LOCAL_MCP_HEALTH_PATH,
    LOCAL_MCP_HOST,
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_MESSAGE_PATH,
    LOCAL_MCP_ROOMS_PATH,
    LOCAL_MCP_SSE_PATH,
    LocalMCPServer,
    local_mcp_auth_headers,
)

AUTH_TOKEN = "loopback-auth-test-token"
ROOM = "room-a"
RECORD_TOOL = "record"


class Credential(StrEnum):
    """What a caller puts in its ``Authorization`` header."""

    VALID = "valid"
    MISSING = "missing"
    WRONG_TOKEN = "wrong-token"
    WRONG_SCHEME = "wrong-scheme"
    BARE_TOKEN = "bare-token"


REFUSED_CREDENTIALS = [c for c in Credential if c is not Credential.VALID]


def credential_headers(credential: Credential) -> dict[str, str]:
    match credential:
        case Credential.VALID:
            return local_mcp_auth_headers(AUTH_TOKEN)
        case Credential.MISSING:
            return {}
        case Credential.WRONG_TOKEN:
            return local_mcp_auth_headers("not-the-token")
        case Credential.WRONG_SCHEME:
            return {LOCAL_MCP_AUTH_HEADER: f"Basic {AUTH_TOKEN}"}
        case Credential.BARE_TOKEN:
            return {LOCAL_MCP_AUTH_HEADER: AUTH_TOKEN}


class Served(StrEnum):
    """Which of the two guarded servers a route lives on."""

    ROOM_BOUND = "room-bound"
    MULTI_ROOM = "multi-room"


@dataclass(frozen=True)
class Route:
    """One request a caller can make; ``{session_id}`` in ``path`` names the
    live SSE session's message endpoint."""

    served: Served
    method: str
    path: str
    body: dict[str, Any] | None = None


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "loopback-auth-test", "version": "0"},
    },
}

_ROOM_PREFIX = f"{LOCAL_MCP_ROOMS_PATH}/{ROOM}"
ROOM_HTTP = Route(
    Served.ROOM_BOUND, "POST", f"{_ROOM_PREFIX}{LOCAL_MCP_HTTP_PATH}", INITIALIZE
)
ROOM_SSE = Route(Served.ROOM_BOUND, "GET", f"{_ROOM_PREFIX}{LOCAL_MCP_SSE_PATH}")
SSE_MESSAGE = Route(
    Served.ROOM_BOUND,
    "POST",
    f"{_ROOM_PREFIX}{LOCAL_MCP_MESSAGE_PATH}?session_id={{session_id}}",
    {"jsonrpc": "2.0", "id": 2, "method": "ping"},
)
MULTI_ROOM_HTTP = Route(Served.MULTI_ROOM, "POST", LOCAL_MCP_HTTP_PATH, INITIALIZE)
MULTI_ROOM_SSE = Route(Served.MULTI_ROOM, "GET", LOCAL_MCP_SSE_PATH)
HEALTH = Route(Served.ROOM_BOUND, "GET", LOCAL_MCP_HEALTH_PATH)


class RecordInput(BaseModel):
    """Record a note."""

    note: str


def record_tool(recorded: list[str]) -> MCPToolRegistration:
    """A tool that appends each call's note to ``recorded``."""

    async def execute(arguments: dict[str, Any]) -> str:
        recorded.append(arguments["note"])
        return "recorded"

    return MCPToolRegistration(
        name=RECORD_TOOL,
        description="Record a note",
        input_model=RecordInput,
        execute=execute,
    )


def tool_call(note: str) -> dict[str, Any]:
    """A JSON-RPC ``tools/call`` of the record tool."""
    return {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": RECORD_TOOL, "arguments": {"note": note}},
    }


@dataclass
class GuardedServers:
    """A room-bound and a multi-room server requiring ``AUTH_TOKEN``, and an
    authorized, initialized SSE session on the room-bound one."""

    room_bound: LocalMCPServer
    multi_room: LocalMCPServer
    http: httpx.AsyncClient
    session: ClientSession
    session_ids: list[str]
    recorded: list[str]

    def url(self, route: Route) -> str:
        server = (
            self.room_bound if route.served is Served.ROOM_BOUND else self.multi_room
        )
        path = route.path.format(session_id=self.session_ids[0])
        return f"http://{LOCAL_MCP_HOST}:{server.port}{path}"

    async def status(
        self,
        route: Route,
        credential: Credential,
        body: dict[str, Any] | None = None,
    ) -> int:
        """The status ``route`` answers ``credential`` with; a streamed
        response is closed once its status arrives."""
        async with self.http.stream(
            route.method,
            self.url(route),
            headers={
                "Accept": "application/json, text/event-stream",
                **credential_headers(credential),
            },
            json=body if body is not None else route.body,
        ) as response:
            return response.status_code


def _guarded_server(recorded: list[str], *, room_bound: bool) -> LocalMCPServer:
    return LocalMCPServer(
        name="loopback-auth-test",
        tool_registrations=[record_tool(recorded)],
        auth_token=AUTH_TOKEN,
        port_min=0,
        port_max=0,
        room_bound=room_bound,
    )


@asynccontextmanager
async def guarded_servers() -> AsyncIterator[GuardedServers]:
    """Both guarded servers running, with an authorized SSE session open on
    the room-bound one; everything is stopped on exit.

    A context manager rather than a fixture: the SSE client's task group must
    be entered and exited in the test's own task.
    """
    recorded: list[str] = []
    session_ids: list[str] = []
    async with (
        _guarded_server(recorded, room_bound=True) as room_bound,
        _guarded_server(recorded, room_bound=False) as multi_room,
        httpx.AsyncClient() as http,
        sse_client(
            room_bound.room_sse_url(ROOM),
            headers=credential_headers(Credential.VALID),
            on_session_created=session_ids.append,
        ) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield GuardedServers(
            room_bound=room_bound,
            multi_room=multi_room,
            http=http,
            session=session,
            session_ids=session_ids,
            recorded=recorded,
        )
