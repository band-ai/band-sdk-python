"""Dependency-free Cursor tool-title parsing and room decision vocabulary."""

from __future__ import annotations

from enum import StrEnum

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
