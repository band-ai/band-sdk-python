"""Codex app-server thread items, shaped as its v2 protocol schema sends them."""

from __future__ import annotations

from typing import Any

from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexItemType
from tests.adapters.codexturns import FIRST_THREAD_ID, event_notification

IN_PROGRESS = "inProgress"

LIST_FILES: dict[str, Any] = {
    "type": CodexItemType.COMMAND_EXECUTION,
    "id": "cmd-1",
    "command": "ls",
    "commandActions": [],
    "cwd": "/workspace",
    "status": IN_PROGRESS,
}
DOCS_LOOKUP: dict[str, Any] = {
    "type": CodexItemType.MCP_TOOL_CALL,
    "id": "mcp-1",
    "server": "docs",
    "tool": "search",
    "arguments": {"query": "activity"},
    "status": IN_PROGRESS,
}
BAND_TOOL_CALL: dict[str, Any] = {
    "type": CodexItemType.DYNAMIC_TOOL_CALL,
    "id": "dyn-1",
    "tool": "band_get_participants",
    "arguments": {},
    "status": IN_PROGRESS,
}
COMPACTION: dict[str, Any] = {"type": CodexItemType.CONTEXT_COMPACTION, "id": "cc-1"}


def item_started(item: dict[str, Any]) -> RpcEvent:
    return event_notification(
        "item/started", {"threadId": FIRST_THREAD_ID, "startedAtMs": 1, "item": item}
    )


def item_completed(item: dict[str, Any]) -> RpcEvent:
    """``item`` finished; an item with a lifecycle status completes it."""
    finished = {**item, "status": "completed"} if "status" in item else item
    return event_notification(
        "item/completed",
        {"threadId": FIRST_THREAD_ID, "completedAtMs": 2, "item": finished},
    )
