"""Replaying a room's prior exchanges into a fresh Parlant session."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.converters.parlant import ParlantMessages

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

logger = logging.getLogger(__name__)

# Marks replayed events so they are distinguishable from the live turn.
HISTORICAL_METADATA = {"historical": True}


def complete_exchanges(history: ParlantMessages) -> ParlantMessages:
    """The non-empty messages of *history* minus unanswered user messages.

    A user message with no assistant reply right after it is still pending:
    the live turn handles it, so replaying it would ask the question twice.
    """
    kept: ParlantMessages = []
    for index, message in enumerate(history):
        content = message.get("content", "")
        if not content:
            continue
        match message.get("role", "user"):
            case "user" if _answered(history, index):
                kept.append(message)
            case "user":
                logger.debug("Skipping unanswered user message: %s...", content[:50])
            case "assistant":
                kept.append(message)
    return kept


async def inject_history(
    *,
    app: Application,
    session_id: SessionId,
    history: ParlantMessages,
    agent_name: str,
) -> int:
    """Replay *history*'s complete exchanges without triggering a response.

    Returns how many messages were injected; one that fails is logged and
    skipped rather than failing the turn.
    """
    count = 0
    for message in complete_exchanges(history):
        try:
            await _inject_message(
                app=app, session_id=session_id, message=message, agent_name=agent_name
            )
        except Exception as e:  # noqa: BLE001 -- one bad historical message must not cost the live turn
            logger.warning(
                "Failed to inject history message (%s): %s", message.get("role"), e
            )
        else:
            count += 1
    return count


def _answered(history: ParlantMessages, index: int) -> bool:
    following = history[index + 1 : index + 2]
    return bool(following) and following[0].get("role") == "assistant"


async def _inject_message(
    *,
    app: Application,
    session_id: SessionId,
    message: dict[str, Any],
    agent_name: str,
) -> None:
    from parlant.core.app_modules.sessions import Moderation  # noqa: PLC0415
    from parlant.core.sessions import EventKind, EventSource  # noqa: PLC0415

    content = message["content"]
    if message.get("role", "user") == "user":
        await app.sessions.create_customer_message(
            session_id=session_id,
            moderation=Moderation.NONE,
            message=content,
            source=EventSource.CUSTOMER,
            trigger_processing=False,
            metadata=dict(HISTORICAL_METADATA),
        )
        return
    # Parlant requires participant info for AI_AGENT messages.
    await app.sessions.create_event(
        session_id=session_id,
        kind=EventKind.MESSAGE,
        source=EventSource.AI_AGENT,
        data={
            "message": content,
            "participant": {"display_name": message.get("sender", agent_name)},
        },
        metadata=dict(HISTORICAL_METADATA),
        trigger_processing=False,
    )
