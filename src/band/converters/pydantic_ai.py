"""Pydantic AI history converter."""

from __future__ import annotations

from typing import Any

try:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
except ImportError as e:
    raise ImportError(
        "Pydantic AI dependencies not installed. "
        "Install with: uv add band-sdk[pydantic_ai]"
    ) from e

from band.core.protocols import HistoryConverter

from ._tool_parsing import parse_tool_call, parse_tool_result

# Type alias for Pydantic AI messages (can be requests or responses)
PydanticAIMessages = list[ModelRequest | ModelResponse]


def _flush_pending_tool_calls(
    messages: PydanticAIMessages, pending_tool_calls: list[ToolCallPart]
) -> None:
    """Flush pending tool calls into a single ModelResponse."""
    if pending_tool_calls:
        messages.append(ModelResponse(parts=list(pending_tool_calls)))
        pending_tool_calls.clear()


def _flush_pending_tool_results(
    messages: PydanticAIMessages,
    pending_tool_results: list[ToolReturnPart | RetryPromptPart],
) -> None:
    """Flush pending tool results into a single ModelRequest.

    Similar to Anthropic's requirement, tool results should be batched
    together to enable parallel tool use patterns.

    Uses ToolReturnPart for successful results and RetryPromptPart for errors.
    """
    if pending_tool_results:
        messages.append(ModelRequest(parts=list(pending_tool_results)))
        pending_tool_results.clear()


class PydanticAIHistoryConverter(HistoryConverter[PydanticAIMessages]):
    """
    Converts platform history to Pydantic AI message format.

    Output:
    - user messages → ModelRequest with UserPromptPart
    - other agents' messages → ModelRequest with UserPromptPart (with [name] prefix)
    - tool_call → ModelResponse with ToolCallPart
    - tool_result → ModelRequest with ToolReturnPart (or RetryPromptPart if is_error=True)
    - this agent's text messages → ModelResponse with TextPart
      (dropped when emitted mid tool call, where it would split the
      ToolCallPart from its ToolReturnPart)

    Tool events are stored in platform as JSON:
    - tool_call: {"name": "...", "args": {...}, "tool_call_id": "..."}
    - tool_result: {"name": "...", "output": "...", "tool_call_id": "...", "is_error": bool}
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Messages from this agent are preserved
                       as ModelResponse. Messages from other agents are included
                       as ModelRequest.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so the converter can recognize this agent's own messages.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> PydanticAIMessages:
        """Convert platform history to Pydantic AI format."""
        messages: PydanticAIMessages = []
        # Collect tool calls to batch them into a single ModelResponse
        pending_tool_calls: list[ToolCallPart] = []
        # Collect tool results to batch them into a single ModelRequest
        # Can be ToolReturnPart (success) or RetryPromptPart (error)
        pending_tool_results: list[ToolReturnPart | RetryPromptPart] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")

            match message_type:
                case "tool_call":
                    self._handle_tool_call(
                        content, messages, pending_tool_calls, pending_tool_results
                    )
                case "tool_result":
                    self._handle_tool_result(
                        content, messages, pending_tool_calls, pending_tool_results
                    )
                case "text":
                    self._handle_text(
                        hist,
                        content,
                        messages,
                        pending_tool_calls,
                        pending_tool_results,
                    )
                # Other types (e.g. "thought") are intentionally ignored.

        # Flush any remaining pending tool calls and results
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        return messages

    def _handle_tool_call(
        self,
        content: str,
        messages: PydanticAIMessages,
        pending_tool_calls: list[ToolCallPart],
        pending_tool_results: list[ToolReturnPart | RetryPromptPart],
    ) -> None:
        """Collect a tool call for batching, flushing any pending results first."""
        _flush_pending_tool_results(messages, pending_tool_results)

        parsed = parse_tool_call(content)
        if parsed:
            pending_tool_calls.append(
                ToolCallPart(
                    tool_name=parsed.name,
                    args=parsed.args,
                    tool_call_id=parsed.tool_call_id,
                )
            )

    def _handle_tool_result(
        self,
        content: str,
        messages: PydanticAIMessages,
        pending_tool_calls: list[ToolCallPart],
        pending_tool_results: list[ToolReturnPart | RetryPromptPart],
    ) -> None:
        """Collect a tool result for batching, flushing the calls it answers first."""
        _flush_pending_tool_calls(messages, pending_tool_calls)

        parsed = parse_tool_result(content)
        if not parsed:
            return

        if parsed.is_error:
            # Use RetryPromptPart for error results
            tool_result_part: ToolReturnPart | RetryPromptPart = RetryPromptPart(
                content=parsed.output,
                tool_name=parsed.name,
                tool_call_id=parsed.tool_call_id,
            )
        else:
            # Use ToolReturnPart for successful results
            tool_result_part = ToolReturnPart(
                tool_name=parsed.name,
                content=parsed.output,
                tool_call_id=parsed.tool_call_id,
            )
        pending_tool_results.append(tool_result_part)

    def _handle_text(
        self,
        hist: dict[str, Any],
        content: str,
        messages: PydanticAIMessages,
        pending_tool_calls: list[ToolCallPart],
        pending_tool_results: list[ToolReturnPart | RetryPromptPart],
    ) -> None:
        """Append a text turn: own text as ModelResponse, others as ModelRequest."""
        role = hist.get("role", "user")
        sender_name = hist.get("sender_name", "")
        is_own = (
            role == "assistant" and self._agent_name and sender_name == self._agent_name
        )

        # Own platform text while a tool call is unresolved is usually the side
        # effect of band_send_message. Replaying it here would split the
        # ToolCallPart from its ToolReturnPart, which providers reject.
        if is_own and pending_tool_calls:
            return

        # Flush pending tool calls and results first
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        if is_own:
            # Preserve own text so restart rehydration knows the agent already replied.
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
        else:
            # User messages AND other agents' messages
            formatted_content = (
                f"[{sender_name}]: {content}" if sender_name else content
            )
            messages.append(
                ModelRequest(parts=[UserPromptPart(content=formatted_content)])
            )
