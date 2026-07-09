"""Copilot SDK history converters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from band.core.protocols import HistoryConverter
from band.core.types import MessageType

logger = logging.getLogger(__name__)

# Task-event metadata key carrying the room's Copilot session id — the single
# source of truth shared by the adapter (writer) and this converter (reader).
SESSION_ID_METADATA_KEY = "copilot_session_id"


@dataclass
class CopilotSDKSessionState:
    """Composite state returned by the Copilot SDK converter.

    Combines the text history (for context injection) with an optional
    ``session_id`` extracted from persisted task events, enabling session
    resume after process restart.
    """

    text: str = ""
    session_id: str | None = None


def _format_line(hist: dict[str, Any]) -> str | None:
    """Format one platform message as a history line, or None to skip it."""
    message_type = hist.get("message_type", MessageType.TEXT)
    content = hist.get("content", "")

    if not content or message_type == MessageType.TASK:
        # Task events are internal bookkeeping — never include in text.
        return None

    if message_type == MessageType.TEXT:
        # Own agent text is included too: the adapter posts replies as plain
        # text and silences band_send_message tool reporting, so this line is
        # the only record of the agent's side of the conversation.
        return f"[{hist.get('sender_name', 'Unknown')}]: {content}"

    if message_type in (MessageType.TOOL_CALL, MessageType.TOOL_RESULT):
        return content

    return None


class CopilotSDKHistoryConverter(HistoryConverter[CopilotSDKSessionState]):
    """Convert platform history to Copilot SDK text format with session state.

    Returns a :class:`CopilotSDKSessionState` containing the text history
    and an optional ``session_id`` extracted from persisted task events,
    enabling session resume after process restart.

    Output example::

        [Alice]: What's the weather?
        {"name": "get_weather", "args": {"location": "NYC"}, "tool_call_id": "call_123"}
        {"name": "get_weather", "output": "{\"temperature\": 72}", "tool_call_id": "call_123"}
        [WeatherAgent]: It's 72 and sunny.
    """

    def __init__(self, agent_name: str = ""):
        # Kept for the converter protocol (SimpleAdapter propagates the agent
        # name); this converter includes own-agent lines, so it never filters.
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> CopilotSDKSessionState:
        if not raw:
            return CopilotSDKSessionState(text="")

        # Scan history in reverse for the latest task event with a session_id.
        session_id = next(
            (
                sid
                for hist in reversed(raw)
                if hist.get("message_type") == MessageType.TASK
                and (sid := (hist.get("metadata") or {}).get(SESSION_ID_METADATA_KEY))
            ),
            None,
        )

        lines = [line for hist in raw if (line := _format_line(hist))]
        return CopilotSDKSessionState(text="\n".join(lines), session_id=session_id)
