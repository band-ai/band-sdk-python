"""The live turn's message, as the room's Parlant customer posts it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.core.types import PlatformMessage

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

# Prefixes the change-triggered roster/contacts updates injected into a turn,
# so the model reads them as platform state, not as the requester speaking.
SYSTEM_UPDATE_PREFIX = "[System Update]: "


def compose_user_message(
    *, msg: PlatformMessage, participants_msg: str | None, contacts_msg: str | None
) -> str:
    """The turn's message with any roster/contacts updates in front of it."""
    user_message = msg.format_for_llm()
    for update in (participants_msg, contacts_msg):
        if update:
            user_message = f"{SYSTEM_UPDATE_PREFIX}{update}\n\n{user_message}"
    return user_message


async def post_user_message(
    *, app: Application, session_id: SessionId, message: str
) -> int:
    """Post *message* as the customer, triggering a response; return its offset."""
    from parlant.core.app_modules.sessions import Moderation  # noqa: PLC0415
    from parlant.core.sessions import EventSource  # noqa: PLC0415

    event = await app.sessions.create_customer_message(
        session_id=session_id,
        moderation=Moderation.NONE,
        message=message,
        source=EventSource.CUSTOMER,
        trigger_processing=True,
        metadata=None,
    )
    return event.offset
