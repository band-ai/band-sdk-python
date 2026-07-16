"""Shared Band MCP backend selection for SDK and local transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from typing_extensions import TypeAliasType

from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name
from band.runtime.mcp_server import (
    LOCAL_MCP_HOST,
    LOCAL_MCP_PORT_MAX,
    LOCAL_MCP_PORT_MIN,
    LocalMCPServer,
    build_resolved_band_mcp_tool_registrations,
)
from band.runtime.tools import ToolDefinition

BandMCPBackendKind = TypeAliasType(
    "BandMCPBackendKind",
    Literal["sdk", "http", "sse"],
)


@dataclass
class BandMCPBackend:
    """Materialized Band MCP backend for a specific transport."""

    kind: BandMCPBackendKind
    server: Any
    allowed_tools: list[str]
    local_server: LocalMCPServer | None = None

    async def stop(self) -> None:
        """Clean up backend resources when needed."""
        if self.local_server is not None:
            await self.local_server.stop()


def _build_allowed_tools(
    tool_definitions: list[ToolDefinition],
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
    kind: BandMCPBackendKind,
    tool_definitions: list[ToolDefinition],
    get_tools: Any,
    additional_tools: list[CustomToolDef] | None = None,
    get_participant_handles: Any | None = None,
    tool_result_hook: Any | None = None,
    host: str = LOCAL_MCP_HOST,
    port_min: int = LOCAL_MCP_PORT_MIN,
    port_max: int = LOCAL_MCP_PORT_MAX,
) -> BandMCPBackend:
    """Create a shared Band MCP backend for the requested transport.

    ``host`` sets the local server's bind interface for the http/sse kinds
    (ignored for ``sdk``); see ``LocalMCPServer`` for the non-loopback caveat.
    ``port_min=0`` requests an OS-assigned ephemeral port — race-free and
    never reused, for callers whose MCP client dials across a network proxy.
    """
    resolved_tools = list(additional_tools or [])
    allowed_tools = _build_allowed_tools(tool_definitions, resolved_tools)

    if kind == "sdk":
        from band.integrations.claude_sdk.tools import (
            build_band_sdk_tools,
            create_band_sdk_mcp_server,
        )

        sdk_tools = build_band_sdk_tools(
            tool_definitions=tool_definitions,
            get_tools=get_tools,
            additional_tools=resolved_tools,
            get_participant_handles=get_participant_handles,
            tool_result_hook=tool_result_hook,
        )
        return BandMCPBackend(
            kind=kind,
            server=create_band_sdk_mcp_server(sdk_tools),
            allowed_tools=allowed_tools,
        )

    local_server = LocalMCPServer(
        name="band",
        tool_registrations=build_resolved_band_mcp_tool_registrations(
            get_tools=get_tools,
            additional_tools=resolved_tools,
            tool_definitions=tool_definitions,
        ),
        host=host,
        port_min=port_min,
        port_max=port_max,
    )
    await local_server.start()
    return BandMCPBackend(
        kind=kind,
        server=local_server,
        allowed_tools=allowed_tools,
        local_server=local_server,
    )
