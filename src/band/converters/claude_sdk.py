"""Claude SDK history converters."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from band.core.protocols import HistoryConverter
from band.runtime.sessionresume import ResumeRecord

logger = logging.getLogger(__name__)


class ClaudeSDKMetadataKey(StrEnum):
    """Metadata keys of the Claude SDK adapter's resume record."""

    ROOM = "claude_sdk_room_id"
    SESSION = "claude_sdk_session_id"


CLAUDE_SDK_RESUME_RECORD = ResumeRecord(
    label="Claude SDK session",
    room_key=ClaudeSDKMetadataKey.ROOM,
    session_key=ClaudeSDKMetadataKey.SESSION,
)


@dataclass
class ClaudeSDKSessionState:
    """Composite state returned by the Claude SDK converter.

    Combines the text history (for context injection) with each room's
    resumable session (the newest ``CLAUDE_SDK_RESUME_RECORD``), enabling
    session resume after process restart.
    """

    text: str = ""
    room_to_session: dict[str, str] = field(default_factory=dict)


def _build_text(raw: list[dict[str, Any]], agent_name: str) -> str:
    """Build text history from raw platform messages."""
    lines: list[str] = []

    for hist in raw:
        message_type = hist.get("message_type", "text")
        content = hist.get("content", "")
        role = hist.get("role", "user")
        sender_name = hist.get("sender_name", "Unknown")

        # Task events are internal bookkeeping — never include in text.
        if message_type == "task":
            continue

        if message_type == "text":
            # Skip own text (redundant with tool results)
            if role == "assistant" and sender_name == agent_name:
                logger.debug("Skipping own message: %s...", content[:50])
                continue
            # Include user and other agents' messages
            if content:
                lines.append(f"[{sender_name}]: {content}")

        elif message_type == "tool_call":
            # Include raw tool_call JSON as-is
            if content:
                lines.append(content)

        elif message_type == "tool_result":
            # Include raw tool_result JSON as-is
            if content:
                lines.append(content)

    return "\n".join(lines) if lines else ""


class ClaudeSDKHistoryConverter(HistoryConverter[ClaudeSDKSessionState]):
    """
    Converts platform history to Claude SDK text format with session state.

    Returns a :class:`ClaudeSDKSessionState` containing the text history
    and each room's resumable session, enabling session resume after process
    restart.

    Output example::

        [Alice]: What's the weather?
        {"name": "get_weather", "args": {"location": "NYC"}, "tool_call_id": "toolu_123"}
        {"output": {"temperature": 72}, "tool_call_id": "toolu_123"}
        [Other Agent]: I can help too!
    """

    def __init__(self, agent_name: str = ""):
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> ClaudeSDKSessionState:
        return ClaudeSDKSessionState(
            text=_build_text(raw, self._agent_name),
            room_to_session=CLAUDE_SDK_RESUME_RECORD.candidates(raw),
        )
