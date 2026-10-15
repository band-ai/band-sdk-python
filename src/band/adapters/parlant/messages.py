"""The live turn's message, as the room's Parlant customer posts it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

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


def process_session_tag(session_id: SessionId | str) -> str:
    """The tag Parlant runs a session's response under in its background tasks.

    Mirrors Parlant's own tag (pinned by a parity test against the engine), so
    cancelling it stops the engine's work on the session.
    """
    return f"process-session({session_id})"


# Parlant 3.3.2 creates this child with asyncio.create_task inside
# process-session and does not cancel it when that task is cancelled
# (engine.py preamble generation). Pinned by the real-engine stop probe.
PREAMBLE_TASK_QUALNAME = "Engine._get_preamble_task.<locals>.preamble_task"


def live_preamble_tasks(session_id: str) -> list[asyncio.Task[Any]]:
    """Preamble generation still running for ``session_id``."""
    tasks: list[asyncio.Task[Any]] = []
    for task in asyncio.all_tasks():
        if task.done():
            continue
        frame = next(
            (
                suspended
                for suspended in task.get_stack()
                if suspended.f_code.co_qualname == PREAMBLE_TASK_QUALNAME
            ),
            None,
        )
        if frame is None:
            continue
        context = frame.f_locals.get("context")
        session = getattr(context, "session", None)
        if str(getattr(session, "id", "")) == str(session_id):
            tasks.append(task)
    return tasks


async def await_stopped_task(task: asyncio.Task[Any]) -> None:
    """Wait until ``task`` finishes, including when it was cancelled."""
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


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
