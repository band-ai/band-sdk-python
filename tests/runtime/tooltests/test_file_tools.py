from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import GetAgentChatContextResponse

from band.client.rest import DEFAULT_REQUEST_OPTIONS, NotFoundError
from band.config.settings import RuntimeSettings
from band.core.exceptions import BandToolError
from band.runtime.execution import ExecutionContext
from band.runtime.tools import (
    DEFAULT_FILE_CAPTION,
    FILE_UNAVAILABLE_MESSAGE,
    MAX_INLINE_IMAGE_BYTES,
    MAX_INLINE_TEXT_BYTES,
    MAX_SEND_CONTENT_BYTES,
    AgentTools,
)
from tests.content import BLANK_CONTENT_CASES
from tests.runtime.tooltests.kit import (
    _attachment,
    _context_response,
    _fake_download,
    _fake_download_not_found,
    _message_with_attachments,
    _mock_attachment_page,
    _posted_message,
)


class TestFileTools:
    """Tests for band_list_room_files / band_read_room_file / band_send_room_file."""

    # --- list_room_files ---

    @pytest.mark.asyncio
    async def test_list_room_files_dedupes_by_attachment_id(
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
    async def test_read_room_file_inlines_small_text(
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
    async def test_read_room_file_inlines_small_image(
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
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
        self, mock_rest_client: MagicMock
    ) -> None:
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=_context_response([])
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.read_room_file("nope")

    @pytest.mark.asyncio
    async def test_read_room_file_download_not_found_raises_band_tool_error(
        self, mock_rest_client: MagicMock
    ) -> None:
        attachment = _attachment(content_type="text/plain", size=5)
        _mock_attachment_page(mock_rest_client, attachment)
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download_not_found()
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.read_room_file("file-1")

    @pytest.mark.asyncio
    async def test_read_room_file_searches_past_the_first_page(
        self, mock_rest_client: MagicMock
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_find_attachment_stops_on_repeated_cursor(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A malformed response repeating a cursor must not loop forever --
        the only pagination guard, since a room's history has no realistic
        depth ceiling to cap against (unlike iter_chat_pages's room count)."""

        def _looping_page(**_kwargs: Any) -> GetAgentChatContextResponse:
            return _context_response(
                [_message_with_attachments("msg", [_attachment("other-file")])],
                next_cursor="same-cursor",
                has_more=True,
            )

        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=_looping_page
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools._find_attachment("file-1")

        get_context = mock_rest_client.agent_api_context.get_agent_chat_context
        assert get_context.await_count == 2

    @pytest.mark.asyncio
    async def test_find_attachment_searches_past_fifty_pages(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A room with more than 50 pages of history before the target file
        must still resolve it -- there is no depth cap."""
        target_page = 60
        target = _attachment(content_type="text/plain", size=5)

        def _paged_history(**kwargs: Any) -> GetAgentChatContextResponse:
            cursor = kwargs.get("cursor")
            page = int(cursor.removeprefix("page-")) if cursor else 1
            if page < target_page:
                return _context_response(
                    [
                        _message_with_attachments(
                            f"msg-{page}", [_attachment("other-file")]
                        )
                    ],
                    next_cursor=f"page-{page + 1}",
                    has_more=True,
                )
            return _context_response(
                [_message_with_attachments(f"msg-{page}", [target])]
            )

        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=_paged_history
        )
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        assert result["text"] == "hello"
        get_context = mock_rest_client.agent_api_context.get_agent_chat_context
        assert get_context.await_count == target_page

    @pytest.mark.asyncio
    async def test_find_attachment_skips_pagination_on_cache_hit(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A second lookup for the same (room, rest, file_id) must not
        re-paginate -- _find_attachment delegates to a shared @alru_cache
        keyed on those three, not on the AgentTools instance calling it."""
        _mock_attachment_page(mock_rest_client, _attachment("file-1"))
        first = AgentTools("room-123", mock_rest_client)
        second = AgentTools("room-123", mock_rest_client)

        await first._find_attachment("file-1")
        await second._find_attachment("file-1")

        mock_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_attachment_does_not_share_cache_across_rooms(
        self, mock_rest_client: MagicMock
    ) -> None:
        """The same file_id looked up in two different rooms must not
        cache-hit across them -- room_id is part of the cache key, not
        just file_id."""
        _mock_attachment_page(mock_rest_client, _attachment("file-1"))
        room_a = AgentTools("room-a", mock_rest_client)
        room_b = AgentTools("room-b", mock_rest_client)

        await room_a._find_attachment("file-1")
        await room_b._find_attachment("file-1")

        assert (
            mock_rest_client.agent_api_context.get_agent_chat_context.await_count == 2
        )

    @pytest.mark.asyncio
    async def test_find_attachment_does_not_share_cache_across_rest_clients(
        self, mock_rest_client: MagicMock
    ) -> None:
        """The same (room_id, file_id) looked up through two different REST
        clients must not cache-hit across them -- the rest client identity is
        part of the cache key, so two agents (or a pool of clients) never
        leak each other's attachment metadata."""
        _mock_attachment_page(mock_rest_client, _attachment("file-1"))
        other_rest_client = MagicMock()
        _mock_attachment_page(other_rest_client, _attachment("file-1"))
        tools_a = AgentTools("room-123", mock_rest_client)
        tools_b = AgentTools("room-123", other_rest_client)

        await tools_a._find_attachment("file-1")
        await tools_b._find_attachment("file-1")

        mock_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once()
        other_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_attachment_retries_pagination_after_a_miss(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A miss must not be cached: a file posted after a failed lookup is
        still findable on the next attempt, not permanently "not found"."""
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=_context_response([])
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools._find_attachment("file-1")

        _mock_attachment_page(mock_rest_client, _attachment("file-1"))
        result = await tools._find_attachment("file-1")

        assert result.id == "file-1"

    @pytest.mark.asyncio
    async def test_find_attachment_rejects_expired_cached_entry(
        self, mock_rest_client: MagicMock
    ) -> None:
        """An attachment past its own expires_at must not be handed back
        from cache -- it's evicted and re-fetched once (the cached copy may
        just predate the platform extending the deadline), and only raised
        as not-found if the fresh read is still expired too -- which also
        evicts it, so the cache never keeps serving metadata it already
        gave up on."""
        expired = _attachment(
            "file-1", expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        _mock_attachment_page(mock_rest_client, expired)
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools._find_attachment("file-1")

        get_context = mock_rest_client.agent_api_context.get_agent_chat_context
        assert get_context.await_count == 2
        assert not AgentTools._attachment_cache().cache_contains(
            "room-123", mock_rest_client, "file-1"
        )

    @pytest.mark.asyncio
    async def test_find_attachment_refreshes_an_expired_entry(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A cache entry whose expires_at has passed gets exactly one
        real re-fetch -- if that comes back not-expired (the platform
        extended it), it's returned, not rejected on the stale cached
        timestamp."""
        not_expired = _attachment(
            "file-1",
            content_type="text/plain",
            size=5,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        expired_page = _context_response(
            [
                _message_with_attachments(
                    "msg-1",
                    [
                        _attachment(
                            "file-1",
                            expires_at=datetime.now(UTC) - timedelta(seconds=1),
                        )
                    ],
                )
            ]
        )
        refreshed_page = _context_response(
            [_message_with_attachments("msg-1", [not_expired])]
        )
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=[expired_page, refreshed_page]
        )
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.read_room_file("file-1")

        assert result["text"] == "hello"

    @pytest.mark.asyncio
    async def test_find_attachment_rejects_expired_naive_timestamp(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A naive (offset-less) expires_at -- the Fern model doesn't enforce
        one -- must be treated as UTC, not raise on comparison to aware
        now()."""
        expired = _attachment(
            "file-1",
            expires_at=datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        )
        _mock_attachment_page(mock_rest_client, expired)
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools._find_attachment("file-1")

    def test_attachment_cache_maxsize_is_wired_to_settings(self) -> None:
        """The @alru_cache's maxsize must actually come from RuntimeSettings
        -- the eviction algorithm itself is async-lru's to test, not ours."""
        assert AgentTools._attachment_cache().cache_parameters()["maxsize"] == (
            RuntimeSettings().BAND_ATTACHMENT_CACHE_MAXSIZE
        )

    def test_attachment_cache_rereads_settings_lazily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """maxsize must come from RuntimeSettings at first real use, not at
        module import -- every example does `from band import Agent` (which
        imports this module) before its own load_dotenv() call, so baking
        RuntimeSettings() into a decorator at class-body time would silently
        ignore a .env-set override."""
        AgentTools._attachment_cache.cache_clear()
        monkeypatch.setenv("BAND_ATTACHMENT_CACHE_MAXSIZE", "42")
        try:
            assert AgentTools._attachment_cache().cache_parameters()["maxsize"] == 42
        finally:
            AgentTools._attachment_cache.cache_clear()

    @pytest.mark.asyncio
    async def test_read_room_file_caches_attachments_across_recreated_tools(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Real ExecutionContext + from_context(): a file found by one turn's
        AgentTools must skip re-pagination on the next turn's recreated
        instance, since AgentTools.from_context() rebuilds fresh every turn.
        """
        _mock_attachment_page(
            mock_rest_client, _attachment(content_type="text/plain", size=5)
        )
        mock_rest_client.agent_api_files.download_agent_chat_file = lambda **_kw: (
            _fake_download(b"hello")
        )
        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )

        tools1 = AgentTools.from_context(ctx)
        await tools1.read_room_file("file-1")

        tools2 = AgentTools.from_context(ctx)
        await tools2.read_room_file("file-1")

        mock_rest_client.agent_api_context.get_agent_chat_context.assert_awaited_once()

    # --- send_room_file ---

    @pytest.mark.asyncio
    async def test_send_room_file_uploads_and_posts_message(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        tools = AgentTools("room-123", mock_rest_client, participants)
        body = "a" * (MAX_SEND_CONTENT_BYTES + 1)

        with pytest.raises(BandToolError, match="exceeds"):
            await tools.send_room_file(body, "big.txt", mentions=["User One"])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_non_ascii_filename_raises_before_upload(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            side_effect=NotFoundError(body=MagicMock())
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match=FILE_UNAVAILABLE_MESSAGE):
            await tools.send_room_file("hi", "f.txt", mentions=["User One"])

    @pytest.mark.asyncio
    async def test_send_room_file_empty_mentions_raises_before_upload(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Sharing a file is a send_message call under the hood, so a missing
        mention must fail before the upload -- not after, which would leave
        an orphaned attachment nothing points at."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.send_room_file("hi", "f.txt", mentions=[])

        mock_rest_client.agent_api_files.upload_agent_chat_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_empty_caption_uses_default(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
    @pytest.mark.parametrize("caption", BLANK_CONTENT_CASES)
    async def test_send_room_file_blank_caption_uses_default(
        self,
        mock_rest_client: MagicMock,
        participants: list[dict[str, str]],
        caption: str,
    ) -> None:
        """A caption that only *looks* non-empty falls back to the default
        too: the send refuses content with no visible characters and returns
        ``None``, so anything narrower than the platform's own rule here
        drops the post after the file is already uploaded, leaving an
        orphaned attachment."""
        uploaded = _attachment("file-9", name="report.txt")
        upload_response = MagicMock()
        upload_response.data = uploaded
        mock_rest_client.agent_api_files.upload_agent_chat_file = AsyncMock(
            return_value=upload_response
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_room_file(
            "hello world", "report.txt", caption=caption, mentions=["User One"]
        )

        assert result["attachment"]["id"] == "file-9"
        assert _posted_message(mock_rest_client).content == (
            DEFAULT_FILE_CAPTION.format(filename="report.txt")
        )

    @pytest.mark.asyncio
    async def test_send_room_file_discoverable_from_a_new_agenttools_instance(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
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
