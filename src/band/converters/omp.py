"""Native OMP resume candidates and read-only room transcript replay."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.core.types import MessageType


class OmpMetadataKey(StrEnum):
    """Task-event metadata keys the native adapter writes and this converter reads."""

    ROOM = "omp_room_id"
    SESSION = "omp_session_file"


class OmpSessionState(BaseModel):
    """The native session file each room may resume, plus the room's transcript."""

    room_to_session: dict[str, str] = Field(default_factory=dict)
    replay_messages: list[str] = Field(default_factory=list)


class OmpHistoryConverter(HistoryConverter[OmpSessionState]):
    """Extract per-room native session files from task-event metadata."""

    def convert(self, raw: list[dict[str, Any]]) -> OmpSessionState:
        sessions: dict[str, str] = {}
        for message in raw:
            match message.get("message_type"), message.get("metadata"):
                case MessageType.TASK, {
                    OmpMetadataKey.ROOM: str(room),
                    OmpMetadataKey.SESSION: str(session),
                } if session:
                    sessions[room] = session
        return OmpSessionState(
            room_to_session=sessions, replay_messages=build_replay_messages(raw)
        )
