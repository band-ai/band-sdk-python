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

from band.adapters.letta import LettaAdapterConfig, LettaMCPConfig
from band.core.exceptions import BandConfigError


class TestLettaApiKeyShim:
    """LettaAdapterConfig.api_key must warn and resolve to provider_key."""

    def test_letta_api_key_warns(self) -> None:

        with pytest.warns(
            DeprecationWarning, match="api_key.*deprecated.*provider_key"
        ):
            config = LettaAdapterConfig(api_key="letta-key")
        assert config.provider_key == "letta-key"
        # api_key is a constructor-only shim, not a model field: a field would
        # be exposed to the environment (bare API_KEY is too generic to read).
        assert "api_key" not in LettaAdapterConfig.model_fields

    def test_letta_provider_key_and_api_key_conflict(self) -> None:

        with pytest.raises(BandConfigError, match="Cannot pass both"):
            LettaAdapterConfig(provider_key="new-key", api_key="old-key")


class TestLettaOrgScopedConfig:
    """org_scoped=True against Letta Cloud must fail at construction.

    Letta Cloud does not expose the self-hosted-only admin API org_scoped
    needs; honoring it would only fail deep inside on_started's real httpx
    calls instead of failing loud up front.
    """

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.letta.com",
            "https://API.LETTA.COM/",
            "  https://api.letta.com  ",
            "api.letta.com",
        ],
    )
    def test_letta_org_scoped_and_cloud_conflict(self, base_url: str) -> None:
        with pytest.raises(BandConfigError, match="org_scoped=True"):
            LettaAdapterConfig(base_url=base_url, org_scoped=True)

    def test_letta_org_scoped_true_on_self_hosted_is_accepted(self) -> None:
        config = LettaAdapterConfig(base_url="http://localhost:8283", org_scoped=True)
        assert config.org_scoped is True


class TestLettaMCPKwargShim:
    """Legacy Letta MCP kwargs must populate the nested MCP config."""

    def test_legacy_mcp_kwargs_warn_and_populate_external_config(self) -> None:

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

    def test_legacy_mcp_kwarg_keeps_the_nested_transport(self) -> None:
        with pytest.warns(DeprecationWarning):
            config = LettaAdapterConfig(
                mcp=LettaMCPConfig(transport="streamable_http"),
                mcp_server_url="http://mcp:9000/mcp",
            )

        assert config.mcp == LettaMCPConfig(
            mode="external",
            server_url="http://mcp:9000/mcp",
            transport="streamable_http",
        )

    def test_legacy_mcp_env_var_populates_external_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LETTA_MCP_SERVER_URL", "http://mcp:9000/sse")

        with pytest.warns(DeprecationWarning):
            config = LettaAdapterConfig()

        assert config.mcp.mode == "external"
        assert config.mcp.server_url == "http://mcp:9000/sse"
