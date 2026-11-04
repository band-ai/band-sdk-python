"""ReadRoomFileImagePassthrough behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter
from band.runtime.tools import ToolCallOutcome


class TestReadRoomFileImagePassthrough:
    @pytest.mark.asyncio
    async def test_image_result_passes_through_as_inline_data(self, mock_tools):
        mock_tools.execute_tool_call_structured = AsyncMock(
            return_value=ToolCallOutcome(
                value={
                    "content": [
                        {"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}
                    ]
                },
                ok=True,
            )
        )
        adapter = GeminiAdapter()
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        parts, _declined = await adapter._process_function_calls(
            function_calls, mock_tools
        )

        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.parts is not None
        assert len(function_response.parts) == 1
        inline_data = function_response.parts[0].inline_data
        assert inline_data is not None
        assert inline_data.mime_type == "image/png"
        assert inline_data.data == b"fake"

    @pytest.mark.asyncio
    async def test_non_image_result_stays_text(self, mock_tools):
        mock_tools.execute_tool_call_structured = AsyncMock(
            return_value=ToolCallOutcome(
                value={"name": "notes.txt", "content_type": "text/plain"}, ok=True
            )
        )
        adapter = GeminiAdapter()
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        parts, _declined = await adapter._process_function_calls(
            function_calls, mock_tools
        )

        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.parts is None
        assert function_response.response == {
            "output": '{"name": "notes.txt", "content_type": "text/plain"}'
        }
