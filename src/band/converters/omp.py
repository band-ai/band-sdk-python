"""Native OMP resume candidates and read-only room transcript replay."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.core.types import MessageType


class OmpMetadataKey(StrEnum):
    """Metadata keys the native adapter writes on task and permission events."""

    ROOM = "omp_room_id"
    SESSION = "omp_session_file"
    SESSION_ID = "omp_session_id"


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
                case MessageType.TASK, dict() as metadata:
                    room = metadata.get(OmpMetadataKey.ROOM)
                    if (
                        not isinstance(room, str)
                        or OmpMetadataKey.SESSION not in metadata
                    ):
                        continue
                    session = metadata.get(OmpMetadataKey.SESSION)
                    if isinstance(session, str) and session:
                        sessions[room] = session
                    else:
                        sessions.pop(room, None)
        return OmpSessionState(
            room_to_session=sessions, replay_messages=build_replay_messages(raw)
        )
