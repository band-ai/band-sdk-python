"""Which Parlant customer and session each Band room talks through."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import parlant.sdk as p
    from parlant.core.agents import AgentId
    from parlant.core.application import Application
    from parlant.core.customers import CustomerId
    from parlant.core.sessions import SessionId

logger = logging.getLogger(__name__)

# Session titles carry this much of the room id, enough to tell rooms apart
# in Parlant's own UI.
SESSION_TITLE_ROOM_CHARS = 8


class RoomSessions:
    """Per-room Parlant customers and sessions on one running server.

    Customers and sessions are cached separately, so a customer created
    before a failed session create is reused on the retry.
    """

    def __init__(
        self, *, server: p.Server, app: Application, agent_id: AgentId
    ) -> None:
        self.app = app
        self._server = server
        self._agent_id = agent_id
        self._customers: dict[str, CustomerId] = {}
        self._sessions: dict[str, SessionId] = {}

    async def session_for(self, room_id: str, *, customer_name: str) -> SessionId:
        """The room's Parlant session, created on first use."""
        if room_id not in self._sessions:
            customer_id = await self._customer_for(room_id, name=customer_name)
            session = await self.app.sessions.create(
                customer_id=customer_id,
                agent_id=self._agent_id,
                title=f"Band Room {room_id[:SESSION_TITLE_ROOM_CHARS]}",
            )
            self._sessions[room_id] = session.id
            logger.info("Session created: %s for room %s", session.id, room_id)
        return self._sessions[room_id]

    def forget(self, room_id: str) -> None:
        self._sessions.pop(room_id, None)
        self._customers.pop(room_id, None)

    async def _customer_for(self, room_id: str, *, name: str) -> CustomerId:
        if room_id not in self._customers:
            # The full room id, not a prefix: it is the customer's stable
            # identity on the server, and two rooms sharing a prefix must not
            # collide onto one customer.
            customer = await self._server.create_customer(
                name=name, id=f"band-{room_id}"
            )
            self._customers[room_id] = customer.id
        return self._customers[room_id]
