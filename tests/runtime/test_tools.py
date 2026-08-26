"""Tests for AgentTools."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from band_rest import (
    ChatMessage,
    GetAgentChatContextResponse,
    GetAgentChatContextResponseMetadata,
)
from pydantic import BaseModel, ValidationError

from band.client.rest import (
    DEFAULT_REQUEST_OPTIONS,
    Attachment,
    ChatMessageRequest,
    NotFoundError,
)
from band.core.exceptions import BandToolError
from band.core.types import Capability
from tests.conftest import make_participant_mock
from band.runtime.tools import (
    DEFAULT_FILE_CAPTION,
    FILE_UNAVAILABLE_MESSAGE,
    MAX_INLINE_IMAGE_BYTES,
    MAX_INLINE_TEXT_BYTES,
    MAX_SEND_CONTENT_BYTES,
    TOOL_MODELS,
    AgentTools,
    SendMessageInput,
    SendRoomFileInput,
    SendEventInput,
    StoreMemoryInput,
    AddParticipantInput,
    LookupPeersInput,
    GetParticipantsInput,
    CreateChatroomInput,
    _matches_identifier,
    append_mention_handles_hint,
    available_mention_handles,
    canonicalize_mcp_tool_name,
    format_tool_validation_error,
    is_room_posting_tool,
)


@pytest.fixture
def participants():
    """Sample participants list."""
    return [
        {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        {"id": "user-2", "name": "User Two", "type": "User", "handle": "@user-two"},
    ]


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_list_memories_omits_none_filters(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_memories(page_size=25)

        mock_rest_client.agent_api_memories.list_agent_memories.assert_awaited_once_with(
            page_size=25,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_store_memory_omits_none_fields(self, mock_rest_client) -> None:
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.store_memory(
            content="remember this",
            system="working",
            type="semantic",
            segment="user",
            thought="useful later",
            scope="organization",
        )

        call_kwargs = (
            mock_rest_client.agent_api_memories.create_agent_memory.call_args.kwargs
        )
        memory_payload = call_kwargs["memory"].model_dump(exclude_unset=True)
        assert "subject_id" not in memory_payload
        assert "metadata" not in memory_payload
        assert call_kwargs["request_options"] is DEFAULT_REQUEST_OPTIONS

    @pytest.mark.asyncio
    async def test_store_memory_agent_scope_sent_without_subject_id(
        self, mock_rest_client
    ) -> None:
        """Agent scope round-trips to the REST payload untouched and needs no
        subject_id -- the scope value real agents fall back to when their owner
        has no organization."""
        response = MagicMock()
        response.data = MagicMock()
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.store_memory(
            content="remember this",
            system="working",
            type="semantic",
            segment="user",
            thought="useful later",
            scope="agent",
        )

        call_kwargs = (
            mock_rest_client.agent_api_memories.create_agent_memory.call_args.kwargs
        )
        memory_payload = call_kwargs["memory"].model_dump(exclude_unset=True)
        assert memory_payload["scope"] == "agent"
        assert "subject_id" not in memory_payload

    @pytest.mark.asyncio
    async def test_list_memories_agent_scope_sent_untouched(
        self, mock_rest_client
    ) -> None:
        """`scope="agent"` passes straight through to the list filter."""
        response = MagicMock()
        response.data = []
        mock_rest_client.agent_api_memories.list_agent_memories = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        await tools.list_memories(scope="agent")

        mock_rest_client.agent_api_memories.list_agent_memories.assert_awaited_once_with(
            page_size=50,
            scope="agent",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_store_memory_rejects_subject_scope_without_subject_id(
        self, mock_rest_client
    ) -> None:
        """Reject subject-scoped writes before they reach the API."""
        mock_rest_client.agent_api_memories.create_agent_memory = AsyncMock()
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="requires a subject_id"):
            await tools.store_memory(
                content="remember this",
                system="working",
                type="semantic",
                segment="user",
                thought="useful later",
                scope="subject",
            )

        mock_rest_client.agent_api_memories.create_agent_memory.assert_not_called()

    def test_store_memory_input_rejects_subject_scope_without_subject_id(self) -> None:
        """Validate tool input rejects subject scope without subject_id."""
        with pytest.raises(ValidationError, match="requires a subject_id"):
            StoreMemoryInput.model_validate(
                {
                    "content": "remember this",
                    "system": "working",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "subject",
                }
            )

    def test_store_memory_input_rejects_type_for_wrong_system(self) -> None:
        """Validate memory type matches the chosen system."""
        with pytest.raises(ValidationError, match='type="semantic" is not valid'):
            StoreMemoryInput.model_validate(
                {
                    "content": "remember this",
                    "system": "sensory",
                    "type": "semantic",
                    "segment": "user",
                    "thought": "useful later",
                    "scope": "organization",
                }
            )

    @pytest.mark.parametrize(
        ("tool_method", "rest_method"),
        [
            ("get_memory", "get_agent_memory"),
            ("supersede_memory", "supersede_agent_memory"),
            ("archive_memory", "archive_agent_memory"),
        ],
    )
    @pytest.mark.asyncio
    async def test_memory_mutation_calls_use_default_request_options(
        self,
        mock_rest_client,
        tool_method: str,
        rest_method: str,
    ) -> None:
        response = MagicMock()
        response.data = MagicMock()
        rest_call = AsyncMock(return_value=response)
        setattr(mock_rest_client.agent_api_memories, rest_method, rest_call)
        tools = AgentTools("room-123", mock_rest_client)

        await getattr(tools, tool_method)("mem-123")

        rest_call.assert_awaited_once_with(
            id="mem-123",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )


def _attachment(
    file_id: str = "file-1",
    *,
    name: str = "notes.txt",
    content_type: str = "text/plain",
    size: int = 20,
) -> Attachment:
    return Attachment(
        id=file_id,
        name=name,
        content_type=content_type,
        bytes=size,
        sha256="a" * 64,
        has_thumb=False,
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


async def _fake_download(body: bytes):
    """A ``download_agent_chat_file`` double: an async generator, not a
    coroutine -- assigning an ``AsyncMock`` here would make the *call* itself
    awaitable, which nothing in the real client does."""
    yield body


async def _fake_download_not_found():
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


class TestFileTools:
    """Tests for band_list_room_files / band_read_room_file / band_send_room_file."""

    # --- list_room_files ---

    @pytest.mark.asyncio
    async def test_list_room_files_dedupes_by_attachment_id(self, mock_rest_client):
        shared = _attachment("file-1")
        other = _attachment("file-2")
        response = _context_response(
            [
                _message_with_attachments("msg-1", [shared]),
                _message_with_attachments("msg-2", [shared, other]),
            ]
        )
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.list_room_files()

        assert [a["id"] for a in result["data"]] == ["file-1", "file-2"]
        mock_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once_with(
            chat_id="room-123",
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    @pytest.mark.asyncio
    async def test_list_room_files_forwards_cursor_and_returns_next(
        self, mock_rest_client
    ):
        response = _context_response(
            [_message_with_attachments("msg-1", [_attachment()])],
            next_cursor="cursor-2",
            has_more=True,
        )
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=response
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.list_room_files(cursor="cursor-1")

        assert result["next_cursor"] == "cursor-2"
        mock_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once_with(
            chat_id="room-123",
            request_options=DEFAULT_REQUEST_OPTIONS,
            cursor="cursor-1",
        )

    # --- read_room_file ---

    @pytest.mark.asyncio
    async def test_read_room_file_inlines_small_text(self, mock_rest_client):
        attachment = _attachment(content_type="text/plain", size=5)
        _mock_attachment_page(mock_rest_client, attachment)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        assert result["text"] == "hello"
        assert result["content_type"] == "text/plain"
        assert "description" not in result

    @pytest.mark.asyncio
    async def test_read_room_file_non_utf8_text_flags_lossy_decode(
        self, mock_rest_client
    ):
        """A Windows-1252-encoded file (no charset in content_type -- the
        platform derives it from magic bytes alone) must not come back
        looking like a clean decode: 0x93/0x94 are curly quotes in
        Windows-1252 but invalid UTF-8 continuation bytes on their own."""
        windows_1252_bytes = "“quoted”".encode("windows-1252")
        attachment = _attachment(
            content_type="text/plain", size=len(windows_1252_bytes)
        )
        _mock_attachment_page(mock_rest_client, attachment)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(windows_1252_bytes)
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        assert "�" in result["text"]
        assert "not valid UTF-8" in result["description"]

    @pytest.mark.asyncio
    async def test_read_room_file_inlines_small_image(self, mock_rest_client):
        attachment = _attachment(content_type="image/png", size=100)
        _mock_attachment_page(mock_rest_client, attachment)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"\x89PNG-fake-bytes")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        block = result["content"][0]
        assert block["type"] == "image"
        assert block["mimeType"] == "image/png"
        assert base64.b64decode(block["data"]) == b"\x89PNG-fake-bytes"

    @pytest.mark.asyncio
    async def test_read_room_file_text_exactly_at_limit_downloads(
        self, mock_rest_client
    ):
        attachment = _attachment(content_type="text/plain", size=MAX_INLINE_TEXT_BYTES)
        _mock_attachment_page(mock_rest_client, attachment)
        download = MagicMock(side_effect=lambda **_kw: _fake_download(b"ok"))
        mock_rest_client.agent_api_files.download_agent_chat_file = download
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        download.assert_called_once()
        assert result["text"] == "ok"

    @pytest.mark.asyncio
    async def test_read_room_file_text_one_byte_over_limit_skips_download(
        self, mock_rest_client
    ):
        attachment = _attachment(
            content_type="text/plain", size=MAX_INLINE_TEXT_BYTES + 1
        )
        _mock_attachment_page(mock_rest_client, attachment)
        download = MagicMock(side_effect=lambda **_kw: _fake_download(b"ok"))
        mock_rest_client.agent_api_files.download_agent_chat_file = download
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        download.assert_not_called()
        assert "text" not in result
        assert "description" in result

    @pytest.mark.asyncio
    async def test_read_room_file_image_exactly_at_limit_downloads(
        self, mock_rest_client
    ):
        attachment = _attachment(content_type="image/png", size=MAX_INLINE_IMAGE_BYTES)
        _mock_attachment_page(mock_rest_client, attachment)
        download = MagicMock(side_effect=lambda **_kw: _fake_download(b"ok"))
        mock_rest_client.agent_api_files.download_agent_chat_file = download
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        download.assert_called_once()
        assert result["content"][0]["type"] == "image"

    @pytest.mark.asyncio
    async def test_read_room_file_image_one_byte_over_limit_skips_download(
        self, mock_rest_client
    ):
        attachment = _attachment(
            content_type="image/png", size=MAX_INLINE_IMAGE_BYTES + 1
        )
        _mock_attachment_page(mock_rest_client, attachment)
        download = MagicMock(side_effect=lambda **_kw: _fake_download(b"ok"))
        mock_rest_client.agent_api_files.download_agent_chat_file = download
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        download.assert_not_called()
        assert "content" not in result
        assert "description" in result

    @pytest.mark.asyncio
    async def test_read_room_file_non_previewable_type_is_described(
        self, mock_rest_client
    ):
        attachment = _attachment(content_type="application/pdf", size=10)
        _mock_attachment_page(mock_rest_client, attachment)
        download = MagicMock(side_effect=lambda **_kw: _fake_download(b"ok"))
        mock_rest_client.agent_api_files.download_agent_chat_file = download
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        download.assert_not_called()
        assert result["description"]

    @pytest.mark.asyncio
    async def test_read_room_file_unknown_id_raises_band_tool_error(
        self, mock_rest_client
    ):
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=_context_response([])
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.read_room_file("nope")

    @pytest.mark.asyncio
    async def test_read_room_file_download_not_found_raises_band_tool_error(
        self, mock_rest_client
    ):
        attachment = _attachment(content_type="text/plain", size=5)
        _mock_attachment_page(mock_rest_client, attachment)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download_not_found()
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.read_room_file("file-1")

    @pytest.mark.asyncio
    async def test_read_room_file_searches_past_the_first_page(self, mock_rest_client):
        """_find_attachment must walk every page -- the target file may be
        older than the first page returned."""
        page_one = _context_response(
            [_message_with_attachments("msg-1", [_attachment("other-file")])],
            next_cursor="cursor-2",
            has_more=True,
        )
        page_two = _context_response(
            [
                _message_with_attachments(
                    "msg-2", [_attachment(content_type="text/plain", size=5)]
                )
            ]
        )
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=[page_one, page_two]
        )
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        assert result["text"] == "hello"
        get_context = mock_rest_client.agent_api_context.get_agent_chat_context
        assert get_context.await_count == 2
        _, second_call_kwargs = get_context.await_args_list[1]
        assert second_call_kwargs["cursor"] == "cursor-2"

    # --- send_room_file ---

    @pytest.mark.asyncio
    async def test_send_room_file_uploads_and_posts_message(
        self, mock_rest_client, participants
    ):
        uploaded = _attachment("file-9", name="report.txt")
        upload_response = MagicMock()
        upload_response.data = uploaded
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            return_value=upload_response
        )

        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_room_file(
            "hello world", "report.txt", caption="here you go", mentions=["User One"]
        )

        assert result["attachment"]["id"] == "file-9"
        upload_call = mock_rest_client.agent_api_files.upload_agent_chat_file
        upload_call.assert_awaited_once()
        _, kwargs = upload_call.await_args
        assert kwargs["chat_id"] == "room-123"
        assert kwargs["request"] == b"hello world"
        headers = kwargs["request_options"]["additional_headers"]
        assert headers["x-file-name"] == "report.txt"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_awaited_once()
        posted = _posted_message(mock_rest_client)
        assert posted.attachment_ids == ["file-9"]
        assert posted.content == "here you go"

    @pytest.mark.asyncio
    async def test_send_room_file_exactly_at_limit_succeeds(
        self, mock_rest_client, participants
    ):
        uploaded = _attachment("file-9")
        upload_response = MagicMock()
        upload_response.data = uploaded
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            return_value=upload_response
        )
        tools = AgentTools("room-123", mock_rest_client, participants)
        body = "a" * MAX_SEND_CONTENT_BYTES

        await tools.send_room_file(body, "big.txt", mentions=["User One"])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_room_file_one_byte_over_limit_raises_before_upload(
        self, mock_rest_client, participants
    ):
        tools = AgentTools("room-123", mock_rest_client, participants)
        body = "a" * (MAX_SEND_CONTENT_BYTES + 1)

        with pytest.raises(BandToolError, match="exceeds"):
            await tools.send_room_file(body, "big.txt", mentions=["User One"])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_non_ascii_filename_raises_before_upload(
        self, mock_rest_client, participants
    ):
        """x-file-name travels as a raw HTTP header value -- httpx refuses to
        encode a non-ASCII header and raises UnicodeEncodeError deep inside
        the upload call. Catch it before uploading with a clear message
        instead of letting that raw codec error surface to the LLM."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="ASCII characters only"):
            await tools.send_room_file("hi", "报告.txt", mentions=["User One"])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_crlf_in_filename_raises_before_upload(
        self, mock_rest_client, participants
    ):
        """A bare '\\r'/'\\n' encodes to ASCII fine, so an "is it ASCII"
        check alone would wave a header-injection payload straight through
        to the upload call -- only rejected there, deep inside h11, as an
        "Illegal header value" once the request is already being built."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="ASCII characters only"):
            await tools.send_room_file(
                "hi", "evil\r\nX-Injected: yes", mentions=["User One"]
            )

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_upload_not_found_raises_band_tool_error(
        self, mock_rest_client, participants
    ):
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            side_effect=NotFoundError(body=MagicMock())
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.send_room_file("hi", "f.txt", mentions=["User One"])

    @pytest.mark.asyncio
    async def test_send_room_file_empty_mentions_raises_before_upload(
        self, mock_rest_client, participants
    ):
        """Sharing a file is a send_message call under the hood, so a missing
        mention must fail before the upload -- not after, which would leave
        an orphaned attachment nothing points at."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.send_room_file("hi", "f.txt", mentions=[])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_empty_caption_uses_default(
        self, mock_rest_client, participants
    ):
        """The platform rejects blank message content outright
        (``validate_length(:content, min: 1)``, unconditional even with
        ``attachment_ids`` set) -- an omitted caption must never reach
        ``send_message`` as ``""``, or the post 422s after the file has
        already been uploaded, leaving an orphaned attachment."""
        uploaded = _attachment("file-9", name="report.txt")
        upload_response = MagicMock()
        upload_response.data = uploaded
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            return_value=upload_response
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_room_file(
            "hello world", "report.txt", mentions=["User One"]
        )

        assert result["attachment"]["id"] == "file-9"
        assert _posted_message(mock_rest_client).content == (
            DEFAULT_FILE_CAPTION.format(filename="report.txt")
        )

    @pytest.mark.asyncio
    async def test_send_room_file_discoverable_from_a_new_agenttools_instance(
        self, mock_rest_client, participants
    ):
        """AgentTools is recreated per execution (a fresh instance per turn),
        so a file must be discoverable through the REST layer alone, not
        through any in-process state the sending instance happened to hold.
        Regression guard for the agent messages endpoint's direct_only
        exclusion of self-authored messages: file discovery must go through
        the context endpoint instead, which includes them."""
        uploaded = _attachment("file-9", name="report.txt", size=5)
        upload_response = MagicMock()
        upload_response.data = uploaded
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            return_value=upload_response
        )
        sender = AgentTools("room-123", mock_rest_client, participants)
        sent = await sender.send_room_file("hello", "report.txt", mentions=["User One"])

        # A later turn: brand new instance, no shared state with `sender`.
        # Mirrors what the real platform now returns -- the context endpoint
        # includes messages this agent sent, unlike the plain messages one.
        _mock_attachment_page(mock_rest_client, uploaded)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        reader = AgentTools("room-123", mock_rest_client, participants)

        listed = await reader.list_room_files()
        read = await reader.read_room_file(sent["attachment"]["id"])

        assert [a["id"] for a in listed["data"]] == ["file-9"]
        assert read["text"] == "hello"


