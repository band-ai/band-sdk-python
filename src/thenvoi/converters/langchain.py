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
        "LangChain dependencies not installed. "
        "Install with: uv add thenvoi-sdk[langgraph]"
    ) from e

from thenvoi.converters._tool_parsing import parse_tool_call, parse_tool_result
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
                parsed_call = parse_tool_call(content)
                if parsed_call:
                    pending_tool_calls.append(
                        {
                            "name": parsed_call.name,
                            "args": parsed_call.args,
                            "tool_call_id": parsed_call.tool_call_id,
                            "run_id": parsed_call.tool_call_id,
                        }
                    )
                    continue

                legacy_call = self._parse_legacy_tool_call(content)
                if legacy_call:
                    pending_tool_calls.append(legacy_call)

            elif message_type == "tool_result":
                parsed_result = parse_tool_result(content)
                if parsed_result:
                    tool_name = parsed_result.name
                    output = parsed_result.output
                    tool_call_id = parsed_result.tool_call_id
                else:
                    legacy_result = self._parse_legacy_tool_result(content)
                    if not legacy_result:
                        continue
                    tool_name = legacy_result["name"]
                    output = legacy_result["output"]
                    tool_call_id = legacy_result["tool_call_id"]

                matching_call = self._pop_matching_tool_call(
                    pending_tool_calls,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )

                if not matching_call:
                    logger.warning(
                        "Skipping tool_result without matching tool_call: %s",
                        tool_name,
                    )
                    continue

                messages.append(
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": tool_call_id,
                                "name": tool_name,
                                "args": matching_call.get("args", {}),
                            }
                        ],
                    )
                )
                messages.append(
                    ToolMessage(content=str(output), tool_call_id=tool_call_id)
                )

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
    def _pop_matching_tool_call(
        pending_tool_calls: list[dict[str, Any]],
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> dict[str, Any] | None:
        if tool_call_id:
            for i, call in enumerate(pending_tool_calls):
                if tool_call_id in (call.get("tool_call_id"), call.get("run_id")):
                    return pending_tool_calls.pop(i)

        for i in range(len(pending_tool_calls) - 1, -1, -1):
            if pending_tool_calls[i].get("name") == tool_name:
                return pending_tool_calls.pop(i)

        return None

    @classmethod
    def _parse_legacy_tool_call(cls, content: str) -> dict[str, Any] | None:
        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_call: %s", content[:100])
            return None

        if not isinstance(event, dict):
            logger.warning("Skipping non-object tool_call: %s", content[:100])
            return None

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_name = event.get("name")
        if not isinstance(tool_name, str):
            logger.warning("Skipping tool_call with missing name: %s", content[:100])
            return None

        tool_input = data.get("input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        run_id = event.get("run_id")
        return {
            "name": tool_name,
            "args": tool_input,
            "tool_call_id": run_id,
            "run_id": run_id,
        }

    @classmethod
    def _parse_legacy_tool_result(cls, content: str) -> dict[str, Any] | None:
        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_result: %s", content[:100])
            return None

        if not isinstance(event, dict):
            logger.warning("Skipping non-object tool_result: %s", content[:100])
            return None

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_name = event.get("name")
        if not isinstance(tool_name, str):
            logger.warning("Skipping tool_result with missing name: %s", content[:100])
            return None

        output = data.get("output", "")
        tool_call_id = cls._extract_tool_call_id(output) or event.get("run_id")
        if not isinstance(tool_call_id, str):
            logger.warning(
                "Skipping tool_result with missing tool_call_id: %s", content[:100]
            )
            return None

        return {"name": tool_name, "output": output, "tool_call_id": tool_call_id}

    @classmethod
    def _extract_tool_call_id(cls, output: Any) -> str | None:
        """Extract tool_call_id from current structured outputs or older string reprs."""
        if isinstance(output, dict):
            value = output.get("tool_call_id")
            if isinstance(value, str):
                return value

            messages = output.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    extracted = cls._extract_tool_call_id(message)
                    if extracted:
                        return extracted

            nested_output = output.get("output")
            if nested_output is not None:
                return cls._extract_tool_call_id(nested_output)

            return None

        if isinstance(output, list):
            for item in output:
                extracted = cls._extract_tool_call_id(item)
                if extracted:
                    return extracted
            return None

        value = getattr(output, "tool_call_id", None)
        if isinstance(value, str):
            return value

        match = re.search(r"tool_call_id='([^']+)'", str(output))
        return match.group(1) if match else None
