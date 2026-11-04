"""Initialization behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest

from band.core.types import Capability, Emit
from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig


class TestInitialization:
    """Tests for adapter initialization."""

    @pytest.mark.asyncio
    async def test_runner_agent_uses_configured_model(self, mock_tools):
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(model="gemini-2.5-pro"))
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        runner = adapter._create_runner(mock_tools)

        assert runner.agent.model.model == "gemini-2.5-pro"
        await runner.close()

    @pytest.mark.asyncio
    async def test_custom_section_is_rendered_into_the_prompt(self):
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(custom_section="Be helpful."))
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")

        assert "Be helpful." in adapter._system_prompt

    @pytest.mark.parametrize("field", ["max_history_messages", "max_transcript_chars"])
    def test_history_bounds_must_be_positive(self, field):
        with pytest.raises(
            ValueError, match=f"{field}\n  Input should be greater than 0"
        ):
            GoogleADKAdapterConfig(**{field: 0})

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            (-1, "max_retries\n  Input should be greater than or equal to 0"),
            (11, "max_retries\n  Input should be less than or equal to 10"),
        ],
    )
    def test_max_retries_rejects_out_of_range_budgets(self, value: int, match: str):
        with pytest.raises(ValueError, match=match):
            GoogleADKAdapterConfig(max_retries=value)

    def test_execution_reporting_default(self):
        """Should default emit to everything the adapter supports."""
        adapter = GoogleADKAdapter()
        assert Emit.TOOL_CALLS in adapter.features.emit

    def test_memory_tools_default(self):
        """Should default memory tools to False."""
        adapter = GoogleADKAdapter()
        assert Capability.MEMORY not in adapter.features.capabilities

    def test_empty_custom_tools(self):
        """Should default to empty custom tools list."""
        adapter = GoogleADKAdapter()
        assert adapter._custom_tools == []

    def test_history_converter_set(self):
        """Should have history converter set by default."""
        adapter = GoogleADKAdapter()
        assert adapter.history_converter is not None