class TestAgentToolsConstruction:
    """Test AgentTools initialization."""

    def test_init_stores_room_id(self, mock_rest_client):
        """Should store room_id."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.room_id == "room-123"

    def test_init_stores_rest_client(self, mock_rest_client):
        """Should store REST client."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.rest is mock_rest_client

    def test_init_empty_participants_by_default(self, mock_rest_client):
        """Should have empty participants by default."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools._participants == []

    def test_init_with_participants(self, mock_rest_client, participants):
        """Should accept participants."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        assert tools._participants == participants


class TestAgentToolsFromContext:
    """Test AgentTools.from_context() factory method."""

    def test_from_context_creates_tools(self, mock_rest_client, participants):
        """from_context() should create AgentTools from ExecutionContext."""
        # Mock ExecutionContext
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = participants

        tools = AgentTools.from_context(mock_ctx)

        assert tools.room_id == "room-456"
        assert tools.rest is mock_rest_client
        assert tools._participants == participants


class TestAgentToolsContextSyncBack:
    """Regression tests for AgentTools._ctx sync-back to ExecutionContext.

    Before the fix, AgentTools.from_context() copied ctx.participants (which
    returns a shallow copy via property).  Mutations to the tools instance
    (add/remove participant) never propagated back to the ExecutionContext.
    On the next turn, from_context() would copy the stale list again.
    """

    @pytest.mark.asyncio
    async def test_add_participant_syncs_back_to_ctx(self, mock_rest_client):
        """add_participant() must call ctx.add_participant() with full dict."""
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = []
        mock_ctx.hub_room_id = None
        mock_ctx.add_participant = MagicMock()

        # Override list_agent_chat_participants to return empty (avoid "already_in_room")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        tools = AgentTools.from_context(mock_ctx)
        assert tools._ctx is mock_ctx

        await tools.add_participant("Agent Two")

        mock_ctx.add_participant.assert_called_once()
        added = mock_ctx.add_participant.call_args.args[0]
        assert added["id"] == "agent-2"
        assert added["name"] == "Agent Two"
        assert added["type"] == "Agent"
        assert added["handle"] == "agent-two"
        assert added["description"] == "Another agent"

    @pytest.mark.asyncio
    async def test_remove_participant_syncs_back_to_ctx(self, mock_rest_client):
        """remove_participant() must call ctx.remove_participant() with correct ID."""
        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = [participant]
        mock_ctx.hub_room_id = None
        mock_ctx.remove_participant = MagicMock()

        # Return same participant from REST so snapshot matches
        p_mock = make_participant_mock("user-1", "User One", "User", handle="user-one")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        tools = AgentTools.from_context(mock_ctx)
        await tools.remove_participant("User One")

        mock_ctx.remove_participant.assert_called_once_with("user-1")

    @pytest.mark.asyncio
    async def test_add_participant_persists_across_recreated_tools(
        self, mock_rest_client
    ):
        """Added participant must survive tools recreation via from_context().

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the mutation would be lost.
        """
        from band.runtime.execution import ExecutionContext

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )

        # Empty room
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        # Turn 1: add participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.add_participant("Agent Two")

        assert len(ctx._participants) == 1
        assert ctx._participants[0]["id"] == "agent-2"

        # Turn 2: recreate tools — participant must still be there
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 1
        assert tools2._participants[0]["id"] == "agent-2"

    @pytest.mark.asyncio
    async def test_remove_participant_persists_across_recreated_tools(
        self, mock_rest_client
    ):
        """Removed participant must stay removed after tools recreation.

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the removal would be lost.
        """
        from band.runtime.execution import ExecutionContext

        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )
        ctx._participants = [participant]

        # REST snapshot must match ctx._participants
        p_mock = make_participant_mock("user-1", "User One", "User", handle="user-one")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        # Turn 1: remove participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.remove_participant("User One")

        assert len(ctx._participants) == 0

        # Turn 2: recreate tools — participant must stay removed
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 0


