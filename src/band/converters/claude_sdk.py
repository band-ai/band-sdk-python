"""Claude SDK history converters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from band.converters._utils import format_replay_message
from band.core.types import is_text_message_type
from band.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)


@dataclass
class ClaudeSDKSessionState:
    """Composite state returned by the Claude SDK converter.

    Combines the text history (for context injection) with an optional
    ``session_id`` extracted from persisted task events, enabling session
    resume after process restart.
    """

    text: str = ""
    session_id: str | None = None


def _build_text(raw: list[dict[str, Any]]) -> str:
    """Build text history from raw platform messages."""
    lines: list[str] = []

    for hist in raw:
        message_type = hist.get("message_type", "text")
        content = hist.get("content", "")
        sender_name = hist.get("sender_name", "Unknown")

        # Task events are internal bookkeeping — never include in text.
        if message_type == "task":
            continue

        if is_text_message_type(message_type):
            if content:
                lines.append(f"[{sender_name}]: {content}")
            continue

        replay = format_replay_message(hist)
        if replay:
            lines.append(replay)

    return "\n".join(lines) if lines else ""


class ClaudeSDKHistoryConverter(HistoryConverter[ClaudeSDKSessionState]):
    """
    Converts platform history to Claude SDK text format with session state.

    Returns a :class:`ClaudeSDKSessionState` containing the text history
    and an optional ``session_id`` extracted from persisted task events,
    enabling session resume after process restart.

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
        if not raw:
            return ClaudeSDKSessionState(text="")

        # Scan history in reverse for the latest task event with a session_id.
        session_id: str | None = None
        for hist in reversed(raw):
            if hist.get("message_type") == "task":
                metadata = hist.get("metadata") or {}
                sid = metadata.get("claude_sdk_session_id")
                if sid:
                    session_id = sid
                    break

        text = _build_text(raw)
        return ClaudeSDKSessionState(text=text, session_id=session_id)
