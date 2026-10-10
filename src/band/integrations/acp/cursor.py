"""Cursor MCP tool-title identity and room decision vocabulary."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum

from band.runtime.tools.registry import (
    BAND_MCP_SERVER_NAME,
    canonicalize_mcp_tool_name,
    mcp_tool_spelling,
)

DECISION_NOT_PENDING_TEMPLATE = "Cursor decision `{token}` is not pending."
DECISION_UNAUTHORIZED_MESSAGE = "You are not authorized to resolve Cursor decisions."

ROOM_COMMAND = "/cursor"
CURSOR_CLI_BINARY = "agent"
CURSOR_TITLE_SEPARATOR = ": "


def cursor_mcp_title(title: str) -> tuple[str, str] | None:
    """Parse Cursor's ``provider-tool: tool`` display title, failing closed."""
    spelling, separator, tool = title.partition(CURSOR_TITLE_SEPARATOR)
    if not separator or not spelling or not tool or spelling == tool:
        return None
    if any(char.isspace() for char in spelling + tool) or ":" in spelling + tool:
        return None
    return spelling, tool


def is_cursor_band_tool(title: str, own_names: Collection[str]) -> bool:
    """Whether both title halves identify the same registered Band MCP tool."""
    parsed = cursor_mcp_title(title)
    if parsed is None:
        return False
    spelling, tool = parsed
    return tool in own_names and spelling == mcp_tool_spelling(
        BAND_MCP_SERVER_NAME, tool
    )


def canonicalize_cursor_tool_name(name: str, own_names: Collection[str]) -> str:
    """Decode a registered tool's exact Cursor title before generic MCP names."""
    if is_cursor_band_tool(name, own_names):
        return name.partition(CURSOR_TITLE_SEPARATOR)[2]
    return canonicalize_mcp_tool_name(name, own_names)


class CursorCommandWord(StrEnum):
    """The room commands accepted by the Cursor adapter."""

    DECISIONS = "decisions"
    SELECT = "select"
    DENY = "deny"
    ACCEPT = "accept"
    REJECT = "reject"
    ANSWER = "answer"


PERMISSION_REQUESTED_TEMPLATE = (
    "Cursor needs permission to run `{tool}`. "
    f"Reply `{ROOM_COMMAND} {CursorCommandWord.SELECT} {{token}} <option-id>` "
    f"or `{ROOM_COMMAND} {CursorCommandWord.DENY} {{token}}`. "
    "Available options: {options}"
)
PLAN_REQUESTED_TEMPLATE = (
    "{plan} needs approval. "
    f"Reply `{ROOM_COMMAND} {CursorCommandWord.ACCEPT} {{token}}` or "
    f"`{ROOM_COMMAND} {CursorCommandWord.REJECT} {{token}}`."
)
DECISION_RESOLVED_TEMPLATE = "Cursor {kind} decision `{token}` resolved."
DECISION_TIMED_OUT_TEMPLATE = (
    "Cursor {kind} decision `{token}` timed out and was cancelled."
)
