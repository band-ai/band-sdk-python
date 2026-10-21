from __future__ import annotations

import pytest

from band.adapters.crewai import (
    CrewAIAdapterConfig,
)


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_creates_crewai_agent(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        crewai_mocks.Agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_custom_role_goal_backstory(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            CrewAIAdapterConfig(
                role="Research Analyst",
                goal="Find information",
                backstory="Expert researcher",
            )
        )

        await adapter.on_started(agent_name="TestBot", agent_description="")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["role"] == "Research Analyst"
        assert call_kwargs["goal"] == "Find information"
        assert "Expert researcher" in call_kwargs["backstory"]

    @pytest.mark.asyncio
    async def test_builds_llm_and_iteration_limit_from_config(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()
        crewai_mocks.LLM.reset_mock()

        adapter = CrewAIAdapter(CrewAIAdapterConfig(model="gpt-5.6-sol", max_iter=7))
        await adapter.on_started(agent_name="TestBot", agent_description="")

        crewai_mocks.LLM.assert_called_once_with(model="gpt-5.6-sol")
        assert crewai_mocks.Agent.call_args[1]["max_iter"] == 7

    @pytest.mark.asyncio
    async def test_uses_agent_name_as_default_role(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["role"] == "TestBot"

    @pytest.mark.asyncio
    async def test_creates_platform_tools(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # Check for required platform tools (don't check exact count to avoid brittleness)
        tool_names = [t.name for t in tools]
        required_tools = [
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_get_participants",
            "band_lookup_peers",
            "band_create_chatroom",
        ]
        for tool_name in required_tools:
            assert tool_name in tool_names, f"Missing required tool: {tool_name}"

    @pytest.mark.asyncio
    async def test_includes_platform_instructions_in_backstory(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        backstory = call_kwargs["backstory"]

        assert "Multi-participant chat" in backstory
        assert "band_send_message" in backstory
        assert "band_lookup_peers" in backstory
