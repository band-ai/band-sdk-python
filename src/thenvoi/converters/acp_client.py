"""History converter for ACP client adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from thenvoi.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from thenvoi.integrations.acp.client_types import ACPClientSessionState

logger = logging.getLogger(__name__)


class ACPClientHistoryConverter(HistoryConverter["ACPClientSessionState"]):
    """Extracts room_id to session_id mappings from platform history.

    Scans platform history for ACP client-specific metadata to rebuild
    the mapping between Thenvoi room_ids and remote ACP session_ids.

    The converter looks for messages with metadata containing:
    - acp_client_session_id: The remote ACP agent's session identifier
    - acp_client_room_id: The corresponding Thenvoi room identifier
    """

    def convert(self, raw: list[dict[str, Any]]) -> ACPClientSessionState:
        """Extract ACP client session state from platform history.

        Args:
            raw: Platform history from format_history_for_llm().

        Returns:
            ACPClientSessionState with room_to_session mappings.
        """
        # Runtime import to avoid circular import at module load time
        from thenvoi.integrations.acp.client_types import ACPClientSessionState

        room_to_session: dict[str, str] = {}

        for msg in raw:
            metadata = msg.get("metadata") or {}

            session_id = metadata.get("acp_client_session_id")
            room_id = metadata.get("acp_client_room_id")
            if session_id and room_id:
                room_to_session[room_id] = session_id
                logger.debug(
                    "Restored ACP client session mapping: %s -> %s",
                    room_id,
                    session_id,
                )

        state = ACPClientSessionState(room_to_session=room_to_session)

        logger.debug(
            "Converted ACP client history: %d room-session mappings",
            len(room_to_session),
        )

        return state
