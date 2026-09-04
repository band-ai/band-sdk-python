"""Tests pinning the deprecation-shim contract for adapter constructors.

These tests guarantee that the legacy api-key / prompt parameters still work
for one release with a clear DeprecationWarning. When the shims are
eventually removed, these tests should be deleted in the same commit.

The legacy ``enable_execution_reporting`` / ``enable_memory_tools`` boolean ->
``features=`` shims (and the Codex/Letta/Opencode config-boolean variants) were
removed outright rather than deprecated: the flattened ``emit=``/``capabilities=``
constructor kwargs (see ``FeatureKwargs``) are the only surface now, so there was
nothing left to shim.
"""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError


class TestSelectiveRenameShims:
    """Anthropic and Gemini get the api_key/prompt selective renames."""

    def test_anthropic_anthropic_api_key_warns(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="anthropic_api_key"):
            AnthropicAdapter(anthropic_api_key="sk-test-key")

    def test_anthropic_anthropic_api_key_resolves_to_provider_key(self) -> None:
        from unittest.mock import patch

        from band.adapters.anthropic import AnthropicAdapter

        with patch("band.adapters.anthropic.AsyncAnthropic") as mock_cls:
            with pytest.warns(DeprecationWarning, match="anthropic_api_key"):
                AnthropicAdapter(anthropic_api_key="sk-old-key")
        mock_cls.assert_called_once_with(api_key="sk-old-key")

    def test_anthropic_api_key_warns(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.warns(
            DeprecationWarning, match="api_key.*deprecated.*provider_key"
        ):
            AnthropicAdapter(api_key="sk-test-key")

    def test_anthropic_api_key_resolves_to_provider_key(self) -> None:
        from unittest.mock import patch

        from band.adapters.anthropic import AnthropicAdapter

        with patch("band.adapters.anthropic.AsyncAnthropic") as mock_cls:
            with pytest.warns(
                DeprecationWarning, match="api_key.*deprecated.*provider_key"
            ):
                AnthropicAdapter(api_key="sk-test-key")
        mock_cls.assert_called_once_with(api_key="sk-test-key")

    def test_anthropic_custom_section_warns(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.warns(DeprecationWarning, match="custom_section"):
            AnthropicAdapter(custom_section="Be helpful.")

    def test_anthropic_provider_key_and_api_key_conflict(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            AnthropicAdapter(provider_key="sk-new", api_key="sk-old")

    def test_anthropic_anthropic_api_key_and_provider_key_conflict(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.raises(BandConfigError, match="Cannot pass"):
            AnthropicAdapter(provider_key="sk-new", anthropic_api_key="sk-old")

    def test_anthropic_prompt_and_custom_section_conflict(self) -> None:
        from band.adapters.anthropic import AnthropicAdapter

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            AnthropicAdapter(prompt="new", custom_section="old")

    def test_gemini_gemini_api_key_warns(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="gemini_api_key"):
            GeminiAdapter(gemini_api_key="AIza-test-key")

    def test_gemini_gemini_api_key_resolves_to_provider_key(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="gemini_api_key"):
            adapter = GeminiAdapter(gemini_api_key="AIza-old-key")
        assert adapter._provider_key == "AIza-old-key"

    def test_gemini_api_key_warns(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.warns(
            DeprecationWarning, match="api_key.*deprecated.*provider_key"
        ):
            GeminiAdapter(api_key="AIza-test-key")

    def test_gemini_api_key_resolves_to_provider_key(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.warns(
            DeprecationWarning, match="api_key.*deprecated.*provider_key"
        ):
            adapter = GeminiAdapter(api_key="AIza-test-key")
        assert adapter._provider_key == "AIza-test-key"

    def test_gemini_custom_section_warns(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.warns(DeprecationWarning, match="custom_section"):
            GeminiAdapter(custom_section="Be concise.")

    def test_gemini_provider_key_and_api_key_conflict(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            GeminiAdapter(provider_key="AIza-new", api_key="AIza-old")

    def test_gemini_gemini_api_key_and_provider_key_conflict(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.raises(BandConfigError, match="Cannot pass"):
            GeminiAdapter(provider_key="AIza-new", gemini_api_key="AIza-old")

    def test_gemini_prompt_and_custom_section_conflict(self) -> None:
        from band.adapters.gemini import GeminiAdapter

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            GeminiAdapter(prompt="new", custom_section="old")


class TestLettaApiKeyShim:
    """LettaAdapterConfig.api_key must warn and resolve to provider_key."""

    def test_letta_api_key_warns(self) -> None:
        from band.adapters.letta import LettaAdapterConfig

        with pytest.warns(
            DeprecationWarning, match="api_key.*deprecated.*provider_key"
        ):
            config = LettaAdapterConfig(api_key="letta-key")
        assert config.provider_key == "letta-key"
        # api_key is a constructor-only shim, not a model field: a field would
        # be exposed to the environment (bare API_KEY is too generic to read).
        assert "api_key" not in LettaAdapterConfig.model_fields

    def test_letta_provider_key_and_api_key_conflict(self) -> None:
        from band.adapters.letta import LettaAdapterConfig

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            LettaAdapterConfig(provider_key="new-key", api_key="old-key")


class TestLettaOrgScopedConfig:
    """org_scoped=True against Letta Cloud must fail at construction.

    Letta Cloud does not expose the self-hosted-only admin API org_scoped
    needs; honoring it would only fail deep inside on_started's real httpx
    calls instead of failing loud up front.
    """

    @pytest.mark.parametrize(
        "base_url", ["https://api.letta.com", "https://API.LETTA.COM/"]
    )
    def test_letta_org_scoped_and_cloud_conflict(self, base_url: str) -> None:
        from band.adapters.letta import LettaAdapterConfig

        with pytest.raises(BandConfigError, match="org_scoped=True"):
            LettaAdapterConfig(base_url=base_url, org_scoped=True)

    def test_letta_org_scoped_true_on_self_hosted_is_accepted(self) -> None:
        from band.adapters.letta import LettaAdapterConfig

        config = LettaAdapterConfig(base_url="http://localhost:8283", org_scoped=True)
        assert config.org_scoped is True


class TestLettaMCPKwargShim:
    """Legacy Letta MCP kwargs must populate the nested MCP config."""

    def test_legacy_mcp_kwargs_warn_and_populate_external_config(self) -> None:
        from band.adapters.letta import LettaAdapterConfig

        with pytest.warns(
            DeprecationWarning,
            match="mcp_server_url.*mcp_server_name.*mcp=LettaMCPConfig",
        ):
            config = LettaAdapterConfig(
                mcp_server_url="http://mcp:9000/sse",
                mcp_server_name="legacy-band",
            )

        assert config.mcp.mode == "external"
        assert config.mcp.server_url == "http://mcp:9000/sse"
        assert config.mcp.server_name == "legacy-band"
        assert config.mcp_server_url is None
        assert config.mcp_server_name is None
