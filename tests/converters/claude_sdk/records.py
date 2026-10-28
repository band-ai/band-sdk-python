"""Claude SDK resume records as raw room history."""

from __future__ import annotations

from typing import Any

from band.converters.claude_sdk import CLAUDE_SDK_RESUME_RECORD
from band.core.types import MessageType


def recorded_session(room_id: str, session_id: str) -> dict[str, Any]:
    """The resume record a room persisted for a Claude session."""
    return {
        "content": CLAUDE_SDK_RESUME_RECORD.label,
        "message_type": MessageType.TASK,
        "metadata": {
            CLAUDE_SDK_RESUME_RECORD.room_key: room_id,
            CLAUDE_SDK_RESUME_RECORD.session_key: session_id,
        },
    }


def legacy_record(session_id: str) -> dict[str, Any]:
    """A session id recorded before records named their room."""
    return {
        "content": CLAUDE_SDK_RESUME_RECORD.label,
        "message_type": MessageType.TASK,
        "metadata": {CLAUDE_SDK_RESUME_RECORD.session_key: session_id},
    }
