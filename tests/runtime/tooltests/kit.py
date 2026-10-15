from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from band_rest import (
    ChatMessage,
    GetAgentChatContextResponse,
    GetAgentChatContextResponseMetadata,
)

from band.client.rest import Attachment, ChatMessageRequest, NotFoundError


def _attachment(
    file_id: str = "file-1",
    *,
    name: str = "notes.txt",
    content_type: str = "text/plain",
    size: int = 20,
    expires_at: datetime | None = None,
) -> Attachment:
    return Attachment(
        id=file_id,
        name=name,
        content_type=content_type,
        bytes=size,
        sha256="a" * 64,
        has_thumb=False,
        expires_at=expires_at,
    )


def _context_response(
    messages: list[ChatMessage],
    *,
    next_cursor: str | None = None,
    has_more: bool = False,
) -> GetAgentChatContextResponse:
    return GetAgentChatContextResponse(
        data=messages,
        metadata=GetAgentChatContextResponseMetadata(
            has_more=has_more, limit=50, next_cursor=next_cursor
        ),
    )


def _message_with_attachments(
    msg_id: str, attachments: list[Attachment]
) -> ChatMessage:
    return ChatMessage(
        id=msg_id,
        content="",
        sender_id="user-1",
        sender_type="User",
        message_type="text",
        attachments=attachments,
    )


def _mock_attachment_page(mock_rest_client: MagicMock, attachment: Attachment) -> None:
    """Configure the context endpoint to return one message carrying `attachment` --
    the shared setup for every read_room_file test that only cares about one file."""
    mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=_context_response(
            [_message_with_attachments("msg-1", [attachment])]
        )
    )


async def _fake_download(body: bytes) -> AsyncIterator[bytes]:
    """A ``download_agent_chat_file`` double: an async generator, not a
    coroutine -- assigning an ``AsyncMock`` here would make the *call* itself
    awaitable, which nothing in the real client does."""
    yield body


async def _fake_download_not_found() -> AsyncIterator[bytes]:
    """Same shape as ``_fake_download``, but raises before ever yielding."""
    if False:
        yield b""
    raise NotFoundError(body=MagicMock())


def _posted_message(mock_rest_client: MagicMock) -> ChatMessageRequest:
    """The ``ChatMessageRequest`` a preceding ``send_message``/``send_room_file``
    call actually posted -- the one observable outcome a REST-backed
    ``AgentTools`` test has for "what did the message body look like"."""
    return (
        mock_rest_client.agent_api_messages.create_agent_chat_message.await_args.kwargs[
            "message"
        ]
    )