class TestAgentToolsSendMessage:
    """Test send_message tool."""

    async def test_send_message_success(self, mock_rest_client, participants):
        """send_message() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_send_message_resolves_mentions(self, mock_rest_client, participants):
        """send_message() should resolve mention names to IDs and handles."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello @User One!", mentions=["User One"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "@user-one"

    async def test_send_message_omits_attachment_ids_when_not_given(
        self, mock_rest_client, participants
    ):
        """Without attachment_ids, the field must be unset, not explicitly None.

        ChatMessageRequest serializes with exclude_unset=True; passing
        attachment_ids=None would still mark the field "set" and send a
        literal JSON null, which the platform rejects (expects an array or
        an absent key) -- reproduced live against a real deployment.
        """
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello!", mentions=["User One"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert "attachment_ids" not in message.model_fields_set

    async def test_send_message_empty_mentions_excludes_self(
        self, mock_rest_client, participants
    ):
        """The empty-mentions error lists other participants but not the agent
        itself — an agent can't @mention itself."""
        from band.core.exceptions import BandToolError

        tools = AgentTools(
            "room-123", mock_rest_client, participants, agent_id="user-2"
        )

        with pytest.raises(BandToolError) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        message = str(exc_info.value)
        assert "@user-one" in message
        assert "@user-two" not in message

    async def test_send_message_unknown_mention_raises(
        self, mock_rest_client, participants
    ):
        """send_message() should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant 'Unknown'"):
            await tools.send_message("Hello!", mentions=["Unknown"])

    async def test_send_message_no_response_raises(
        self, mock_rest_client, participants
    ):
        """send_message() should raise if no response data."""
        mock_rest_client.agent_api_messages.create_agent_chat_message.return_value = (
            MagicMock(data=None)
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(RuntimeError, match="Failed to send message"):
            await tools.send_message("Hello!", mentions=["User One"])


class TestAgentToolsSendEvent:
    """Test send_event tool."""

    async def test_send_event_success(self, mock_rest_client):
        """send_event() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.send_event("Thinking...", "thought")

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["message_type"] == "thought"
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()

    async def test_send_event_with_metadata(self, mock_rest_client):
        """send_event() should pass metadata."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.send_event("Error!", "error", metadata={"code": 500})

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        event = call_args.kwargs["event"]
        assert event.metadata == {"code": 500}

    async def test_send_event_no_response_raises(self, mock_rest_client):
        """send_event() should raise if no response data."""
        mock_rest_client.agent_api_events.create_agent_chat_event.return_value = (
            MagicMock(data=None)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="Failed to send event"):
            await tools.send_event("Error!", "error")

    async def test_send_event_within_limit_untouched(self, mock_rest_client):
        """send_event() should pass short content through unchanged."""
        tools = AgentTools("room-123", mock_rest_client)
        content = "x" * 16384

        await tools.send_event(content, "tool_result")

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        assert call_args.kwargs["event"].content == content

    async def test_send_event_truncates_oversized_content(self, mock_rest_client):
        """send_event() caps oversized content, keeping both its head and tail."""
        tools = AgentTools("room-123", mock_rest_client)
        content = "HEAD" * 10000 + "TAIL" * 10000

        await tools.send_event(content, "tool_result")

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        sent_content = call_args.kwargs["event"].content
        assert len(sent_content) == 16384
        assert sent_content.startswith("HEAD")  # head is retained
        assert sent_content.endswith("TAIL")  # and the tail isn't dropped
        assert "[truncated]" in sent_content  # with a marker between them

    async def test_send_event_substitutes_placeholder_for_blank_content(
        self, mock_rest_client
    ):
        """send_event() should never send a blank string — the platform 422s on it."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.send_event("", "tool_result")

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        assert call_args.kwargs["event"].content == "(no content)"


class TestMatchesIdentifier:
    """Tests for the _matches_identifier helper."""

    def test_match_by_handle(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "alice") is True

    def test_match_by_name(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "Alice Smith") is True

    def test_match_by_id(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "u-1") is True

    def test_case_insensitive(self):
        entity = {"handle": "Alice", "name": "ALICE SMITH", "id": "U-1"}
        assert _matches_identifier(entity, "alice") is True
        assert _matches_identifier(entity, "alice smith") is True
        assert _matches_identifier(entity, "u-1") is True

    def test_no_match(self):
        entity = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}
        assert _matches_identifier(entity, "bob") is False

    def test_missing_fields(self):
        """Should handle entities with missing or None fields."""
        assert _matches_identifier({"name": "Alice"}, "Alice") is True
        assert _matches_identifier({"handle": None, "name": "Alice"}, "Alice") is True
        assert _matches_identifier({}, "anything") is False

    def test_at_prefix_normalization(self):
        """@alice and alice should match regardless of which side has the prefix."""
        entity_with_at = {"handle": "@alice", "name": "Alice Smith", "id": "u-1"}
        entity_without_at = {"handle": "alice", "name": "Alice Smith", "id": "u-1"}

        # identifier has @, entity doesn't
        assert _matches_identifier(entity_without_at, "@alice") is True
        # entity has @, identifier doesn't
        assert _matches_identifier(entity_with_at, "alice") is True
        # both have @
        assert _matches_identifier(entity_with_at, "@alice") is True
        # neither has @
        assert _matches_identifier(entity_without_at, "alice") is True

    def test_empty_identifier(self):
        """Empty string should only match empty field values."""
        entity = {"handle": "alice", "name": "Alice", "id": "u-1"}
        assert _matches_identifier(entity, "") is False


