"""OnStarted behavior for Gemini."""

from __future__ import annotations

import pytest

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_renders_system_prompt(self):
        adapter = GeminiAdapter()
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_when_provided(self):
        adapter = GeminiAdapter(
            GeminiAdapterConfig(
                system_prompt="Custom prompt here.", custom_section="Be terse."
            )
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        assert adapter._system_prompt == "Custom prompt here."
