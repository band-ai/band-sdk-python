"""Hydrated conversation context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ConversationContext:
    """
    Hydrated context for a room.

    Contains conversation history and participant information
    for context-aware processing.
    """

    room_id: str
    messages: list[dict[str, Any]]
    participants: list[dict[str, Any]]
    hydrated_at: datetime
