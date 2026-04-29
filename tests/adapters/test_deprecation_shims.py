"""Tests pinning the deprecation-shim contract for adapter constructors.

These tests guarantee that the legacy boolean / api-key / prompt parameters
still work for one release with a clear DeprecationWarning. When the shims
are eventually removed, these tests should be deleted in the same commit.
"""

from __future__ import annotations

import pytest

from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.types import AdapterFeatures, Capability, Emit


class TestUniversalBooleanShims:
    """Every adapter with legacy booleans must shim them with DeprecationWarning."""

    def test_anthropic_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="enable_memory_tools"):
            adapter = AnthropicAdapter(enable_memory_tools=True)
        assert Capability.MEMORY in adapter.features.capabilities

    def test_anthropic_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="enable_execution_reporting"):
            adapter = AnthropicAdapter(enable_execution_reporting=True)
        assert Emit.EXECUTION in adapter.features.emit

    def test_anthropic_both_booleans_and_features_raises(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            AnthropicAdapter(
                enable_memory_tools=True,
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )

    def test_gemini_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="enable_memory_tools"):
            adapter = GeminiAdapter(enable_memory_tools=True)
        assert Capability.MEMORY in adapter.features.capabilities

    def test_gemini_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.warns(
            DeprecationWarning, match="enable_memory_tools|enable_execution_reporting"
        ):
            adapter = GeminiAdapter(enable_execution_reporting=True)
        assert Emit.EXECUTION in adapter.features.emit

    def test_gemini_both_booleans_and_features_raises(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            GeminiAdapter(
                enable_memory_tools=True,
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )

    def test_langgraph_enable_memory_tools_warns(self) -> None:
        from unittest.mock import MagicMock

        from thenvoi.adapters.langgraph import LangGraphAdapter

        with pytest.warns(DeprecationWarning, match="enable_memory_tools"):
            adapter = LangGraphAdapter(
                llm=MagicMock(), checkpointer=MagicMock(), enable_memory_tools=True
            )
        assert Capability.MEMORY in adapter.features.capabilities

    def test_pydantic_ai_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

        with pytest.warns(DeprecationWarning, match="enable_execution_reporting"):
            adapter = PydanticAIAdapter(
                model="openai:gpt-4o", enable_execution_reporting=True
            )
        assert Emit.EXECUTION in adapter.features.emit

    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install thenvoi-sdk[claude_sdk])",
    )
    def test_claude_sdk_enable_execution_reporting_maps_to_execution_and_thoughts(
        self,
    ) -> None:
        """ClaudeSDK historically emitted thoughts under enable_execution_reporting."""
        from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

        with pytest.warns(DeprecationWarning, match="enable_execution_reporting"):
            adapter = ClaudeSDKAdapter(enable_execution_reporting=True)
        assert Emit.EXECUTION in adapter.features.emit
        assert Emit.THOUGHTS in adapter.features.emit

    def test_crewai_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.crewai import CrewAIAdapter

        with pytest.warns(
            DeprecationWarning, match="enable_execution_reporting|enable_memory_tools"
        ):
            adapter = CrewAIAdapter(enable_execution_reporting=True)
        assert Emit.EXECUTION in adapter.features.emit

    def test_crewai_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.crewai import CrewAIAdapter

        with pytest.warns(
            DeprecationWarning, match="enable_execution_reporting|enable_memory_tools"
        ):
            adapter = CrewAIAdapter(enable_memory_tools=True)
        assert Capability.MEMORY in adapter.features.capabilities

    def test_crewai_both_booleans_and_features_raises(self) -> None:
        from thenvoi.adapters.crewai import CrewAIAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            CrewAIAdapter(
                enable_memory_tools=True,
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )

    def test_crewai_system_prompt_warns(self) -> None:
        from thenvoi.adapters.crewai import CrewAIAdapter

        with pytest.warns(DeprecationWarning, match="system_prompt.*deprecated"):
            CrewAIAdapter(system_prompt="Be a helpful agent.")

    def test_google_adk_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.google_adk import GoogleADKAdapter

        with pytest.warns(
            DeprecationWarning, match="enable_execution_reporting|enable_memory_tools"
        ):
            adapter = GoogleADKAdapter(enable_execution_reporting=True)
        assert Emit.EXECUTION in adapter.features.emit

    def test_google_adk_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.google_adk import GoogleADKAdapter

        with pytest.warns(
            DeprecationWarning, match="enable_execution_reporting|enable_memory_tools"
        ):
            adapter = GoogleADKAdapter(enable_memory_tools=True)
        assert Capability.MEMORY in adapter.features.capabilities

    def test_google_adk_both_booleans_and_features_raises(self) -> None:
        from thenvoi.adapters.google_adk import GoogleADKAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            GoogleADKAdapter(
                enable_execution_reporting=True,
                features=AdapterFeatures(emit={Emit.EXECUTION}),
            )

    def test_codex_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

        config = CodexAdapterConfig(enable_execution_reporting=True)
        with pytest.warns(DeprecationWarning, match="enable_execution_reporting"):
            adapter = CodexAdapter(config=config)
        assert Emit.EXECUTION in adapter.features.emit

    def test_codex_emit_thought_events_warns(self) -> None:
        from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

        config = CodexAdapterConfig(emit_thought_events=True)
        with pytest.warns(DeprecationWarning, match="emit_thought_events"):
            adapter = CodexAdapter(config=config)
        assert Emit.THOUGHTS in adapter.features.emit

    def test_codex_config_booleans_and_features_raises(self) -> None:
        from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

        config = CodexAdapterConfig(enable_execution_reporting=True)
        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            CodexAdapter(
                config=config,
                features=AdapterFeatures(emit={Emit.EXECUTION}),
            )

    def test_codex_default_config_has_task_events_without_warning(self) -> None:
        """Default CodexAdapterConfig enables task events without firing a warning."""
        import warnings as _warnings

        from thenvoi.adapters.codex import CodexAdapter

        with _warnings.catch_warnings():
            _warnings.simplefilter("error", DeprecationWarning)
            adapter = CodexAdapter()
        assert Emit.TASK_EVENTS in adapter.features.emit
        assert Emit.EXECUTION not in adapter.features.emit
        assert Emit.THOUGHTS not in adapter.features.emit

    def test_codex_explicit_features_honored(self) -> None:
        """Passing features= bypasses config-boolean mapping."""
        import warnings as _warnings

        from thenvoi.adapters.codex import CodexAdapter

        with _warnings.catch_warnings():
            _warnings.simplefilter("error", DeprecationWarning)
            adapter = CodexAdapter(features=AdapterFeatures(emit={Emit.EXECUTION}))
        assert Emit.EXECUTION in adapter.features.emit
        assert Emit.TASK_EVENTS not in adapter.features.emit

    def test_codex_supported_features_surface(self) -> None:
        """SUPPORTED_EMIT advertises the three Codex-supported emit channels."""
        from thenvoi.adapters.codex import CodexAdapter

        assert CodexAdapter.SUPPORTED_EMIT == frozenset(
            {Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS}
        )


