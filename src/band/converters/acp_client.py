"""History converter for ACP client adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.converters.helpers import build_replay_messages
from band.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from band.integrations.acp.client_types import ACPClientSessionState

logger = logging.getLogger(__name__)


class ACPClientHistoryConverter(HistoryConverter["ACPClientSessionState"]):
    """Extracts room-to-session resume candidates from platform history.

    Scans platform history for ACP client-specific metadata. The adapter validates
    each candidate with the connected ACP agent before reusing it.

    The converter looks for messages with metadata containing:
    - acp_client_session_id: The remote ACP agent's session identifier
    - acp_client_room_id: The corresponding Band room identifier
    """

    def convert(self, raw: list[dict[str, Any]]) -> ACPClientSessionState:
        """Extract ACP client session state from platform history.

        Args:
            raw: Platform history from format_history_for_llm().

        Returns:
            ACPClientSessionState with room-to-session resume candidates.
        """
        # Runtime import to avoid circular import at module load time
        from band.integrations.acp.client_types import ACPClientSessionState

        room_to_session: dict[str, str] = {}

        for msg in raw:
            metadata = msg.get("metadata") or {}

            session_id = metadata.get("acp_client_session_id")
            room_id = metadata.get("acp_client_room_id")
            if session_id and room_id:
                room_to_session[room_id] = session_id
                logger.debug(
                    "Found persisted ACP session candidate: %s -> %s",
                    room_id,
                    session_id,
                )

        state = ACPClientSessionState(
            room_to_session=room_to_session,
            replay_messages=build_replay_messages(raw),
        )

        logger.debug(
            "Converted ACP client history: %d room-session candidates",
            len(room_to_session),
        )

        return state
