from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import Capability
from band.runtime.custom_tools import get_custom_tool_name


class TestCustomTools:
    """Tests for custom tool support (CustomToolDef → MCP)."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter accepts list of CustomToolDef tuples."""

        class EchoInput(BaseModel):
            """Echo the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
        )

        assert len(adapter._custom_tools) == 1
        assert adapter._custom_tools[0][0] is EchoInput

    def test_multiple_custom_tools(self):
        """Should accept multiple custom tools."""

        class Tool1Input(BaseModel):
            """Tool 1."""

            x: int

        class Tool2Input(BaseModel):
            """Tool 2."""

            y: str

        def tool1(args: Tool1Input) -> int:
            return args.x + 1

        def tool2(args: Tool2Input) -> str:
            return args.y.upper()

        adapter = ClaudeSDKAdapter(
            additional_tools=[(Tool1Input, tool1), (Tool2Input, tool2)],
        )

        assert len(adapter._custom_tools) == 2

    @pytest.mark.asyncio
    async def test_custom_tools_added_to_allowed_tools(self):
        """Custom tools should be added to allowed_tools list."""

        class CalculatorInput(BaseModel):
            """Perform calculations."""

            a: float
            b: float

        def calc(args: CalculatorInput) -> float:
            return args.a + args.b

        adapter = ClaudeSDKAdapter(
            additional_tools=[(CalculatorInput, calc)],
        )

        # Mock the session manager creation
        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            # Capture the ClaudeAgentOptions passed to session manager
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            # Get the options passed to ClaudeSessionManager
            call_args = mock_manager_class.call_args
            sdk_options = call_args[0][0]

            # Verify custom tool is in allowed_tools
            assert "mcp__band__calculator" in sdk_options.allowed_tools
            # Platform tools should still be there
            assert "mcp__band__band_send_message" in sdk_options.allowed_tools

    @pytest.mark.asyncio
    async def test_custom_tools_registered_in_mcp_server(self):
        """Custom tools should be registered in MCP server (memory tools disabled)."""

        class EchoInput(BaseModel):
            """Echo tool."""

            message: str

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
        )

        mock_backend = MagicMock()
        mock_backend.allowed_tools = [f"tool-{i}" for i in range(13)]
        mock_backend.server = MagicMock()

        with patch(
            "band.adapters.claude_sdk.create_band_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools registered
        assert "band_send_message" in tool_names
        assert "band_send_event" in tool_names
        assert "band_add_participant" in tool_names
        assert "band_remove_participant" in tool_names
        assert "band_get_participants" in tool_names
        assert "band_lookup_peers" in tool_names
        assert "band_create_chatroom" in tool_names
        # Memory and contacts excluded (no capabilities set)
        assert "band_list_contacts" not in tool_names
        assert "band_list_memories" not in tool_names

    @pytest.mark.asyncio
    async def test_custom_tools_registered_with_memory_tools_enabled(self):
        """Custom tools should be registered in MCP server (memory tools enabled)."""

        class EchoInput(BaseModel):
            """Echo tool."""

            message: str

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = ClaudeSDKAdapter(
            additional_tools=[(EchoInput, echo)],
            capabilities=Capability.MEMORY,
        )

        mock_backend = MagicMock()
        mock_backend.allowed_tools = [f"tool-{i}" for i in range(18)]
        mock_backend.server = MagicMock()

        with patch(
            "band.adapters.claude_sdk.create_band_mcp_backend",
            new=AsyncMock(return_value=mock_backend),
        ) as mock_create_backend:
            backend = await adapter._create_mcp_backend()

        assert backend is mock_backend
        mock_create_backend.assert_awaited_once()
        tool_definitions = mock_create_backend.await_args.kwargs["tool_definitions"]
        tool_names = [td.name for td in tool_definitions]
        # Base platform tools
        assert "band_send_message" in tool_names
        assert "band_create_chatroom" in tool_names
        # Memory tools included
        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_get_memory" in tool_names
        # Contacts excluded (not in capabilities)
        assert "band_list_contacts" not in tool_names

    def test_tool_name_derived_from_input_model(self):
        """Tool name should be derived from Pydantic model class name."""

        class MyCustomToolInput(BaseModel):
            """A custom tool."""

            value: str

        name = get_custom_tool_name(MyCustomToolInput)
        assert name == "mycustomtool"

        class CalculatorInput(BaseModel):
            """Calculator."""

            x: int

        name = get_custom_tool_name(CalculatorInput)
        assert name == "calculator"
