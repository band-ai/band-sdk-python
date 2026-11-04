"""OnStarted behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        """Should render system prompt from agent metadata."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        """Should use custom system_prompt instead of rendered one."""
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(system_prompt="Custom prompt here.")
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter._system_prompt == "Custom prompt here."

    @pytest.mark.asyncio
    async def test_sets_agent_name(self):
        """Should set agent_name on the adapter."""
        adapter = GoogleADKAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot"
