"""Push handler for unsolicited ACP session updates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from band.core.types import PlatformMessage
from band.integrations.acp.event_converter import EventConverter

if TYPE_CHECKING:
    from band.integrations.acp.server_adapter import BandACPServerAdapter

logger = logging.getLogger(__name__)


class ACPPushHandler:
    """Sends unsolicited session_update to the editor.

    When platform messages arrive for rooms with active ACP sessions
    but no pending prompt, this handler pushes the update to the
    editor so it can display real-time activity (e.g., other agents
    working in the same room).
    """

    def __init__(self, adapter: BandACPServerAdapter) -> None:
        """Initialize push handler.

        Args:
            adapter: The server adapter with session state and ACP client.
        """
        self._adapter = adapter

    async def handle_push_event(self, msg: PlatformMessage, room_id: str) -> None:
        """Push unsolicited session_update to editor.

        Looks up the session_id from room_id, converts the message
        via EventConverter, and sends it as a session_update.

        Args:
            msg: The platform message to push.
            room_id: The room the message came from.
        """
        session_id = self._adapter.get_session_for_room(room_id)
        if not session_id:
            logger.debug("Push skipped: no session mapping for room %s", room_id)
            return

        acp_client = self._adapter.get_acp_client()
        if not acp_client:
            logger.debug("Push skipped: no ACP client connected")
            return

        chunk = EventConverter.convert(msg)
        if chunk is not None:
            await acp_client.session_update(
                session_id=session_id,
                update=chunk,
            )
            logger.debug(
                "Pushed session_update for session %s (room %s)",
                session_id,
                room_id,
            )
