"""ExecutionReporting behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.core.types import Emit
from tests.adapters.pydanticai.kit import make_stream_events, message_types


class TestExecutionReporting:
    """Tests for execution reporting (tool_call and tool_result events)."""

    @pytest.mark.asyncio
    async def test_emits_tool_call_events_when_enabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit tool_call events when emit=Emit.TOOL_CALLS."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
                tool_results=[("band_send_message", "Message sent", "call-123")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was called with tool_call
        mock_tools.send_event.assert_any_call(
            content='{"name": "band_send_message", "args": {"content": "Hello"}, "tool_call_id": "call-123"}',
            message_type="tool_call",
        )

    @pytest.mark.asyncio
    async def test_tool_call_event_redacts_send_room_file_content(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """band_send_room_file's content arg can carry up to
        MAX_SEND_CONTENT_BYTES of real file bytes; the tool_call event must
        report a bounded placeholder instead of the raw content."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[
                    (
                        "band_send_room_file",
                        {"content": "SECRET FILE BYTES", "filename": "f.txt"},
                        "call-123",
                    )
                ],
                tool_results=[("band_send_message", "Message sent", "call-2")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        reported_content = mock_tools.send_event.call_args_list[0].kwargs["content"]
        assert "SECRET FILE BYTES" not in reported_content
        assert "byte file content" in reported_content
        assert '"filename": "f.txt"' in reported_content

    @pytest.mark.asyncio
    async def test_emits_tool_result_events_when_enabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit tool_result events when emit=Emit.TOOL_CALLS."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_results=[
                    ("band_send_message", "Message sent successfully", "call-123")
                ],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was called with tool_result
        mock_tools.send_event.assert_any_call(
            content='{"name": "band_send_message", "output": "Message sent successfully", "tool_call_id": "call-123"}',
            message_type="tool_result",
        )

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_binary_content(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """band_read_room_file's image result is a list[BinaryContent]; str()
        on that embeds the raw image bytes via BinaryContent.__repr__. The
        tool_result event must report a bounded placeholder instead."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        image = BinaryContent(
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, media_type="image/png"
        )
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_results=[
                    ("band_read_room_file", [image], "call-1"),
                    ("band_send_message", "Message sent", "call-2"),
                ],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        mock_tools.send_event.assert_any_call(
            content='{"name": "band_read_room_file", "output": "<1 image content block(s)>", "tool_call_id": "call-1"}',
            message_type="tool_result",
        )

    @pytest.mark.asyncio
    async def test_no_events_when_reporting_disabled(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """emit=() disables tool_call/tool_result events (emit otherwise defaults on)."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=()
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
                tool_results=[("band_send_message", "Message sent", "call-123")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify send_event was NOT called for tool_call or tool_result
        assert not set(message_types(mock_tools)) & {"tool_call", "tool_result"}

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_all_reported(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should emit events for all tool calls in sequence."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_calls=[
                    ("band_lookup_peers", {}, "call-1"),
                    ("band_add_participant", {"identifier": "Helper"}, "call-2"),
                    ("band_send_message", {"content": "Done"}, "call-3"),
                ],
                tool_results=[
                    ("band_lookup_peers", "[{...}]", "call-1"),
                    ("band_add_participant", "Added", "call-2"),
                    ("band_send_message", "Sent", "call-3"),
                ],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Count tool_call and tool_result events
        types = message_types(mock_tools)
        assert types.count("tool_call") == 3
        assert types.count("tool_result") == 3

    @pytest.mark.asyncio
    async def test_event_failure_does_not_crash_run(
        self, sample_message, mock_pydantic_agent
    ):
        """Should continue running if send_event fails."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.TOOL_CALLS
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        # Mock tools where send_event fails with a real transport error — the
        # tool_call event's own local guard swallows this and logs a warning;
        # a generic Exception would be a bug and is intentionally left to
        # propagate.
        failing_tools = AsyncMock()
        failing_tools.send_event = AsyncMock(
            side_effect=httpx.ConnectError("Network error")
        )

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[ModelRequest(parts=[UserPromptPart(content="test")])],
                tool_calls=[("band_send_message", {"content": "Hello"}, "call-123")],
                tool_results=[("band_send_message", "Message sent", "call-123")],
            )
        )

        # Should not raise
        await adapter.on_message(
            msg=sample_message,
            tools=failing_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # History should still be updated
        assert "room-123" in adapter._message_history
