"""Tests for transport security configuration.

These tests verify that band-mcp properly exposes DNS rebinding protection
settings, allowing users to configure allowed hosts for Docker/remote deployments.
"""

from __future__ import annotations

import pytest

from band.integrations.mcp.engine import build_engine
from band_mcp.config import Config, Settings, Transport, settings
from band_mcp.server import _build_transport_security, standalone_spec
from band_mcp.shared import build_standalone_resolver


class TestTransportSecuritySettings:
    """Tests for band-mcp transport security configuration."""

    def test_default_enables_dns_rebinding_protection(self) -> None:
        """DNS rebinding protection should be enabled by default for security."""
        settings = Settings()

        assert settings.enable_dns_rebinding_protection is True

    def test_default_allowed_hosts_is_empty(self) -> None:
        """Allowed hosts should be empty by default (users must configure)."""
        settings = Settings()

        assert settings.allowed_hosts == []

    def test_default_allowed_origins_is_empty(self) -> None:
        """Allowed origins should be empty by default."""
        settings = Settings()

        assert settings.allowed_origins == []

    def test_can_disable_protection_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to disable protection via environment variable."""
        monkeypatch.setenv("ENABLE_DNS_REBINDING_PROTECTION", "false")

        settings = Settings()

        assert settings.enable_dns_rebinding_protection is False

    def test_can_configure_allowed_hosts_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to configure allowed hosts via environment variable."""
        monkeypatch.setenv("ALLOWED_HOSTS", '["localhost:*", "host.docker.internal:*"]')

        settings = Settings()

        assert settings.allowed_hosts == ["localhost:*", "host.docker.internal:*"]

    def test_can_configure_allowed_origins_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to configure allowed origins via environment variable."""
        monkeypatch.setenv("ALLOWED_ORIGINS", '["http://localhost:3000"]')

        settings = Settings()

        assert settings.allowed_origins == ["http://localhost:3000"]


class TestMcpTransportSecurityIntegration:
    """The engine, built via the CLI's own factories, carries transport security.

    ``server.py`` builds a fresh engine per ``run()`` call -- no
    module-level FastMCP singleton to import. Build one here the same way
    (``standalone_spec`` + ``build_engine`` with ``_build_transport_security()``).
    """

    def _build_mcp(self) -> object:
        config = Config(scope=["agent"], agent_key="band_a_test")
        resolver = build_standalone_resolver(config)
        return build_engine(
            standalone_spec(config, resolver),
            transport_security=_build_transport_security(settings.transport),
        )

    def test_mcp_transport_security_reflects_settings(self) -> None:
        """Transport security should reflect the configured settings."""
        mcp = self._build_mcp()
        transport_security = mcp.settings.transport_security

        assert (
            transport_security.enable_dns_rebinding_protection
            == settings.enable_dns_rebinding_protection
        )
        assert transport_security.allowed_hosts == settings.allowed_hosts
        assert transport_security.allowed_origins == settings.allowed_origins

    def test_warns_on_cli_transport_even_without_env_var(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The warning must judge the CLI-resolved transport, not just TRANSPORT.

        A ``--transport sse`` flag with no ``TRANSPORT`` env var leaves
        ``settings.transport`` at its stdio default; the warning has to be
        driven by ``args.transport or settings.transport`` (what ``run()``
        actually starts with) or it never fires despite the server coming up
        in SSE mode with an empty ``allowed_hosts``.
        """
        assert settings.transport == Transport.STDIO
        with caplog.at_level("WARNING"):
            _build_transport_security(Transport.SSE)

        assert any(
            "DNS rebinding protection enabled" in record.message
            for record in caplog.records
        )
