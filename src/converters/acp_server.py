"""History converter for ACP server adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

# Use TYPE_CHECKING to avoid circular import:
# acp/__init__.py -> server_adapter.py -> this module -> acp/types.py -> acp/__init__.py
if TYPE_CHECKING:
    from band.integrations.acp.types import ACPSessionState

logger = logging.getLogger(__name__)


class ACPServerHistoryConverter(HistoryConverter["ACPSessionState"]):
    """Extracts session_id to room_id mappings from platform history.

    Unlike LLM converters that transform message content for model consumption,
    this converter extracts metadata for session state restoration. It scans
    platform history for ACP-specific metadata to rebuild the mapping
    between ACP session_ids and Band room_ids.

    The converter looks for messages with metadata containing:
    - acp_session_id: The ACP session identifier
    - acp_room_id: The corresponding Band room identifier
    """

    def convert(self, raw: list[dict[str, Any]]) -> ACPSessionState:
        """Extract ACP server session state from platform history.

        Args:
            raw: Platform history from format_history_for_llm().
                 Each dict has: role, content, sender_name, sender_type,
                 message_type, metadata, room_id, sender_id.

        Returns:
            ACPSessionState with session_to_room mapping extracted
            from the history.
        """
        # Runtime import to avoid circular import at module load time
        from band.integrations.acp.types import ACPSessionState

        session_to_room: dict[str, str] = {}
        session_cwd: dict[str, str] = {}
        session_mcp_servers: dict[str, list[dict[str, Any]]] = {}

        for msg in raw:
            metadata = msg.get("metadata") or {}

            # Extract session_id -> room_id mapping from ACP events
            session_id = metadata.get("acp_session_id")
            if session_id:
                room_id = metadata.get("acp_room_id") or msg.get("room_id")
                if room_id:
                    session_to_room[session_id] = room_id
                cwd = metadata.get("acp_cwd")
                if isinstance(cwd, str) and cwd:
                    session_cwd[session_id] = cwd
                mcp_servers = metadata.get("acp_mcp_servers")
                if isinstance(mcp_servers, list):
                    session_mcp_servers[session_id] = [
                        server for server in mcp_servers if isinstance(server, dict)
                    ]
                if room_id:
                    logger.debug(
                        "Restored ACP session mapping: %s -> %s",
                        session_id,
                        room_id,
                    )

        state = ACPSessionState(
            session_to_room=session_to_room,
            session_cwd=session_cwd,
            session_mcp_servers=session_mcp_servers,
        )

        logger.debug(
            "Converted ACP server history: %d sessions",
            len(session_to_room),
        )

        return state
