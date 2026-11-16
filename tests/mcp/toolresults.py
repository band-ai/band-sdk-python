"""How an MCP client reads a Band tool's result."""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult, TextContent


def mcp_text_result(text: str) -> CallToolResult:
    """A tool's successful result carrying ``text``."""
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=False)


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


def result_text(result: CallToolResult) -> str:
    """The text a tool result carries, as an MCP client hands it to a model."""
    return "".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def content_blocks(result: CallToolResult) -> list[dict[str, Any]]:
    """A tool result's content as a client forwards it raw: plain blocks."""
    return [block.model_dump(exclude_none=True) for block in result.content]
