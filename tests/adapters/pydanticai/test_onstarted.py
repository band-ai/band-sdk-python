"""OnStarted behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.core.types import Capability


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_sets_agent_name_and_description(self, mock_pydantic_agent):
        """Should set agent_name and agent_description."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot"

    @pytest.mark.asyncio
    async def test_creates_pydantic_agent(self, mock_pydantic_agent):
        """Should create Pydantic AI agent after start."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        assert adapter._agent is None

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._agent is not None

    @pytest.mark.asyncio
    async def test_persists_rendered_system_prompt(self):
        """Should persist rendered prompt for capability-gating visibility."""
        with patch("band.adapters.pydantic_ai.Agent"):
            adapter = PydanticAIAdapter(
                PydanticAIAdapterConfig(model="test"),
                capabilities=Capability.MEMORY,
            )
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._system_prompt is not None
        assert "Memory Tools" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_agent_has_tools_registered(self, mock_pydantic_agent):
        """Should register all platform tools on the agent."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        # Get registered tool names
        tool_names = list(adapter._agent._function_tools.keys())

        expected_tools = [
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_lookup_peers",
            "band_get_participants",
            "band_create_chatroom",
        ]

        for tool in expected_tools:
            assert tool in tool_names, f"Tool {tool} not found"
