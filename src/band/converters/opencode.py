"""OpenCode history converter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.sessionresume import ResumeRecord


class OpencodeMetadataKey(StrEnum):
    """Metadata keys of the OpenCode adapter's resume record.

    ``SESSION`` differs from the ``opencode_session_id`` that earlier releases
    posted when a session was created, before its seeding prompt ran, so
    those records are never resumed.
    """

    ROOM = "opencode_room_id"
    SESSION = "opencode_seeded_session_id"
    MCP_SERVER_NAME = "opencode_mcp_server_name"


OPENCODE_RESUME_RECORD = ResumeRecord(
    label="OpenCode session",
    room_key=OpencodeMetadataKey.ROOM,
    session_key=OpencodeMetadataKey.SESSION,
)


class OpencodeHistoryConverter(HistoryConverter[OpencodeSessionState]):
    """Extract each room's resumable OpenCode session plus the room's transcript."""

    def set_agent_name(self, name: str) -> None:
        """No-op for metadata-only converter compatibility."""

    def convert(self, raw: list[dict[str, Any]]) -> OpencodeSessionState:
        return OpencodeSessionState(
            room_to_session=OPENCODE_RESUME_RECORD.candidates(raw),
            session_registrations=_session_registrations(raw),
            replay_messages=build_replay_messages(raw),
        )


def _session_registrations(raw: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Map each OpenCode session in ``raw`` to the MCP registration that recorded it.

    Only resume records count, read by the same rule as
    ``OPENCODE_RESUME_RECORD.candidates``.
    """
    records = (
        message.get("metadata")
        for message in raw
        if OPENCODE_RESUME_RECORD.matches(message)
    )
    return {
        entry[OpencodeMetadataKey.SESSION]: entry[OpencodeMetadataKey.MCP_SERVER_NAME]
        for entry in records
        if isinstance(entry, Mapping)
        and isinstance(entry.get(OpencodeMetadataKey.ROOM), str)
        and isinstance(entry.get(OpencodeMetadataKey.SESSION), str)
        and entry[OpencodeMetadataKey.SESSION]
        and isinstance(entry.get(OpencodeMetadataKey.MCP_SERVER_NAME), str)
    }
