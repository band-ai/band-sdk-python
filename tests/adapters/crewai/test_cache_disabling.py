from __future__ import annotations

import pytest

from band.core.types import Capability
from tests.adapters.crewai.kit import EchoInput, echo_message


class TestCacheDisabling:
    """Regression tests for CrewAI CacheHandler bypass.

    CrewAI's CacheHandler caches by (tool_name, input_string) globally — not
    per-room.  Since room_id lives in a ContextVar, the same tool+input across
    two rooms would return stale cached results.  The fix sets
    ``cache_function = lambda *a, **kw: False`` on every tool so the handler
    never caches.
    """

    @pytest.mark.asyncio
    async def test_all_crewai_platform_tools_disable_cache(
        self, CrewAIAdapter, crewai_mocks
    ):
        """Every band_* platform tool must have cache_function returning False."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            capabilities=Capability.CONTACTS | Capability.MEMORY,
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        platform_tools = [t for t in tools if t.name.startswith("band_")]

        assert len(platform_tools) > 0, "Expected at least one band_* tool"

        for tool in platform_tools:
            assert callable(tool.cache_function), (
                f"Tool {tool.name}: cache_function is not callable"
            )
            assert tool.cache_function({"arg": "val"}, "result") is False, (
                f"Tool {tool.name}: cache_function should return False"
            )

    @pytest.mark.asyncio
    async def test_custom_crewai_tools_disable_cache(self, CrewAIAdapter, crewai_mocks):
        """Custom tools passed via additional_tools must also disable cache."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(
            additional_tools=[(EchoInput, echo_message)],
        )
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        echo_tool = next((t for t in tools if t.name == "echo"), None)
        assert echo_tool is not None, "Expected 'echo' tool in tool list"

        assert callable(echo_tool.cache_function), (
            "Custom tool cache_function is not callable"
        )
        assert echo_tool.cache_function({"message": "hi"}, "Echo: hi") is False, (
            "Custom tool cache_function should return False"
        )
