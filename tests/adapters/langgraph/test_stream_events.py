from __future__ import annotations

import json

import pytest

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import AdapterFeatures, Emit
from band.integrations.langgraph.langchain_tools import TOOL_EXECUTION_ERROR_PREFIX


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
            "name": "custom_search",
            "run_id": "run-123",
            "data": {"input": {"query": "Hello"}},
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
            "name": "custom_search",
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
            "name": "custom_search",
            "run_id": "run-123",
            "data": {"error": "missing query"},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "tool_result"
        payload = json.loads(call_kwargs["content"])
        assert payload["is_error"] is True
        assert payload["output"] == "missing query"

    @pytest.mark.asyncio
    async def test_reports_platform_tool_errors_as_error_events(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Failed platform tools should not produce orphan tool_result events."""
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
        assert call_kwargs["message_type"] == "error"
        assert call_kwargs["content"] == "band_send_message failed: missing mentions"

    @pytest.mark.asyncio
    async def test_reports_platform_tool_wrapper_error_outputs_as_error_events(
        self, mock_tools, mock_llm, mock_checkpointer
    ):
        """Platform tool wrapper failures arrive as tool_end output strings."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )

        event = {
            "event": "on_tool_end",
            "name": "band_send_message",
            "run_id": "run-123",
            "data": {
                "output": f"{TOOL_EXECUTION_ERROR_PREFIX}band_send_message: see agent logs."
            },
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_awaited_once()
        call_kwargs = mock_tools.send_event.call_args.kwargs
        assert call_kwargs["message_type"] == "error"
        assert (
            call_kwargs["content"]
            == "band_send_message failed: Error executing band_send_message: see agent logs."
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["band_send_message", "band_send_event"])
    @pytest.mark.parametrize("event_type", ["on_tool_start", "on_tool_end"])
    async def test_skips_reporting_for_platform_tools_with_visible_output(
        self, event_type, tool_name, mock_tools, mock_llm, mock_checkpointer
    ):
        """Visible platform tools should not be mirrored as execution events."""
        adapter = LangGraphAdapter(
            llm=mock_llm,
            checkpointer=mock_checkpointer,
            features=AdapterFeatures(emit=frozenset({Emit.EXECUTION})),
        )

        event = {
            "event": event_type,
            "name": tool_name,
            "run_id": "run-123",
            "data": {"output": "success"},
        }

        await adapter._handle_stream_event(event, "room-123", mock_tools)

        mock_tools.send_event.assert_not_awaited()

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
