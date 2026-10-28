"""Codex history converter."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from band.core.protocols import HistoryConverter
from band.integrations.codex.types import CodexSessionState
from band.runtime.sessionresume import ResumeRecord


class CodexMetadataKey(StrEnum):
    """Metadata keys of the Codex resume record, shared with its task narration."""

    ROOM = "codex_room_id"
    THREAD = "codex_thread_id"
    TRANSPORT = "codex_transport"


CODEX_RESUME_RECORD = ResumeRecord(
    label="Codex thread",
    room_key=CodexMetadataKey.ROOM,
    session_key=CodexMetadataKey.THREAD,
)


class CodexHistoryConverter(HistoryConverter["CodexSessionState"]):
    """Extract each room's resumable Codex thread from platform history.

    Unlike LLM prompt converters this only reads the newest
    ``CODEX_RESUME_RECORD`` per room; the adapter replays the transcript itself.
    """

    def set_agent_name(self, name: str) -> None:
        """No-op: Codex converter does not use agent name."""

    def convert(self, raw: list[dict[str, Any]]) -> CodexSessionState:
        """Return each room's newest recorded thread."""
        return CodexSessionState(room_to_thread=CODEX_RESUME_RECORD.candidates(raw))