class TestAgentToolsAddParticipant:
    """Test add_participant tool."""

    async def test_add_participant_by_name(self, mock_rest_client):
        """add_participant() should match by name and add via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("Agent Two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["role"] == "member"
        assert result["status"] == "added"
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_called_once()

    async def test_add_participant_by_handle(self, mock_rest_client):
        """add_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["status"] == "added"

    async def test_add_participant_by_id(self, mock_rest_client):
        """add_participant() should match by ID."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-2", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["status"] == "added"

    async def test_add_participant_already_in_room_by_handle(self, mock_rest_client):
        """add_participant() should detect already-in-room by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("user-one", role="member")

        assert result["id"] == "user-1"
        assert result["status"] == "already_in_room"
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_not_called()

    async def test_add_participant_ambiguous_name_resolved_by_handle(
        self, mock_rest_client
    ):
        """Two peers with the same display name — handle disambiguates (INT-287)."""
        peer_a = MagicMock()
        peer_a.id = "agent-a"
        peer_a.name = "Weather Agent"
        peer_a.type = "Agent"
        peer_a.handle = "@alice/weather"
        peer_a.description = "Alice's weather agent"

        peer_b = MagicMock()
        peer_b.id = "agent-b"
        peer_b.name = "Weather Agent"
        peer_b.type = "Agent"
        peer_b.handle = "@bob/weather"
        peer_b.description = "Bob's weather agent"

        peers_response = MagicMock()
        peers_response.data = [peer_a, peer_b]
        peers_response.metadata = MagicMock()
        peers_response.metadata.page = 1
        peers_response.metadata.page_size = 100
        peers_response.metadata.total_count = 2
        peers_response.metadata.total_pages = 1
        mock_rest_client.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=peers_response
        )

        tools = AgentTools("room-123", mock_rest_client)

        # Using handle should pick the correct one
        result = await tools.add_participant("@bob/weather", role="member")

        assert result["id"] == "agent-b"
        assert result["name"] == "Weather Agent"
        assert result["status"] == "added"

    async def test_add_participant_not_found_raises(self, mock_rest_client):
        """add_participant() should raise if peer not found."""
        # Return empty peers
        mock_rest_client.agent_api_peers.list_agent_peers.return_value = MagicMock(
            data=[], metadata=MagicMock(total_pages=1)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="Participant 'Unknown' not found"):
            await tools.add_participant("Unknown")


