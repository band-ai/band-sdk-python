"""Typed session state for the OpenCode integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OpencodeSessionState:
    """Session metadata persisted in Band task events."""

    session_id: str | None = None
    room_id: str | None = None
    created_at: datetime | None = None
    replay_messages: list[str] = field(default_factory=list)
