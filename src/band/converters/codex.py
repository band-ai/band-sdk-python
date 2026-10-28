"""Codex history converter."""

from __future__ import annotations

from typing import Any

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.integrations.codex.types import CodexMetadataKey, CodexSessionState
from band.runtime.sessionresume import ResumeRecord

CODEX_RESUME_RECORD = ResumeRecord(
    label="Codex thread",
    room_key=CodexMetadataKey.ROOM,
    session_key=CodexMetadataKey.THREAD,
)


class CodexHistoryConverter(HistoryConverter["CodexSessionState"]):
    """Extract each room's resumable Codex thread from platform history.

    Reads the newest ``CODEX_RESUME_RECORD`` per room, and renders the room's
    text messages as the replay a fresh thread is sent.
    """

    def set_agent_name(self, name: str) -> None:
        """No-op: Codex converter does not use agent name."""

    def convert(self, raw: list[dict[str, Any]]) -> CodexSessionState:
        """Return each room's newest recorded thread and the room's transcript."""
        return CodexSessionState(
            room_to_thread=CODEX_RESUME_RECORD.candidates(raw),
            replay_messages=build_replay_messages(raw),
        )