class TestSelectiveRenameShims:
    """Anthropic and Gemini get the api_key/prompt selective renames."""

    def test_anthropic_anthropic_api_key_warns(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="anthropic_api_key"):
            AnthropicAdapter(anthropic_api_key="sk-test-key")

    def test_anthropic_custom_section_warns(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="custom_section"):
            AnthropicAdapter(custom_section="Be helpful.")

    def test_anthropic_api_key_and_anthropic_api_key_conflict(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            AnthropicAdapter(api_key="sk-new", anthropic_api_key="sk-old")

    def test_anthropic_prompt_and_custom_section_conflict(self) -> None:
        from thenvoi.adapters.anthropic import AnthropicAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            AnthropicAdapter(prompt="new", custom_section="old")

    def test_gemini_gemini_api_key_warns(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="gemini_api_key"):
            GeminiAdapter(gemini_api_key="AIza-test-key")

    def test_gemini_custom_section_warns(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="custom_section"):
            GeminiAdapter(custom_section="Be concise.")

    def test_gemini_api_key_and_gemini_api_key_conflict(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            GeminiAdapter(api_key="AIza-new", gemini_api_key="AIza-old")

    def test_gemini_prompt_and_custom_section_conflict(self) -> None:
        from thenvoi.adapters.gemini import GeminiAdapter

        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            GeminiAdapter(prompt="new", custom_section="old")


class TestOpencodeDeprecationShims:
    """Opencode config booleans must warn when non-default."""

    def test_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

        config = OpencodeAdapterConfig(enable_execution_reporting=True)
        with pytest.warns(
            DeprecationWarning,
            match="enable_memory_tools and enable_execution_reporting",
        ):
            OpencodeAdapter(config=config)

    def test_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

        config = OpencodeAdapterConfig(enable_memory_tools=True)
        with pytest.warns(
            DeprecationWarning,
            match="enable_memory_tools and enable_execution_reporting",
        ):
            OpencodeAdapter(config=config)

    def test_conflict_raises(self) -> None:
        from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

        config = OpencodeAdapterConfig(enable_execution_reporting=True)
        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            OpencodeAdapter(config=config, features=AdapterFeatures())


class TestLettaDeprecationShims:
    """Letta config booleans must warn when non-default."""

    def test_enable_execution_reporting_warns(self) -> None:
        from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

        config = LettaAdapterConfig(enable_execution_reporting=True)
        with pytest.warns(
            DeprecationWarning,
            match="enable_memory_tools and enable_execution_reporting",
        ):
            LettaAdapter(config=config)

    def test_enable_memory_tools_warns(self) -> None:
        from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

        config = LettaAdapterConfig(enable_memory_tools=True)
        with pytest.warns(
            DeprecationWarning,
            match="enable_memory_tools and enable_execution_reporting",
        ):
            LettaAdapter(config=config)

    def test_conflict_raises(self) -> None:
        from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

        config = LettaAdapterConfig(enable_memory_tools=True)
        with pytest.raises(ThenvoiConfigError, match="Cannot pass both"):
            LettaAdapter(config=config, features=AdapterFeatures())
