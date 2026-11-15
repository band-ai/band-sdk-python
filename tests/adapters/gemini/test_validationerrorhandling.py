"""ValidationErrorHandling behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from google.genai import types
from pydantic import ValidationError

from band.adapters.gemini import GeminiAdapter


class TestValidationErrorHandling:
    @pytest.mark.asyncio
    async def test_validation_error_returns_friendly_message(self, mock_tools):
        adapter = GeminiAdapter()

        mock_tools.execute_tool_call_structured = AsyncMock(
            side_effect=ValidationError.from_exception_data(
                title="SendMessageInput",
                line_errors=[
                    {
                        "type": "missing",
                        "loc": ("content",),
                        "msg": "Field required",
                        "input": {},
                    }
                ],
            )
        )

        function_calls = [
            types.FunctionCall(name="band_send_message", args={}, id="call_1")
        ]
        parts, _declined = await adapter._process_function_calls(
            function_calls, mock_tools
        )

        assert len(parts) == 1
        resp = parts[0].function_response
        assert resp is not None
        assert "Invalid arguments for band_send_message" in resp.response["error"]
        assert "content" in resp.response["error"]
