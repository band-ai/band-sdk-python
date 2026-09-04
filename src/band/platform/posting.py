"""Chat message/event posting -- the single REST choke point for sends.

``AgentTools.send_message``/``send_event`` and every other caller that posts
to a room (the A2A/ACP/Slack bridges, the contact hub-room notifier) funnel
through here rather than the raw Fern client directly, so the platform's
content rules (visible-content, the events content cap) are enforced once
for every caller instead of re-implemented per call site.

``rest`` is taken per call, not cached at construction -- same rationale as
``MessageLifecycle``: the caller owns the REST client and may swap it at any
point, so every call here uses whatever ``rest`` the caller currently has.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.core.content import has_visible_content

if TYPE_CHECKING:
    from band.client.rest import AsyncRestClient, ChatEventRequest, ChatMessageRequest
    from band_rest.types import EventCreatedResponse, MessageSentResponse

logger = logging.getLogger(__name__)

# The Agent Events API enforces a hard cap on event content and rejects
# anything larger with a 422 before it reaches the room (thenvoi-platform
# events_controller.ex `@content_max_length`). There is no equivalent cap on
# messages, so only events are truncated here.
_EVENT_CONTENT_MAX_LENGTH = 16384
_EVENT_TRUNCATION_MARKER = "... [truncated] ..."


def _truncate_event_content(content: str) -> str:
    """Cap *content* at ``_EVENT_CONTENT_MAX_LENGTH`` chars, keeping its head
    and tail around a marker.

    Both ends are preserved because the tail is often the informative part of
    a truncated payload -- the final lines of a raw error dump, or a trailing
    status -- which a head-only cut would silently drop. A no-op when
    *content* is already within the limit, so callers can run it
    unconditionally rather than checking the length themselves first.
    """
    if len(content) <= _EVENT_CONTENT_MAX_LENGTH:
        return content
    budget = _EVENT_CONTENT_MAX_LENGTH - len(_EVENT_TRUNCATION_MARKER)
    head_len = budget // 2
    tail_len = budget - head_len
    return content[:head_len] + _EVENT_TRUNCATION_MARKER + content[-tail_len:]


async def post_message(
    rest: AsyncRestClient, room_id: str, request: ChatMessageRequest
) -> MessageSentResponse | None:
    """POST a chat message, refusing content with no visible characters.

    Returns ``None`` (without making an API call) instead of sending content
    the platform would reject with "content can't be blank" anyway --
    matching ``Chat.validate_visible_content/1`` rather than a naive
    non-empty check, so whitespace-only content is refused too.
    """
    if not has_visible_content(request.content):
        logger.warning(
            "Refusing to send a chat message with no visible content in room %s",
            room_id,
        )
        return None

    response = await rest.agent_api_messages.create_agent_chat_message(
        chat_id=room_id,
        message=request,
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    if not response.data:
        raise RuntimeError("Failed to send message - no response data")
    return response.data


async def post_event(
    rest: AsyncRestClient, room_id: str, request: ChatEventRequest
) -> EventCreatedResponse | None:
    """POST a chat event, refusing content with no visible characters.

    Returns ``None`` (without making an API call) instead of sending content
    the platform would reject -- see ``post_message``. Oversized content is
    truncated rather than refused: the platform's cap exists to bound
    broadcast fan-out, not to reject legitimate large payloads (an ACP
    tool_result mirroring a large file, for instance).
    """
    if not has_visible_content(request.content):
        logger.warning(
            "Refusing to send a %s event with no visible content in room %s",
            request.message_type,
            room_id,
        )
        return None

    content = _truncate_event_content(request.content)
    if len(content) != len(request.content):
        logger.warning(
            "Truncated oversized %s event content for room %s (%d chars > %d limit)",
            request.message_type,
            room_id,
            len(request.content),
            _EVENT_CONTENT_MAX_LENGTH,
        )
        request = request.model_copy(update={"content": content})

    response = await rest.agent_api_events.create_agent_chat_event(
        chat_id=room_id,
        event=request,
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    if not response.data:
        raise RuntimeError("Failed to send event - no response data")
    return response.data
