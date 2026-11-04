"""EmitToolCallsReporter behavior at the CrewAI tool boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.types import AdapterFeatures, Emit
from tests.crewaicontract import tool_result_data


class TestEmitToolCallsReporter:
    @pytest.mark.asyncio
    async def test_does_not_emit_when_tool_calls_unset(self, builder_mod):

        features = AdapterFeatures()  # empty emit set
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        tools.send_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_when_tool_calls_set(self, builder_mod):

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result")

        assert tools.send_event.call_count == 2

    @pytest.mark.asyncio
    async def test_emits_canonical_event_schema(self, builder_mod):
        """The emitted payloads must use the canonical name/args/output schema.

        Every framework's tool_call / tool_result events are read back through
        the shared ``parse_tool_call`` / ``parse_tool_result`` (and the E2E
        observer), which key off ``name`` / ``args`` / ``output``. crewai once
        emitted ``tool`` / ``input`` / ``result`` instead, so its tool events
        were silently dropped on read. Pin the schema here so a count-only
        assertion can't let that drift back in.
        """

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_call(tools, "lookup", {"key": "alpha"})
        await reporter.report_result(tools, "lookup", "SECRET-123")

        call_kwargs, result_kwargs = (c.kwargs for c in tools.send_event.call_args_list)

        assert call_kwargs["message_type"] == "tool_call"
        assert tool_result_data(call_kwargs["content"]) == {
            "name": "lookup",
            "args": {"key": "alpha"},
        }

        assert result_kwargs["message_type"] == "tool_result"
        assert tool_result_data(result_kwargs["content"]) == {
            "name": "lookup",
            "output": "SECRET-123",
            "is_error": False,
        }

    @pytest.mark.asyncio
    async def test_error_result_sets_is_error(self, builder_mod):

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock()

        await reporter.report_result(tools, "lookup", "boom", is_error=True)

        assert tool_result_data(tools.send_event.call_args.kwargs["content"]) == {
            "name": "lookup",
            "output": "boom",
            "is_error": True,
        }

    @pytest.mark.asyncio
    async def test_send_event_failure_does_not_propagate(self, builder_mod):

        features = AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS}))
        reporter = builder_mod.EmitToolCallsReporter(features)
        tools = MagicMock()
        tools.send_event = AsyncMock(side_effect=Exception("403 Forbidden"))

        # Both must not raise
        await reporter.report_call(tools, "tool", {"k": "v"})
        await reporter.report_result(tools, "tool", "result", is_error=True)
