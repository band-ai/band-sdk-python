from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import (
    USAGE_EVENT_TYPE,
    USAGE_METADATA_KEY,
    AdapterFeatures,
    Emit,
    TurnUsage,
)


class TestUsageReporting:
    """Tests for the Emit.USAGE seam (usage mapping + emission)."""

    def test_usage_from_on_chat_model_end(self):
        """Maps AIMessage.usage_metadata (incl. cache token details) to TurnUsage."""
        output = SimpleNamespace(
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "input_token_details": {"cache_read": 5, "cache_creation": 3},
            }
        )
        event = {"event": "on_chat_model_end", "data": {"output": output}}
        assert LangGraphAdapter._usage_from_stream_event(event) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_usage_from_non_model_event_is_empty(self):
        """Non-model events (and non-dict events) contribute empty usage."""
        assert (
            LangGraphAdapter._usage_from_stream_event(
                {"event": "on_tool_end", "data": {}}
            )
            == TurnUsage()
        )
        assert LangGraphAdapter._usage_from_stream_event("not a dict") == TurnUsage()

    @pytest.mark.asyncio
    async def test_emits_usage_event_when_enabled(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """With Emit.USAGE on, a non-empty TurnUsage rides a task event's metadata."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.USAGE})),
        )
        await adapter.emit_usage(
            mock_tools, TurnUsage(input_tokens=100, output_tokens=20)
        )
        mock_tools.send_event.assert_awaited_once()
        kwargs = mock_tools.send_event.call_args.kwargs
        assert kwargs["message_type"] == USAGE_EVENT_TYPE
        assert kwargs["metadata"][USAGE_METADATA_KEY]["input_tokens"] == 100


class TestStreamEventHandling:
    """Tests for _handle_stream_event() method."""

    @pytest.mark.asyncio
    async def test_handles_on_tool_start(self, mock_tools, mock_llm, mock_checkpointer):
        """Should send tool_call event on on_tool_start."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )

        event = {
            "event": "on_tool_start",
            "name": "band_send_message",
            "run_id": "run-123",
            "data": {"input": {"content": "Hello"}},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_handles_on_tool_end(self, mock_tools, mock_llm, mock_checkpointer):
        """Should send tool_result event on on_tool_end."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )

        event = {
            "event": "on_tool_end",
            "name": "band_send_message",
            "run_id": "run-123",
            "data": {"output": "success"},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_result"
        payload = json.loads(call_kwargs["content"])
        assert payload["is_error"] is False

    @pytest.mark.asyncio
    async def test_handles_on_tool_error(self, mock_tools, mock_llm, mock_checkpointer):
        """Failed tools should be visible as error tool_results."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )

        event = {
            "event": "on_tool_error",
            "name": "band_send_message",
            "run_id": "run-123",
            "data": {"error": "missing mentions"},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_result"
        payload = json.loads(call_kwargs["content"])
        assert payload["is_error"] is True
        assert payload["output"] == "missing mentions"

    @pytest.mark.asyncio
    async def test_ignores_other_events(self, mock_tools, mock_llm, mock_checkpointer):
        """Should ignore events other than tool_start/end."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        event = {
            "event": "on_chat_model_start",
            "name": "ChatOpenAI",
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_malformed_events(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Malformed stream payloads should not crash event handling."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        await adapter._handle_stream_event(["not", "a", "dict"], "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_emit_when_execution_feature_off(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Execution stream events are gated by Emit.EXECUTION."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
        )

        event = {
            "event": "on_tool_start",
            "name": "band_send_message",
            "run_id": "run-123",
            "data": {"input": {"content": "Hello"}},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()

    def test_enable_execution_reporting_shim_enables_execution_emit(
        self, mock_llm, mock_checkpointer
    ):
        """Legacy execution-reporting flag maps to Emit.EXECUTION."""
        with pytest.warns(DeprecationWarning):
            adapter = LangGraphAdapter(
                llm=mock_llm,
                checkpointer=mock_checkpointer,
                enable_execution_reporting=True,
            )

        assert Emit.EXECUTION in adapter.features.emit
