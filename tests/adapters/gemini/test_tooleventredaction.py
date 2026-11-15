"""ToolEventRedaction behavior for Gemini."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter
from band.core.types import Emit, ToolEventKey
from band.runtime.tools import ToolCallOutcome


class TestToolEventRedaction:
    @pytest.mark.asyncio
    async def test_read_room_file_image_result_reports_placeholder_not_raw_base64(
        self, mock_tools
    ):
        """The tool_result event for an image band_read_room_file call must
        report a bounded placeholder, not the raw base64 payload -- the LLM-
        facing inline_data content block (asserted in
        TestReadRoomFileImagePassthrough above) is a separate path from what
        gets reported to the platform-visible event."""

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
        adapter = GeminiAdapter(emit=Emit.TOOL_CALLS)
        function_calls = [
            types.FunctionCall(
                name="band_read_room_file", args={"file_id": "f1"}, id="c1"
            )
        ]

        await adapter._process_function_calls(function_calls, mock_tools)

        result_event = mock_tools.send_event.call_args_list[-1]
        reported = json.loads(result_event.kwargs["content"])
        assert reported[ToolEventKey.OUTPUT] == "<1 image content block(s)>"
        assert "ZmFrZQ==" not in result_event.kwargs["content"]

    @pytest.mark.asyncio
    async def test_send_room_file_reports_content_placeholder_not_raw_bytes(
        self, mock_tools
    ):
        """The tool_call event for band_send_room_file must report a bounded
        placeholder for `args`, not the raw file text -- real file bytes
        (up to ~1MB) have no business in a platform-visible log event."""

        mock_tools.execute_tool_call_structured = AsyncMock(
            return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
        )
        adapter = GeminiAdapter(emit=Emit.TOOL_CALLS)
        raw_content = "the quick brown fox" * 100
        function_calls = [
            types.FunctionCall(
                name="band_send_room_file",
                args={"content": raw_content, "filename": "notes.txt"},
                id="c1",
            )
        ]

        await adapter._process_function_calls(function_calls, mock_tools)

        call_event = mock_tools.send_event.call_args_list[0]
        reported = json.loads(call_event.kwargs["content"])
        assert reported[ToolEventKey.ARGS]["content"] == (
            f"<{len(raw_content.encode('utf-8'))} byte file content>"
        )
        assert raw_content not in call_event.kwargs["content"]
