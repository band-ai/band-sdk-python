"""Agno history converter."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

from ._tool_parsing import parse_tool_call, parse_tool_result

if TYPE_CHECKING:
    from agno.models.message import Message

logger = logging.getLogger(__name__)

# Forward-referenced so this module imports without agno installed; the real
# Message type is imported lazily inside convert().
AgnoMessages = list["Message"]


class AgnoHistoryConverter(HistoryConverter[AgnoMessages]):
    """
    Convert platform history to Agno message format.

    Output:
    - this agent's text messages -> Message(role="assistant", content=...)
    - everyone else's text messages -> Message(role="user", content="[name]: ...")
    - tool_call events -> Message(role="assistant", tool_calls=[{id, type, function}])
      (consecutive calls are batched into one assistant message)
    - tool_result events -> Message(role="tool", tool_call_id=..., content=output)

    This is Agno's own history shape, so rehydrated tool turns round-trip back
    through ``Agent.arun(input=...)`` for whichever model the agent uses.
    """

    def __init__(self, agent_name: str = ""):
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> AgnoMessages:
        """Dispatch each platform event to its Agno-message builder."""
        messages: AgnoMessages = []
        # Buffer consecutive tool calls so they land in a single assistant
        # message (matching how Agno emits parallel tool calls).
        pending_calls: list[dict[str, Any]] = []

        for hist in raw:
            match hist.get("message_type", "text"):
                case "tool_call":
                    call = self._tool_call_dict(hist.get("content", ""))
                    if call is not None:
                        pending_calls.append(call)
                case "tool_result":
                    # The assistant tool_calls message must precede its results.
                    self._flush_tool_calls(messages, pending_calls)
                    self._append_tool_result(messages, hist.get("content", ""))
                case "text":
                    self._flush_tool_calls(messages, pending_calls)
                    messages.append(self._text_message(hist))
                case _:
                    continue  # skip thought and other non-text, non-tool events

        self._flush_tool_calls(messages, pending_calls)
        return messages

    @staticmethod
    def _tool_call_dict(content: str) -> dict[str, Any] | None:
        """Shape a tool_call event into an Agno (OpenAI-style) tool call."""
        parsed = parse_tool_call(content)
        if parsed is None:
            return None
        return {
            "id": parsed.tool_call_id,
            "type": "function",
            "function": {
                "name": parsed.name,
                "arguments": json.dumps(parsed.args),
            },
        }

    @staticmethod
    def _flush_tool_calls(
        messages: AgnoMessages, pending_calls: list[dict[str, Any]]
    ) -> None:
        """Emit buffered tool calls as one assistant message, then clear them."""
        if not pending_calls:
            return
        from agno.models.message import Message

        messages.append(
            Message(role="assistant", content=None, tool_calls=list(pending_calls))
        )
        pending_calls.clear()

    @staticmethod
    def _append_tool_result(messages: AgnoMessages, content: str) -> None:
        """Append a tool_result event as a tool-role message."""
        parsed = parse_tool_result(content)
        if parsed is None:
            return
        from agno.models.message import Message

        messages.append(
            Message(
                role="tool",
                tool_call_id=parsed.tool_call_id,
                tool_name=parsed.name,
                content=parsed.output,
                tool_call_error=parsed.is_error,
            )
        )

    def _text_message(self, hist: dict[str, Any]) -> Message:
        """Map a text event to a user/assistant message with sender attribution."""
        from agno.models.message import Message

        content = hist.get("content", "")
        if hist.get("role") == "assistant" and hist.get("sender_name") == (
            self._agent_name
        ):
            return Message(role="assistant", content=content)

        sender_name = hist.get("sender_name", "")
        formatted = f"[{sender_name}]: {content}" if sender_name else content
        return Message(role="user", content=formatted)
