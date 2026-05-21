"""LangChain/LangGraph history converter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
except ImportError as e:
    raise ImportError(
        "LangChain dependencies not installed. Install with: uv add band-sdk[langgraph]"
    ) from e

from thenvoi.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)

# Type alias for LangChain messages
LangChainMessages = list[AIMessage | HumanMessage | ToolMessage]


class LangChainHistoryConverter(HistoryConverter[LangChainMessages]):
    """
    Converts platform history to LangChain message types.

    Handles:
    - tool_call + tool_result pairing with tool_call_id extraction
    - Skipping this agent's redundant text messages
    - Including other agents' messages as HumanMessage
    - User messages as HumanMessage
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Messages from this agent are skipped
                       (they're redundant with tool calls). Messages from other
                       agents are included as HumanMessage.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so converter knows which messages to skip.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> LangChainMessages:
        """Convert platform history to LangChain messages."""
        messages: LangChainMessages = []
        pending_tool_calls: list[dict[str, Any]] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")
            role = hist.get("role")
            sender_name = hist.get("sender_name", "")

            if message_type == "tool_call":
                try:
                    event = json.loads(content)
                    pending_tool_calls.append(event)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse tool_call: %s", content[:100])

            elif message_type == "tool_result":
                try:
                    event = json.loads(content)
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")

                    tool_call_id = self._extract_tool_call_id(str(output))
                    if not tool_call_id:
                        tool_call_id = event.get("run_id", "unknown")

                    # Match with pending tool call
                    matching_call = None
                    for i, call in enumerate(pending_tool_calls):
                        if call.get("name") == tool_name:
                            matching_call = pending_tool_calls.pop(i)
                            break

                    if matching_call:
                        tool_input = matching_call.get("data", {}).get("input", {})
                        messages.append(
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "id": tool_call_id,
                                        "name": tool_name,
                                        "args": tool_input,
                                    }
                                ],
                            )
                        )

                    messages.append(
                        ToolMessage(content=str(output), tool_call_id=tool_call_id)
                    )
                except json.JSONDecodeError:
                    logger.warning("Failed to parse tool_result: %s", content[:100])

            elif message_type == "text":
                if role == "assistant" and sender_name == self._agent_name:
                    # Skip only THIS agent's text (redundant with tool calls)
                    logger.debug("Skipping own message: %s", content[:50])
                else:
                    # Include user messages AND other agents' messages
                    messages.append(HumanMessage(content=f"[{sender_name}]: {content}"))

        # Warn about unmatched tool calls
        if pending_tool_calls:
            logger.warning(
                "Found %s tool_calls without matching tool_results",
                len(pending_tool_calls),
            )

        return messages

    @staticmethod
    def _extract_tool_call_id(output: str) -> str | None:
        """Extract tool_call_id from tool output string."""
        match = re.search(r"tool_call_id='([^']+)'", output)
        return match.group(1) if match else None
