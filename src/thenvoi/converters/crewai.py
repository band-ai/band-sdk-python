"""CrewAI history converter."""

from __future__ import annotations

import json
import logging
from typing import Any

from thenvoi.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)

# Type alias for CrewAI messages (simple dict format)
CrewAIMessages = list[dict[str, Any]]


class CrewAIHistoryConverter(HistoryConverter[CrewAIMessages]):
    """
    Converts platform history to CrewAI-compatible message format.

    Output: [{"role": "user"|"assistant", "content": "...", "sender": "..."}]

    Notes:
    - Text messages preserve sender context
    - Other agents' text messages remain "assistant"
    - This agent's own text messages are skipped (CrewAI already tracks them in-run)
    - Tool events are converted to replayable text so CrewAI can see prior actions/results
    """

    def __init__(self, agent_name: str = ""):
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    @staticmethod
    def _dump_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _user_content(content: str, sender_name: str) -> str:
        return f"[{sender_name}]: {content}" if sender_name else content

    def _convert_tool_call(self, hist: dict[str, Any]) -> dict[str, Any] | None:
        content = hist.get("content", "")
        sender_name = hist.get("sender_name", self._agent_name)
        sender_type = hist.get("sender_type", "Agent")

        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse CrewAI tool_call: %s", repr(content[:100]))
            return None

        tool_name = event.get("name") or event.get("tool")
        if not tool_name:
            logger.warning(
                "Skipping CrewAI tool_call with missing tool name: %s",
                repr(content[:100]),
            )
            return None

        tool_input = event.get("args")
        if tool_input is None:
            tool_input = event.get("input", {})

        rendered = f"[Tool Call] {tool_name}"
        if tool_input not in ({}, None, ""):
            rendered = f"{rendered} {self._dump_json(tool_input)}"

        return {
            "role": "assistant",
            "content": rendered,
            "sender": sender_name,
            "sender_type": sender_type,
        }

    def _convert_tool_result(self, hist: dict[str, Any]) -> dict[str, Any] | None:
        content = hist.get("content", "")
        sender_name = hist.get("sender_name", "")
        sender_type = hist.get("sender_type", "System")

        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse CrewAI tool_result: %s", repr(content[:100])
            )
            return None

        tool_name = event.get("name") or event.get("tool")
        if not tool_name:
            logger.warning(
                "Skipping CrewAI tool_result with missing tool name: %s",
                repr(content[:100]),
            )
            return None

        is_error = bool(event.get("is_error")) or "error" in event
        output = event.get("output")
        if output is None:
            output = event.get("result")
        if output is None and "error" in event:
            output = event.get("error")
        if output is None:
            logger.warning(
                "Skipping CrewAI tool_result with missing output/result: %s",
                repr(content[:100]),
            )
            return None

        rendered_output = output if isinstance(output, str) else self._dump_json(output)
        prefix = "[Tool Error]" if is_error else "[Tool Result]"

        return {
            "role": "user",
            "content": self._user_content(
                f"{prefix} {tool_name}: {rendered_output}",
                sender_name,
            ),
            "sender": sender_name,
            "sender_type": sender_type,
        }

    def convert(self, raw: list[dict[str, Any]]) -> CrewAIMessages:
        """Convert platform history to CrewAI format."""
        messages: CrewAIMessages = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            role = hist.get("role", "user")
            content = hist.get("content", "")
            sender_name = hist.get("sender_name", "")
            sender_type = hist.get("sender_type", "User")

            if message_type == "thought":
                continue

            if message_type == "tool_call":
                tool_call = self._convert_tool_call(hist)
                if tool_call is not None:
                    messages.append(tool_call)
                continue

            if message_type == "tool_result":
                tool_result = self._convert_tool_result(hist)
                if tool_result is not None:
                    messages.append(tool_result)
                continue

            if message_type != "text":
                continue

            if role == "assistant" and sender_name == self._agent_name:
                continue
            if role == "assistant":
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "sender": sender_name,
                        "sender_type": sender_type,
                    }
                )
                continue

            messages.append(
                {
                    "role": "user",
                    "content": self._user_content(content, sender_name),
                    "sender": sender_name,
                    "sender_type": sender_type,
                }
            )

        return messages
