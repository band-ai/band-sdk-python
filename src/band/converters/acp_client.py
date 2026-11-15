"""History converter for ACP client adapter."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter
from band.runtime.sessionresume import ResumeRecord

if TYPE_CHECKING:
    from band.integrations.acp.client_types import ACPClientSessionState

logger = logging.getLogger(__name__)


class ACPClientMetadataKey(StrEnum):
    """Metadata keys of the ACP client adapter's resume record."""

    ROOM = "acp_client_room_id"
    SESSION = "acp_client_session_id"


ACP_CLIENT_RESUME_RECORD = ResumeRecord(
    label="ACP client session",
    room_key=ACPClientMetadataKey.ROOM,
    session_key=ACPClientMetadataKey.SESSION,
)


class ACPClientHistoryConverter(HistoryConverter["ACPClientSessionState"]):
    """Extracts room-to-session resume candidates plus a replayable transcript.

    The candidates are the newest ``ACP_CLIENT_RESUME_RECORD`` per room; the
    adapter validates each with the connected ACP agent before reusing it.

    It also renders the room's text messages as replay lines (the adapter's
    fallback context when no persisted session can be restored); the adapter's
    own narration events (thought/tool_call/tool_result/task) are excluded.
    """

    def convert(self, raw: list[dict[str, Any]]) -> ACPClientSessionState:
        """Extract ACP client session state from platform history.

        Args:
            raw: Platform history from format_history_for_llm().

        Returns:
            ACPClientSessionState with room-to-session resume candidates and
            the room's replayable text transcript.
        """
        # band.integrations.acp.client_types imports client_runtime, which
        # imports the optional `acp` extra (agent-client-protocol) at module
        # top level — deferred so this converter stays importable without it.
        from band.integrations.acp.client_types import (  # noqa: PLC0415
            ACPClientSessionState,
        )

        room_to_session = ACP_CLIENT_RESUME_RECORD.candidates(raw)
        state = ACPClientSessionState(
            room_to_session=room_to_session,
            replay_messages=build_replay_messages(raw),
        )

        logger.debug(
            "Converted ACP client history: %d room-session candidates, %d replay lines",
            len(room_to_session),
            len(state.replay_messages),
        )

        return state
