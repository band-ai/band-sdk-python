from __future__ import annotations

import asyncio

import pytest

from band.core.types import Emit


class TestExecutionReporting:
    @pytest.mark.asyncio
    async def test_emit_kwarg_controls_tool_call_reporting(
        self, CrewAIAdapter, crewai_mocks
    ):
        adapter_enabled = CrewAIAdapter(emit=Emit.TOOL_CALLS)
        adapter_disabled = CrewAIAdapter(emit=())

        assert Emit.TOOL_CALLS in adapter_enabled.features.emit
        assert Emit.TOOL_CALLS not in adapter_disabled.features.emit

    def test_reports_tool_call_when_enabled(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(emit=Emit.TOOL_CALLS)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "band_send_message")

        with room_context("room-123"):
            send_message_tool._run(content="Hello!", mentions=[])

        assert mock_tools.send_event.call_count >= 2

    @pytest.mark.asyncio
    async def test_report_tool_call_403_does_not_crash(
        self, CrewAIAdapter, crewai_mocks, mock_tools
    ):
        """send_event 403 in EmitToolCallsReporter.report_call should not propagate."""
        from band.integrations.crewai import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
            EmitToolCallsReporter,
        )

        adapter = CrewAIAdapter(emit=Emit.TOOL_CALLS)
        reporter = EmitToolCallsReporter(adapter.features)
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        # Should not raise
        await reporter.report_call(mock_tools, "search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_report_tool_result_403_does_not_crash(
        self, CrewAIAdapter, crewai_mocks, mock_tools
    ):
        """send_event 403 in EmitToolCallsReporter.report_result should not propagate."""
        from band.integrations.crewai import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
            EmitToolCallsReporter,
        )

        adapter = CrewAIAdapter(emit=Emit.TOOL_CALLS)
        reporter = EmitToolCallsReporter(adapter.features)
        mock_tools.send_event.side_effect = Exception("403 Forbidden")

        # Should not raise
        await reporter.report_result(mock_tools, "search", "some result")
        await reporter.report_result(mock_tools, "search", "some error", is_error=True)
