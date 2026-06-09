"""Parlant history converter."""

from __future__ import annotations

from typing import Any

from band.core.protocols import HistoryConverter

# Type alias for Parlant messages (simple dict format)
ParlantMessages = list[dict[str, Any]]


class ParlantHistoryConverter(HistoryConverter[ParlantMessages]):
    """
    Converts platform history to Parlant message format.

    Output: [{"role": "user", "content": "...", "sender": "..."}]

    Note:
    - Only converts text messages (tool_call/tool_result events are skipped)
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

            # Only convert text messages
            if message_type != "text":
                continue

            role = hist.get("role", "user")
            content = hist.get("content", "")
            sender_name = hist.get("sender_name", "")
            sender_type = hist.get("sender_type", "User")

            if not content:
                continue

            if role == "assistant":
                # Include ALL assistant messages (this agent + other agents)
                # Parlant needs full history to reconstruct session state
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
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
