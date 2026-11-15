"""ExecutionReporting behavior of the Google ADK adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.types import Emit
from band.runtime.tools import BandTool
from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestExecutionReporting:
    """Tests for _report_event execution reporting."""

    @pytest.mark.asyncio
    async def test_reports_function_calls(self, mock_tools):
        """Should report function calls as tool_call events."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fc = MagicMock()
        mock_fc.name = "band_send_message"
        mock_fc.args = {"content": "Hello"}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_call"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_reports_function_responses(self, mock_tools):
        """Should report function responses as tool_result events."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fr = MagicMock()
        mock_fr.name = "band_send_message"
        mock_fr.response = {"status": "sent"}
        mock_fr.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = []
        event.get_function_responses.return_value = [mock_fr]

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_called_once()
        call_args = mock_tools.send_event.call_args
        assert call_args.kwargs["message_type"] == "tool_result"
        assert "band_send_message" in call_args.kwargs["content"]

    @pytest.mark.asyncio
    async def test_reports_read_room_file_image_result_as_placeholder(self, mock_tools):
        """band_read_room_file's image result must not leak its base64 data
        into a tool_result event. run_async json.dumps a non-str tool
        result before returning it, so ADK's own __build_response_event
        wraps that json string as {"result": <that string>} (its spec
        requires a dict) -- str()ing that wrapper would otherwise embed the
        full base64 payload."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        image_result = {
            "content": [
                {
                    "type": "image",
                    "data": "not-really-base64-but-huge-in-real-life",
                    "mimeType": "image/png",
                }
            ]
        }
        mock_fr = MagicMock()
        mock_fr.name = BandTool.READ_ROOM_FILE
        mock_fr.response = {"result": json.dumps(image_result)}
        mock_fr.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = []
        event.get_function_responses.return_value = [mock_fr]

        await adapter._report_event(event, mock_tools)

        content = mock_tools.send_event.call_args.kwargs["content"]
        assert "not-really-base64-but-huge-in-real-life" not in content
        assert "<1 image content block(s)>" in content

    @pytest.mark.asyncio
    async def test_reports_send_room_file_args_content_as_placeholder(self, mock_tools):
        """band_send_room_file's raw file content must not leak into a
        tool_call event's reported ARGS."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        mock_fc = MagicMock()
        mock_fc.name = BandTool.SEND_ROOM_FILE
        mock_fc.args = {"content": "the entire raw file body", "filename": "f.txt"}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        await adapter._report_event(event, mock_tools)

        content = mock_tools.send_event.call_args.kwargs["content"]
        assert "the entire raw file body" not in content
        assert "byte file content" in content

    @pytest.mark.asyncio
    async def test_skips_event_without_function_methods(self, mock_tools):
        """Should skip events that lack function call/response methods."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)

        event = MagicMock(spec=[])  # No attributes

        await adapter._report_event(event, mock_tools)

        mock_tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_report_failure_gracefully(self, mock_tools):
        """Should not raise when event reporting fails."""
        adapter = GoogleADKAdapter(emit=Emit.TOOL_CALLS)
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network error"))

        mock_fc = MagicMock()
        mock_fc.name = "test_tool"
        mock_fc.args = {}
        mock_fc.id = "fc-1"

        event = MagicMock()
        event.get_function_calls.return_value = [mock_fc]
        event.get_function_responses.return_value = []

        # Should not raise
        await adapter._report_event(event, mock_tools)
