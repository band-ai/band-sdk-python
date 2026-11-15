"""CustomTools behavior for Gemini."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import types
from pydantic import BaseModel, Field

from band.adapters.gemini import GeminiAdapter
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.gemini.kit import _response_with_function_call, _response_with_text
from tests.framework_conformance.turnprobes import (
    CUSTOM_TOOL_DECLARATIONS,
)


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_executes_custom_tool(self, mock_tools):
        class EchoInput(BaseModel):
            text: str = Field(...)

        async def echo_tool(inp: EchoInput) -> str:
            return inp.text

        adapter = GeminiAdapter(
            additional_tools=[(EchoInput, echo_tool)],
        )
        function_calls = [
            types.FunctionCall(name="echo", args={"text": "hello"}, id="c1")
        ]

        parts, _declined = await adapter._process_function_calls(
            function_calls, mock_tools
        )

        assert len(parts) == 1
        function_response = parts[0].function_response
        assert function_response is not None
        assert function_response.response == {"output": "hello"}
        mock_tools.execute_tool_call_structured.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, sample_message, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        async def file_report(inp: FileInput) -> str:
            return "filed"

        tools = FakeAgentTools(room_id="room-123")
        adapter = GeminiAdapter(additional_tools=[(FileInput, file_report)])
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call("file", {"note": "go"}, "call_1"),
                    _response_with_text(""),
                ]
            ),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert tools.turn.complete is complete
