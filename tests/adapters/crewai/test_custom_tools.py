from __future__ import annotations

import asyncio
import json

import pytest

from band.core.types import Emit
from tests.adapters.crewai.kit import (
    CalculatorInput,
    EchoInput,
    calculate,
    echo_message,
    failing_tool,
)


class TestCustomTools:
    def test_accepts_additional_tools_parameter(self, CrewAIAdapter):
        """Adapter should accept list of (Model, func) tuples."""
        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_accepts_multiple_custom_tools(self, CrewAIAdapter):
        """Adapter should accept multiple custom tools."""
        adapter = CrewAIAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_custom_tools_converted_to_crewai_format(
        self, CrewAIAdapter, crewai_mocks
    ):
        """Custom tools should be converted to CrewAI BaseTool instances."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check that custom tool is included alongside platform tools
        tool_names = [t.name for t in tools]
        assert "band_send_message" in tool_names  # Platform tool should exist
        assert "echo" in tool_names  # Custom tool should exist

        # Find the echo tool
        echo_tool = next((t for t in tools if t.name == "echo"), None)
        assert echo_tool is not None
        assert echo_tool.description == "Echo back the provided message."
        assert echo_tool.args_schema is EchoInput

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_in_agent(self, CrewAIAdapter, crewai_mocks):
        """Multiple custom tools should all be available to the agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[
                (EchoInput, echo_message),
                (CalculatorInput, calculate),
            ],
        )
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check that both custom tools are included alongside platform tools
        tool_names = [t.name for t in tools]
        assert "band_send_message" in tool_names  # Platform tool should exist
        assert "echo" in tool_names  # Custom tool should exist
        assert "calculator" in tool_names  # Custom tool should exist

    def test_custom_tool_execution_async(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Async custom tool should execute correctly."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            result = echo_tool._run(message="Hello world")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "Echo: Hello world" in result_data["result"]

    def test_custom_tool_execution_sync(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Sync custom tool should execute correctly."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(CalculatorInput, calculate)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        calc_tool = next(t for t in tools if t.name == "calculator")

        with room_context("room-123"):
            result = calc_tool._run(operation="add", left=5.0, right=3.0)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "8.0" in result_data["result"]

    def test_custom_tool_error_handling(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Custom tool exception should result in error response."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, failing_tool)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            result = echo_tool._run(message="test")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "Service unavailable" in result_data["message"]

    def test_custom_tool_reports_execution_when_enabled(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Custom tool should report tool_call and tool_result events when enabled."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            emit=Emit.TOOL_CALLS,
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        with room_context("room-123"):
            echo_tool._run(message="Hello!")

        # Should have called send_event for tool_call and tool_result
        assert mock_tools.send_event.call_count >= 2

    def test_custom_tool_without_room_context(self, CrewAIAdapter, crewai_mocks):
        """Custom tool should return error when called without room context."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        echo_tool = next(t for t in tools if t.name == "echo")

        # Call without setting context
        result = echo_tool._run(message="Hello!")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "No room context available" in result_data["message"]