class TestAgentToolsRemoveParticipant:
    """Test remove_participant tool."""

    async def test_remove_participant_by_name(self, mock_rest_client):
        """remove_participant() should match by name and remove via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("User One")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"
        mock_rest_client.agent_api_participants.remove_agent_chat_participant.assert_called_once()

    async def test_remove_participant_by_handle(self, mock_rest_client):
        """remove_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("user-one")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"

    async def test_remove_participant_not_found_raises(self, mock_rest_client):
        """remove_participant() should raise if not in room."""
        # Return empty participants
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="not found in this room"):
            await tools.remove_participant("Unknown")


class TestAgentToolsLookupPeers:
    """Test lookup_peers tool."""

    async def test_lookup_peers_success(self, mock_rest_client):
        """lookup_peers() should return the Fern response directly."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.lookup_peers(page=1, page_size=50)

        # Now returns full Fern response with .data and .metadata
        assert len(result.data) == 1
        assert result.data[0].name == "Agent Two"
        assert result.metadata.page == 1

    async def test_lookup_peers_filters_by_room(self, mock_rest_client):
        """lookup_peers() should filter by not_in_chat."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.lookup_peers()

        call_args = mock_rest_client.agent_api_peers.list_agent_peers.call_args
        assert call_args.kwargs["not_in_chat"] == "room-123"


