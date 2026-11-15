"""OnMessage behavior for Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter
from band.core.types import Emit
from tests.adapters.gemini.kit import _response_with_function_call, _response_with_text


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        adapter = GeminiAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter, "_call_gemini", AsyncMock(return_value=_response_with_text("ok"))
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )
            assert "room-123" in adapter._message_history
            assert len(adapter._message_history["room-123"]) >= 2

    @pytest.mark.asyncio
    async def test_executes_tool_loop(self, sample_message, mock_tools):
        adapter = GeminiAdapter(
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    ),
                    _response_with_text("done"),
                ]
            ),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.execute_tool_call_structured.assert_called_once_with(
            "band_lookup_peers", {"page": "1"}
        )
        # tool_call + tool_result reporting
        assert mock_tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_crash_tool_execution(
        self, sample_message, mock_tools
    ):
        adapter = GeminiAdapter(
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("TestBot", "Test bot")
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        with patch.object(
            adapter,
            "_call_gemini",
            AsyncMock(
                side_effect=[
                    _response_with_function_call(
                        "band_lookup_peers", {"page": "1"}, "call_1"
                    ),
                    _response_with_text("done"),
                ]
            ),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.execute_tool_call_structured.assert_called_once()

    def test_extract_candidate_content_preserves_function_call_id_in_fallback(self):
        adapter = GeminiAdapter()
        response = MagicMock()
        response.candidates = [MagicMock(content=None)]
        response.function_calls = [
            types.FunctionCall(name="band_lookup_peers", args={"page": "1"}, id="c1")
        ]

        content = adapter._extract_candidate_content(response)

        assert content is not None
        assert content.role == "model"
        assert len(content.parts) == 1
        function_call = content.parts[0].function_call
        assert function_call is not None
        assert function_call.id == "c1"
        assert function_call.name == "band_lookup_peers"
        assert function_call.args == {"page": "1"}
