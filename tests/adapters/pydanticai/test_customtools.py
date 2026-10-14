"""CustomTools behavior for Pydantic AI."""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.core.protocols import AgentToolsProtocol, TurnResultAlreadyReported
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.pydanticai.kit import _streamed_tool_calls, make_stream_events
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class TestCustomTools:
    """Tests for custom tool support (PydanticAI-native functions)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("declared", "reported"), [(True, []), (False, [MISSING_REPLY_FAILURE])]
    )
    async def test_native_tool_counts_only_the_effect_it_declared(
        self, declared: bool, reported: list
    ):
        """A native tool runs outside the portable custom-tool path, so its
        declared effect still has to reach the turn; an undeclared one only
        observes."""

        async def finish(ctx: RunContext[AgentToolsProtocol], note: str) -> str:
            """Finish the task."""
            return "done"

        if declared:
            declares_turn_effect(TurnEffect.ACT)(finish)
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="test"), additional_tools=[finish]
        )
        await adapter.on_started("Probe", "probe")
        adapter._agent.model = _streamed_tool_calls(("finish", {"note": "go"}))
        tools = FakeAgentTools(room_id=ROOM_ID)

        with contextlib.suppress(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == reported

    @pytest.mark.asyncio
    async def test_a_native_tool_failure_value_leaves_the_turn_unanswered(self) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def finish(
            ctx: RunContext[AgentToolsProtocol], note: str
        ) -> dict[str, Any]:
            """Finish the task."""
            return {"ok": False, "error": "upstream refused"}

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="test"), additional_tools=[finish]
        )
        await adapter.on_started("Probe", "probe")
        adapter._agent.model = _streamed_tool_calls(("finish", {"note": "go"}))
        tools = FakeAgentTools(room_id=ROOM_ID)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    def test_accepts_additional_tools_parameter(self):
        """Adapter accepts list of callables."""

        async def my_tool(ctx: RunContext[AgentToolsProtocol], message: str) -> str:
            """A custom tool."""
            return f"Echo: {message}"

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), additional_tools=[my_tool]
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0] == my_tool

    def test_multiple_custom_tools(self):
        """Should accept multiple custom tools."""

        async def tool_one(ctx: RunContext[AgentToolsProtocol], a: int) -> int:
            """Tool one."""
            return a + 1

        def tool_two(ctx: RunContext[AgentToolsProtocol], b: str) -> str:
            """Tool two."""
            return b.upper()

        async def tool_three(
            ctx: RunContext[AgentToolsProtocol], x: float, y: float
        ) -> float:
            """Tool three."""
            return x + y

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"),
            additional_tools=[tool_one, tool_two, tool_three],
        )

        assert len(adapter._custom_tools) == 3

    @pytest.mark.asyncio
    async def test_registers_custom_tools_with_agent(self):
        """Custom tools should be registered via agent.tool()."""

        async def my_echo(ctx: RunContext[AgentToolsProtocol], message: str) -> str:
            """Echo the message."""
            return f"Echo: {message}"

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="test"), additional_tools=[my_echo]
        )

        # Mock the Agent class to track tool registrations
        registered_tools = []

        with patch("band.adapters.pydantic_ai.Agent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.tool = MagicMock(
                side_effect=lambda f: registered_tools.append(f)
            )
            MockAgent.return_value = mock_agent

            await adapter.on_started("TestBot", "Test bot")

        # Should have registered platform tools + custom tool
        tool_names = [t.__name__ for t in registered_tools]
        assert "my_echo" in tool_names

    @pytest.mark.asyncio
    async def test_custom_tool_appears_in_agent_function_tools(
        self, mock_pydantic_agent
    ):
        """Custom tool should appear in agent._function_tools after registration."""

        async def calculator(
            ctx: RunContext[AgentToolsProtocol], a: float, b: float
        ) -> float:
            """Add two numbers."""
            return a + b

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"),
            additional_tools=[calculator],
        )

        # Add calculator to mock agent's function tools when tool() is called
        def register_tool(func):
            mock_pydantic_agent._function_tools[func.__name__] = MagicMock(
                name=func.__name__
            )

        mock_pydantic_agent.tool = MagicMock(side_effect=register_tool)

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            # Manually call tool registration since we're mocking _create_agent
            for custom_tool in adapter._custom_tools:
                mock_pydantic_agent.tool(custom_tool)

        assert "calculator" in mock_pydantic_agent._function_tools

    @pytest.mark.asyncio
    async def test_custom_tools_work_with_on_message(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Custom tools should work during message handling."""

        async def my_helper(ctx: RunContext[AgentToolsProtocol], value: str) -> str:
            """Helper tool."""
            return f"Helped: {value}"

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"),
            additional_tools=[my_helper],
        )

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        result_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=result_messages,
                tool_results=[("band_send_message", "Message sent", "call-1")],
            )
        )

        # Should not raise
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
