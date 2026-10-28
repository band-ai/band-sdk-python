"""Native OMP resume candidates and read-only room transcript replay."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.runtime.sessionresume import ResumeRecord


class OmpMetadataKey(StrEnum):
    """Metadata keys the native adapter writes on task and permission events."""

    ROOM = "omp_room_id"
    SESSION = "omp_session_file"
    SESSION_ID = "omp_session_id"
    REQUEST_ID = "omp_request_id"


OMP_RESUME_RECORD = ResumeRecord(
    label="OMP session",
    room_key=OmpMetadataKey.ROOM,
    session_key=OmpMetadataKey.SESSION,
)


class OmpSessionState(BaseModel):
    """The native session file each room may resume, plus the room's transcript."""

    room_to_session: dict[str, str] = Field(default_factory=dict)
    replay_messages: list[str] = Field(default_factory=list)


class OmpHistoryConverter(HistoryConverter[OmpSessionState]):
    """Extract per-room native session files from task-event metadata."""

    def convert(self, raw: list[dict[str, Any]]) -> OmpSessionState:
        return OmpSessionState(
            room_to_session=OMP_RESUME_RECORD.candidates(raw),
            replay_messages=build_replay_messages(raw),
        )
