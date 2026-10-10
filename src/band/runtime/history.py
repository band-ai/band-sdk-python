"""Refetching a room's earlier transcript for a session the runtime did not bootstrap."""

from __future__ import annotations

from band.core.protocols import AgentToolsProtocol
from band.runtime.formatters import messages_before


async def fetch_earlier_messages(
    tools: AgentToolsProtocol, *, room_id: str, trigger_id: str | None
) -> list[dict]:
    """The room transcript strictly before ``trigger_id``, without the trigger.

    The runtime hands history over only on bootstrap, so a session minted
    later (its predecessor was torn down or could not be resumed) refetches
    it. Entries from the trigger onward are this turn and pending turns of
    their own. A failed fetch propagates; the caller decides between failing
    the turn and starting without history.
    """
    context = await tools.fetch_room_context(room_id=room_id)
    earlier = messages_before(context.get("data") or [], trigger_id)
    return [message for message in earlier if message.get("id") != trigger_id]