class TestAgentToolsGetParticipants:
    """Test get_participants tool."""

    async def test_get_participants_success(self, mock_rest_client):
        """get_participants() should return Fern participant models."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert len(result) == 1
        assert result[0].name == "User One"

    async def test_get_participants_empty(self, mock_rest_client):
        """get_participants() should return empty list if none."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert result == []

    async def test_get_participants_updates_cache(self, mock_rest_client):
        """get_participants() should refresh self._participants for mention resolution."""
        tools = AgentTools("room-123", mock_rest_client)
        assert tools._participants == []

        await tools.get_participants()

        assert tools._participants == [
            {
                "id": "user-1",
                "name": "User One",
                "type": "User",
                "handle": "user-one",
                "description": None,
            }
        ]

    async def test_get_participants_preserves_cache_when_data_none(
        self, mock_rest_client
    ):
        """``data is None`` indicates a transient/unexpected response — the
        cache should be preserved rather than wiped (an agent should always
        be a participant in its own room)."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        cached = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
        ]
        tools = AgentTools("room-123", mock_rest_client, participants=cached)

        result = await tools.get_participants()

        assert result == []
        assert tools._participants == cached

    async def test_get_participants_clears_cache_on_empty_list(self, mock_rest_client):
        """An explicit empty list from the server is authoritative — cache should clear."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        stale = [{"id": "ghost", "name": "Ghost", "type": "User", "handle": "ghost"}]
        tools = AgentTools("room-123", mock_rest_client, participants=stale)

        await tools.get_participants()

        assert tools._participants == []

    async def test_get_participants_syncs_roster_to_ctx(self, mock_rest_client):
        """get_participants() must make the ctx roster follow the REST list —
        stale entries drop (even ctx-only ones this AgentTools never saw) and
        new ones appear — so the refresh survives turn boundaries."""
        from band.runtime.execution import ExecutionContext

        ctx = ExecutionContext(
            room_id="room-123",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )
        ctx.add_participant(
            {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
        )
        tools = AgentTools.from_context(ctx)
        # A ctx-only participant that joined after tools were built, then left
        # before the refresh — it must not survive as a ghost.
        ctx.add_participant({"id": "user-3", "name": "User Three", "type": "User"})

        # Server returns only a *new* participant — user-1 and user-3 are gone.
        new_p = make_participant_mock("user-2", "User Two", "User", handle="user-two")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[new_p]))
        )

        await tools.get_participants()

        assert [p["id"] for p in ctx.participants] == ["user-2"]
        assert ctx.participants[0]["handle"] == "user-two"

    async def test_send_message_mentions_newly_discovered_participant(
        self, mock_rest_client
    ):
        """Mentioning a participant first seen via get_participants() should not raise."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.get_participants()
        await tools.send_message("Hi @user-one!", mentions=["user-one"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "user-one"


class TestAgentToolsCreateChatroom:
    """Test create_chatroom tool."""

    async def test_create_chatroom_success(self, mock_rest_client):
        """create_chatroom() should call REST API and return room ID."""
        mock_response = Mock()
        mock_response.data.id = "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom(task_id="task-789")

        assert result == "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat.assert_called_once()

    async def test_create_chatroom_without_task_id(self, mock_rest_client):
        """create_chatroom() should work without task_id."""
        mock_response = Mock()
        mock_response.data.id = "room-abc"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom()

        assert result == "room-abc"


class TestAgentToolsSchemas:
    """Test tool schema generation."""

    def test_tool_models_registry(self):
        """TOOL_MODELS should contain all tool input models."""
        assert "band_send_message" in TOOL_MODELS
        assert "band_send_event" in TOOL_MODELS
        assert "band_add_participant" in TOOL_MODELS
        assert "band_remove_participant" in TOOL_MODELS
        assert "band_lookup_peers" in TOOL_MODELS
        assert "band_get_participants" in TOOL_MODELS
        assert "band_create_chatroom" in TOOL_MODELS

    def test_tool_models_property(self, mock_rest_client):
        """tool_models property should return registry."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.tool_models is TOOL_MODELS

    def test_get_tool_schemas_openai(self, mock_rest_client):
        """get_tool_schemas('openai') should return OpenAI format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("openai")

        tool_names = [s["function"]["name"] for s in schemas]
        # Base platform tools
        assert "band_send_message" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_create_chatroom" in tool_names
        # Contact tools included by default
        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        # Memory tools excluded by default
        assert "band_list_memories" not in tool_names
        assert "band_store_memory" not in tool_names

        send_msg = next(
            s for s in schemas if s["function"]["name"] == "band_send_message"
        )
        assert send_msg["type"] == "function"
        assert "parameters" in send_msg["function"]
        assert "description" in send_msg["function"]

    def test_get_tool_schemas_openai_with_memory(self, mock_rest_client):
        """get_tool_schemas('openai', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "openai", capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS})
        )

        tool_names = [s["function"]["name"] for s in schemas]
        # Memory tools present
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_get_memory" in tool_names
        assert "band_supersede_memory" in tool_names
        assert "band_archive_memory" in tool_names
        # Base and contact tools still present
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names

    def test_get_tool_schemas_anthropic(self, mock_rest_client):
        """get_tool_schemas('anthropic') should return Anthropic format (memory tools excluded by default)."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas("anthropic")

        tool_names = [s["name"] for s in schemas]
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names
        assert "band_list_memories" not in tool_names

        send_msg = next(s for s in schemas if s["name"] == "band_send_message")
        assert "input_schema" in send_msg
        assert "description" in send_msg

    def test_get_tool_schemas_anthropic_with_memory(self, mock_rest_client):
        """get_tool_schemas('anthropic', include_memory=True) should include memory tools."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "anthropic",
            capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS}),
        )

        tool_names = [s["name"] for s in schemas]
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_send_message" in tool_names
        assert "band_list_contacts" in tool_names

    def test_schemas_drop_numeric_bounds(self, mock_rest_client):
        """Pydantic Field(ge=.., le=..) renders minimum/maximum, which some
        providers reject on integer params; the schemas must omit them while the
        models still enforce the bounds at execution."""
        tools = AgentTools("room-123", mock_rest_client)

        schemas = tools.get_tool_schemas(
            "openai", capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS})
        )

        page_size = next(
            s["function"]["parameters"]["properties"]["page_size"]
            for s in schemas
            if s["function"]["name"] == "band_lookup_peers"
        )
        assert "minimum" not in page_size
        assert "maximum" not in page_size
        assert page_size["type"] == "integer"


