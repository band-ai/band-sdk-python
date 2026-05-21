"""Pydantic AI history converter."""

from __future__ import annotations

from typing import Any

try:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
except ImportError as e:
    raise ImportError(
        "Pydantic AI dependencies not installed. "
        "Install with: uv add band-sdk[pydantic_ai]"
    ) from e

from thenvoi.core.protocols import HistoryConverter

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
    - this agent's text messages → skipped (redundant with tool results)

    Tool events are stored in platform as JSON:
    - tool_call: {"name": "...", "args": {...}, "tool_call_id": "..."}
    - tool_result: {"name": "...", "output": "...", "tool_call_id": "...", "is_error": bool}
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Messages from this agent are skipped
                       (they're redundant with tool results). Messages from other
                       agents are included as ModelRequest.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so converter knows which messages to skip.

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

            if message_type == "tool_call":
                # Flush pending tool results before starting new tool calls
                _flush_pending_tool_results(messages, pending_tool_results)

                # Parse tool call JSON and collect for batching
                parsed = parse_tool_call(content)
                if parsed:
                    tool_call_part = ToolCallPart(
                        tool_name=parsed.name,
                        args=parsed.args,
                        tool_call_id=parsed.tool_call_id,
                    )
                    pending_tool_calls.append(tool_call_part)

            elif message_type == "tool_result":
                # Flush pending tool calls first (tool results follow tool calls)
                _flush_pending_tool_calls(messages, pending_tool_calls)

                # Parse tool result JSON and collect for batching
                parsed = parse_tool_result(content)
                if parsed:
                    if parsed.is_error:
                        # Use RetryPromptPart for error results
                        tool_result_part: ToolReturnPart | RetryPromptPart = (
                            RetryPromptPart(
                                content=parsed.output,
                                tool_name=parsed.name,
                                tool_call_id=parsed.tool_call_id,
                            )
                        )
                    else:
                        # Use ToolReturnPart for successful results
                        tool_result_part = ToolReturnPart(
                            tool_name=parsed.name,
                            content=parsed.output,
                            tool_call_id=parsed.tool_call_id,
                        )
                    pending_tool_results.append(tool_result_part)

            elif message_type == "text":
                # Flush pending tool calls and results first
                _flush_pending_tool_calls(messages, pending_tool_calls)
                _flush_pending_tool_results(messages, pending_tool_results)

                role = hist.get("role", "user")
                sender_name = hist.get("sender_name", "")

                if role == "assistant" and sender_name == self._agent_name:
                    # Skip THIS agent's text (redundant with tool results)
                    continue
                else:
                    # User messages AND other agents' messages
                    formatted_content = (
                        f"[{sender_name}]: {content}" if sender_name else content
                    )
                    messages.append(
                        ModelRequest(parts=[UserPromptPart(content=formatted_content)])
                    )

        # Flush any remaining pending tool calls and results
        _flush_pending_tool_calls(messages, pending_tool_calls)
        _flush_pending_tool_results(messages, pending_tool_results)

        return messages
