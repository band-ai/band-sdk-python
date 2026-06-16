"""CrewAI history converter."""

from __future__ import annotations

from typing import Any

from thenvoi.converters._utils import format_replay_message
from thenvoi.core.protocols import HistoryConverter
from thenvoi.core.types import is_text_message_type

# Type alias for CrewAI messages (simple dict format)
CrewAIMessages = list[dict[str, Any]]


class CrewAIHistoryConverter(HistoryConverter[CrewAIMessages]):
    """
    Converts platform history to CrewAI-compatible message format.

    Output: [{"role": "user", "content": "...", "sender": "..."}]

    Note:
    - Text messages are converted normally
    - Tool call/result events are included as labeled replay context
    - User and peer-agent messages include sender name for context
    - Only this agent's own messages are included with role "assistant"
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent, used to distinguish own-agent
                       turns from peer-agent context.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name used to identify own-agent turns.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> CrewAIMessages:
        """Convert platform history to CrewAI format."""
        messages: CrewAIMessages = []

        for hist in raw:
            message_type = hist.get("message_type", "text")

            role = hist.get("role", "user")
            content = hist.get("content", "")
            sender_name = hist.get("sender_name", "")
            sender_type = hist.get("sender_type", "User")

            if not is_text_message_type(message_type):
                replay = format_replay_message(hist)
                if not replay:
                    continue
                # Replay lines are already labeled ([Tool Call]: / [Tool
                # Result]:) — no sender prefix on top.
                messages.append(
                    {
                        "role": "assistant" if message_type == "tool_call" else "user",
                        "content": replay,
                        "sender": "System",
                        "sender_type": "System",
                    }
                )
                continue

            if content is None or (
                isinstance(content, str) and content and not content.strip()
            ):
                continue

            if (
                role == "assistant"
                and self._agent_name
                and sender_name == self._agent_name
            ):
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "sender": sender_name,
                        "sender_type": sender_type,
                    }
                )
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[{sender_name}]: {content}"
                        if sender_name
                        else content,
                        "sender": sender_name,
                        "sender_type": sender_type,
                    }
                )

        return messages
