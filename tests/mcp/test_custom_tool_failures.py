"""A failed custom tool reaches the MCP client as an error result."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent

from band.integrations.mcp.engine import (
    CustomToolSpec,
    EngineSpec,
    build_custom_tool_registration,
    build_engine,
)
from band.runtime.tools.schema import tool_execution_failure
from band.testing.fake_tools import FakeAgentTools
from tests.integrations.activitykit import LookupInput

LOOKUP_DOWN = "lookup service down"
FAILURE_REASON = "no definition for that note"
STRUCTURED_FAILURE = {"ok": False, "reason": FAILURE_REASON}
STRING_FAILURE = f"Error: {FAILURE_REASON}"


def raising_lookup(inp: LookupInput) -> str:
    raise ValueError(LOOKUP_DOWN)


def structured_failure_lookup(inp: LookupInput) -> dict[str, Any]:
    return STRUCTURED_FAILURE


def string_failure_lookup(inp: LookupInput) -> str:
    return STRING_FAILURE


def mcp_tool_error(tool_name: str, message: str) -> CallToolResult:
    """FastMCP's rendering of any exception raised by a tool."""
    return CallToolResult(
        content=[
            TextContent(
                type="text", text=f"Error executing tool {tool_name}: {message}"
            )
        ],
        isError=True,
    )


class TestCustomToolFailures:
    @pytest.mark.parametrize(
        ("handler", "message"),
        [
            (raising_lookup, tool_execution_failure("lookup", ValueError(LOOKUP_DOWN))),
            (structured_failure_lookup, json.dumps(STRUCTURED_FAILURE)),
            (string_failure_lookup, STRING_FAILURE),
        ],
        ids=["raised", "structured-value", "legacy-string-value"],
    )
    async def test_failure_is_an_error_result_for_the_client(
        self, handler: Callable[[LookupInput], Any], message: str
    ) -> None:
        registration = build_custom_tool_registration(
            CustomToolSpec(input_model=LookupInput, handler=handler),
            get_tools={"room-1": FakeAgentTools(room_id="room-1")}.get,
        )
        server = build_engine(EngineSpec(name="lookup", tools=(registration,)))

        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "lookup", {"note": "failure", "chat_id": "room-1"}
            )

        assert result == mcp_tool_error("lookup", message)
