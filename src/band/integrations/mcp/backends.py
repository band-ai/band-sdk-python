"""The shared Band MCP backend: one local MCP server per adapter."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from band.integrations.mcp.engine import (
    RoomToolResolver,
    build_resolved_band_mcp_tool_registrations,
)
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HOST,
    LOCAL_MCP_PORT_MAX,
    LOCAL_MCP_PORT_MIN,
    LocalMCPServer,
)
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name
from band.runtime.tools import BAND_MCP_SERVER_NAME, ToolDefinition

logger = logging.getLogger(__name__)


class BandMCPTransport(StrEnum):
    """The wire transports every Band MCP server serves."""

    HTTP = "http"
    SSE = "sse"


@dataclass(frozen=True)
class BandMCPBackend:
    """A running Band MCP server (both transports) and the tool names it exposes."""

    allowed_tools: list[str]
    local_server: LocalMCPServer

    @property
    def is_running(self) -> bool:
        """False once the backing local server has crashed or stopped."""
        return self.local_server.is_running

    def endpoint(self, transport: BandMCPTransport, room_id: str | None = None) -> str:
        """Return the URL a consumer dials: its room's, or the multi-room one.

        Raises ``ValueError`` when passing (or omitting) ``room_id`` doesn't
        match how the backend was created (``room_bound``).
        """
        server = self.local_server
        match transport:
            case BandMCPTransport.HTTP:
                return (
                    server.http_url
                    if room_id is None
                    else server.room_http_url(room_id)
                )
            case BandMCPTransport.SSE:
                return (
                    server.sse_url if room_id is None else server.room_sse_url(room_id)
                )

    async def stop(self) -> None:
        """Stop the backing local server."""
        await self.local_server.stop()

    async def restart_if_crashed(self) -> bool:
        """Restart the backing local server if its serve task died; True if it did.

        The serve task can end on its own, and nothing else notices: every
        consumer would keep dialing a dead port. The restart lands on a
        different port when the range has another free one, so a consumer
        holding the old URL can tell.
        """
        if self.is_running:
            return False
        logger.warning("Band MCP server crashed; restarting it")
        await self.local_server.stop()
        await self.local_server.start()
        return True


def _build_allowed_tools(
    tool_definitions: Sequence[ToolDefinition],
    additional_tools: list[CustomToolDef],
) -> list[str]:
    allowed_tools = [f"mcp__band__{definition.name}" for definition in tool_definitions]
    allowed_tools.extend(
        f"mcp__band__{get_custom_tool_name(input_model)}"
        for input_model, _ in additional_tools
    )
    return allowed_tools


async def create_band_mcp_backend(
    *,
    tool_definitions: Sequence[ToolDefinition],
    get_tools: RoomToolResolver,
    additional_tools: list[CustomToolDef] | None = None,
    room_bound: bool = False,
    host: str = LOCAL_MCP_HOST,
    port_min: int = LOCAL_MCP_PORT_MIN,
    port_max: int = LOCAL_MCP_PORT_MAX,
) -> BandMCPBackend:
    """Start a shared Band MCP server, serving both transports.

    A ``room_bound`` backend serves one endpoint per room
    (``endpoint(transport, room_id)``) whose tools take their room from the
    path and advertise no ``chat_id``; otherwise one multi-room endpoint
    routes by a required ``chat_id`` argument. ``host`` sets the bind
    interface; see ``LocalMCPServer`` for the non-loopback caveat.
    ``port_min=0`` requests an OS-assigned ephemeral port — race-free and
    rarely reused, for callers whose MCP client dials across a network proxy.
    """
    resolved_tools = list(additional_tools or [])
    local_server = LocalMCPServer(
        name=BAND_MCP_SERVER_NAME,
        tool_registrations=build_resolved_band_mcp_tool_registrations(
            get_tools=get_tools,
            additional_tools=resolved_tools,
            tool_definitions=tool_definitions,
            room_from_connection=room_bound,
        ),
        host=host,
        port_min=port_min,
        port_max=port_max,
        room_bound=room_bound,
    )
    await local_server.start()
    return BandMCPBackend(
        allowed_tools=_build_allowed_tools(tool_definitions, resolved_tools),
        local_server=local_server,
    )
