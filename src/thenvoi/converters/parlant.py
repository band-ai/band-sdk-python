"""Parlant history converter."""

from __future__ import annotations

from typing import Any

from thenvoi.converters._utils import format_replay_message
from thenvoi.core.protocols import HistoryConverter
from thenvoi.core.types import is_text_message_type

# Type alias for Parlant messages (simple dict format)
ParlantMessages = list[dict[str, Any]]


class ParlantHistoryConverter(HistoryConverter[ParlantMessages]):
    """
    Converts platform history to Parlant message format.

    Output: [{"role": "user", "content": "...", "sender": "..."}]

    Note:
    - Text messages are converted normally
    - Tool call/result events are included as labeled replay context
    - User messages are prefixed with sender name for context
    - ALL assistant messages are included (unlike other adapters)

    Unlike LangGraph/Claude adapters, Parlant needs the FULL conversation history
    including this agent's own responses, because we reconstruct the session state
    in Parlant's internal storage.
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent (stored but not used for filtering,
                       since Parlant needs full history).
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> ParlantMessages:
        """Convert platform history to Parlant format.

        Unlike other adapters, Parlant needs the full conversation history
        including this agent's own responses to properly reconstruct sessions.
        """
        messages: ParlantMessages = []

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

            if content is None or not str(content).strip():
                continue

            if role == "assistant":
                # Include ALL assistant messages (this agent + other agents)
                # Parlant needs full history to reconstruct session state
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"[{sender_name}]: {content}"
                        if sender_name
                        else content,
                        "sender": sender_name,
                        "sender_type": sender_type,
                    }
                )
            else:
                # User messages
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
