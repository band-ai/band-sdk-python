"""Tests for the Parlant room file tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.exceptions import BandToolError
from band.integrations.parlant.tools import create_parlant_tools, set_session_tools
from band.runtime.tools import DEFAULT_FILE_CAPTION
from band.testing import FakeAgentTools
from tests.integrations.parlant.helpers import SESSION_ID

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


class TestFileTools:
    """Drives the real file tools built by ``create_parlant_tools``."""

    @pytest.mark.asyncio
    async def test_list_room_files_returns_formatted_list(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return formatted list of room files."""
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        result = await list_room_files(mock_context, "")

        mock_tools.list_room_files.assert_called_once_with(None)
        assert "report.txt" in result.data
        assert "file-1" in result.data

    @pytest.mark.asyncio
    async def test_list_room_files_passes_cursor(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should forward a non-empty cursor to the underlying tool."""
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        await list_room_files(mock_context, "cursor-1")

        mock_tools.list_room_files.assert_called_once_with("cursor-1")

    @pytest.mark.asyncio
    async def test_list_room_files_handles_empty_result(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should handle an empty room-file list."""
        mock_tools.list_room_files.return_value = {"data": [], "next_cursor": None}
        set_session_tools(mock_context.session_id, mock_tools)

        list_room_files = parlant_tools["band_list_room_files"]
        result = await list_room_files(mock_context, "")

        assert "No files found in this room" in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_returns_text(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return the file's decoded text."""
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        mock_tools.read_room_file.assert_called_once_with("file-1")
        assert "hello world" in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_describes_image_instead_of_inlining(
        self, parlant_tools, mock_tools, mock_context
    ):
        """An image result must be described, not passed through as bytes."""
        mock_tools.read_room_file.return_value = {
            "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
        }
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        assert "image/png" in result.data
        assert "ZmFrZQ==" not in result.data

    @pytest.mark.asyncio
    async def test_read_room_file_describes_non_previewable_file(
        self, parlant_tools, mock_tools, mock_context
    ):
        """A too-large/non-previewable file returns its description, not bytes."""
        mock_tools.read_room_file.return_value = {
            "name": "archive.zip",
            "content_type": "application/zip",
            "bytes": 999_999,
            "description": "File not shown inline: exceeds the inline text limit.",
        }
        set_session_tools(mock_context.session_id, mock_tools)

        read_room_file = parlant_tools["band_read_room_file"]
        result = await read_room_file(mock_context, "file-1")

        assert "archive.zip" in result.data
        assert "not shown inline" in result.data

    @pytest.mark.asyncio
    async def test_send_room_file_calls_tools_send_room_file(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should call tools.send_room_file with parsed mentions."""
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(
            mock_context, "file body", "notes.txt", "Alice, Bob", "here's a file"
        )

        mock_tools.send_room_file.assert_called_once_with(
            "file body", "notes.txt", "here's a file", ["Alice", "Bob"]
        )
        assert "notes.txt" in result.data
        assert "file-2" in result.data

    @pytest.mark.asyncio
    async def test_send_room_file_requires_mentions(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error when no mentions provided."""
        mock_tools.agent_id = "self"
        mock_tools.participants = [
            {"id": "user-1", "handle": "@alice"},
            {"id": "self", "handle": "@self"},
        ]
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(mock_context, "body", "notes.txt", "", "")

        assert "At least one mention is required" in result.data
        assert "@alice" in result.data
        mock_tools.send_room_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_room_file_translates_band_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """BandToolError from underlying tool must surface as ToolResult, not crash."""
        mock_tools.send_room_file.side_effect = BandToolError(
            "Filename must use plain printable ASCII characters"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        send_room_file = parlant_tools["band_send_room_file"]
        result = await send_room_file(mock_context, "body", "café.txt", "Alice", "")

        assert "Error sending room file" in result.data
        assert "ASCII" in result.data

    @pytest.mark.parametrize(
        "name,args,field",
        [
            ("band_read_room_file", {"file_id": ""}, "file_id"),
            ("band_read_room_file", {"file_id": "../files/"}, "file_id"),
        ],
    )
    @pytest.mark.asyncio
    async def test_path_tools_reject_before_execution(
        self,
        parlant_tools: dict[str, Any],
        mock_tools: MagicMock,
        mock_context: SimpleNamespace,
        name: str,
        args: dict[str, object],
        field: str,
    ) -> None:
        method = name.removeprefix("band_")
        setattr(mock_tools, method, AsyncMock())
        set_session_tools(mock_context.session_id, mock_tools)
        result = await parlant_tools[name](mock_context, **args)
        assert f"Invalid arguments for {name}" in result.data
        assert field in result.data
        getattr(mock_tools, method).assert_not_awaited()


async def test_send_room_file_without_caption_uses_the_default(plugin_server):
    """The engine sends None for an omitted caption; the tool's default applies."""
    tools = FakeAgentTools()
    set_session_tools(SESSION_ID, tools)
    await plugin_server.enable(create_parlant_tools())

    result = await plugin_server.call(
        "band_send_room_file",
        session_id=SESSION_ID,
        arguments={
            "content": "hello",
            "filename": "notes.txt",
            "mentions": "@alice",
            "caption": None,
        },
    )

    assert result.startswith("Uploaded 'notes.txt'")
    [message] = tools.messages_sent
    assert message["content"] == DEFAULT_FILE_CAPTION.format(filename="notes.txt")
