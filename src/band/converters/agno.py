"""Agno history converter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from agno.models.message import Message

logger = logging.getLogger(__name__)

# Forward-referenced so this module imports without agno installed; the real
# Message type is imported lazily inside convert().
AgnoMessages = list["Message"]


class AgnoHistoryConverter(HistoryConverter[AgnoMessages]):
    """
    Convert platform history to Agno message format.

    Output (text-only skeleton):
    - this agent's text messages -> Message(role="assistant", content=...)
    - everyone else's text messages -> Message(role="user", content="[name]: ...")

    NOTE: tool_call / tool_result / thought events are skipped for now. Tool-event
    conversion lands together with Band platform-tool wiring in a follow-up.
    """

    def __init__(self, agent_name: str = ""):
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> AgnoMessages:
        """Convert platform history to Agno messages."""
        from agno.models.message import Message

        messages: list[Message] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            if message_type != "text":
                # Skip tool_call / tool_result / thought events for now.
                continue

            content = hist.get("content", "")
            role = hist.get("role", "user")
            sender_name = hist.get("sender_name", "")

            if role == "assistant" and sender_name == self._agent_name:
                messages.append(Message(role="assistant", content=content))
            else:
                formatted = f"[{sender_name}]: {content}" if sender_name else content
                messages.append(Message(role="user", content=formatted))

        return messages
