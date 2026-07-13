"""Strands Agents history converter."""

from __future__ import annotations

import logging
from typing import Any

try:
    from strands.types.content import ContentBlock, Message
except ImportError as e:
    raise ImportError(
        "Strands Agents dependencies not installed. "
        "Install with: uv add band-sdk[strands]"
    ) from e

from band.core.protocols import HistoryConverter
from band.core.types import MessageType

from ._tool_parsing import parse_tool_call, parse_tool_result

logger = logging.getLogger(__name__)

# Type alias for Strands conversation history (Bedrock-Converse-shaped messages)
StrandsMessages = list[Message]


def _flush_pending_tool_calls(
    messages: StrandsMessages, pending_tool_calls: list[ContentBlock]
) -> None:
    """Flush pending toolUse blocks into a single assistant message."""
    if pending_tool_calls:
        messages.append({"role": "assistant", "content": list(pending_tool_calls)})
        pending_tool_calls.clear()


def _flush_pending_tool_results(
    messages: StrandsMessages, pending_tool_results: list[ContentBlock]
) -> None:
    """Flush pending toolResult blocks into a single user message.

    Converse requires every toolUse in an assistant message to be answered by
    toolResult blocks in the following user message, so results are batched
    together to support parallel tool use.
    """
    if pending_tool_results:
        messages.append({"role": "user", "content": list(pending_tool_results)})
        pending_tool_results.clear()


class StrandsHistoryConverter(HistoryConverter[StrandsMessages]):
    """
    Converts platform history to Strands (Bedrock Converse) message format.

    Output:
    - user messages → {"role": "user", "content": [{"text": ...}]}
    - other agents' messages → user role with [name] prefix
    - tool_call → assistant message with a toolUse content block
    - tool_result → user message with a toolResult content block
      (status "error" when is_error=True)
    - this agent's text messages → {"role": "assistant", "content": [{"text": ...}]}
      (dropped when emitted mid tool call, where it would split the toolUse
      from its toolResult)

    Tool events are stored in platform as JSON:
    - tool_call: {"name": "...", "args": {...}, "tool_call_id": "..."}
    - tool_result: {"name": "...", "output": "...", "tool_call_id": "...", "is_error": bool}
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Messages from this agent are preserved
                       as assistant turns. Messages from other agents are included
                       as user turns with a [name] prefix.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so the converter can recognize this agent's own messages.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> StrandsMessages:
        """Convert platform history to Strands Converse format."""
        messages: StrandsMessages = []
        # Collect tool calls to batch them into a single assistant message
        pending_tool_calls: list[ContentBlock] = []
        # Collect tool results to batch them into a single user message
        pending_tool_results: list[ContentBlock] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")

            match message_type:
                case MessageType.TOOL_CALL:
                    self._handle_tool_call(
                        content, messages, pending_tool_calls, pending_tool_results
                    )
                case MessageType.TOOL_RESULT:
                    self._handle_tool_result(
                        content, messages, pending_tool_calls, pending_tool_results
                    )
                case MessageType.TEXT:
                    self._handle_text(
                        hist,
                        content,
                        messages,
                        pending_tool_calls,
                        pending_tool_results,
                    )
                case MessageType.THOUGHT | MessageType.ERROR | MessageType.TASK:
                    # Known platform-internal types intentionally excluded from
                    # LLM history.
                    pass
                case _:
                    logger.warning("Unknown message_type in history: %s", message_type)

        # Flush any remaining pending tool calls and results
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        return messages

    def _handle_tool_call(
        self,
        content: str,
        messages: StrandsMessages,
        pending_tool_calls: list[ContentBlock],
        pending_tool_results: list[ContentBlock],
    ) -> None:
        """Collect a tool call for batching, flushing any pending results first."""
        _flush_pending_tool_results(messages, pending_tool_results)

        parsed = parse_tool_call(content)
        if parsed:
            pending_tool_calls.append(
                {
                    "toolUse": {
                        "toolUseId": parsed.tool_call_id,
                        "name": parsed.name,
                        "input": parsed.args,
                    }
                }
            )

    def _handle_tool_result(
        self,
        content: str,
        messages: StrandsMessages,
        pending_tool_calls: list[ContentBlock],
        pending_tool_results: list[ContentBlock],
    ) -> None:
        """Collect a tool result for batching, flushing the calls it answers first."""
        _flush_pending_tool_calls(messages, pending_tool_calls)

        parsed = parse_tool_result(content)
        if not parsed:
            return

        pending_tool_results.append(
            {
                "toolResult": {
                    "toolUseId": parsed.tool_call_id,
                    "status": "error" if parsed.is_error else "success",
                    "content": [{"text": parsed.output}],
                }
            }
        )

    def _handle_text(
        self,
        hist: dict[str, Any],
        content: str,
        messages: StrandsMessages,
        pending_tool_calls: list[ContentBlock],
        pending_tool_results: list[ContentBlock],
    ) -> None:
        """Append a text turn: own text as assistant, others as user."""
        role = hist.get("role", "user")
        sender_name = hist.get("sender_name", "")
        is_own = (
            role == "assistant" and self._agent_name and sender_name == self._agent_name
        )

        # Own platform text while a tool call is unresolved is usually the side
        # effect of band_send_message. Replaying it here would split the toolUse
        # from its toolResult, which Converse rejects.
        if is_own and pending_tool_calls:
            return

        # Flush pending tool calls and results first
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        if is_own:
            # Preserve own text so restart rehydration knows the agent already replied.
            messages.append({"role": "assistant", "content": [{"text": content}]})
        else:
            # User messages AND other agents' messages
            formatted_content = (
                f"[{sender_name}]: {content}" if sender_name else content
            )
            messages.append({"role": "user", "content": [{"text": formatted_content}]})
