"""Copilot SDK integration for Band."""

from .operator_console import (
    NO_ANSWER_TEXT,
    UNAVAILABLE_TEXT,
    LogGate,
    OperatorConsole,
)
from .prompts import TURN_COMPLETION_GUIDANCE
from .room_ask_user import (
    ASK_USER_ROOM,
    QUESTION_DELIVERED_ANSWER,
    ROOM_ASK_USER_GUIDANCE,
    render_room_question,
)
from .session_manager import CopilotSessionManager

__all__ = [
    "ASK_USER_ROOM",
    "NO_ANSWER_TEXT",
    "QUESTION_DELIVERED_ANSWER",
    "ROOM_ASK_USER_GUIDANCE",
    "TURN_COMPLETION_GUIDANCE",
    "UNAVAILABLE_TEXT",
    "CopilotSessionManager",
    "LogGate",
    "OperatorConsole",
    "render_room_question",
]
