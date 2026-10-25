"""The live turn's message, as the room's Parlant customer posts it."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from band.core.types import PlatformMessage
from band.runtime.formatters import format_current_inputs, mention_label
from band.runtime.prompts import SYSTEM_UPDATE_PREFIX

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

# A mention token is ``@`` plus a whitespace-free label. ``.``, ``/``, and ``-``
# continue it (``alexander`` must not consume ``alexander.zaikman/tom``, and
# ``owner/agent`` must not consume ``owner/agent-2``); other punctuation does not.
_MENTION_CONTINUATION = r"[\w./-]"


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
    alternation = "|".join(re.escape(token) for token in tokens)
    pattern = re.compile(rf"(?<!\S)(?:{alternation})(?!{_MENTION_CONTINUATION})")
    return pattern.sub(lambda _match: agent_name, content)


def compose_user_message(
    *,
    messages: Sequence[PlatformMessage],
    participants_msg: str | None,
    contacts_msg: str | None,
    agent_id: str | None = None,
    agent_name: str = "",
    participants: list[Any] | None = None,
) -> str:
    """The turn's inputs with any roster/contacts updates in front of them."""
    addressed = [
        replace(
            message,
            content=name_this_agent(
                message.content,
                agent_id=agent_id,
                agent_name=agent_name,
                participants=participants or [],
            ),
        )
        for message in messages
    ]
    user_message = format_current_inputs(addressed)
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
# AlphaEngine._get_preamble_task and does not cancel it when process-session
# is cancelled. The nested coroutine's qualname is the class that defines it.
PREAMBLE_TASK_QUALNAME = "AlphaEngine._get_preamble_task.<locals>.preamble_task"


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


def _self_mention_tokens(agent_id: str, participants: list[Any]) -> list[str]:
    """Spellings this agent's mention may already have, longest first.

    The pattern tries them left to right, so a shorter spelling must not win
    when one of this agent's own tokens is a prefix of another.
    """
    own_label: str | None = None
    other_labels: set[str] = set()
    for participant in participants:
        label = mention_label(
            handle=_field(participant, "handle"),
            name=_field(participant, "name"),
        )
        if _field(participant, "id") == agent_id:
            own_label = label
            continue
        if label:
            other_labels.add(label)
    tokens = {f"@[[{agent_id}]]"}
    # A shared label is already ambiguous after the room text is rendered, so
    # rewriting it would rename the other participant too.
    if own_label and own_label not in other_labels:
        tokens.add(f"@{own_label}")
    return sorted(tokens, key=len, reverse=True)


def _field(participant: Any, key: str) -> str | None:
    value = (
        participant.get(key)
        if isinstance(participant, dict)
        else getattr(participant, key, None)
    )
    return value if isinstance(value, str) and value else None
