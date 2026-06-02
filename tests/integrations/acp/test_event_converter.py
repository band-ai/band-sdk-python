"""Tests for EventConverter."""

from __future__ import annotations

from band.integrations.acp.event_converter import EventConverter

from .conftest import (
    make_platform_message,
    make_tool_call_message,
    make_tool_result_message,
)


class TestEventConverterConvert:
    """Tests for EventConverter.convert()."""

    def test_convert_text(self) -> None:
        """Should convert text message to agent_message_chunk."""
        msg = make_platform_message("Hello world", message_type="text")
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_message_chunk"

    def test_convert_thought(self) -> None:
        """Should convert thought message to agent_thought_chunk."""
        msg = make_platform_message("Thinking...", message_type="thought")
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_thought_chunk"

    def test_convert_tool_call(self) -> None:
        """Should convert tool_call message to ToolCallStart."""
        msg = make_tool_call_message(
            name="get_weather",
            args={"city": "NYC"},
            tool_call_id="tc-abc",
        )
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "tool_call"
        assert getattr(chunk, "tool_call_id", None) == "tc-abc"
        assert getattr(chunk, "title", None) == "get_weather"
        assert getattr(chunk, "status", None) == "in_progress"

    def test_convert_tool_result(self) -> None:
        """Should convert tool_result message to ToolCallProgress."""
        msg = make_tool_result_message(
            name="get_weather",
            output="72F sunny",
            tool_call_id="tc-abc",
        )
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "tool_call_update"
        assert getattr(chunk, "tool_call_id", None) == "tc-abc"
        assert getattr(chunk, "status", None) == "completed"

    def test_convert_tool_result_error(self) -> None:
        """Should set status=failed for error tool results."""
        msg = make_tool_result_message(
            name="get_weather",
            output="API error",
            tool_call_id="tc-fail",
            is_error=True,
        )
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "status", None) == "failed"

    def test_convert_error(self) -> None:
        """Should convert error message to agent_message_chunk with prefix."""
        msg = make_platform_message("Something failed", message_type="error")
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_message_chunk"
        # Content should include [Error] prefix
        content = getattr(chunk, "content", None)
        if content:
            text = getattr(content, "text", "")
            assert "[Error]" in text

    def test_convert_task(self) -> None:
        """Should convert task message to plan update."""
        msg = make_platform_message("Implementing feature", message_type="task")
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "plan"

    def test_convert_unknown(self) -> None:
        """Should return None for unknown message types."""
        msg = make_platform_message("Hello", message_type="custom_unknown")
        chunk = EventConverter.convert(msg)

        assert chunk is None

    def test_convert_tool_call_malformed_metadata(self) -> None:
        """Should fall back to text for malformed tool_call content."""
        msg = make_platform_message(
            "not valid json",
            message_type="tool_call",
        )
        chunk = EventConverter.convert(msg)

        # Should fall back to agent_message_chunk
        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_message_chunk"

    def test_convert_tool_result_malformed(self) -> None:
        """Should fall back to text for malformed tool_result content."""
        msg = make_platform_message(
            "not valid json",
            message_type="tool_result",
        )
        chunk = EventConverter.convert(msg)

        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_message_chunk"

    def test_convert_tool_call_missing_fields(self) -> None:
        """Should fall back to text when tool_call JSON lacks required fields."""
        msg = make_platform_message(
            '{"some": "data"}',
            message_type="tool_call",
        )
        chunk = EventConverter.convert(msg)

        # parse_tool_call returns None for missing name/tool_call_id
        assert chunk is not None
        assert getattr(chunk, "session_update", None) == "agent_message_chunk"
