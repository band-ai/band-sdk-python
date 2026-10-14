"""The room agent base class a NOOA model sees.

NOOA puts the globals of every module in an agent class's MRO into the model's
execution context, so this module holds nothing but the class and the names its
annotations need. Anything else defined here would be advertised to the model,
and callable from its code, on every model call.
"""

from __future__ import annotations

from nooa import Agent

from band.adapters.nooa.tools import RoomTools

# NOOA hides these names from the model's execution context. ``annotations`` is
# the __future__ feature object that the import above binds.
_agentdoc_hidden_names = frozenset({"annotations"})


class NooaRoomAgent(Agent):
    """You are a participant in a Band chat room.

    Handle each incoming room message with the Band tools on `self.room`.
    """

    room: RoomTools

    async def respond(self, message: str) -> None:
        """Handle one incoming Band room message.

        Reply with `await self.room.band_send_message(...)`, or decline with
        `await self.room.band_no_reply(...)` when no reply is needed. Call other
        tools on `self.room` first when the reply needs them.
        """
        ...  # noqa: PIE790 -- NOOA hands an ellipsis-bodied method to the model
