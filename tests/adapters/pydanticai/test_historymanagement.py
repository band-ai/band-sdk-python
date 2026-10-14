"""HistoryManagement behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from band.adapters.pydantic_ai import (
    PydanticAIAdapter,
    PydanticAIAdapterConfig,
    _drop_non_replayable_messages,
    _is_replayable_history_message,
)
from tests.adapters.pydanticai.kit import make_stream_events


class TestHistoryManagement:
    """Tests for message history management."""

    @pytest.mark.asyncio
    async def test_updates_history_after_run(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should update stored history with all messages from run."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        new_messages = [
            ModelRequest(parts=[UserPromptPart(content="Q1")]),
            ModelResponse(parts=[TextPart(content="A1")]),
        ]

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=new_messages,
                tool_results=[("band_send_message", "Message sent", "call-1")],
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

        assert adapter._message_history["room-123"] == new_messages

    @pytest.mark.asyncio
    async def test_keeps_native_history_and_drops_content_null_responses(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should keep native tool history but drop responses that replay as null."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        user_request = ModelRequest(parts=[UserPromptPart(content="Q1")])
        tool_call_response = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="band_send_message",
                    args={"content": "A1", "mentions": ["Alice"]},
                    tool_call_id="call_1",
                )
            ]
        )
        tool_return_request = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="band_send_message",
                    content={"id": "msg_1"},
                    tool_call_id="call_1",
                )
            ]
        )
        content_null_response = ModelResponse(parts=[])
        text_response = ModelResponse(parts=[TextPart(content="A1")])
        result_messages = [
            user_request,
            tool_call_response,
            tool_return_request,
            content_null_response,
            text_response,
        ]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=result_messages,
                tool_results=[("band_send_message", {"id": "msg_1"}, "call_1")],
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

        stored_history = adapter._message_history["room-123"]
        assert stored_history == [
            user_request,
            tool_call_response,
            tool_return_request,
            text_response,
        ]
        assert content_null_response not in stored_history

    def test_keeps_response_with_only_native_tool_part(self):
        """Native tool calls carry content the provider expects — keep them."""
        response = ModelResponse(
            parts=[
                NativeToolCallPart(
                    tool_name="web_search",
                    args={"query": "weather"},
                    tool_call_id="call_1",
                )
            ]
        )

        assert _is_replayable_history_message(response) is True

    def test_history_processor_strips_content_null_responses(self):
        """The processor drops empty/thinking-only responses, keeps real content.

        This runs before every model request (mid-run included), so an empty or
        thinking-only response the model emits within a turn is never replayed as
        assistant content:null — which providers reject.
        """
        user_request = ModelRequest(parts=[UserPromptPart(content="Q1")])
        tool_call = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="band_send_message",
                    args={"content": "hi", "mentions": ["Alice"]},
                    tool_call_id="call_1",
                )
            ]
        )
        tool_return = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="band_send_message",
                    content={"id": "msg_1"},
                    tool_call_id="call_1",
                )
            ]
        )
        empty_response = ModelResponse(parts=[])
        thinking_only = ModelResponse(parts=[ThinkingPart(content="hmm")])
        text_response = ModelResponse(parts=[TextPart(content="done")])

        processed = _drop_non_replayable_messages(
            [
                user_request,
                tool_call,
                tool_return,
                empty_response,
                thinking_only,
                text_response,
            ]
        )

        assert processed == [user_request, tool_call, tool_return, text_response]
        assert empty_response not in processed
        assert thinking_only not in processed

    @pytest.mark.asyncio
    async def test_ensures_history_exists_for_non_bootstrap(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should create history if not bootstrap and room doesn't exist."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_results=[("band_send_message", "Message sent", "call-1")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,  # Not bootstrap
            room_id="new-room",
        )

        # Should have created empty history
        assert "new-room" in adapter._message_history