class TestAgentToolsExecuteToolCall:
    """Test execute_tool_call dispatch."""

    async def test_execute_send_message(self, mock_rest_client, participants):
        """execute_tool_call() should dispatch band_send_message."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert result["id"] == "msg-123"

    async def test_execute_send_event(self, mock_rest_client):
        """execute_tool_call() should dispatch band_send_event."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call(
            "band_send_event", {"content": "Thinking...", "message_type": "thought"}
        )

        assert result["message_type"] == "thought"

    async def test_execute_lookup_peers(self, mock_rest_client):
        """execute_tool_call() should dispatch band_lookup_peers."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("band_lookup_peers", {"page": 1})

        # execute_tool_call calls .model_dump() on the Fern response
        assert isinstance(result, dict)

    async def test_execute_get_participants(self, mock_rest_client):
        """execute_tool_call() should dispatch band_get_participants."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("band_get_participants", {})

        assert isinstance(result, list)
        assert result[0]["id"] == "user-1"
        assert result[0]["name"] == "User One"

    async def test_execute_unknown_tool(self, mock_rest_client):
        """execute_tool_call() should return error for unknown tool."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("unknown_tool", {})

        assert "Unknown tool" in result

    async def test_execute_validation_error(self, mock_rest_client):
        """execute_tool_call() should return validation error in LLM-friendly format."""
        tools = AgentTools("room-123", mock_rest_client)

        # Missing required field
        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello"}
        )

        assert "Invalid arguments for band_send_message" in result
        assert "mentions" in result  # Should mention the missing field

    async def test_execute_runtime_error(self, mock_rest_client, participants):
        """execute_tool_call() should return execution error."""
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            Exception("Network error")
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert "Error executing" in result


class TestFormatToolValidationError:
    """Pins the exact wire-message shape `format_tool_validation_error` produces.

    The published band-mcp CLI surfaces this string verbatim to its callers
    (via ``StandaloneResolver``), so the separator between multiple field
    errors and the dotted-path format for a nested field are part of the
    wire contract, not just an internal formatting detail free to drift.
    """

    def test_multiple_and_nested_field_errors_joined_with_comma(self):
        class Nested(BaseModel):
            name: str

        class Model(BaseModel):
            items: list[Nested]
            extra: str

        with pytest.raises(ValidationError) as exc_info:
            Model(items=[{}], extra=None)

        message = format_tool_validation_error("some_tool", exc_info.value)

        assert message == (
            "Invalid arguments for some_tool: "
            "items.0.name: Field required, "
            "extra: Input should be a valid string"
        )


class TestEmptyMentionsValidation:
    """Test that empty mentions return a helpful error with participant names."""

    async def test_raises_error_with_participant_names(
        self, mock_rest_client, participants
    ):
        """Should raise BandToolError listing available participants when mentions empty."""
        from band.core.exceptions import BandToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(
            BandToolError, match="At least one mention is required"
        ) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        assert "@user-one" in str(exc_info.value)
        assert "@user-two" in str(exc_info.value)
        # Should NOT have called the API
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_called()

    async def test_raises_error_when_mentions_none(
        self, mock_rest_client, participants
    ):
        """Should raise BandToolError when mentions is None."""
        from band.core.exceptions import BandToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.send_message("Hello!", mentions=None)

    async def test_uses_handle_when_available(self, mock_rest_client):
        """Should prefer handle over name in error message."""
        from band.core.exceptions import BandToolError

        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="@user-one"):
            await tools.send_message("Hello!", mentions=[])

    async def test_omits_participant_without_handle(self, mock_rest_client):
        """Should omit handle-less participants — they can't be @mentioned."""
        from band.core.exceptions import BandToolError

        participants = [
            {"id": "user-1", "name": "User One", "type": "User"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(
            BandToolError, match="At least one mention is required"
        ) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        # Name must not be offered as a mention target — only real handles are.
        assert "User One" not in str(exc_info.value)
        # With no mentionable handles there is nothing to suggest, so the error
        # carries no handle list rather than an empty one.
        assert "Available handles:" not in str(exc_info.value)

    async def test_no_error_when_mentions_provided(
        self, mock_rest_client, participants
    ):
        """Should proceed normally when mentions are provided."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model; verify it has the expected attribute
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_execute_tool_call_raises_band_tool_error(
        self, mock_rest_client, participants
    ):
        """execute_tool_call lets BandToolError propagate for wrapper translation."""
        from band.core.exceptions import BandToolError

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.execute_tool_call(
                "band_send_message", {"content": "Hello!", "mentions": []}
            )


class TestMentionResolution:
    """Test mention resolution logic."""

    def test_resolve_string_mentions(self, mock_rest_client, participants):
        """Should resolve string mentions to dicts with id and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_dict_mentions_with_id(self, mock_rest_client, participants):
        """Should pass through dict mentions with ID and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"id": "custom-id", "handle": "@custom"}])

        assert resolved[0] == {"id": "custom-id", "handle": "@custom"}

    def test_resolve_dict_mentions_without_id(self, mock_rest_client, participants):
        """Should resolve dict mentions without ID by name lookup."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"name": "User One"}])

        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_unknown_raises(self, mock_rest_client, participants):
        """Should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant"):
            tools._resolve_mentions(["Unknown Person"])


class TestHandleMentionResolution:
    """Test handle-based mention resolution."""

    def test_available_mention_handles_excludes_self_and_missing_handles(self):
        """Available handle hints should include only mentionable room handles."""
        participants = [
            {"id": "user-1", "name": "User One", "handle": "@user-one"},
            {"id": "self", "name": "Self", "handle": "@self"},
            {"id": "user-3", "name": "No Handle", "handle": None},
        ]

        assert available_mention_handles(participants, agent_id="self") == ["@user-one"]

    def test_append_mention_handles_hint_is_idempotent(self):
        """An error already carrying the hint is returned unchanged, so the same
        error can pass through multiple adapter enrichers without doubling."""
        enriched = append_mention_handles_hint(
            "At least one mention is required", ["@alice"]
        )
        assert enriched.count("Available handles:") == 1

        twice = append_mention_handles_hint(enriched, ["@alice"])
        assert twice == enriched

    def test_append_mention_handles_hint_no_handles_is_noop(self):
        """With no mentionable handles there is nothing to suggest."""
        error = "At least one mention is required"
        assert append_mention_handles_hint(error, []) == error

    def test_resolve_by_handle(self, mock_rest_client, participants):
        """Should resolve mentions by handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one"])

        assert len(resolved) == 1
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_handle_takes_priority(self, mock_rest_client):
        """Should try handle lookup before name lookup."""
        # Participant with handle different from name
        participants = [
            {
                "id": "agent-1",
                "name": "Weather Agent",
                "type": "Agent",
                "handle": "@john/weather",
            },
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        # Resolve by handle
        resolved = tools._resolve_mentions(["@john/weather"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

        # Resolve by name still works
        resolved = tools._resolve_mentions(["Weather Agent"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

    def test_resolve_mixed_handles_and_names(self, mock_rest_client, participants):
        """Should resolve a mix of handles and names."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_unknown_handle_raises(self, mock_rest_client, participants):
        """Should raise for unknown handle."""
        tools = AgentTools(
            "room-123", mock_rest_client, participants, agent_id="user-2"
        )

        # @ prefix is stripped during normalization
        with pytest.raises(ValueError, match="Unknown participant 'unknown'") as exc:
            tools._resolve_mentions(["@unknown"])

        message = str(exc.value)
        assert "@user-one" in message
        assert "@user-two" not in message

    def test_resolve_participant_without_handle(self, mock_rest_client):
        """Should resolve by name when participant has no handle."""
        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": None},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One"])

        assert resolved[0] == {"id": "user-1", "handle": None}


class TestToolInputModels:
    """Test Pydantic tool input models."""

    def test_send_message_input_validation(self):
        """SendMessageInput should validate fields."""
        model = SendMessageInput(content="Hello", mentions=["User"])
        assert model.content == "Hello"
        assert model.mentions == ["User"]

    def test_send_message_input_accepts_empty_mentions(self):
        """SendMessageInput allows empty mentions (runtime validates instead)."""
        model = SendMessageInput(content="Hello", mentions=[])
        assert model.mentions == []

    def test_send_room_file_input_requires_mentions(self):
        """band_send_room_file posts a message to attach the file, so its
        schema must require mentions just like SendMessageInput -- an
        optional-looking field here would let the LLM omit it, upload the
        file, and only then discover the platform's mention requirement."""
        with pytest.raises(ValidationError):
            SendRoomFileInput(content="hi", filename="f.txt")

    def test_send_room_file_input_accepts_empty_mentions(self):
        """Present but empty is still valid at the schema level (runtime
        validates the "at least one" rule instead, same as SendMessageInput)."""
        model = SendRoomFileInput(content="hi", filename="f.txt", mentions=[])
        assert model.mentions == []

    def test_send_event_input_validation(self):
        """SendEventInput should validate fields."""
        model = SendEventInput(content="Thinking", message_type="thought")
        assert model.message_type == "thought"

    def test_send_event_input_validates_type(self):
        """SendEventInput should validate message_type."""
        with pytest.raises(Exception):
            SendEventInput(content="Test", message_type="invalid")

    def test_add_participant_input_defaults(self):
        """AddParticipantInput should have default role."""
        model = AddParticipantInput(identifier="User")
        assert model.role == "member"

    def test_add_participant_input_accepts_legacy_name_field(self):
        """AddParticipantInput should accept 'name' as alias for backward compat."""
        model = AddParticipantInput.model_validate({"name": "Agent Two"})
        assert model.identifier == "Agent Two"

    def test_remove_participant_input_accepts_legacy_name_field(self):
        """RemoveParticipantInput should accept 'name' as alias for backward compat."""
        from band.runtime.tools import RemoveParticipantInput

        model = RemoveParticipantInput.model_validate({"name": "User One"})
        assert model.identifier == "User One"

    def test_lookup_peers_input_defaults(self):
        """LookupPeersInput should have defaults."""
        model = LookupPeersInput()
        assert model.page == 1
        assert model.page_size == 50

    def test_get_participants_input_no_fields(self):
        """GetParticipantsInput should have no required fields."""
        model = GetParticipantsInput()
        assert model is not None

    def test_create_chatroom_input_validation(self):
        """CreateChatroomInput should allow optional task_id."""
        model = CreateChatroomInput(task_id="task-123")
        assert model.task_id == "task-123"

    def test_create_chatroom_input_no_task_id(self):
        """CreateChatroomInput should work without task_id."""
        model = CreateChatroomInput()
        assert model.task_id is None


class TestIsRoomPostingTool:
    """Which tool calls count as having replied in the room."""

    def test_sdk_injected_tool(self):
        assert is_room_posting_tool("band_send_message") is True

    def test_standalone_band_mcp_tool(self):
        assert is_room_posting_tool("create_agent_chat_message") is True

    def test_mcp_server_prefixed_names(self):
        """MCP clients may prefix the server name onto the tool name."""
        assert is_room_posting_tool("band-band_send_message") is True
        assert is_room_posting_tool("band-create_agent_chat_message") is True

    def test_non_posting_tools(self):
        assert is_room_posting_tool("band_send_event") is False
        assert is_room_posting_tool("band_lookup_peers") is False
        assert is_room_posting_tool("get_weather") is False

    def test_no_substring_false_positive(self):
        """Only an exact or server-prefixed match counts, not any substring."""
        assert is_room_posting_tool("band_send_message_draft") is False

    def test_non_band_server_prefix_does_not_resolve(self):
        """An unrelated MCP server's own tool must never be treated as a Band
        room-posting tool just because it ends in ``-band_send_message``."""
        assert is_room_posting_tool("other-band_send_message") is False


class TestCanonicalizeMcpToolName:
    """Recovering the canonical band name from an MCP ``<server>-`` spelling."""

    OWN = frozenset({"band_send_message", "echo"})

    def test_strips_server_prefix_off_own_tool(self):
        name = canonicalize_mcp_tool_name("band-band_send_message", self.OWN)
        assert name == "band_send_message"

    def test_custom_tool_prefix_stripped(self):
        assert canonicalize_mcp_tool_name("band-echo", self.OWN) == "echo"

    def test_non_band_server_prefix_passes_through(self):
        """Only the Band MCP server's own ``band-`` prefix resolves -- an
        unrelated server's tool that happens to end in one of our names must
        not be misattributed to us (e.g. narrated/suppressed as our own)."""
        name = canonicalize_mcp_tool_name("platform-band_send_message", self.OWN)
        assert name == "platform-band_send_message"

    def test_foreign_tool_passes_through(self):
        """A prefixed name that reveals none of ours stays as reported."""
        assert canonicalize_mcp_tool_name("band-grep", self.OWN) == "band-grep"

    def test_unprefixed_name_passes_through(self):
        assert (
            canonicalize_mcp_tool_name("band_send_message", self.OWN)
            == "band_send_message"
        )


class TestFetchRoomContext:
    """Paging the agent context endpoint."""

    @pytest.mark.asyncio
    async def test_prefers_the_required_pagination_field(self, mock_rest_client):
        """`metadata` is required; `meta` is optional and often absent.

        Reading only `meta` collapses every room to one synthesized page, so a
        caller paging through history silently stops at the first page.
        """
        pagination = MagicMock()
        pagination.model_dump.return_value = {"total_pages": 3}
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=SimpleNamespace(data=[], metadata=pagination)
        )

        context = await AgentTools("room-123", mock_rest_client).fetch_room_context(
            room_id="room-123"
        )

        assert context["meta"]["total_pages"] == 3
