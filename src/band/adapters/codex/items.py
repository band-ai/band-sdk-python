"""Codex thread item kinds and how the adapter renders them."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from band.integrations.codex.types import CodexItemType
from band.runtime.tools import redact_tool_call_args

# item/completed "type" values gated on Emit.TOOL_CALLS; dispatched in
# tool_item.
TOOL_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {
        CodexItemType.COMMAND_EXECUTION,
        CodexItemType.FILE_CHANGE,
        CodexItemType.MCP_TOOL_CALL,
        CodexItemType.WEB_SEARCH,
        CodexItemType.IMAGE_VIEW,
        CodexItemType.COLLAB_AGENT_TOOL_CALL,
    }
)


# Tools Codex asks the adapter to run (item/tool/call). The adapter reports them
# there, with the real result, so their item/completed is the same call again.
REQUESTED_TOOL_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.DYNAMIC_TOOL_CALL}
)


# The prompt and the reply; the reply's text is read by the turn loop.
MESSAGE_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.USER_MESSAGE, CodexItemType.AGENT_MESSAGE}
)


# Read only to attribute a sub-agent's requests to its turn; never rendered.
ROUTING_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {CodexItemType.SUB_AGENT_ACTIVITY}
)


# Items that show as the turn's activity from item/started to item/completed.
ACTIVITY_ITEM_TYPES = TOOL_ITEM_TYPES | {CodexItemType.CONTEXT_COMPACTION}


UNRENDERED_ITEM_TYPES = (
    REQUESTED_TOOL_ITEM_TYPES | MESSAGE_ITEM_TYPES | ROUTING_ITEM_TYPES
)


# item/completed "type" values gated on Emit.THOUGHTS; dispatched in
# thought_text.
THOUGHT_ITEM_TYPES: frozenset[CodexItemType] = frozenset(
    {
        CodexItemType.REASONING,
        CodexItemType.PLAN,
        CodexItemType.CONTEXT_COMPACTION,
        CodexItemType.ENTERED_REVIEW_MODE,
        CodexItemType.EXITED_REVIEW_MODE,
    }
)


class CodexToolItem(NamedTuple):
    """(name, args, output) for one tool-like item/completed entry."""

    name: str
    args: dict[str, Any]
    output: str


def item_call_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("callId") or item.get("toolCallId") or "")


def tool_item(item_type: str, item: dict[str, Any]) -> CodexToolItem:
    """(name, args, output) for a tool-like item."""
    match item_type:
        case CodexItemType.COMMAND_EXECUTION:
            return _command_execution(item)
        case CodexItemType.FILE_CHANGE:
            return _file_change(item)
        case CodexItemType.MCP_TOOL_CALL:
            return _mcp_tool_call(item)
        case CodexItemType.WEB_SEARCH:
            return _web_search(item)
        case CodexItemType.IMAGE_VIEW:
            return _image_view(item)
        case CodexItemType.COLLAB_AGENT_TOOL_CALL:
            return _collab_agent_tool_call(item)
        case _:
            return CodexToolItem(item_type, {}, "completed")


def _command_execution(item: dict[str, Any]) -> CodexToolItem:
    args: dict[str, Any] = {
        "command": item.get("command", ""),
        "cwd": item.get("cwd", ""),
    }
    output_parts: list[str] = []
    if item.get("aggregated_output"):
        output_parts.append(str(item["aggregated_output"]))
    if (exit_code := item.get("exitCode")) is not None:
        output_parts.append(f"exit_code={exit_code}")
    output = "\n".join(output_parts) if output_parts else str(item.get("status", ""))
    return CodexToolItem("exec", args, output)


def _file_change(item: dict[str, Any]) -> CodexToolItem:
    changes = item.get("changes", [])
    if not isinstance(changes, list):
        changes = []
    file_paths = [c.get("path", "") for c in changes if isinstance(c, dict)]
    return CodexToolItem(
        "file_edit", {"files": file_paths}, str(item.get("status", "applied"))
    )


def _mcp_tool_call(item: dict[str, Any]) -> CodexToolItem:
    tool = item.get("tool", "")
    mcp_args = item.get("arguments", {})
    if not isinstance(mcp_args, dict):
        mcp_args = {}
    # Redaction matches the bare tool name (e.g. "band_send_room_file"), not
    # the "mcp:{server}/{tool}" display name.
    mcp_args = redact_tool_call_args(tool, mcp_args)
    output = display_text(
        item.get("result"), item.get("error"), default="completed", raw_fallback=True
    )
    return CodexToolItem(f"mcp:{item.get('server', '')}/{tool}", mcp_args, output)


def _web_search(item: dict[str, Any]) -> CodexToolItem:
    output = display_text(item.get("action"), default="completed", raw_fallback=True)
    return CodexToolItem("web_search", {"query": item.get("query", "")}, output)


def _image_view(item: dict[str, Any]) -> CodexToolItem:
    return CodexToolItem(
        "view_image",
        {"path": item.get("path", "")},
        str(item.get("status", "viewed")),
    )


def _collab_agent_tool_call(item: dict[str, Any]) -> CodexToolItem:
    collab_args: dict[str, Any] = {}
    if item.get("prompt"):
        collab_args["prompt"] = item["prompt"]
    if item.get("agents"):
        collab_args["agents"] = item["agents"]
    output = display_text(item.get("result"), default="completed", raw_fallback=True)
    return CodexToolItem(f"collab:{item.get('tool', '')}", collab_args, output)


def _list_item_text(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(text := item.get("text"), str):
        return text
    return None


def display_text(*values: Any, default: str, raw_fallback: bool = False) -> str:
    """The first present value as displayable text.

    Strings pass through; lists contribute their joined ``str`` /
    ``dict["text"]`` entries. A list with no text means nothing to a thought
    (``raw_fallback=False``), so the next value is tried, but is still real
    data in a tool's output (``raw_fallback=True``), so it is dumped as JSON.
    """
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            text_parts = [
                text for item in value if (text := _list_item_text(item)) is not None
            ]
            if text_parts:
                return "\n".join(text_parts)
            if not raw_fallback:
                continue
        return json.dumps(value, default=str)
    return default


def thought_text(item_type: str, item: dict[str, Any]) -> str | None:
    """Display text for a thought-like item; None when it says nothing."""
    match item_type:
        case CodexItemType.REASONING:
            return display_text(item.get("summary"), default="").strip() or None
        case CodexItemType.PLAN:
            return display_text(item.get("text"), default="").strip() or None
        case CodexItemType.CONTEXT_COMPACTION:
            return "Context compaction performed"
        case CodexItemType.ENTERED_REVIEW_MODE | CodexItemType.EXITED_REVIEW_MODE:
            text = display_text(item.get("text"), default="").strip()
            return text or f"Review mode: {item_type}"
        case _:
            return None
