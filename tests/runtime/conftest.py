"""Shared REST/BandLink test doubles for tests/runtime.

These build real ``band_rest`` (Fern-generated) model instances and the
``band.core.types.PlatformMessage`` OneShotInvoker actually type-annotates
against — not bare MagicMocks — so a real schema change (a renamed field, a
newly required one, ``data`` widening from a bare list to Optional) breaks
these fixtures the same way it would break production, instead of a loose
mock silently tolerating the drift.

Note: ``band.runtime.types.PlatformMessage`` (used by the root
``tests/conftest.py`` WS-event builders) is a *different* class from
``band.core.types.PlatformMessage`` used here — the two aren't
interchangeable, so don't reach for ``sample_platform_message`` from the root
conftest when building oneshot/REST-side fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band_rest import (
    AgentMe,
    ChatMessage,
    ChatParticipant,
    GetAgentChatContextResponse,
    GetAgentChatContextResponseMetadata,
    GetAgentMeResponse,
    ListAgentChatParticipantsResponse,
)

from band.core.types import PlatformConnection, PlatformMessage


def make_participant(p: dict[str, Any]) -> ChatParticipant:
    return ChatParticipant(
        id=p["id"],
        name=p["name"],
        type=p["type"],
        handle=p.get("handle"),
        description=p.get("description"),
        role="member",
        status="active",
    )


def platform_msg(
    msg_id: str, *, sender_type: str = "User", sender_id: str = "user-1"
) -> PlatformMessage:
    """Stand-in for a PlatformMessage from get_next_message. Callers
    typically only care about ``id``/``sender_type``/``sender_id``; the rest
    are real required fields on the dataclass, filled with plausible values.
    """
    return PlatformMessage(
        id=msg_id,
        room_id="room-1",
        content="hello",
        sender_id=sender_id,
        sender_type=sender_type,
        sender_name=None,
        message_type="user",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


def ctx_item(
    msg_id: str,
    *,
    content: str = "hi",
    sender_id: str = "user-1",
    sender_type: str = "User",
    sender_name: str = "Alice",
) -> ChatMessage:
    """A context item as returned by get_agent_chat_context."""
    return ChatMessage(
        id=msg_id,
        content=content,
        sender_id=sender_id,
        sender_type=sender_type,
        sender_name=sender_name,
        message_type="user",
        metadata={},
        inserted_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
    )


def make_link_mock(
    participants: list[dict[str, Any]] | None = None,
    history_items: list[ChatMessage] | None = None,
    next_messages: list[PlatformMessage | None] | None = None,
    *,
    agent_name: str = "TestBot",
    agent_description: str = "a test agent",
    history_pages: list[list[ChatMessage]] | None = None,
) -> MagicMock:
    """Build a fake BandLink.

    ``next_messages`` controls successive ``get_next_message`` returns; once
    exhausted, all further calls return ``None``. The identity endpoint is
    stubbed so ``startup()`` succeeds. Only the async REST *calls* are
    mocked — everything they return is a real ``band_rest`` model instance,
    so the response shape (required fields, ``data`` never ``None`` for a
    list field, cursor-based pagination metadata) matches what the live API
    actually sends.

    ``history_pages`` models the context endpoint's cursor pagination across
    multiple invocations: page *n*'s response has ``has_more=True`` and a
    ``next_cursor`` until the last page. Give either this or ``history_items``
    (a single-page shorthand), not both.
    """
    link = MagicMock()

    # Platform connection (startup() injects it into the adapter).
    link.api_key = "test-api-key"
    link.rest_url = "https://app.band.ai"
    link.ws_url = "wss://app.band.ai/api/v1/socket/websocket"
    link.to_platform_connection = MagicMock(
        side_effect=lambda agent_id: PlatformConnection(
            agent_id=agent_id,
            api_key=link.api_key,
            rest_url=link.rest_url,
            ws_url=link.ws_url,
        )
    )

    # Identity (for startup()).
    identity_response = GetAgentMeResponse(
        data=AgentMe(
            handle="test/bot",
            id="agent-1",
            inserted_at=datetime.now(timezone.utc),
            name=agent_name,
            description=agent_description,
            owner_uuid="owner-1",
            updated_at=datetime.now(timezone.utc),
        )
    )
    link.rest.agent_api_identity.get_agent_me = AsyncMock(
        return_value=identity_response
    )

    # Participants. ``data`` is a bare (never Optional) list on the real
    # response type — an empty room is `[]`, not `None`.
    participants_response = ListAgentChatParticipantsResponse(
        data=[make_participant(p) for p in (participants or [])]
    )
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=participants_response
    )

    # History / context. Metadata is cursor-based (``has_more``/
    # ``next_cursor``); ``page``/``page_size`` on the request side are
    # documented deprecated on the real client.
    if history_pages is not None:
        cursors: list[str | None] = [f"cursor-{i}" for i in range(len(history_pages))]
        responses_by_cursor: dict[str | None, GetAgentChatContextResponse] = {}
        for i, page_items in enumerate(history_pages):
            is_last = i == len(history_pages) - 1
            requested_by = None if i == 0 else cursors[i - 1]
            responses_by_cursor[requested_by] = GetAgentChatContextResponse(
                data=page_items,
                metadata=GetAgentChatContextResponseMetadata(
                    has_more=not is_last,
                    limit=50,
                    next_cursor=None if is_last else cursors[i],
                ),
            )

        async def _get_context(
            *_args: Any, cursor: str | None = None, **_kwargs: Any
        ) -> GetAgentChatContextResponse:
            return responses_by_cursor[cursor]

        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=_get_context
        )
    else:
        context_response = GetAgentChatContextResponse(
            data=history_items or [],
            metadata=GetAgentChatContextResponseMetadata(
                has_more=False,
                limit=50,
                next_cursor=None,
            ),
        )
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=context_response
        )

    # Lifecycle markers.
    sequence = list(next_messages or [])

    async def _get_next(*_args: Any, **_kwargs: Any) -> PlatformMessage | None:
        return sequence.pop(0) if sequence else None

    link.get_next_message = AsyncMock(side_effect=_get_next)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.disconnect = AsyncMock()
    return link
