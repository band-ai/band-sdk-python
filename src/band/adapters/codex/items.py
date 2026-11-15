"""Codex thread item kinds and how the adapter renders them."""

from __future__ import annotations

from typing import Any, NamedTuple

from band.integrations.codex.types import CodexItemType

# item/completed "type" values gated on Emit.TOOL_CALLS; dispatched in
# CodexAdapter._extract_tool_item.
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
# CodexAdapter._extract_thought_text.
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
