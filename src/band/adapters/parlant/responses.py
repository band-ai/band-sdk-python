"""Waiting for the Parlant engine's response to a turn and relaying it.

Parlant may answer with a preamble (an acknowledgment tagged
``__preamble__``, sent before tool execution) followed by final messages.
Final messages in one event batch are joined into one reply; preambles are
never relayed. A Band tool that already replied or declined makes the
engine's own text redundant, so it is not relayed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, NamedTuple

from band.core.delivery import relay_reply
from band.core.protocols import AgentToolsProtocol

if TYPE_CHECKING:
    from parlant.core.application import Application
    from parlant.core.sessions import SessionId

logger = logging.getLogger(__name__)

PARLANT_PREAMBLE_TAG = "__preamble__"
EMPTY_READ_BACKOFF_SECONDS = 0.05
FINAL_SEGMENT_SEPARATOR = "\n\n"


class PollMiss(Enum):
    """A poll window that produced no events to read."""

    NO_UPDATE = auto()
    """The window elapsed with no new agent message."""
    NOT_VISIBLE = auto()
    """An update was signalled but no event is query-visible yet."""
    FAILED = auto()
    """Parlant raised while waiting or reading; the wait is abandoned."""


class AgentMessages(NamedTuple):
    """What one batch of agent message events means for the turn."""

    offset: int
    final_texts: list[str]
    # An empty final message still ends the wait, so it is tracked apart from
    # final_texts.
    final_seen: bool


def agent_messages(events: Sequence[Any], *, offset: int) -> AgentMessages:
    """Read a batch of Parlant agent message events past *offset*."""
    final_texts: list[str] = []
    final_seen = False
    for event in events:
        offset = max(offset, event.offset)
        text, tags = _message_parts(event.data)
        if PARLANT_PREAMBLE_TAG in tags:
            logger.debug("Skipping preamble message: %s...", text[:50])
            continue
        final_seen = True
        if text:
            final_texts.append(text)
        else:
            logger.warning("Empty message content in agent event")
    return AgentMessages(offset=offset, final_texts=final_texts, final_seen=final_seen)


async def next_agent_events(
    *,
    app: Application,
    session_id: SessionId,
    offset: int,
    window: float,
) -> Sequence[Any] | PollMiss:
    """The agent message events after *offset*, waiting up to *window*."""
    from parlant.core.async_utils import Timeout  # noqa: PLC0415
    from parlant.core.sessions import EventKind, EventSource  # noqa: PLC0415

    try:
        has_update = await app.sessions.wait_for_more_events(
            session_id=session_id,
            min_offset=offset + 1,
            kinds=[EventKind.MESSAGE],
            source=EventSource.AI_AGENT,
            timeout=Timeout(window),
        )
        if not has_update:
            return PollMiss.NO_UPDATE
        events = await app.sessions.find_events(
            session_id=session_id,
            min_offset=offset + 1,
            source=EventSource.AI_AGENT,
            kinds=[EventKind.MESSAGE],
            trace_id=None,  # Required by Parlant SDK v3.x
        )
    except Exception:
        logger.exception("Session %s: error waiting for agent events", session_id)
        return PollMiss.FAILED
    if not events:
        logger.warning(
            "Session %s: no events found despite update signal; still waiting",
            session_id,
        )
        return PollMiss.NOT_VISIBLE
    return events


async def relay_agent_response(
    *,
    app: Application,
    session_id: SessionId,
    min_offset: int,
    tools: AgentToolsProtocol,
    sender_name: str,
    timeout: float,
    poll: float,
) -> None:
    """Relay the engine's final reply to *sender_name*, waiting up to *timeout*.

    Waiting polls in *poll*-second windows and keeps going past empty ones,
    so a slow (cold-start) turn is still answered. If the budget elapses with
    no final message the turn is given up honestly: Parlant intermittently
    stalls after a preamble, and a preamble is not an answer.
    """
    offset = min_offset
    # perf_counter keeps its resolution on Windows, where monotonic() is coarse.
    deadline = time.perf_counter() + timeout
    while (remaining := deadline - time.perf_counter()) > 0:
        polled = await next_agent_events(
            app=app, session_id=session_id, offset=offset, window=min(poll, remaining)
        )
        match polled:
            case PollMiss.FAILED:
                return
            case PollMiss.NO_UPDATE if tools.turn.replied:
                return
            case PollMiss():
                await _back_off(deadline)
                continue
        batch = agent_messages(polled, offset=offset)
        offset = batch.offset
        if batch.final_texts:
            await relay_reply(
                tools,
                FINAL_SEGMENT_SEPARATOR.join(batch.final_texts),
                mentions=[sender_name],
            )
        if batch.final_seen or tools.turn.replied:
            return
        logger.debug("Session %s: only a preamble so far; still waiting", session_id)

    if not tools.turn.replied:
        logger.warning(
            "Session %s: timed out after %ss waiting for agent response",
            session_id,
            timeout,
        )


def _message_parts(data: Any) -> tuple[str, list[str]]:
    """An agent message event's text and tags."""
    match data:
        case dict():
            raw_tags = data.get("tags", [])
            tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
            return str(data.get("message", "")), tags
        case str():
            return data, []
        case _:
            return "", []


async def _back_off(deadline: float) -> None:
    backoff = min(EMPTY_READ_BACKOFF_SECONDS, deadline - time.perf_counter())
    if backoff > 0:
        await asyncio.sleep(backoff)
