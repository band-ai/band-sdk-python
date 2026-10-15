"""The live turn's message, as the room's Parlant customer posts it."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from band.core.types import PlatformMessage

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

# Prefixes the change-triggered roster/contacts updates injected into a turn,
# so the model reads them as platform state, not as the requester speaking.
SYSTEM_UPDATE_PREFIX = "[System Update]: "


def name_this_agent(
    content: str,
    *,
    agent_id: str | None,
    agent_name: str,
    participants: list[Any],
) -> str:
    """Rewrite this agent's mention to the name Parlant's prompt uses.

    The tool caller is told "You are an AI agent named {agent_name}". The room
    text it also sees has already turned ``@[[id]]`` into ``@handle`` (or a
    whitespace-collapsed ``@name``), which does not match that name, so the
    model reads the mention as a different agent.
    """
    if not content or not agent_id or not agent_name:
        return content
    tokens = _self_mention_tokens(agent_id, participants)
    pattern = re.compile("|".join(rf"{re.escape(token)}(?!\S)" for token in tokens))
    return pattern.sub(agent_name, content)


def compose_user_message(
    *,
    msg: PlatformMessage,
    participants_msg: str | None,
    contacts_msg: str | None,
    agent_id: str | None = None,
    agent_name: str = "",
    participants: list[Any] | None = None,
) -> str:
    """The turn's message with any roster/contacts updates in front of it."""
    addressed = replace(
        msg,
        content=name_this_agent(
            msg.content,
            agent_id=agent_id,
            agent_name=agent_name,
            participants=participants or [],
        ),
    )
    user_message = addressed.format_for_llm()
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


def _self_mention_tokens(agent_id: str, participants: list[Any]) -> list[str]:
    """Every token this agent's mention may already have been written as.

    Longest first, so ``@owner/agent`` does not consume ``@owner/agent-2``.
    """
    tokens = {f"@[[{agent_id}]]"}
    for participant in participants:
        if _field(participant, "id") != agent_id:
            continue
        handle = _field(participant, "handle")
        name = _field(participant, "name")
        if handle:
            tokens.add(f"@{handle}")
        if name:
            collapsed = "-".join(name.split())
            if collapsed:
                tokens.add(f"@{collapsed}")
    return sorted(tokens, key=len, reverse=True)


def _field(participant: Any, key: str) -> str | None:
    value = (
        participant.get(key)
        if isinstance(participant, dict)
        else getattr(participant, key, None)
    )
    return value if isinstance(value, str) and value else None
