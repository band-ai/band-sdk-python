"""End-to-end test pinning the capability-gating contract.

The render_system_prompt function gates memory and contact tool sections
behind ``AdapterFeatures.capabilities``. Adapters must forward
``self.features`` to ``render_system_prompt()`` in ``on_started()`` for
the gating to take effect. This test verifies the round trip: when an
adapter is constructed with Capability.MEMORY, the actual rendered system
prompt contains the Memory Tools section.

Without this test, the capability-gating mechanism could silently fail
if a future adapter forgets to forward the features parameter.
"""

from __future__ import annotations

import pytest

from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from thenvoi.core.types import AdapterFeatures, Capability


@pytest.mark.asyncio
class TestCapabilityGatingEndToEnd:
    async def test_anthropic_adapter_renders_memory_section_when_enabled(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" in adapter._system_prompt
        assert "thenvoi_store_memory" in adapter._system_prompt

    async def test_anthropic_adapter_omits_memory_section_when_disabled(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter()
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" not in adapter._system_prompt

    async def test_anthropic_adapter_renders_contacts_section_when_enabled(
        self,
    ) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(
            features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "## Contact Management Tools" in adapter._system_prompt

    async def test_anthropic_adapter_renders_both_sections_when_both_enabled(
        self,
    ) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS}
            ),
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" in adapter._system_prompt
        assert "## Contact Management Tools" in adapter._system_prompt

    async def test_gemini_adapter_renders_memory_section_when_enabled(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        adapter = GeminiAdapter(
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" in adapter._system_prompt

    async def test_langgraph_adapter_renders_memory_section_when_enabled(self) -> None:
        from unittest.mock import MagicMock

        from thenvoi.adapters.langgraph import LangGraphAdapter

        adapter = LangGraphAdapter(
            llm=MagicMock(),
            checkpointer=MagicMock(),
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" in adapter._system_prompt

    async def test_pydantic_ai_adapter_renders_memory_section_when_enabled(
        self,
    ) -> None:
        """PydanticAI on_started requires a live OpenAI client; skip without API key.

        We still cover the contract via Anthropic + Gemini + LangGraph above.
        """
        import os

        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("PydanticAIAdapter requires OPENAI_API_KEY to start")

        from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

        adapter = PydanticAIAdapter(
            model="openai:gpt-4o",
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        await adapter.on_started("test-agent", "A test agent")

        rendered = getattr(adapter, "_system_prompt", None) or getattr(
            adapter, "system_prompt", None
        )
        assert rendered is not None, "PydanticAIAdapter does not expose rendered prompt"
        assert "## Memory Tools" in rendered

    async def test_anthropic_adapter_with_no_features_omits_capability_sections(
        self,
    ) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter()
        await adapter.on_started("test-agent", "A test agent")

        assert "## Memory Tools" not in adapter._system_prompt
        assert "## Contact Management Tools" not in adapter._system_prompt
        # But base instructions are still present
        assert "## Environment" in adapter._system_prompt

    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install band-sdk[claude_sdk])",
    )
    async def test_claude_sdk_adapter_renders_memory_section_when_enabled(
        self,
    ) -> None:
        """Claude SDK prompt should include memory tools section when MEMORY capability is set."""
        from thenvoi.integrations.claude_sdk.prompts import (
            generate_claude_sdk_agent_prompt,
        )

        prompt = generate_claude_sdk_agent_prompt(
            agent_name="test-agent",
            agent_description="A test agent",
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        assert "Memory Tools" in prompt["append"]
        assert "thenvoi_store_memory" in prompt["append"]

    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install band-sdk[claude_sdk])",
    )
    async def test_claude_sdk_adapter_omits_memory_section_when_disabled(
        self,
    ) -> None:
        from thenvoi.integrations.claude_sdk.prompts import (
            generate_claude_sdk_agent_prompt,
        )

        prompt = generate_claude_sdk_agent_prompt(
            agent_name="test-agent",
            agent_description="A test agent",
        )
        assert "Memory Tools" not in prompt["append"]

    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install band-sdk[claude_sdk])",
    )
    async def test_claude_sdk_adapter_renders_contacts_section_when_enabled(
        self,
    ) -> None:
        from thenvoi.integrations.claude_sdk.prompts import (
            generate_claude_sdk_agent_prompt,
        )

        prompt = generate_claude_sdk_agent_prompt(
            agent_name="test-agent",
            agent_description="A test agent",
            features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        )
        assert "Contact Management Tools" in prompt["append"]

    async def test_crewai_adapter_renders_memory_section_when_enabled(self) -> None:
        """CrewAI backstory should contain memory instructions when MEMORY capability is set."""
        from unittest.mock import MagicMock, patch

        with (
            patch("thenvoi.adapters.crewai.CrewAIAgent") as mock_agent_cls,
            patch("thenvoi.adapters.crewai.LLM"),
        ):
            mock_agent_cls.return_value = MagicMock()
            from thenvoi.adapters.crewai import CrewAIAdapter

            adapter = CrewAIAdapter(
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )
            await adapter.on_started("test-agent", "A test agent")

            backstory = mock_agent_cls.call_args[1]["backstory"]
            assert "Memory Tools" in backstory

    async def test_crewai_adapter_omits_memory_section_when_disabled(self) -> None:
        from unittest.mock import MagicMock, patch

        with (
            patch("thenvoi.adapters.crewai.CrewAIAgent") as mock_agent_cls,
            patch("thenvoi.adapters.crewai.LLM"),
        ):
            mock_agent_cls.return_value = MagicMock()
            from thenvoi.adapters.crewai import CrewAIAdapter

            adapter = CrewAIAdapter()
            await adapter.on_started("test-agent", "A test agent")

            backstory = mock_agent_cls.call_args[1]["backstory"]
            assert "Memory Tools" not in backstory

    async def test_anthropic_include_base_instructions_false_drops_base(self) -> None:
        """include_base_instructions=False renders identity without base instructions."""
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(
            prompt="Focus on Python.",
            include_base_instructions=False,
        )
        await adapter.on_started("test-agent", "A test agent")

        # Identity preserved
        assert "test-agent" in adapter._system_prompt
        # Custom section preserved
        assert "Focus on Python." in adapter._system_prompt
        # Base instructions stripped
        assert "## Environment" not in adapter._system_prompt
        assert "## Communication" not in adapter._system_prompt

    async def test_anthropic_include_base_instructions_false_still_gates_capabilities(
        self,
    ) -> None:
        """Capability sections respect include_base_instructions=False."""
        from thenvoi.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(
            include_base_instructions=False,
            features=AdapterFeatures(capabilities={Capability.MEMORY}),
        )
        await adapter.on_started("test-agent", "A test agent")

        # Without base instructions, capability sections are also absent
        # (they are part of the base instructions block)
        assert "## Environment" not in adapter._system_prompt
        assert "## Memory Tools" not in adapter._system_prompt

    async def test_gemini_include_base_instructions_false_drops_base(self) -> None:
        """GeminiAdapter honors include_base_instructions=False end-to-end."""
        from thenvoi.adapters.gemini import GeminiAdapter

        adapter = GeminiAdapter(
            prompt="Focus on Python.",
            include_base_instructions=False,
        )
        await adapter.on_started("test-agent", "A test agent")

        assert "test-agent" in adapter._system_prompt
        assert "Focus on Python." in adapter._system_prompt
        assert "## Environment" not in adapter._system_prompt
