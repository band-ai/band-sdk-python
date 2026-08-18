"""Compatibility re-export for the old ``band.runtime.mcp_server`` import path.

The embedded MCP front door moved to ``band.integrations.mcp.local_server``
(INT-1096) -- this module now just re-exports its public names so an
external consumer importing ``band.runtime.mcp_server`` directly (band-sdk is
published) doesn't break on the move. Keep for at least one minor release;
new code should import from the new location instead.
"""

from __future__ import annotations

from band.integrations.mcp.local_server import (
    LOCAL_MCP_HEALTH_PATH,
    LOCAL_MCP_HOST,
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_MESSAGE_PATH,
    LOCAL_MCP_PORT_MAX,
    LOCAL_MCP_PORT_MIN,
    LOCAL_MCP_SSE_PATH,
    SERVER_START_TIMEOUT_S,
    SERVER_STOP_TIMEOUT_S,
    EmbeddedUvicornServer,
    LocalMCPServer,
    RoomToolResolver,
    build_band_mcp_tool_registrations,
    build_resolved_band_mcp_tool_registrations,
)
from band.integrations.mcp.engine import MCPToolExecutor, MCPToolRegistration

__all__ = [
    "LOCAL_MCP_HEALTH_PATH",
    "LOCAL_MCP_HOST",
    "LOCAL_MCP_HTTP_PATH",
    "LOCAL_MCP_MESSAGE_PATH",
    "LOCAL_MCP_PORT_MAX",
    "LOCAL_MCP_PORT_MIN",
    "LOCAL_MCP_SSE_PATH",
    "SERVER_START_TIMEOUT_S",
    "SERVER_STOP_TIMEOUT_S",
    "EmbeddedUvicornServer",
    "LocalMCPServer",
    "MCPToolExecutor",
    "MCPToolRegistration",
    "RoomToolResolver",
    "build_band_mcp_tool_registrations",
    "build_resolved_band_mcp_tool_registrations",
]
