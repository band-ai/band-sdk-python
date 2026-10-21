from __future__ import annotations

import pytest

from band.core.types import Capability


class TestContactAndMemoryToolRegistration:
    @pytest.mark.asyncio
    async def test_contact_tools_are_excluded_by_default(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "band_list_contacts" not in tool_names
        assert "band_add_contact" not in tool_names
        assert "band_remove_contact" not in tool_names
        assert "band_list_contact_requests" not in tool_names
        assert "band_respond_contact_request" not in tool_names

    @pytest.mark.asyncio
    async def test_contact_tools_are_included_when_enabled(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            capabilities=Capability.CONTACTS,
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "band_list_contacts" in tool_names
        assert "band_add_contact" in tool_names
        assert "band_remove_contact" in tool_names
        assert "band_list_contact_requests" in tool_names
        assert "band_respond_contact_request" in tool_names

    @pytest.mark.asyncio
    async def test_memory_tools_are_excluded_by_default(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "band_list_memories" not in tool_names
        assert "band_store_memory" not in tool_names
        assert "band_get_memory" not in tool_names
        assert "band_supersede_memory" not in tool_names
        assert "band_archive_memory" not in tool_names

    @pytest.mark.asyncio
    async def test_memory_tools_are_included_when_enabled(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        tool_names = {tool.name for tool in tools}

        assert "band_list_memories" in tool_names
        assert "band_store_memory" in tool_names
        assert "band_get_memory" in tool_names
        assert "band_supersede_memory" in tool_names
        assert "band_archive_memory" in tool_names
